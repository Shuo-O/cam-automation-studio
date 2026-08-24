from __future__ import annotations

import copy
import json
import time
import unittest
from pathlib import Path
from types import MappingProxyType

from cam_automation.flow_contracts import compute_semantic_hash
from cam_automation.flow_validation import (
    DEFAULT_RESOURCE_LIMITS,
    ERROR_CODES,
    ResourceLimits,
    _native_preflight_is_clean,
    _preflight_diagnostics,
    _resource_diagnostics,
    _safety_diagnostics,
    _schema_definition,
    _schema_errors,
    _schema_validator,
    validate_contract,
    validate_flow_graph,
)
from cam_automation.flow_versions import GraphCommand


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "cam-flow" / "contracts"
)


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def codes(result) -> set[str]:
    return {item.code for item in result.diagnostics}


def performance_fixture(
    bundle: dict,
) -> tuple[dict, dict]:
    graph = copy.deepcopy(bundle["flow_graph"])
    manifest = copy.deepcopy(bundle["capability_manifest"])
    node_template = next(
        item
        for item in graph["flows"][0]["nodes"]
        if item["node_type"] == "cam.operation.inspect"
    )
    definition = next(
        item
        for item in manifest["node_types"]
        if item["node_type"] == "cam.operation.inspect"
    )
    for port in definition["ports"]:
        port["cardinality"] = "many"

    asset_id = "asset:nx:validation-performance"
    revision_id = "asset-revision:nx:validation-performance:1"
    digest = "sha256:" + ("a" * 64)
    nodes = []
    mappings = []
    for index in range(500):
        node_id = f"node:{index:04d}"
        mapping_id = f"mapping:{index:04d}"
        node = copy.deepcopy(node_template)
        node["node_id"] = node_id
        node["configuration"] = {"fixture_index": index}
        node["source_mapping_ids"] = [mapping_id]
        node["bindings"] = [
            {
                "binding_id": f"binding:{index:04d}",
                "target": {"node_id": node_id, "port_id": "tolerance"},
                "kind": "graph_parameter",
                "literal": None,
                "graph_parameter_id": "parameter:tolerance",
                "source_output": None,
                "secret_ref": None,
                "source_mapping_ids": [mapping_id],
                "extensions": {},
            }
        ]
        nodes.append(node)
        mappings.append(
            {
                "mapping_id": mapping_id,
                "asset_id": asset_id,
                "asset_revision_id": revision_id,
                "source_digest": digest,
                "source_line": index + 1,
                "source_span": {
                    "start_byte": index * 16,
                    "end_byte": index * 16 + 15,
                    "start_line": index + 1,
                    "end_line": index + 1,
                    "start_column": 0,
                    "end_column": 15,
                    "column_encoding": "unicode_scalar",
                },
                "target": {
                    "flow_id": "flow:main",
                    "node_id": node_id,
                    "property_path": "/configuration/fixture_index",
                },
                "role": "primary",
                "mapping_quality": "exact",
                "excerpt_hash": "sha256:" + (f"{index:064x}"[-64:]),
                "extensions": {},
            }
        )

    edges = []
    for index in range(499):
        edges.append(
            {
                "edge_id": f"edge:chain:{index:04d}",
                "kind": "control",
                "source": {"node_id": f"node:{index:04d}", "port_id": "next"},
                "target": {
                    "node_id": f"node:{index + 1:04d}",
                    "port_id": "previous",
                },
                "condition": None,
                "priority": None,
                "source_mapping_ids": [],
                "extensions": {},
            }
        )
    for index in range(301):
        edges.append(
            {
                "edge_id": f"edge:skip:{index:04d}",
                "kind": "control",
                "source": {"node_id": f"node:{index:04d}", "port_id": "next"},
                "target": {
                    "node_id": f"node:{index + 2:04d}",
                    "port_id": "previous",
                },
                "condition": None,
                "priority": None,
                "source_mapping_ids": [],
                "extensions": {},
            }
        )

    flow = graph["flows"][0]
    flow["nodes"] = nodes
    flow["edges"] = edges
    flow["parameter_ids"] = ["parameter:tolerance"]
    flow["source_mapping_ids"] = [item["mapping_id"] for item in mappings]
    graph["asset_refs"] = [
        {
            "asset_id": asset_id,
            "asset_revision_id": revision_id,
            "content_hash": digest,
            "encoding": "utf-8",
            "bom": "none",
            "newline_profile": "lf",
        }
    ]
    graph["source_mappings"] = mappings
    graph["layout"] = {
        "nodes": {
            node["node_id"]: {"x": index % 25, "y": index // 25}
            for index, node in enumerate(nodes)
        }
    }
    graph["semantic_hash"] = compute_semantic_hash(graph)
    return graph, manifest


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

    def test_compiled_schema_fast_path_matches_complete_diagnostics(self) -> None:
        for fixture_name, values in (
            ("minimal", self.minimal),
            ("invalid", self.invalid),
        ):
            for schema_name, value in values.items():
                with self.subTest(
                    fixture=fixture_name,
                    schema_name=schema_name,
                ):
                    definition = _schema_definition(schema_name)
                    self.assertEqual(
                        not _schema_errors(value, definition),
                        _schema_validator(schema_name)(value),
                    )

    def test_combined_preflight_matches_independent_full_scans(self) -> None:
        cases = [
            (schema_name, value, DEFAULT_RESOURCE_LIMITS)
            for values in (self.minimal, self.invalid)
            for schema_name, value in values.items()
        ]
        constrained = ResourceLimits(
            max_asset_bytes=1,
            max_nodes=1,
            max_edges=1,
            max_depth=2,
            max_string_bytes=1,
            max_diagnostics=5_000,
        )
        cases.append(("flow_graph", self.complete["flow_graph"], constrained))
        unsafe_preview = copy.deepcopy(self.minimal["preview_plan"])
        unsafe_preview["execution_mode"] = "live"
        unsafe_preview["transport"] = "native"
        unsafe_preview["steps"] = [{"machine_output": "redacted"}]
        cases.append(("preview_plan", unsafe_preview, DEFAULT_RESOURCE_LIMITS))
        cases.append(
            (
                "preview_plan",
                MappingProxyType(unsafe_preview),
                DEFAULT_RESOURCE_LIMITS,
            )
        )

        for schema_name, value, limits in cases:
            with self.subTest(schema_name=schema_name, limits=limits):
                expected = _resource_diagnostics(value, schema_name, limits)
                expected.extend(_safety_diagnostics(value, schema_name))
                self.assertEqual(
                    [item.to_dict() for item in expected],
                    [
                        item.to_dict()
                        for item in _preflight_diagnostics(value, schema_name, limits)
                    ],
                )

    def test_native_preflight_falls_back_for_every_anomalous_shape(self) -> None:
        graph = copy.deepcopy(self.complete["flow_graph"])
        self.assertTrue(
            _native_preflight_is_clean(
                graph,
                "flow_graph",
                DEFAULT_RESOURCE_LIMITS,
            )
        )

        custom_mapping = MappingProxyType(graph)
        self.assertFalse(
            _native_preflight_is_clean(
                custom_mapping,
                "flow_graph",
                DEFAULT_RESOURCE_LIMITS,
            )
        )

        unsafe = copy.deepcopy(graph)
        unsafe["extensions"]["machine.output"] = {"value": "redacted"}
        self.assertFalse(
            _native_preflight_is_clean(
                unsafe,
                "flow_graph",
                DEFAULT_RESOURCE_LIMITS,
            )
        )
        self.assertIn(
            "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
            codes(validate_flow_graph(unsafe)),
        )

        constrained = ResourceLimits(max_depth=2)
        self.assertFalse(
            _native_preflight_is_clean(
                graph,
                "flow_graph",
                constrained,
            )
        )
        self.assertIn(
            "RESOURCE_LIMIT_EXCEEDED",
            codes(validate_flow_graph(graph, limits=constrained)),
        )

        invalid_schema = copy.deepcopy(self.minimal["flow_node"])
        del invalid_schema["node_id"]
        self.assertIn(
            "FLOW_SCHEMA_INVALID",
            codes(validate_contract(invalid_schema, "flow_node")),
        )

    def test_parameter_edit_validation_meets_frozen_scale_budget(self) -> None:
        graph, manifest = performance_fixture(self.complete)
        self.assertEqual(500, len(graph["flows"][0]["nodes"]))
        self.assertEqual(800, len(graph["flows"][0]["edges"]))
        self.assertEqual(500, len(graph["source_mappings"]))
        self.assertTrue(
            validate_flow_graph(
                graph,
                capability_manifests=[manifest],
            ).valid
        )
        command = GraphCommand.replace(
            "command:test:parameter-performance",
            "semantic",
            "/graph_parameters/0/default",
            0.01,
            0.02,
        )

        def operation() -> None:
            changed = command.apply(graph)
            result = validate_flow_graph(
                changed,
                capability_manifests=[manifest],
            )
            self.assertTrue(result.valid, result.to_dict())

        operation()
        samples = []
        for _ in range(20):
            start = time.perf_counter_ns()
            operation()
            samples.append((time.perf_counter_ns() - start) / 1_000_000)
        ordered = sorted(samples)
        p50 = ordered[9]
        p95 = ordered[18]
        self.assertLessEqual(
            p95,
            100.0,
            f"p50={p50:.3f}ms p95={p95:.3f}ms max={max(samples):.3f}ms",
        )

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
