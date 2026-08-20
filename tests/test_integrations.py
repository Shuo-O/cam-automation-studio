from __future__ import annotations

import unittest

from cam_automation.codex_bridge import validate_review
from cam_automation.integrations import analyze, capability_manifest, connection_statuses
from cam_automation.sample import SAMPLE_LOG


class IntegrationTests(unittest.TestCase):
    def test_powermill_uses_unified_analysis_shape(self) -> None:
        result = analyze(product="powermill", source=SAMPLE_LOG, name="pm-review")

        self.assertEqual("powermill", result["product"])
        self.assertEqual("powermill_macro", result["output"]["kind"])
        self.assertEqual("cam.codex.bridge.v1", result["codex_context"]["protocol"])
        self.assertTrue(result["activity_events"])

    def test_nx_journal_is_static_and_dry_run(self) -> None:
        source = """
import NXOpen
session = NXOpen.Session.GetSession()
part = session.Parts.Work
operation = part.CAMSetup.CAMOperationCollection.Create("mill_planar")
operation.GenerateToolPath()
"""
        result = analyze(product="nx", source=source, source_format="nx_journal")

        self.assertEqual("nx", result["recipe"]["profile"])
        self.assertEqual("nx_preview", result["output"]["kind"])
        self.assertEqual("dry-run", result["adapter"]["execution_mode"])
        self.assertGreaterEqual(result["parse"]["commands"], 2)
        self.assertEqual(
            result["parse"]["commands"],
            len(result["activity_events"]),
        )
        self.assertTrue(
            all(
                event["mode"] == "manual"
                and event["product"] == "nx"
                for event in result["activity_events"]
            )
        )
        self.assertIn("does not import NXOpen", result["output"]["text"])

    def test_manifest_and_review_validation_are_explicit(self) -> None:
        manifest = capability_manifest()
        self.assertEqual("dry-run", manifest["execution_mode"])
        self.assertEqual({"nx", "powermill"}, {item["key"] for item in manifest["products"]})
        self.assertEqual(
            "approved_for_simulation",
            validate_review(
                {
                    "review_status": "approved_for_simulation",
                    "findings": ["check tool and holder"],
                    "required_gates": ["collision_check"],
                }
            )["review_status"],
        )

    def test_empty_plugin_set_exposes_only_the_core(self) -> None:
        manifest = capability_manifest(set())

        self.assertEqual("CAM Automation Studio Core", manifest["module"])
        self.assertEqual([], manifest["products"])
        self.assertEqual("unavailable", manifest["execution_mode"])
        self.assertFalse(manifest["capture"]["installed"])
        self.assertFalse(manifest["codex"]["installed"])
        self.assertEqual([], connection_statuses(set()))

    def test_connection_statuses_keep_all_three_bridges_visible(self) -> None:
        keys = {item["key"] for item in connection_statuses()}
        self.assertEqual({"codex", "nx", "powermill"}, keys)


if __name__ == "__main__":
    unittest.main()
