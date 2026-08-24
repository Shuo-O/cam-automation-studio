from __future__ import annotations

import gc
import json
import subprocess
import sys
import tempfile
import time
import unittest

from ._support import (
    BarrierHarness,
    all_nodes,
    assert_zero_execution,
    exact_target,
    product_manifest,
    ROOT,
    samples,
)


class FlowEndToEndBarrierTests(unittest.TestCase):
    def setUp(self) -> None:
        if (
            self._testMethodName
            == "test_two_targets_per_product_and_one_thousand_plans_never_cross"
        ):
            return
        self.directory = tempfile.TemporaryDirectory()
        self.harness = BarrierHarness(self.directory.name)

    def tearDown(self) -> None:
        if hasattr(self, "harness"):
            del self.harness
            self.directory.cleanup()
        gc.collect()

    def test_three_assets_per_product_complete_the_offline_workflow(self) -> None:
        observed = {"nx": 0, "powermill": 0}
        for sample in samples():
            with self.subTest(sample=sample["sample_id"]):
                observed[sample["product"]] += 1
                imported = self.harness.import_sample(sample)
                assert_zero_execution(self, imported)
                graph = imported["graph"]
                self.assertEqual(sample["product"], graph["product"])
                self.assertEqual(
                    sample["source"].encode("utf-8"),
                    self.harness.service.asset_registry.read_bytes(
                        imported["asset"]["asset_revision_id"]
                    ),
                )

                manifest_types = {
                    item["node_type"]
                    for item in product_manifest(sample["product"])["node_types"]
                }
                nodes = all_nodes(graph)
                self.assertGreaterEqual(len(nodes), 3)
                self.assertTrue(
                    all(node["node_type"] in manifest_types for node in nodes)
                )
                self.assertTrue(graph["source_mappings"])
                for mapping in graph["source_mappings"]:
                    self.assertEqual(
                        mapping["source_line"],
                        mapping["source_span"]["start_line"],
                    )
                    self.assertLessEqual(
                        mapping["source_span"]["start_byte"],
                        mapping["source_span"]["end_byte"],
                    )

                original, edited = self.harness.edit_graph(graph, sample)
                self.assertEqual(
                    {
                        "sample_id": sample["sample_id"],
                        "preserve": True,
                    },
                    edited["x_t10_unknown_optional"],
                )
                diff = self.harness.service.diff_graphs(
                    graph["graph_id"],
                    original["revision_id"],
                    edited["revision_id"],
                )
                self.assertEqual("changes_detected", diff["status"])
                self.assertTrue(
                    any(
                        change["path"].endswith(str(sample["edit"]["path"]))
                        or change["path"] == sample["edit"]["path"]
                        for change in diff["changes"]
                    )
                )
                test_report = self.harness.service.test_graph(
                    edited["graph_id"],
                    edited["revision_id"],
                )
                self.assertEqual("passed", test_report["status"])
                assert_zero_execution(self, test_report)

                compatibility, version, request = self.harness.prepare_preview(
                    edited,
                    sample,
                )
                self.assertTrue(compatibility["preview_eligible"])
                self.assertEqual("reviewed_for_fixture", version["status"])
                plan = self.harness.service.create_preview_plan(request)
                self.assertEqual("ready", plan["status"])
                completed = self.harness.service.execute_preview_plan(plan["plan_id"])
                self.assertEqual("succeeded", completed["status"])
                self.assertEqual(
                    sample["target_instance_id"],
                    completed["target"]["target_instance_id"],
                )
                assert_zero_execution(self, completed)

        self.assertEqual({"nx": 3, "powermill": 3}, observed)

    def test_two_targets_per_product_and_one_thousand_plans_never_cross(self) -> None:
        worker = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.flow_barrier._plan_isolation_worker",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(0, worker.returncode, worker.stderr)
        result = json.loads(worker.stdout.strip())
        self.assertEqual(
            {"nx": 2, "powermill": 2},
            result["target_counts"],
        )
        self.assertEqual(1_000, result["plan_count"])
        self.assertEqual(1_000, result["unique_plan_count"])
        self.assertEqual(1_000, result["zero_execution_plans"])
        self.assertEqual(0, result["cross_wires"])
        self.assertEqual("PREVIEW_TARGET_AMBIGUOUS", result["ambiguous_code"])
        self.assertEqual(2, result["ambiguous_candidates"])

    def test_revocation_closes_within_two_seconds_and_never_auto_resumes(self) -> None:
        sample = samples()[0]
        imported = self.harness.import_sample(sample)
        _original, graph = self.harness.edit_graph(imported["graph"], sample)
        _compatibility, _version, request = self.harness.prepare_preview(
            graph,
            sample,
            plan_id="preview:t10:revocation",
        )
        plan = self.harness.service.create_preview_plan(request)
        self.assertEqual("ready", plan["status"])

        started = time.perf_counter()
        revoked = self.harness.service.revoke_fixture_permission(
            sample["target_instance_id"],
            "read:selected-files",
        )
        elapsed = time.perf_counter() - started
        self.assertLessEqual(elapsed, 2.0)
        self.assertEqual([plan["plan_id"]], revoked["cancelled_plan_ids"])
        self.assertFalse(revoked["auto_resumed"])
        self.assertEqual(
            "cancelled",
            self.harness.service.get_preview_plan(plan["plan_id"])["status"],
        )

        granted = self.harness.service.grant_fixture_permission(
            sample["target_instance_id"],
            "read:selected-files",
        )
        self.assertFalse(granted["auto_resumed"])
        self.assertEqual([], granted["resumed_plan_ids"])
        self.assertEqual(
            "cancelled",
            self.harness.service.get_preview_plan(plan["plan_id"])["status"],
        )

    def test_network_is_disabled_by_default_for_assets_manifests_and_targets(self) -> None:
        for sample in (samples()[0], samples()[3]):
            with self.subTest(product=sample["product"]):
                imported = self.harness.import_sample(sample)
                self.assertFalse(
                    imported["asset"]["rights"]["network_egress_allowed"]
                )
                manifest = product_manifest(sample["product"])
                self.assertFalse(
                    manifest["extensions"]["cam_automation"]["network_egress"]
                )
                target = exact_target(self.harness.service, sample)
                self.assertNotIn("network:declared-hosts", target["permissions"])
                self.assertEqual(
                    "none",
                    target["extensions"]["cam.flow.fixture"]["transport"],
                )


if __name__ == "__main__":
    unittest.main()
