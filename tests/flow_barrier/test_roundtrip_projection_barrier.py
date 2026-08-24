from __future__ import annotations

import copy
import json
import tempfile
import unittest
from typing import Any

from cam_automation.flow_roundtrip import SourcePatch, verify_round_trip
from cam_automation.flow_contracts import compute_capability_lock_hash
from cam_automation.flow_service import FlowConflictError, FlowService
from cam_automation.flow_versions import prepare_graph_snapshot

from ._support import (
    FIXED_TIME,
    VERSION_FIXTURE_ROOT,
    BarrierHarness,
    all_nodes,
    product_manifest,
    samples,
    target_binding,
)


class FixtureRoundTripAdapter:
    def __init__(self, reparsed: dict[str, Any]) -> None:
        self.reparsed = reparsed
        self.candidates: list[bytes] = []

    def reparse_candidate(
        self,
        candidate: bytes,
        *,
        target_graph: dict[str, Any],
    ) -> dict[str, Any]:
        self.candidates.append(candidate)
        return copy.deepcopy(self.reparsed)


class RoundTripAndProjectionBarrierTests(unittest.TestCase):
    def test_f0_f1_f2_f3_fb_opaque_and_candidate_reparse(self) -> None:
        bundle = json.loads(
            (VERSION_FIXTURE_ROOT / "linear-flow.json").read_text(encoding="utf-8")
        )
        golden = json.loads(
            (VERSION_FIXTURE_ROOT / "roundtrip-golden.json").read_text(
                encoding="utf-8"
            )
        )
        original = prepare_graph_snapshot(bundle["graph"]).to_dict()
        target = copy.deepcopy(original)
        target["graph_parameters"][0]["default"] = 2
        target = prepare_graph_snapshot(target).to_dict()
        source = bundle["source_text"].encode("utf-8")

        for case in golden["cases"]:
            with self.subTest(case=case["name"]):
                patches = []
                if case["replacement"] is not None:
                    patches.append(
                        SourcePatch(
                            case.get("mapping_id", "mapping:value"),
                            case["replacement"].encode("utf-8"),
                        )
                    )
                expected_graph = target if patches else original
                adapter = FixtureRoundTripAdapter(expected_graph)
                result = verify_round_trip(
                    original,
                    expected_graph,
                    source,
                    patches,
                    adapter,
                    required_fidelity=case["required_fidelity"],
                    checked_at=FIXED_TIME,
                )
                report = result.report.to_dict()
                self.assertEqual(case["expected_status"], report["status"])
                self.assertEqual(case["expected_fidelity"], report["fidelity"])
                if report["status"] == "passed":
                    self.assertIsNotNone(result.candidate)
                    self.assertTrue(report["candidate_reparsed"])
                    self.assertTrue(report["opaque_spans_preserved"])
                    self.assertEqual(1, len(adapter.candidates))
                else:
                    self.assertIsNone(result.candidate)
                    self.assertFalse(report["candidate_reparsed"])

    def test_opaque_nodes_and_unknown_evidence_survive_public_import(self) -> None:
        cases = (
            (
                "powermill",
                'VENDOR.Gizmo APPLY "SAFE_OPAQUE"\n',
                "opaque.mac",
                "PowerMill 2025",
            ),
            (
                "nx",
                "import NXOpen\nresult = vendor_unknown_call('SAFE_OPAQUE')\n",
                "opaque.py",
                "NX 2406",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            harness = BarrierHarness(directory)
            for product, source, source_name, target_version in cases:
                with self.subTest(product=product):
                    imported = harness.integration.import_source(
                        product=product,
                        source=source,
                        source_name=source_name,
                        target_versions=[target_version],
                    )
                    fetched = harness.service.get_graph(
                        imported["graph"]["graph_id"],
                        imported["graph"]["revision_id"],
                    )
                    opaque = [
                        node
                        for node in all_nodes(fetched)
                        if node["opaque"] is not None
                    ]
                    self.assertTrue(opaque)
                    mapping_ids = {
                        mapping["mapping_id"]
                        for mapping in fetched["source_mappings"]
                    }
                    for node in opaque:
                        self.assertFalse(node["opaque"]["semantic_editable"])
                        self.assertTrue(
                            set(node["opaque"]["source_span_ids"]) <= mapping_ids
                        )

    def test_product_manifests_expose_a_recipe_projection_path(self) -> None:
        for product in ("nx", "powermill"):
            with self.subTest(product=product):
                semantic_nodes = [
                    node
                    for node in product_manifest(product)["node_types"]
                    if node["node_type"] not in {"cam.flow.start", "cam.flow.end"}
                    and node["preview_support"]
                ]
                self.assertTrue(semantic_nodes)
                self.assertTrue(
                    any(node["recipe_projection_support"] for node in semantic_nodes),
                    f"{product} has no previewable node that can project to Recipe v1",
                )

    def test_projection_blocker_prevents_reviewed_version_and_preview(self) -> None:
        sample = {
            "sample_id": "pm-blocked-projection",
            "product": "powermill",
            "source_name": "blocked-projection.mac",
            "target_version": "PowerMill 2025",
            "target_instance_id": "powermill:4102:process",
            "source": "REAL $value = 0\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            harness = BarrierHarness(directory)
            imported = harness.import_sample(sample)
            graph = imported["graph"]
            target = next(
                item
                for item in harness.service.list_fixture_targets()
                if item["target_instance_id"] == sample["target_instance_id"]
            )
            evidence = harness.service.prepare_review_evidence(
                graph["graph_id"],
                target,
                revision_id=graph["revision_id"],
            )
            report = evidence["projection_report"]
            self.assertEqual("blocked", report["status"])
            self.assertFalse(report["preview_eligible"])
            self.assertIn(
                "PROJECTION_NODE_UNSUPPORTED",
                {blocker["code"] for blocker in report["blockers"]},
            )

            with self.assertRaises(FlowConflictError) as version_blocked:
                harness.service.create_version(
                    graph_id=graph["graph_id"],
                    revision_id=graph["revision_id"],
                    status="reviewed_for_fixture",
                    round_trip_report_id=evidence["round_trip_report"]["report_id"],
                    compatibility_report_id=evidence["compatibility_report"][
                        "report_id"
                    ],
                    projection_report_id=report["report_id"],
                )
            self.assertEqual(
                "PROJECTION_REVIEW_REQUIRED",
                version_blocked.exception.code,
            )

            legacy = FlowService.create_version(
                harness.service,
                graph_id=graph["graph_id"],
                revision_id=graph["revision_id"],
                status="reviewed_for_fixture",
                round_trip_report_id=evidence["round_trip_report"]["report_id"],
                compatibility_report_id=evidence["compatibility_report"]["report_id"],
            )
            request = {
                "graph_id": graph["graph_id"],
                "revision_id": graph["revision_id"],
                "flow_version_id": legacy["version_id"],
                "execution_mode": "fixture_dry_run",
                "transport": "none",
                "target": target_binding(target),
                "hashes": {
                    "source_snapshot_hash": graph["source_snapshot_hash"],
                    "semantic_hash": graph["semantic_hash"],
                    "capability_lock_hash": compute_capability_lock_hash(graph),
                    "reviewed_recipe_hash": None,
                },
            }
            with self.assertRaises(FlowConflictError) as preview_blocked:
                harness.service.create_preview_plan(request)
            self.assertEqual(
                "PROJECTION_REVIEW_REQUIRED",
                preview_blocked.exception.code,
            )


if __name__ == "__main__":
    unittest.main()
