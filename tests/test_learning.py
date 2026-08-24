import unittest
from datetime import datetime, timedelta, timezone

from cam_automation.learning import WorkflowLearner, command_shape, learn_workflow
from cam_automation.sample import SAMPLE_LOG
from cam_automation.sessions import SessionService


class LearningTests(unittest.TestCase):
    def test_command_shape_abstracts_literals(self) -> None:
        self.assertEqual(
            "EDIT TOOL <STRING> DIAMETER <NUMBER>",
            command_shape("EDIT TOOL 'T10' DIAMETER 10.0"),
        )

    def test_learns_parameters_across_matching_sessions(self) -> None:
        parsed, recipe = learn_workflow(SAMPLE_LOG, name="demo")

        self.assertEqual(2, len(parsed.sessions))
        self.assertEqual(8, len(recipe.steps))
        self.assertEqual(["cavity-a", "cavity-b"], recipe.sessions_matched)
        parameter_names = {parameter.name for parameter in recipe.parameters}
        self.assertIn("model_path", parameter_names)
        self.assertIn("model_name", parameter_names)
        self.assertEqual(1, sum(step.risk == "review" for step in recipe.steps))
        self.assertEqual(1, sum(step.risk == "blocked" for step in recipe.steps))

    def test_selects_dominant_structural_sequence(self) -> None:
        text = """
# session: a
SIZE MODEL 'a'
PRINT 'a'
# session: b
SIZE MODEL 'b'
PRINT 'b'
# session: outlier
SIZE TOOLPATH 'x'
"""
        _, recipe = learn_workflow(text)

        self.assertEqual(["a", "b"], recipe.sessions_matched)
        self.assertEqual(2, len(recipe.steps))
        self.assertTrue(any("Used 2 of 3" in item for item in recipe.diagnostics))

    def test_empty_log_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "No PowerMill commands"):
            learn_workflow("# comments only")


def activity_event(
    source_session: str,
    seq: int,
    timestamp: datetime,
    action: str,
    *,
    params: dict | None = None,
    marker: str | None = None,
    category: str = "operation",
) -> dict:
    value = {
        "schema_version": 1,
        "session_id": source_session,
        "seq": seq,
        "product": "nx",
        "instance_id": "nx:fixture",
        "project_id": "project-a",
        "action": action,
        "category": category,
        "mode": "manual",
        "source_mode": "manual",
        "params": params or {},
        "timestamp": timestamp.isoformat(),
        "target_version": "NX 2406",
    }
    if marker is not None:
        value["explicit_marker"] = marker
    return value


class WorkflowLearnerTests(unittest.TestCase):
    def _similar_sessions(self) -> tuple[SessionService, list[str]]:
        base = datetime(2026, 8, 23, tzinfo=timezone.utc)
        sequences = [
            [
                ("cam.operation.create", {"operation_type": "planar"}),
                ("cam.parameter.set", {"tolerance": 0.02, "enabled": True}),
                ("cam.branch.optional", {"strategy": "rest"}),
                ("cam.toolpath.generate", {}),
            ],
            [
                ("cam.operation.create", {"operation_type": "planar"}),
                ("cam.parameter.set", {"tolerance": 0.03, "enabled": False}),
                ("cam.branch.optional", {"strategy": "rest"}),
                ("cam.toolpath.generate", {}),
            ],
            [
                ("cam.operation.create", {"operation_type": "planar"}),
                ("cam.parameter.set", {"tolerance": 0.015, "enabled": True}),
                ("cam.parameter.set", {"tolerance": 0.015, "enabled": True}),
                ("cam.toolpath.generate", {}),
                ("cam.explore.inspect", {"note": "one-off"}),
            ],
        ]
        events = []
        for session_index, sequence in enumerate(sequences):
            started = base + timedelta(hours=session_index)
            source = f"source-{session_index}"
            for seq, (action, params) in enumerate(sequence):
                events.append(
                    activity_event(
                        source,
                        seq,
                        started + timedelta(seconds=seq),
                        action,
                        params=params,
                        marker=f"marker-{session_index}" if seq == 0 else None,
                    )
                )
            events.append(
                activity_event(
                    source,
                    len(sequence),
                    started + timedelta(seconds=len(sequence)),
                    "nx.undo_mark",
                    params={"noise": True},
                    category="evidence",
                )
            )
        service = SessionService()
        sessions = service.build(events)
        return service, [item.session_id for item in sessions]

    def test_mines_stable_flow_branches_rework_parameters_and_evidence(self) -> None:
        service, session_ids = self._similar_sessions()
        learner = WorkflowLearner(service)

        candidates = learner.mine(reversed(session_ids))

        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual(3, candidate.support["matched_sessions"])
        self.assertEqual(
            [
                "cam.operation.create",
                "cam.parameter.set",
                "cam.toolpath.generate",
            ],
            [item["action"] for item in candidate.common_steps],
        )
        parameter_types = {item.name: item.value_type for item in candidate.parameters}
        self.assertEqual("number", parameter_types["tolerance"])
        self.assertEqual("boolean", parameter_types["enabled"])
        classifications = {item["classification"] for item in candidate.branches}
        self.assertIn("branch", classifications)
        self.assertIn("rework", classifications)
        self.assertIn("exploration", classifications)
        self.assertTrue(candidate.evidence)

        recipe = candidate.to_recipe()
        self.assertEqual("review_required", recipe.status)
        self.assertEqual("L3", candidate.view_level)
        self.assertIn("cam_simulation", recipe.required_gates)
        self.assertIn("collision_check", recipe.required_gates)
        self.assertIn("shop_approval", recipe.required_gates)

    def test_single_and_completely_different_sessions_have_no_candidate(self) -> None:
        service, session_ids = self._similar_sessions()
        self.assertEqual([], WorkflowLearner(service).mine(session_ids[:1]))

        base = datetime(2026, 8, 23, tzinfo=timezone.utc)
        unrelated = [
            activity_event(
                "unrelated-a",
                0,
                base,
                "cam.alpha.first",
                marker="unrelated-a",
            ),
            activity_event(
                "unrelated-a",
                1,
                base + timedelta(seconds=1),
                "cam.alpha.second",
            ),
            activity_event(
                "unrelated-b",
                0,
                base + timedelta(hours=1),
                "cam.beta.first",
                marker="unrelated-b",
            ),
            activity_event(
                "unrelated-b",
                1,
                base + timedelta(hours=1, seconds=1),
                "cam.beta.second",
            ),
        ]
        different_service = SessionService()
        different = different_service.build(unrelated)
        self.assertEqual(
            [],
            WorkflowLearner(different_service).mine(
                item.session_id for item in different
            ),
        )

    def test_reordered_mining_and_scale_smoke_are_deterministic(self) -> None:
        base = datetime(2026, 8, 23, tzinfo=timezone.utc)
        events = []
        for session_index in range(300):
            started = base + timedelta(minutes=session_index)
            source = f"scale-{session_index:04d}"
            for seq in range(6):
                events.append(
                    activity_event(
                        source,
                        seq,
                        started + timedelta(seconds=seq),
                        f"cam.scale.step_{seq}",
                        params={"feed": 100 + session_index, "step": seq},
                        marker=source if seq == 0 else None,
                    )
                )
        service = SessionService()
        sessions = service.build(reversed(events))
        ids = [item.session_id for item in sessions]
        learner = WorkflowLearner(service)

        first = [item.to_dict() for item in learner.mine(ids)]
        second = [item.to_dict() for item in learner.mine(reversed(ids))]

        self.assertEqual(first, second)
        self.assertEqual(300, first[0]["support"]["matched_sessions"])
        self.assertEqual(6, len(first[0]["common_steps"]))


if __name__ == "__main__":
    unittest.main()

