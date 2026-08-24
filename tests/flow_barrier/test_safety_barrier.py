from __future__ import annotations

import ast
import copy
import gc
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cam_automation.asset_registry import AssetRegistryError
from cam_automation.adapters.powermill_macro import PowerMillOfflineImportError
from cam_automation.flow_roundtrip import SourcePatch, verify_round_trip
from cam_automation.flow_service import FlowServiceError
from cam_automation.flow_validation import validate_contract, validate_flow_graph
from cam_automation.flow_versions import prepare_graph_snapshot
from cam_automation.integrations import FlowIntegrationError

from ._support import (
    FIXED_TIME,
    ROOT,
    VERSION_FIXTURE_ROOT,
    BarrierHarness,
    load_fixture,
    samples,
)


class RejectingRoundTripAdapter:
    def __init__(self, target: dict) -> None:
        self.target = target

    def validate_candidate(self, candidate: bytes, *, target_graph: dict):
        return [
            {
                "code": "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
                "message": "Rejected category.",
            }
        ]

    def reparse_candidate(self, candidate: bytes, *, target_graph: dict):
        return copy.deepcopy(self.target)


class FlowSafetyBarrierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payloads = load_fixture("adversarial-payloads.json")
        self.marker = self.payloads["marker"]
        self.directory = tempfile.TemporaryDirectory()
        self.harness = BarrierHarness(self.directory.name)
        self.sample = samples()[0]
        self.imported = self.harness.import_sample(self.sample)
        _compatibility, _version, request = self.harness.prepare_preview(
            self.imported["graph"],
            self.sample,
            plan_id="preview:t10:safety-template",
        )
        self.plan = self.harness.service.create_preview_plan(request)

    def tearDown(self) -> None:
        if hasattr(self, "harness"):
            del self.harness
            self.directory.cleanup()
        gc.collect()

    def test_live_journal_macro_cam_command_and_machine_output_are_rejected(self) -> None:
        for mutation in self.payloads["preview_mutations"]:
            with self.subTest(case=mutation["name"]):
                value = copy.deepcopy(self.plan)
                value[mutation["field"]] = mutation["value"]
                result = validate_contract(value, "preview_plan")
                codes = {item.code for item in result.diagnostics}
                self.assertIn(mutation["code"], codes)
                self.assertNotIn(
                    self.marker,
                    json.dumps(
                        [item.to_dict() for item in result.diagnostics],
                        sort_keys=True,
                    ),
                )

    def test_nc_gcode_clsf_postprocess_machine_control_and_injection_are_rejected(
        self,
    ) -> None:
        graph = self.imported["graph"]
        for mutation in self.payloads["graph_mutations"]:
            with self.subTest(case=mutation["name"]):
                value = copy.deepcopy(graph)
                value["flows"][0]["nodes"][1]["configuration"][
                    mutation["field"]
                ] = mutation["value"]
                result = validate_flow_graph(value)
                codes = {item.code for item in result.diagnostics}
                self.assertIn(mutation["code"], codes)
                diagnostics = json.dumps(
                    [item.to_dict() for item in result.diagnostics],
                    sort_keys=True,
                )
                self.assertNotIn(self.marker, diagnostics)

    def test_rejected_payload_never_reaches_storage_diff_report_logs_or_ai_state(
        self,
    ) -> None:
        graph = self.imported["graph"]
        rejected = copy.deepcopy(graph)
        rejected["parent_revision_id"] = graph["revision_id"]
        rejected["revision_id"] = f"{graph['revision_id']}:rejected"
        rejected["flows"][0]["nodes"][1]["configuration"]["nc"] = self.marker

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with self.assertRaises(FlowServiceError) as caught:
                self.harness.service.save_graph(
                    rejected,
                    expected_revision_id=graph["revision_id"],
                )
        finally:
            root_logger.removeHandler(handler)
        self.assertEqual(
            "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
            caught.exception.code,
        )
        current = self.harness.service.get_graph(graph["graph_id"])
        diff = self.harness.service.diff_graphs(
            graph["graph_id"],
            graph["revision_id"],
            graph["revision_id"],
        )

        bundle = json.loads(
            (VERSION_FIXTURE_ROOT / "linear-flow.json").read_text(encoding="utf-8")
        )
        original = prepare_graph_snapshot(bundle["graph"]).to_dict()
        target = copy.deepcopy(original)
        target["graph_parameters"][0]["default"] = 2
        target = prepare_graph_snapshot(target).to_dict()
        round_trip = verify_round_trip(
            original,
            target,
            bundle["source_text"].encode("utf-8"),
            [SourcePatch("mapping:value", self.marker.encode("utf-8"))],
            RejectingRoundTripAdapter(target),
            checked_at=FIXED_TIME,
        )
        self.assertIsNone(round_trip.candidate)

        rejection_surfaces = {
            "error": caught.exception.to_dict(),
            "stored_graph": current,
            "diff": diff,
            "round_trip_report": round_trip.report.to_dict(),
            "logs": stream.getvalue(),
            "ai_context": {
                "codex_request": None,
                "codex_review": None,
            },
        }
        self.assertNotIn(
            self.marker,
            json.dumps(rejection_surfaces, sort_keys=True),
        )
        persisted = b"".join(
            path.read_bytes()
            for path in Path(self.directory.name).rglob("*")
            if path.is_file()
        )
        self.assertNotIn(self.marker.encode("utf-8"), persisted)

    def test_machine_sources_and_commercial_binary_fail_before_registration(self) -> None:
        cases = (
            ("nx", self.payloads["nx_machine_source"], "blocked.py"),
            (
                "powermill",
                self.payloads["powermill_machine_source"],
                "blocked.tap",
            ),
        )
        for product, source, source_name in cases:
            with self.subTest(product=product):
                with self.assertRaises(
                    (FlowIntegrationError, PowerMillOfflineImportError)
                ) as caught:
                    self.harness.integration.import_source(
                        product=product,
                        source=source,
                        source_name=source_name,
                        target_versions=[
                            "NX 2406" if product == "nx" else "PowerMill 2025"
                        ],
                    )
                self.assertEqual(
                    "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
                    caught.exception.code,
                )
                self.assertNotIn(self.marker, str(caught.exception))

        before = len(self.harness.service.asset_registry.list_revisions())
        with self.assertRaises(AssetRegistryError) as binary:
            self.harness.service.register_asset(
                self.payloads["binary_payload"].encode("utf-8"),
                asset_id="asset:t10:commercial-binary",
                product="powermill",
                asset_type="vendor_export",
                display_name="commercial-addon.dll",
                source_locator="selected-file",
            )
        self.assertEqual(
            "ASSET_BINARY_INSPECTION_FORBIDDEN",
            binary.exception.code,
        )
        self.assertEqual(
            before,
            len(self.harness.service.asset_registry.list_revisions()),
        )

    def test_public_import_calls_only_the_selected_product_parser(self) -> None:
        nx_sample = samples()[3]
        pm_sample = samples()[0]
        from cam_automation.adapters.powermill_flow import PowerMillFlowImporter
        from ugcam_ai.adapters.nx_flow import NxFlowMapper

        with patch.object(
            NxFlowMapper,
            "map_source",
            side_effect=AssertionError("PowerMill called the NX parser"),
        ) as nx_parser:
            self.harness.import_sample(pm_sample)
        nx_parser.assert_not_called()

        with patch.object(
            PowerMillFlowImporter,
            "import_source",
            side_effect=AssertionError("NX called the PowerMill parser"),
        ) as pm_parser:
            self.harness.import_sample(nx_sample)
        pm_parser.assert_not_called()

    def test_parser_source_dependencies_do_not_cross_product_boundaries(self) -> None:
        paths = {
            "nx": (
                ROOT
                / "plugins"
                / "ug-cam-copilot"
                / "src"
                / "ugcam_ai"
                / "adapters"
                / "nx_journal.py"
            ),
            "powermill": ROOT / "cam_automation" / "adapters" / "powermill_macro.py",
        }
        imports = {}
        for product, path in paths.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports[product] = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
        self.assertFalse(
            any("powermill" in name.casefold() for name in imports["nx"])
        )
        self.assertFalse(
            any(
                "ugcam_ai" in name.casefold() or "nx_journal" in name.casefold()
                for name in imports["powermill"]
            )
        )


if __name__ == "__main__":
    unittest.main()
