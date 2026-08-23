from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from cam_automation.flow_contracts import (
    CONTRACT_MODELS,
    SCHEMA_FILENAMES,
    canonical_json,
    compute_artifact_hash,
    compute_semantic_hash,
    compute_source_snapshot_hash,
    contract_model,
    load_schema,
    parse_contract_json,
    round_trip_contract,
)
from cam_automation.flow_validation import schema_files, validate_contract


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "cam-flow" / "contracts"
)


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class FlowContractModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.minimal = fixture("minimal-contracts.json")
        cls.complete = fixture("complete-flow-bundle.json")
        cls.unknown = fixture("unknown-fields.json")

    def test_all_twelve_contract_models_round_trip(self) -> None:
        self.assertEqual(set(CONTRACT_MODELS), set(self.minimal))
        for schema_name, value in self.minimal.items():
            with self.subTest(schema_name=schema_name):
                model = contract_model(value, schema_name)
                self.assertEqual(value, model.to_dict())
                self.assertEqual(value, round_trip_contract(value, schema_name))

    def test_models_are_immutable_snapshots(self) -> None:
        source = copy.deepcopy(self.minimal["flow_graph"])
        model = contract_model(source, "flow_graph")
        source["layout"]["nodes"]["changed"] = True
        returned = model.to_dict()
        returned["layout"]["nodes"]["also_changed"] = True

        self.assertNotIn("changed", model.layout["nodes"])
        self.assertNotIn("also_changed", model.to_dict()["layout"]["nodes"])

    def test_strict_json_parser_rejects_duplicates_and_nonfinite_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_contract_json(
                '{"node_id":"first","node_id":"second"}',
                "flow_node",
            )
        with self.assertRaisesRegex(ValueError, "numeric constant"):
            parse_contract_json('{"value":NaN}', "flow_node")

    def test_unknown_optional_fields_are_preserved_exactly(self) -> None:
        graph = copy.deepcopy(self.minimal["flow_graph"])
        graph.update(self.unknown["top_level_future"])
        graph["flows"][0]["nodes"][0].update(self.unknown["node_future"])
        graph["extensions"].update(self.unknown["nonsemantic_extension"])
        graph["extensions"].update(self.unknown["semantic_extension"])

        self.assertEqual(graph, round_trip_contract(graph, "flow_graph"))
        read_only = validate_contract(graph, "flow_graph")
        self.assertTrue(read_only.valid)

        editable = validate_contract(
            graph,
            "flow_graph",
            require_known_semantics=True,
        )
        self.assertIn("FLOW_UNKNOWN_SEMANTICS", editable.blocker_codes)
        refs = {item.object_ref for item in editable.diagnostics}
        self.assertIn("$.future_contract_note", refs)
        self.assertIn("$.flows[0].nodes[0].future_node_option", refs)
        self.assertIn(
            "$.extensions.vendor.example/process_rule",
            refs,
        )
        self.assertNotIn(
            "$.extensions.vendor.example/view_state",
            refs,
        )

    def test_rfc8785_key_number_and_unicode_profile(self) -> None:
        self.assertEqual(
            '{"a":0.000001,"b":1,"c":1e+21,"d":0}',
            canonical_json({"d": -0.0, "c": 1e21, "b": 1.0, "a": 1e-6}),
        )
        self.assertEqual(
            canonical_json({"😀": 1, "a": 2, "€": 3}),
            canonical_json({"€": 3, "😀": 1, "a": 2}),
        )
        self.assertNotEqual(canonical_json({"é": 1}), canonical_json({"e\u0301": 1}))
        with self.assertRaisesRegex(ValueError, "IEEE-754"):
            canonical_json(9_007_199_254_740_992)

    def test_semantic_hash_excludes_layout_source_and_review_metadata(self) -> None:
        first = copy.deepcopy(self.complete["flow_graph"])
        second = copy.deepcopy(first)
        second["graph_id"] = "graph:renamed"
        second["revision_id"] = "revision:other"
        second["layout"] = {"nodes": {"new": {"x": 999, "y": 999}}}
        second["flows"][0]["name"] = "Renamed flow"
        second["flows"][0]["nodes"][0]["review_status"] = "needs_review"
        second["flows"][0]["nodes"].reverse()
        second["flows"][0]["edges"].reverse()
        second["source_mappings"] = [
            copy.deepcopy(self.minimal["source_mapping"])
        ]

        self.assertEqual(
            compute_semantic_hash(first),
            compute_semantic_hash(second),
        )

    def test_semantic_hash_changes_for_configuration_and_semantic_extension(self) -> None:
        base = copy.deepcopy(self.complete["flow_graph"])
        configuration_change = copy.deepcopy(base)
        configuration_change["flows"][0]["nodes"][1]["configuration"]["value"] = 0.02
        extension_change = copy.deepcopy(base)
        extension_change["extensions"]["fixture.example/process_rule"]["value"][
            "fixture_only"
        ] = False
        nonsemantic_change = copy.deepcopy(base)
        nonsemantic_change["extensions"]["fixture.example/view"] = {
            "semantic": False,
            "value": {"zoom": 2}
        }

        base_hash = compute_semantic_hash(base)
        self.assertNotEqual(base_hash, compute_semantic_hash(configuration_change))
        self.assertNotEqual(base_hash, compute_semantic_hash(extension_change))
        self.assertEqual(base_hash, compute_semantic_hash(nonsemantic_change))

    def test_source_snapshot_hash_sorts_assets_and_includes_text_profile(self) -> None:
        first = copy.deepcopy(self.minimal["flow_graph"]["asset_refs"][0])
        second = {
            "asset_id": "asset:fixture:2",
            "asset_revision_id": "asset-revision:fixture:2",
            "content_hash": (
                "sha256:"
                "5555555555555555555555555555555555555555555555555555555555555555"
            ),
            "encoding": "utf-8",
            "bom": "none",
            "newline_profile": "lf",
        }
        forward = compute_source_snapshot_hash([first, second])
        reverse = compute_source_snapshot_hash([second, first])
        changed = copy.deepcopy(second)
        changed["newline_profile"] = "crlf"

        self.assertEqual(forward, reverse)
        self.assertNotEqual(forward, compute_source_snapshot_hash([first, changed]))

    def test_artifact_hash_accepts_exact_bytes_or_canonical_json(self) -> None:
        value = {"b": 2, "a": 1}
        self.assertEqual(
            compute_artifact_hash(value),
            compute_artifact_hash({"a": 1, "b": 2}),
        )
        self.assertNotEqual(
            compute_artifact_hash(b'{"a":1,"b":2}'),
            compute_artifact_hash(b'{ "a": 1, "b": 2 }'),
        )

    def test_hashes_are_deterministic_for_twenty_runs(self) -> None:
        graph = self.complete["flow_graph"]
        semantic = {compute_semantic_hash(graph) for _ in range(20)}
        source = {compute_source_snapshot_hash(graph) for _ in range(20)}
        artifact = {compute_artifact_hash(graph) for _ in range(20)}

        self.assertEqual(1, len(semantic))
        self.assertEqual(1, len(source))
        self.assertEqual(1, len(artifact))

    def test_schema_entry_files_are_complete_and_parseable(self) -> None:
        files = schema_files()
        filenames = {path.name for path in files}
        self.assertEqual(13, len(files))
        self.assertEqual(12, len(SCHEMA_FILENAMES))
        for schema_name, filename in SCHEMA_FILENAMES.items():
            with self.subTest(schema_name=schema_name):
                schema = load_schema(schema_name)
                self.assertIn(filename, filenames)
                self.assertIn("$schema", schema)
                self.assertIn("$ref", schema)


if __name__ == "__main__":
    unittest.main()
