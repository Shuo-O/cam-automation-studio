from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from cam_automation.flow_contracts import (
    compute_capability_lock_hash,
    compute_semantic_hash,
)
from cam_automation.flow_roundtrip import SourcePatch, verify_round_trip
from cam_automation.flow_versions import CommandHistory, GraphCommand
from cam_automation.fixture_runtime import build_fixture_flow_service
from cam_automation.integrations import (
    OfflineFlowIntegration,
    load_product_flow_manifest,
    register_product_flow_capability,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "cam-flow" / "barrier"
VERSION_FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "cam-flow" / "versions"
FIXED_TIME = "2026-08-24T08:00:00Z"
ZERO_EXECUTION = {
    "transport": "none",
    "commands_sent": 0,
    "journal_executed": False,
    "macro_executed": False,
    "machine_output_count": 0,
}
RIGHTS = {
    "status": "legal_reviewed",
    "evidence_ref": "rights:t10:fixture",
    "sharing_scope": "private",
    "redistribution_allowed": False,
    "network_egress_allowed": False,
    "binary_inspection": False,
}


def load_fixture(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{name} must contain a JSON object")
    return value


def samples() -> list[dict[str, Any]]:
    return list(load_fixture("e2e-samples.json")["samples"])


def all_nodes(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(node)
        for flow in graph.get("flows", [])
        for node in flow.get("nodes", [])
        if isinstance(node, Mapping)
    ]


def exact_target(service: Any, sample: Mapping[str, Any]) -> dict[str, Any]:
    return next(
        target
        for target in service.list_fixture_targets()
        if target["target_instance_id"] == sample["target_instance_id"]
    )


def target_binding(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: target[key]
        for key in (
            "product",
            "target_version",
            "target_instance_id",
            "project_id",
            "project_snapshot_hash",
            "target_kind",
        )
    }


def assert_zero_execution(case: Any, value: Mapping[str, Any]) -> None:
    for field, expected in ZERO_EXECUTION.items():
        case.assertEqual(expected, value[field], field)


class BarrierRoundTripAdapter:
    def __init__(self, target_graph: Mapping[str, Any]) -> None:
        self.target_graph = copy.deepcopy(dict(target_graph))

    def reparse_candidate(
        self,
        candidate: bytes,
        *,
        target_graph: Mapping[str, Any],
    ) -> dict[str, Any]:
        del candidate, target_graph
        return copy.deepcopy(self.target_graph)


def source_patch_for_edit(
    graph: Mapping[str, Any],
    source: bytes,
    before: Any,
    after: Any,
) -> SourcePatch:
    needle = str(before).encode("utf-8")
    replacement = str(after).encode("utf-8")
    for mapping in graph.get("source_mappings", []):
        if (
            mapping.get("mapping_quality") != "exact"
            or mapping.get("role") == "opaque"
        ):
            continue
        span = mapping.get("source_span", {})
        start = int(span.get("start_byte", -1))
        end = int(span.get("end_byte", -1))
        if start < 0 or end < start:
            continue
        excerpt = source[start:end]
        if needle in excerpt:
            return SourcePatch(
                str(mapping["mapping_id"]),
                excerpt.replace(needle, replacement, 1),
            )
    raise AssertionError("No exact editable source mapping contains the fixture value.")


class BarrierHarness:
    def __init__(self, data_dir: str | Path) -> None:
        self.service = build_fixture_flow_service(data_dir)
        for product in ("nx", "powermill"):
            register_product_flow_capability(self.service, product)
        self.integration = OfflineFlowIntegration(self.service)

    def import_sample(self, sample: Mapping[str, Any]) -> dict[str, Any]:
        return self.integration.import_source(
            product=str(sample["product"]),
            source=str(sample["source"]),
            source_name=str(sample["source_name"]),
            target_versions=[str(sample["target_version"])],
            rights=RIGHTS,
        )

    def edit_graph(
        self,
        graph: Mapping[str, Any],
        sample: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        edit = sample["edit"]
        command = GraphCommand.replace(
            f"barrier:{sample['sample_id']}:parameter",
            "semantic",
            str(edit["path"]),
            edit["before"],
            edit["after"],
            message="T10 reviewed parameter change",
        )
        history = CommandHistory(graph)
        original = history.graph.to_dict()
        edited = history.execute(command).to_dict()
        if history.undo().to_dict() != original:
            raise AssertionError("GraphCommand undo did not restore the exact graph")
        if history.redo().to_dict() != edited:
            raise AssertionError("GraphCommand redo did not restore the edited graph")

        edited["parent_revision_id"] = graph["revision_id"]
        edited["revision_id"] = (
            f"{graph['revision_id']}:t10:{sample['sample_id']}"
        )
        edited["x_t10_unknown_optional"] = {
            "sample_id": sample["sample_id"],
            "preserve": True,
        }
        edited.setdefault("extensions", {})["t10.barrier"] = {
            "semantic": False,
            "sample_id": sample["sample_id"],
        }
        target = exact_target(self.service, sample)
        compatibility = self.service.check_compatibility(
            str(graph["graph_id"]),
            target,
            revision_id=str(graph["revision_id"]),
        )
        node_statuses = {
            item["node_id"]: item["status"]
            for item in compatibility["node_results"]
        }
        if any(status != "supported" for status in node_statuses.values()):
            raise AssertionError("Fixture compatibility review must support every node.")
        for flow in edited["flows"]:
            for node in flow["nodes"]:
                node["compatibility_status"] = node_statuses[node["node_id"]]
                node["review_status"] = "accepted"
        edited["semantic_hash"] = compute_semantic_hash(edited)
        saved = self.service.save_graph(
            edited,
            expected_revision_id=str(graph["revision_id"]),
        )
        asset_revision_id = str(graph["asset_refs"][0]["asset_revision_id"])
        source = self.service.asset_registry.read_bytes(asset_revision_id)
        round_trip = verify_round_trip(
            graph,
            saved,
            source,
            [
                source_patch_for_edit(
                    graph,
                    source,
                    edit["before"],
                    edit["after"],
                )
            ],
            BarrierRoundTripAdapter(saved),
            required_fidelity="F2",
            checked_at=FIXED_TIME,
        )
        report = self.service.register_round_trip_result(round_trip)
        if report["status"] != "passed":
            raise AssertionError("Edited fixture round-trip evidence must pass.")
        return original, saved

    def prepare_preview(
        self,
        graph: Mapping[str, Any],
        sample: Mapping[str, Any],
        *,
        plan_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        target = exact_target(self.service, sample)
        evidence = self.service.prepare_review_evidence(
            str(graph["graph_id"]),
            target,
            revision_id=str(graph["revision_id"]),
        )
        compatibility = evidence["compatibility_report"]
        projection = evidence["projection_report"]
        if evidence["status"] != "ready" or not evidence["preview_eligible"]:
            raise AssertionError("Fixture review evidence must be preview-eligible.")
        version = self.service.create_version(
            graph_id=str(graph["graph_id"]),
            revision_id=str(graph["revision_id"]),
            status="reviewed_for_fixture",
            author_ref="operator:t10",
            message="T10 offline fixture review",
            round_trip_report_id=evidence["round_trip_report"]["report_id"],
            compatibility_report_id=compatibility["report_id"],
            projection_report_id=projection["report_id"],
        )
        request = {
            "graph_id": graph["graph_id"],
            "revision_id": graph["revision_id"],
            "flow_version_id": version["version_id"],
            "execution_mode": "fixture_dry_run",
            "transport": "none",
            "target": target_binding(target),
            "hashes": {
                "source_snapshot_hash": graph["source_snapshot_hash"],
                "semantic_hash": graph["semantic_hash"],
                "capability_lock_hash": compute_capability_lock_hash(graph),
                "reviewed_recipe_hash": projection["recipe_hash"],
            },
        }
        if plan_id is not None:
            request["plan_id"] = plan_id
        return compatibility, version, request


def product_manifest(product: str) -> dict[str, Any]:
    return copy.deepcopy(load_product_flow_manifest(product))
