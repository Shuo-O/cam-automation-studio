from __future__ import annotations

import copy
import unittest

from cam_automation.flow_compatibility import project_flow_to_recipe
from cam_automation.flow_contracts import compute_artifact_hash, compute_semantic_hash
from cam_automation.flow_versions import (
    CommandHistory,
    FlowVersionStore,
    GraphCommand,
    create_flow_version,
    four_layer_diff,
    migrate_flow_graph,
)
from cam_automation.flow_validation import validate_contract

from .helpers import FIXED_TIME, compatibility, graph, manifest, target_graph


def review_evidence(value: dict):
    compatible = compatibility(value)
    projection = project_flow_to_recipe(
        value,
        compatibility_report=compatible,
        manifests=[manifest()],
        checked_at=FIXED_TIME,
    )
    round_trip = {
        "report_id": "roundtrip:fixture",
        "status": "passed",
        "fidelity": "F3",
    }
    return round_trip, compatible, projection.report


class FlowVersionTests(unittest.TestCase):
    def test_layout_and_semantic_commands_are_separate_and_reversible(self) -> None:
        original = graph()
        semantic_hash = compute_semantic_hash(original)
        layout = GraphCommand.replace(
            "command:layout",
            "layout",
            "/layout/nodes/node:inspect/x",
            10,
            50,
        )
        history = CommandHistory(original)
        laid_out = history.execute(layout)
        self.assertEqual(semantic_hash, compute_semantic_hash(laid_out))
        self.assertNotEqual(
            compute_artifact_hash(original),
            compute_artifact_hash(laid_out),
        )

        semantic = GraphCommand.replace(
            "command:value",
            "semantic",
            "/graph_parameters/0/default",
            1,
            2,
        )
        changed = history.execute(semantic)
        changed_hash = compute_semantic_hash(changed)
        self.assertNotEqual(semantic_hash, changed_hash)
        self.assertEqual(semantic_hash, compute_semantic_hash(history.undo()))
        restored_layout = history.undo()
        self.assertEqual(semantic_hash, compute_semantic_hash(restored_layout))
        restored = history.redo()
        self.assertEqual(semantic_hash, compute_semantic_hash(restored))
        self.assertEqual(
            "preserve-me",
            restored.to_dict()["extensions"]["fixture"]["unknown_optional_field"],
        )

    def test_command_category_rejects_cross_boundary_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "only change /layout"):
            GraphCommand.replace(
                "command:bad-layout",
                "layout",
                "/graph_parameters/0/default",
                1,
                2,
            )
        with self.assertRaisesRegex(ValueError, "may not change /layout"):
            GraphCommand.replace(
                "command:bad-semantic",
                "semantic",
                "/layout/nodes/node:inspect/x",
                10,
                20,
            )

    def test_review_hashes_are_invalidated_but_layout_is_excluded(self) -> None:
        original = graph()
        round_trip, compatible, projection = review_evidence(original)
        parent = create_flow_version(
            original,
            status="reviewed_for_fixture",
            author_ref="user:fixture",
            round_trip_report=round_trip,
            compatibility_report=compatible,
            projection_report=projection,
            created_at=FIXED_TIME,
        )
        self.assertEqual("reviewed_for_fixture", parent.status)
        self.assertTrue(validate_contract(parent, "flow_version").valid)

        layout_graph = GraphCommand.replace(
            "command:layout",
            "layout",
            "/layout/nodes/node:inspect/x",
            10,
            99,
        ).apply(original)
        layout_evidence = review_evidence(layout_graph)
        layout_version = create_flow_version(
            layout_graph,
            parent_versions=(parent,),
            status="reviewed_for_fixture",
            author_ref="user:fixture",
            round_trip_report=layout_evidence[0],
            compatibility_report=layout_evidence[1],
            projection_report=layout_evidence[2],
            created_at=FIXED_TIME,
        )
        self.assertEqual("reviewed_for_fixture", layout_version.status)

        changed = target_graph()
        changed_evidence = review_evidence(changed)
        invalidated = create_flow_version(
            changed,
            parent_versions=(parent,),
            status="reviewed_for_fixture",
            author_ref="user:fixture",
            round_trip_report=changed_evidence[0],
            compatibility_report=changed_evidence[1],
            projection_report=changed_evidence[2],
            created_at=FIXED_TIME,
        )
        self.assertEqual("review_required", invalidated.status)
        self.assertTrue(
            invalidated.to_dict()["extensions"]["cam.flow_versions"][
                "review_invalidated"
            ]
        )

    def test_migration_always_creates_new_revision(self) -> None:
        original = graph()
        command = GraphCommand.replace(
            "command:migrate",
            "semantic",
            "/graph_parameters/0/default",
            1,
            2,
        )
        result = migrate_flow_graph(
            original,
            command,
            new_revision_id="revision:fixture:linear:2",
            checked_at=FIXED_TIME,
        )
        self.assertEqual(
            "revision:fixture:linear:1",
            result.graph.parent_revision_id,
        )
        self.assertEqual("revision:fixture:linear:2", result.graph.revision_id)
        self.assertEqual("review_required", result.report["status"])
        with self.assertRaisesRegex(ValueError, "distinct new revision"):
            migrate_flow_graph(
                original,
                command,
                new_revision_id=original["revision_id"],
            )

    def test_four_layer_diff_excludes_layout_and_is_deterministic(self) -> None:
        original = graph()
        layout = GraphCommand.replace(
            "command:layout",
            "layout",
            "/layout/nodes/node:inspect/y",
            20,
            200,
        ).apply(original)
        empty = four_layer_diff(original, layout)
        self.assertEqual((), empty.graph_changes)
        self.assertEqual((), empty.source_changes)

        changed = target_graph()
        before_compat = compatibility(original)
        after_compat = compatibility(changed)
        reports = [
            four_layer_diff(
                original,
                changed,
                before_compatibility=before_compat,
                after_compatibility=after_compat,
            ).canonical_bytes()
            for _ in range(20)
        ]
        self.assertTrue(
            four_layer_diff(original, changed).graph_changes
        )
        self.assertEqual(1, len(set(reports)))

    def test_store_is_content_addressed_and_idempotent(self) -> None:
        store = FlowVersionStore()
        options = {
            "author_ref": "user:fixture",
            "created_at": FIXED_TIME,
        }
        first = store.save(graph(), **options)
        duplicate = store.save(copy.deepcopy(graph()), **options)
        self.assertIs(first, duplicate)
        self.assertEqual((first,), store.versions(first.version.graph_id))


if __name__ == "__main__":
    unittest.main()
