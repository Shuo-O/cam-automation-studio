from __future__ import annotations

import copy
import unittest
from unittest import mock

from cam_automation import flow_versions
from cam_automation.flow_compatibility import project_flow_to_recipe
from cam_automation.flow_contracts import (
    canonical_json,
    compute_artifact_hash,
    compute_semantic_hash,
)
from cam_automation.flow_versions import (
    CommandHistory,
    FlowVersionStore,
    GraphChange,
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

    def test_history_reuses_exact_immutable_snapshots_without_rehashing(self) -> None:
        original = graph()
        command = GraphCommand.replace(
            "command:value",
            "semantic",
            "/graph_parameters/0/default",
            1,
            2,
        )
        history = CommandHistory(original)
        with mock.patch.object(
            flow_versions,
            "compute_semantic_hash",
            wraps=compute_semantic_hash,
        ) as semantic_hash:
            changed = history.execute(command)
            self.assertEqual(1, semantic_hash.call_count)
            undone = history.undo()
            redone = history.redo()
            self.assertEqual(1, semantic_hash.call_count)

        self.assertIs(undone, history.undo())
        self.assertIs(redone, history.redo())
        self.assertEqual(original, undone.to_dict())
        self.assertEqual(changed.to_dict(), redone.to_dict())
        self.assertEqual(canonical_json(original), canonical_json(undone))
        self.assertEqual(canonical_json(changed), canonical_json(redone))

        detached = undone.to_dict()
        detached["graph_parameters"][0]["default"] = 99
        self.assertEqual(1, undone.to_dict()["graph_parameters"][0]["default"])

    def test_history_branch_discards_only_obsolete_redo_snapshot(self) -> None:
        history = CommandHistory(graph())
        first = GraphCommand.replace(
            "command:first",
            "semantic",
            "/graph_parameters/0/default",
            1,
            2,
        )
        second = GraphCommand.replace(
            "command:second",
            "semantic",
            "/graph_parameters/0/default",
            1,
            3,
        )
        history.execute(first)
        history.undo()
        history.execute(second)
        self.assertFalse(history.can_redo)
        with self.assertRaisesRegex(IndexError, "available to redo"):
            history.redo()
        self.assertEqual(3, history.graph.to_dict()["graph_parameters"][0]["default"])

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

    def test_json_diff_matches_canonical_reference_for_nested_json(self) -> None:
        before = {
            "same_number": 1,
            "bool": True,
            "escaped": {"a/b~c": ["same", {"value": 1.0}]},
            "removed": [{"id": 1}, {"id": 2}],
        }
        after = {
            "same_number": 1.0,
            "bool": 1,
            "escaped": {"a/b~c": ["same", {"value": 2}]},
            "added": {"nested": ["value"]},
            "removed": [{"id": 1}],
        }

        def reference_changes(left, right, path=""):
            if isinstance(left, dict) and isinstance(right, dict):
                result = []
                for key in sorted(set(left) | set(right)):
                    child = (
                        f"{path}/"
                        f"{str(key).replace('~', '~0').replace('/', '~1')}"
                    )
                    if key not in left:
                        result.append(
                            flow_versions.JsonChange("add", child, after=right[key])
                        )
                    elif key not in right:
                        result.append(
                            flow_versions.JsonChange("remove", child, before=left[key])
                        )
                    else:
                        result.extend(reference_changes(left[key], right[key], child))
                return result
            if isinstance(left, list) and isinstance(right, list):
                if canonical_json(left) == canonical_json(right):
                    return []
                result = []
                common = min(len(left), len(right))
                for index in range(common):
                    result.extend(
                        reference_changes(left[index], right[index], f"{path}/{index}")
                    )
                for index in range(len(left) - 1, common - 1, -1):
                    result.append(
                        flow_versions.JsonChange(
                            "remove",
                            f"{path}/{index}",
                            before=left[index],
                        )
                    )
                for index in range(common, len(right)):
                    result.append(
                        flow_versions.JsonChange(
                            "add",
                            f"{path}/{index}",
                            after=right[index],
                        )
                    )
                return result
            if canonical_json(left) == canonical_json(right):
                return []
            return [
                flow_versions.JsonChange(
                    "replace",
                    path or "/",
                    before=left,
                    after=right,
                )
            ]

        expected = [item.to_dict() for item in reference_changes(before, after)]
        actual = [
            item.to_dict()
            for item in flow_versions._json_changes(before, after)
        ]
        self.assertEqual(expected, actual)

    def test_large_diff_avoids_container_canonicalization(self) -> None:
        before = {
            "nodes": [
                {
                    "node_id": f"node:{index:04d}",
                    "enabled": True,
                    "configuration": {"fixture_index": index},
                }
                for index in range(500)
            ],
            "edges": [
                {
                    "edge_id": f"edge:{index:04d}",
                    "source": f"node:{index % 500:04d}",
                    "target": f"node:{(index + 1) % 500:04d}",
                }
                for index in range(800)
            ],
        }
        after = copy.deepcopy(before)
        after["nodes"][-1]["configuration"]["fixture_index"] = 500

        with mock.patch.object(
            flow_versions,
            "canonical_json",
            wraps=canonical_json,
        ) as canonical:
            changes = flow_versions._json_changes(before, after)

        self.assertEqual(
            ["/nodes/499/configuration/fixture_index"],
            [change.path for change in changes],
        )
        self.assertTrue(
            all(
                not isinstance(call.args[0], (dict, list, tuple))
                for call in canonical.call_args_list
            )
        )

    def test_four_layer_diff_takes_one_full_snapshot_per_graph(self) -> None:
        original = graph()
        changed = target_graph()
        with mock.patch.object(
            flow_versions,
            "_raw",
            wraps=flow_versions._raw,
        ) as raw:
            diff = four_layer_diff(original, changed)
        self.assertTrue(diff.graph_changes)
        self.assertEqual(2, raw.call_count)

    def test_graph_change_preconditions_keep_json_number_semantics(self) -> None:
        value = {"value": 1, "flag": True}
        flow_versions._apply_change(
            value,
            GraphChange.replace("/value", 1.0, 2),
        )
        self.assertEqual(2, value["value"])
        with self.assertRaisesRegex(ValueError, "precondition failed"):
            flow_versions._apply_change(
                value,
                GraphChange.replace("/flag", 1, False),
            )

    def test_json_comparison_keeps_canonical_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "lone Unicode surrogates"):
            flow_versions._json_changes(
                {"value": "\ud800"},
                {"value": "\ud800"},
            )
        with self.assertRaisesRegex(ValueError, "exactly representable"):
            flow_versions._json_changes(
                {"value": 9_007_199_254_740_992},
                {"value": 9_007_199_254_740_992},
            )

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
