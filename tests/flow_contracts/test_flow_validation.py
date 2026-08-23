from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from cam_automation.flow_validation import (
    ERROR_CODES,
    ResourceLimits,
    validate_contract,
    validate_flow_graph,
)


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "cam-flow" / "contracts"
)


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def codes(result) -> set[str]:
    return {item.code for item in result.diagnostics}


class FlowValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.minimal = fixture("minimal-contracts.json")
        cls.invalid = fixture("invalid-contracts.json")
        cls.complete = fixture("complete-flow-bundle.json")

    def test_all_twelve_minimal_objects_validate_independently(self) -> None:
        manifest = self.minimal["capability_manifest"]
        for schema_name, value in self.minimal.items():
            with self.subTest(schema_name=schema_name):
                result = validate_contract(
                    value,
                    schema_name,
                    capability_manifests=[manifest]
                    if schema_name == "flow_graph"
                    else None,
                )
                self.assertTrue(result.valid, result.to_dict())

    def test_all_twelve_error_fixtures_fail_schema(self) -> None:
        for schema_name, value in self.invalid.items():
            with self.subTest(schema_name=schema_name):
                result = validate_contract(value, schema_name)
                self.assertFalse(result.valid)
                self.assertIn("FLOW_SCHEMA_INVALID", codes(result))

    def test_namespace_and_single_product_fail_closed(self) -> None:
        graph = copy.deepcopy(self.minimal["flow_graph"])
        graph["flows"][0]["nodes"][0]["node_type"] = "powermill.operation.inspect"
        result = validate_flow_graph(graph)
        self.assertIn("FLOW_PRODUCT_MIXED", codes(result))

        graph["flows"][0]["nodes"][0]["node_type"] = "vendor.operation.inspect"
        result = validate_flow_graph(graph)
        self.assertIn("FLOW_NAMESPACE_INVALID", codes(result))

    def test_entry_and_cross_flow_edges_are_rejected(self) -> None:
        graph = copy.deepcopy(self.minimal["flow_graph"])
        graph["flows"][0]["kind"] = "subflow"
        self.assertIn("FLOW_ENTRY_INVALID", codes(validate_flow_graph(graph)))

        graph = copy.deepcopy(self.minimal["flow_graph"])
        child = copy.deepcopy(graph["flows"][0])
        child["flow_id"] = "flow:child"
        child["kind"] = "subflow"
        child["name"] = "Child"
        child["nodes"][0]["node_id"] = "node:child"
        child["edges"] = []
        graph["flows"].append(child)
        graph["flows"][0]["edges"].append(
            {
                "edge_id": "edge:cross",
                "kind": "control",
                "source": {"node_id": "node:start", "port_id": "next"},
                "target": {"node_id": "node:child", "port_id": "previous"},
                "condition": None,
                "priority": None,
                "source_mapping_ids": [],
                "extensions": {},
            }
        )
        self.assertIn("FLOW_CROSS_FLOW_EDGE", codes(validate_flow_graph(graph)))

    def test_dependency_and_control_cycles_are_rejected(self) -> None:
        graph = copy.deepcopy(self.complete["flow_graph"])
        graph["flows"][0]["edges"].extend(
            [
                {
                    "edge_id": "edge:dependency:a",
                    "kind": "dependency",
                    "source": {"node_id": "node:start", "port_id": "next"},
                    "target": {"node_id": "node:inspect", "port_id": "previous"},
                    "condition": None,
                    "priority": None,
                    "source_mapping_ids": [],
                    "extensions": {},
                },
                {
                    "edge_id": "edge:dependency:b",
                    "kind": "dependency",
                    "source": {"node_id": "node:inspect", "port_id": "next"},
                    "target": {"node_id": "node:start", "port_id": "next"},
                    "condition": None,
                    "priority": None,
                    "source_mapping_ids": [],
                    "extensions": {},
                },
            ]
        )
        self.assertIn(
            "FLOW_DEPENDENCY_CYCLE",
            codes(
                validate_flow_graph(
                    graph,
                    capability_manifests=[self.complete["capability_manifest"]],
                )
            ),
        )

        graph = copy.deepcopy(self.complete["flow_graph"])
        graph["flows"][0]["edges"].append(
            {
                "edge_id": "edge:control:return",
                "kind": "control",
                "source": {"node_id": "node:inspect", "port_id": "next"},
                "target": {"node_id": "node:start", "port_id": "next"},
                "condition": None,
                "priority": None,
                "source_mapping_ids": [],
                "extensions": {},
            }
        )
        self.assertIn(
            "FLOW_CONTROL_CYCLE_UNSUPPORTED",
            codes(
                validate_flow_graph(
                    graph,
                    capability_manifests=[self.complete["capability_manifest"]],
                )
            ),
        )

    def test_subflow_recursion_is_rejected(self) -> None:
        graph = copy.deepcopy(self.minimal["flow_graph"])
        first = graph["flows"][0]["nodes"][0]
        first["node_type"] = "flow.subflow_call"
        first["capability_ref"]["node_type"] = "flow.subflow_call"
        first["configuration"] = {"subflow_id": "flow:child"}
        child = copy.deepcopy(graph["flows"][0])
        child["flow_id"] = "flow:child"
        child["kind"] = "subflow"
        child["name"] = "Child"
        child["nodes"][0]["node_id"] = "node:child-call"
        child["nodes"][0]["configuration"] = {"subflow_id": "flow:main"}
        child["edges"] = []
        graph["flows"].append(child)

        self.assertIn("FLOW_SUBFLOW_RECURSION", codes(validate_flow_graph(graph)))

    def test_binding_one_of_is_enforced(self) -> None:
        binding = copy.deepcopy(self.minimal["parameter_binding"])
        binding["graph_parameter_id"] = "parameter:tolerance"
        result = validate_contract(binding, "parameter_binding")
        self.assertIn("BINDING_ONE_OF_INVALID", codes(result))

    def test_port_direction_type_unit_and_cardinality_are_enforced(self) -> None:
        manifest = self.complete["capability_manifest"]
        graph = self.complete["flow_graph"]

        wrong_direction = copy.deepcopy(manifest)
        wrong_direction["node_types"][1]["ports"][0]["direction"] = "input"
        self.assertIn(
            "PORT_DIRECTION_INVALID",
            codes(
                validate_flow_graph(
                    graph,
                    capability_manifests=[wrong_direction],
                )
            ),
        )

        wrong_type = copy.deepcopy(manifest)
        wrong_type["node_types"][2]["ports"][2]["type_ref"] = "cam.angle"
        self.assertIn(
            "PORT_TYPE_MISMATCH",
            codes(validate_flow_graph(graph, capability_manifests=[wrong_type])),
        )

        wrong_unit = copy.deepcopy(manifest)
        wrong_unit["node_types"][1]["ports"][0]["constraints"]["unit"] = "inch"
        self.assertIn(
            "PORT_UNIT_MISMATCH",
            codes(validate_flow_graph(graph, capability_manifests=[wrong_unit])),
        )

        too_many = copy.deepcopy(graph)
        duplicate = copy.deepcopy(too_many["flows"][0]["edges"][1])
        duplicate["edge_id"] = "edge:value-tolerance:2"
        too_many["flows"][0]["edges"].append(duplicate)
        self.assertIn(
            "PORT_CARDINALITY_EXCEEDED",
            codes(validate_flow_graph(too_many, capability_manifests=[manifest])),
        )

    def test_capability_missing_revoked_and_risk_floor_are_enforced(self) -> None:
        graph = copy.deepcopy(self.complete["flow_graph"])
        manifest = self.complete["capability_manifest"]
        self.assertIn(
            "CAPABILITY_MISSING",
            codes(validate_flow_graph(graph, capability_manifests=[])),
        )

        revoked = copy.deepcopy(manifest)
        revoked["revocation"] = {
            "revoked_at": "2026-08-24T00:00:00Z",
            "reason": "fixture"
        }
        self.assertIn(
            "CAPABILITY_REVOKED",
            codes(validate_flow_graph(graph, capability_manifests=[revoked])),
        )

        graph["flows"][0]["nodes"][2]["risk"] = "safe"
        self.assertIn(
            "CAPABILITY_RISK_DOWNGRADE",
            codes(validate_flow_graph(graph, capability_manifests=[manifest])),
        )

    def test_source_digest_span_ambiguity_and_opaque_rules_are_enforced(self) -> None:
        graph = copy.deepcopy(self.minimal["flow_graph"])
        mapping = graph["source_mappings"][0]
        mapping["source_digest"] = (
            "sha256:9999999999999999999999999999999999999999999999999999999999999999"
        )
        mapping["source_line"] = 2
        mapping["source_span"]["end_byte"] = -1
        mapping["mapping_quality"] = "ambiguous"
        result = validate_flow_graph(graph)
        self.assertTrue(
            {
                "SOURCE_DIGEST_MISMATCH",
                "SOURCE_SPAN_INVALID",
                "SOURCE_MAPPING_AMBIGUOUS",
            }.issubset(codes(result))
        )

        node = graph["flows"][0]["nodes"][0]
        node["compatibility_status"] = "unsupported"
        node["opaque"] = {
            "reason": "fixture",
            "asset_revision_id": "asset-revision:fixture:1",
            "source_span_ids": ["mapping:start"],
            "content_hash": graph["asset_refs"][0]["content_hash"],
            "round_trip_policy": "opaque_preserved",
            "semantic_editable": True,
            "diagnostic_codes": ["SOURCE_OPAQUE_EDIT"],
        }
        self.assertIn("SOURCE_OPAQUE_EDIT", codes(validate_flow_graph(graph)))

    def test_asset_rights_and_binary_inspection_fail_closed(self) -> None:
        asset = copy.deepcopy(self.minimal["automation_asset"])
        asset["runtime_modes"].append("fixture_dry_run")
        asset["rights"]["status"] = "unreviewed"
        asset["rights"]["binary_inspection"] = True
        result = validate_contract(asset, "automation_asset")
        self.assertIn("ASSET_RIGHTS_UNKNOWN", codes(result))
        self.assertIn("ASSET_BINARY_INSPECTION_FORBIDDEN", codes(result))

    def test_product_asset_and_manifest_safety_contracts_are_enforced(self) -> None:
        asset = copy.deepcopy(self.minimal["automation_asset"])
        asset["product"] = "powermill"
        asset["source_locator"] = "C:\\restricted\\fixture.log"
        asset_result = validate_contract(asset, "automation_asset")
        self.assertIn("FLOW_PRODUCT_MIXED", codes(asset_result))
        self.assertIn("FLOW_SCHEMA_INVALID", codes(asset_result))

        manifest = copy.deepcopy(self.minimal["capability_manifest"])
        manifest["prohibited_operations"].remove("machine_control")
        self.assertIn(
            "FLOW_SCHEMA_INVALID",
            codes(validate_contract(manifest, "capability_manifest")),
        )

    def test_roundtrip_and_compatibility_report_semantics_are_enforced(self) -> None:
        report = copy.deepcopy(self.minimal["round_trip_report"])
        report["untouched_spans_exact"] = False
        self.assertIn(
            "SOURCE_SILENT_REWRITE",
            codes(validate_contract(report, "round_trip_report")),
        )

        compatibility = copy.deepcopy(self.minimal["compatibility_report"])
        compatibility["selector_results"] = [
            {"selector_id": "selector:fixture", "status": "ambiguous"}
        ]
        self.assertIn(
            "COMPAT_SELECTOR_UNRESOLVED",
            codes(validate_contract(compatibility, "compatibility_report")),
        )

    def test_ai_authority_and_fixture_gate_claims_are_rejected(self) -> None:
        preview = copy.deepcopy(self.minimal["preview_plan"])
        preview["gate_results"][0]["status"] = "passed"
        preview["extensions"]["ai_gate_passed"] = True
        self.assertIn(
            "SAFETY_AI_AUTHORITY_EXCEEDED",
            codes(validate_contract(preview, "preview_plan")),
        )

    def test_resource_limits_fail_without_truncation(self) -> None:
        graph = self.complete["flow_graph"]
        result = validate_flow_graph(
            graph,
            limits=ResourceLimits(
                max_asset_bytes=1,
                max_nodes=1,
                max_edges=1,
                max_depth=64,
                max_string_bytes=1024 * 1024,
                max_diagnostics=5000,
            ),
        )
        refs = {item.object_ref for item in result.diagnostics}
        self.assertIn("graph:nodes", refs)
        self.assertIn("graph:edges", refs)
        self.assertIn("RESOURCE_LIMIT_EXCEEDED", codes(result))

    def test_live_and_machine_output_are_rejected_without_payload_echo(self) -> None:
        preview = copy.deepcopy(self.minimal["preview_plan"])
        secret_marker = "sensitive-fixture-marker"
        preview["execution_mode"] = "live"
        preview["transport"] = "native"
        preview["steps"] = [{"nc_code": secret_marker}]
        result = validate_contract(preview, "preview_plan")
        self.assertIn("SAFETY_LIVE_EXECUTION_FORBIDDEN", codes(result))
        self.assertIn("SAFETY_MACHINE_OUTPUT_FORBIDDEN", codes(result))
        rendered = json.dumps(result.to_dict(), sort_keys=True)
        self.assertNotIn(secret_marker, rendered)

    def test_diagnostics_and_error_codes_are_stable_for_twenty_runs(self) -> None:
        graph = copy.deepcopy(self.complete["flow_graph"])
        graph["flows"][0]["nodes"][2]["risk"] = "safe"
        outputs = {
            json.dumps(
                validate_flow_graph(
                    graph,
                    capability_manifests=[self.complete["capability_manifest"]],
                ).to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for _ in range(20)
        }
        self.assertEqual(1, len(outputs))
        self.assertEqual(len(ERROR_CODES), len(set(ERROR_CODES)))
        self.assertEqual("FLOW_SCHEMA_INVALID", ERROR_CODES[0])
        self.assertEqual("RESOURCE_LIMIT_EXCEEDED", ERROR_CODES[-1])


if __name__ == "__main__":
    unittest.main()
