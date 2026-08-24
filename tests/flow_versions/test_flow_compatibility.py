from __future__ import annotations

import copy
import unittest

from cam_automation.flow_compatibility import (
    build_compatibility_report,
    project_flow_to_recipe,
    target_version_matches,
)
from cam_automation.flow_validation import validate_contract
from cam_automation.recipes import RecipeService, compute_recipe_hash

from .helpers import FIXED_TIME, compatibility, graph, manifest


class FlowCompatibilityTests(unittest.TestCase):
    def test_target_range_and_valid_report(self) -> None:
        self.assertTrue(
            target_version_matches("NX 2406", "NX >=2400,<2500")
        )
        self.assertFalse(
            target_version_matches("NX 2500", "NX >=2400,<2500")
        )
        report = compatibility().to_dict()
        self.assertEqual("compatible", report["status"])
        self.assertTrue(report["preview_eligible"])
        self.assertEqual([], report["blocker_codes"])
        self.assertEqual("none", report["extensions"]["cam.flow_compatibility"]["transport"])
        self.assertTrue(validate_contract(report, "compatibility_report").valid)

    def test_capability_major_and_target_mismatch_fail_closed(self) -> None:
        major = graph()
        major["flows"][0]["nodes"][1]["node_type_version"] = "2.0.0"
        major_report = compatibility(major).to_dict()
        self.assertEqual("incompatible", major_report["status"])
        self.assertIn(
            "CAPABILITY_VERSION_MISMATCH",
            major_report["blocker_codes"],
        )

        target_report = build_compatibility_report(
            graph(),
            [manifest()],
            target_profile={"target_version": "NX 2600"},
            checked_at=FIXED_TIME,
        ).to_dict()
        self.assertEqual("incompatible", target_report["status"])
        self.assertFalse(target_report["preview_eligible"])

        unreviewed_target = build_compatibility_report(
            graph(),
            [manifest()],
            target_profile={"target_version": "NX 2412"},
            checked_at=FIXED_TIME,
        ).to_dict()
        self.assertIn(
            "COMPAT_CONTEXT_INCOMPLETE",
            unreviewed_target["blocker_codes"],
        )

    def test_selector_and_context_must_be_resolved(self) -> None:
        report = build_compatibility_report(
            graph(),
            [manifest()],
            target_profile={"target_version": "NX 2406"},
            selector_results=(
                {
                    "selector_id": "selector:fixture",
                    "match_count": 2,
                },
            ),
            required_context=("project_id", "mcs"),
            context={"project_id": "fixture-project"},
            checked_at=FIXED_TIME,
        ).to_dict()
        self.assertIn("COMPAT_SELECTOR_UNRESOLVED", report["blocker_codes"])
        self.assertIn("COMPAT_CONTEXT_INCOMPLETE", report["blocker_codes"])
        self.assertFalse(report["preview_eligible"])

    def test_linear_graph_projects_every_node_and_edge(self) -> None:
        value = graph()
        compatible = compatibility(value)
        result = project_flow_to_recipe(
            value,
            compatibility_report=compatible,
            manifests=[manifest()],
            checked_at=FIXED_TIME,
        )
        report = result.report.to_dict()
        self.assertEqual("projected", report["status"])
        self.assertTrue(report["preview_eligible"])
        self.assertIsNotNone(result.recipe)
        assert result.recipe is not None
        self.assertEqual(result.recipe.recipe_hash, compute_recipe_hash(result.recipe))
        saved = RecipeService().save(result.recipe)
        self.assertEqual(result.recipe.recipe_hash, saved.recipe_hash)
        self.assertEqual(["node:inspect"], [step.step_id for step in result.recipe.steps])
        self.assertEqual(
            {"parameter": "value"},
            result.recipe.steps[0].arguments["value"],
        )
        self.assertTrue(
            all(item["status"] == "projected" for item in report["node_results"])
        )
        self.assertTrue(
            all(item["status"] == "projected" for item in report["edge_results"])
        )

    def test_general_graph_is_not_silently_linearized(self) -> None:
        value = graph()
        value["flows"][0]["edges"].append(
            {
                "edge_id": "edge:start-end-direct",
                "kind": "control",
                "source": {"node_id": "node:start", "port_id": "next"},
                "target": {"node_id": "node:end", "port_id": "in"},
                "condition": {"kind": "literal", "value": True},
                "priority": 1,
                "source_mapping_ids": [],
                "extensions": {},
            }
        )
        compatible = compatibility(value)
        result = project_flow_to_recipe(
            value,
            compatibility_report=compatible,
            manifests=[manifest()],
            checked_at=FIXED_TIME,
        )
        self.assertIsNone(result.recipe)
        blockers = result.report.to_dict()["blockers"]
        self.assertIn(
            "PROJECTION_CONTROL_FLOW_UNSUPPORTED",
            {item["code"] for item in blockers},
        )

    def test_control_edge_priority_is_not_silently_dropped(self) -> None:
        value = graph()
        value["flows"][0]["edges"][0]["priority"] = 1
        compatible = compatibility(value)
        result = project_flow_to_recipe(
            value,
            compatibility_report=compatible,
            manifests=[manifest()],
            checked_at=FIXED_TIME,
        )
        self.assertIsNone(result.recipe)
        self.assertIn(
            "PROJECTION_EDGE_UNSUPPORTED",
            {item["code"] for item in result.report.to_dict()["blockers"]},
        )

    def test_projection_support_is_required_and_reports_are_deterministic(self) -> None:
        capability = manifest()
        capability["node_types"][1]["recipe_projection_support"] = False
        value = graph()
        compatible = compatibility(value, capability)
        results = [
            project_flow_to_recipe(
                value,
                compatibility_report=compatible,
                manifests=[capability],
                checked_at=FIXED_TIME,
            ).report.canonical_bytes()
            for _ in range(20)
        ]
        self.assertEqual(1, len(set(results)))
        result = project_flow_to_recipe(
            value,
            compatibility_report=compatible,
            manifests=[capability],
            checked_at=FIXED_TIME,
        )
        self.assertIsNone(result.recipe)
        self.assertIn(
            "PROJECTION_NODE_UNSUPPORTED",
            {
                item["code"]
                for item in result.report.to_dict()["blockers"]
            },
        )

    def test_compatibility_report_is_byte_deterministic_for_twenty_runs(self) -> None:
        reports = [compatibility().canonical_bytes() for _ in range(20)]
        self.assertEqual(1, len(set(reports)))


if __name__ == "__main__":
    unittest.main()
