from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from cam_automation.adapters.powermill_flow import (
    PowerMillFlowImporter,
    import_powermill_flow,
    round_trip_powermill_bytes,
)
from cam_automation.adapters.powermill_macro import (
    FixturePowerMillTransport,
    PowerMillOfflineImportError,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "flow"


def _nodes(graph: dict[str, object]) -> list[dict[str, object]]:
    return graph["flows"][0]["nodes"]  # type: ignore[index,return-value]


class PowerMillFlowFidelityTests(unittest.TestCase):
    def test_f0_preserves_bom_mixed_newlines_tokens_trivia_and_envelope(self) -> None:
        fixture = json.loads(
            (FIXTURES / "mixed-fidelity.json").read_text(encoding="utf-8")
        )
        source = base64.b64decode(fixture["base64"], validate=True)
        importer = PowerMillFlowImporter(
            source_name="mixed-fidelity.mac",
            target_versions=["PowerMill 2025"],
        )

        graph = importer.import_source(source)
        extension = graph["extensions"]["powermill.offline_import"]
        artifact = extension["source_artifact"]
        cst = extension["lossless_cst"]

        self.assertEqual("cam.flowgraph.v1", graph["contract"])
        self.assertEqual("powermill", graph["product"])
        self.assertEqual(f"sha256:{fixture['sha256']}", artifact["content_hash"])
        self.assertEqual(fixture["byte_length"], artifact["byte_length"])
        self.assertEqual("utf-8", artifact["encoding"])
        self.assertEqual("utf8", artifact["bom"])
        self.assertEqual("mixed", artifact["newline_profile"])
        self.assertEqual(source, importer.round_trip_bytes())
        self.assertEqual(source, round_trip_powermill_bytes(graph))
        self.assertEqual(source, base64.b64decode(artifact["raw_bytes_base64"]))

        decoded = source[3:].decode("utf-8")
        reconstructed = "".join(
            token["raw"]
            for token in cst["tokens"]
            if token["kind"] != "trivia.bom"
        )
        self.assertEqual(decoded, reconstructed)
        self.assertTrue(any(token["kind"] == "trivia.newline" for token in cst["tokens"]))
        self.assertTrue(any(token["kind"] == "envelope" for token in cst["tokens"]))
        self.assertTrue(any(token["kind"] == "string" for token in cst["tokens"]))

        for mapping in graph["source_mappings"]:
            span = mapping["source_span"]
            excerpt = source[span["start_byte"]:span["end_byte"]]
            self.assertEqual(
                f"sha256:{hashlib.sha256(excerpt).hexdigest()}",
                mapping["excerpt_hash"],
                mapping["mapping_id"],
            )
            self.assertEqual(span["start_line"], mapping["source_line"])
        self.assertTrue(
            any(
                "property_path" in mapping["target"]
                for mapping in graph["source_mappings"]
            )
        )
        self.assertTrue(
            all(
                node["node_type"].startswith(("cam.", "powermill."))
                for node in _nodes(graph)
            )
        )
        self.assertEqual(0, extension["coverage"]["commands_sent"])
        self.assertFalse(extension["coverage"]["macro_executed"])
        self.assertEqual(0, extension["coverage"]["machine_output_count"])
        self.assertEqual("none", extension["coverage"]["transport"])

    def test_command_log_envelopes_keep_exact_command_and_line_spans(self) -> None:
        path = FIXTURES / "command-envelope.log"
        source = path.read_bytes()
        importer = PowerMillFlowImporter(
            source_name=path.name,
            target_versions=["PowerMill 2025"],
        )

        graph = importer.import_source(path)
        statements = graph["extensions"]["powermill.offline_import"]["lossless_cst"][
            "statements"
        ]

        self.assertEqual(2, len(statements))
        self.assertEqual(
            ["2026-08-24T08:00:00Z", "2026-08-24T08:00:01Z"],
            [statement["envelope"]["timestamp"] for statement in statements],
        )
        self.assertTrue(all(statement["envelope"]["prompted"] for statement in statements))
        for statement in statements:
            span = statement["command_span"]
            command_bytes = source[span["start_byte"]:span["end_byte"]]
            self.assertEqual(statement["command"], command_bytes.decode("utf-8"))
            self.assertEqual(span["start_line"], span["end_line"])


class PowerMillFlowSemanticTests(unittest.TestCase):
    def test_control_flow_variables_and_unknown_recorded_state_are_explicit(self) -> None:
        graph = import_powermill_flow(
            FIXTURES / "control-flow.mac",
            target_versions=["PowerMill 2025"],
        )
        nodes = _nodes(graph)
        node_types = {node["node_type"] for node in nodes}
        edge_roles = {
            edge["extensions"]["powermill.control_role"]
            for edge in graph["flows"][0]["edges"]
        }

        self.assertTrue(
            {
                "powermill.function.declare",
                "powermill.variable.declare",
                "powermill.variable.assign",
                "powermill.control.while",
                "powermill.control.if",
                "powermill.control.break",
                "powermill.control.else",
                "powermill.function.return",
            }.issubset(node_types)
        )
        self.assertTrue(
            {"branch_true", "branch_false", "loop_back", "loop_exit", "break"}.issubset(
                edge_roles
            )
        )
        self.assertTrue(
            any(edge["kind"] == "data" for edge in graph["flows"][0]["edges"])
        )
        semantic_nodes = [
            node for node in nodes if node["node_type"].startswith("powermill.")
        ]
        function = next(
            node for node in nodes if node["node_type"] == "powermill.function.declare"
        )
        self.assertEqual(
            "unknown",
            function["configuration"]["recorded"]["parameters"][0][
                "recorded_default"
            ],
        )
        self.assertTrue(
            all(
                node["configuration"]["preconditions"]["project_units"] == "unknown"
                and node["configuration"]["preconditions"]["selection_state"] == "unknown"
                and node["configuration"]["preconditions"]["active_entity"] == "unknown"
                for node in semantic_nodes
            )
        )
        self.assertTrue(
            all(
                mapping["source_span"]["end_byte"]
                >= mapping["source_span"]["start_byte"]
                for mapping in graph["source_mappings"]
            )
        )

    def test_dynamic_docommand_missing_include_and_vendor_syntax_are_visible(self) -> None:
        path = FIXTURES / "unsupported.mac"
        first_importer = PowerMillFlowImporter(
            source_name=path.name,
            target_versions=["PowerMill 2025"],
        )
        first = first_importer.import_source(path)
        semantic_nodes = _nodes(first)[1:-1]

        baseline_json = json.dumps(first, ensure_ascii=False, sort_keys=True)
        for _ in range(19):
            repeated_importer = PowerMillFlowImporter(
                source_name=path.name,
                target_versions=["PowerMill 2025"],
            )
            repeated = repeated_importer.import_source(path)
            self.assertEqual(first["semantic_hash"], repeated["semantic_hash"])
            self.assertEqual(first_importer.diagnostics, repeated_importer.diagnostics)
            self.assertEqual(
                baseline_json,
                json.dumps(repeated, ensure_ascii=False, sort_keys=True),
            )
        self.assertEqual(
            [
                "CAPABILITY_MISSING",
                "CAPABILITY_MISSING",
                "FLOW_UNKNOWN_SEMANTICS",
                "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
            ],
            [diagnostic["code"] for diagnostic in first_importer.diagnostics],
        )
        self.assertEqual(
            [
                "powermill.include",
                "powermill.unsupported.vendor",
                "powermill.command.dynamic",
                "powermill.command.dynamic",
                "powermill.command.dynamic",
            ],
            [node["node_type"] for node in semantic_nodes],
        )
        self.assertTrue(all(node["opaque"] is not None for node in semantic_nodes[:3]))
        self.assertEqual("blocked", semantic_nodes[2]["risk"])
        self.assertEqual("review", semantic_nodes[3]["risk"])
        self.assertIsNone(semantic_nodes[3]["opaque"])
        self.assertEqual("blocked", semantic_nodes[4]["risk"])
        self.assertEqual(
            ["SAFETY_MACHINE_OUTPUT_FORBIDDEN"],
            semantic_nodes[4]["opaque"]["diagnostic_codes"],
        )

        source = path.read_bytes()
        mapping_by_id = {
            mapping["mapping_id"]: mapping for mapping in first["source_mappings"]
        }
        for node in semantic_nodes[:3]:
            mapping = mapping_by_id[node["opaque"]["source_span_ids"][0]]
            span = mapping["source_span"]
            self.assertTrue(source[span["start_byte"]:span["end_byte"]])
            self.assertEqual(
                node["opaque"]["content_hash"],
                f"sha256:{hashlib.sha256(source[span['start_byte']:span['end_byte']]).hexdigest()}",
            )

        with_include = PowerMillFlowImporter(
            source_name=path.name,
            target_versions=["PowerMill 2025"],
            include_sources={"missing.mac": b"PRINT 1\n"},
        ).import_source(path)
        self.assertIsNone(_nodes(with_include)[1]["opaque"])

    def test_other_loops_switch_interaction_selection_and_state_stay_explicit(self) -> None:
        source = b"""\
FOREACH $item IN $items {
  CONTINUE
}
DO {
  $count = $count + 1
} WHILE $count < 2
SWITCH $count {
  CASE 1
    PRINT 1
  DEFAULT
    PRINT 2
}
INPUT $choice
NOGUI ON
SELECT TOOLPATH "Rough A"
ACTIVATE TOOL "T1"
MACRO "provided.mac"
"""
        graph = import_powermill_flow(
            source,
            source_name="language-families.mac",
            target_versions=["PowerMill 2025"],
            include_sources={"provided.mac": b"PRINT 1\n"},
        )
        nodes = _nodes(graph)
        node_types = {node["node_type"] for node in nodes}
        edge_roles = {
            edge["extensions"]["powermill.control_role"]
            for edge in graph["flows"][0]["edges"]
        }

        self.assertTrue(
            {
                "powermill.control.foreach",
                "powermill.control.continue",
                "powermill.control.do",
                "powermill.control.do_while",
                "powermill.control.switch",
                "powermill.control.case",
                "powermill.control.default",
                "powermill.interaction.input",
                "powermill.gui.nogui",
                "powermill.selection",
                "powermill.state.change",
                "powermill.macro.call",
            }.issubset(node_types)
        )
        self.assertTrue({"continue", "loop_back", "case", "default"}.issubset(edge_roles))
        interaction = next(
            node for node in nodes if node["node_type"] == "powermill.interaction.input"
        )
        self.assertEqual(
            "unknown",
            interaction["configuration"]["recorded"]["recorded_value"],
        )
        self.assertIsNone(
            next(node for node in nodes if node["node_type"] == "powermill.macro.call")[
                "opaque"
            ]
        )


class PowerMillFlowIsolationAndSafetyTests(unittest.TestCase):
    def test_jsonl_without_command_is_visible_parse_error(self) -> None:
        importer = PowerMillFlowImporter(target_versions=["PowerMill 2025"])
        graph = importer.import_source(
            b'{"schema_version":1,"product":"powermill","timestamp":"2026-08-24T00:00:00Z"}\n'
        )

        node = _nodes(graph)[1]
        self.assertEqual("blocked", node["risk"])
        self.assertIsNotNone(node["opaque"])
        self.assertEqual("FLOW_SCHEMA_INVALID", importer.diagnostics[0]["code"])

    def test_resource_limits_fail_closed_without_partial_graph(self) -> None:
        source = (
            "".join(f"IF ${index} {{\n" for index in range(65))
            + "}\n" * 65
        ).encode("ascii")

        with self.assertRaises(PowerMillOfflineImportError) as raised:
            import_powermill_flow(
                source,
                source_name="too-deep.mac",
                target_versions=["PowerMill 2025"],
            )

        self.assertEqual("RESOURCE_LIMIT_EXCEEDED", raised.exception.code)

    def test_nx_input_is_rejected_without_importing_or_calling_an_nx_parser(self) -> None:
        nx_source = b"import NXOpen\nsession = NXOpen.Session.GetSession()\n"
        with patch.object(
            importlib,
            "import_module",
            side_effect=AssertionError("no dynamic imports are allowed"),
        ) as dynamic_import:
            with self.assertRaises(PowerMillOfflineImportError) as raised:
                import_powermill_flow(nx_source, source_name="journal.py")

        self.assertEqual("FLOW_PRODUCT_MIXED", raised.exception.code)
        self.assertNotIn("NXOpen.Session.GetSession", str(raised.exception))
        dynamic_import.assert_not_called()

    def test_import_never_executes_attaches_sends_or_generates_machine_output(self) -> None:
        source = b'NCPROGRAM WRITE "out.tap"\nPRINT 1\n'
        with (
            patch.object(
                FixturePowerMillTransport,
                "query",
                side_effect=AssertionError("transport query must not run"),
            ) as query,
            patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("subprocess must not run"),
            ) as run,
            patch.object(
                subprocess,
                "Popen",
                side_effect=AssertionError("subprocess must not start"),
            ) as popen,
            patch.object(
                os,
                "system",
                side_effect=AssertionError("shell must not run"),
            ) as system,
            patch.object(
                ctypes,
                "CDLL",
                side_effect=AssertionError("native/COM libraries must not load"),
            ) as cdll,
        ):
            graph = import_powermill_flow(
                source,
                source_name="blocked.mac",
                target_versions=["PowerMill 2025"],
            )

        query.assert_not_called()
        run.assert_not_called()
        popen.assert_not_called()
        system.assert_not_called()
        cdll.assert_not_called()
        extension = graph["extensions"]["powermill.offline_import"]
        self.assertEqual(0, extension["coverage"]["commands_sent"])
        self.assertFalse(extension["coverage"]["macro_executed"])
        self.assertEqual(0, extension["coverage"]["machine_output_count"])
        self.assertEqual("blocked", _nodes(graph)[1]["risk"])
        self.assertEqual(
            "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
            _nodes(graph)[1]["opaque"]["diagnostic_codes"][0],
        )

        with self.assertRaises(PowerMillOfflineImportError) as raised:
            import_powermill_flow(b"G1 X1 Y2\n", source_name="machine-output.tap")
        self.assertEqual("SAFETY_MACHINE_OUTPUT_FORBIDDEN", raised.exception.code)
        self.assertNotIn("G1 X1 Y2", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
