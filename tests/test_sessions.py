from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta, timezone

from cam_automation.sessions import SessionService


def event(
    source_session: str,
    seq: int,
    timestamp: str,
    action: str,
    *,
    instance_id: str = "nx:instance-a",
    project_id: str = "project-a",
    params: dict | None = None,
    explicit_marker: str | None = None,
    duration_ms: int | None = None,
) -> dict:
    value = {
        "schema_version": 1,
        "session_id": source_session,
        "seq": seq,
        "product": "nx",
        "instance_id": instance_id,
        "project_id": project_id,
        "action": action,
        "category": "operation",
        "mode": "manual",
        "source_mode": "manual",
        "params": params or {},
        "timestamp": timestamp,
        "duration_ms": duration_ms,
        "target_version": "NX 2406",
    }
    if explicit_marker is not None:
        value["explicit_marker"] = explicit_marker
    return value


class SessionServiceTests(unittest.TestCase):
    def test_empty_log_and_input_reordering_are_deterministic(self) -> None:
        service = SessionService()
        self.assertEqual([], service.build([]))

        events = [
            event("source-a", 0, "2026-08-23T01:00:00Z", "cam.operation.create"),
            event("source-a", 1, "2026-08-23T01:00:01Z", "cam.toolpath.generate"),
            event(
                "source-b",
                0,
                "2026-08-23T02:00:00Z",
                "cam.operation.create",
                instance_id="nx:instance-b",
            ),
        ]
        first = [item.to_dict() for item in service.build(events)]
        second = [item.to_dict() for item in service.build(reversed(events))]

        self.assertEqual(first, second)
        self.assertEqual(2, len(first))
        self.assertNotEqual(first[0]["instance_id"], first[1]["instance_id"])

    def test_source_event_refs_may_repeat_across_isolated_instances(self) -> None:
        events = [
            event(
                "shared-source",
                0,
                "2026-08-23T01:00:00Z",
                "cam.operation.create",
                instance_id="nx:instance-a",
            ),
            event(
                "shared-source",
                0,
                "2026-08-23T01:00:00Z",
                "cam.operation.create",
                instance_id="nx:instance-b",
            ),
        ]

        sessions = SessionService().build(events)

        self.assertEqual(2, len(sessions))
        self.assertEqual(
            {"nx:instance-a", "nx:instance-b"},
            {item.instance_id for item in sessions},
        )

    def test_project_marker_and_time_gap_split_sessions(self) -> None:
        events = [
            event(
                "source-a",
                0,
                "2026-08-23T01:00:00Z",
                "cam.operation.create",
                explicit_marker="first",
            ),
            event("source-a", 1, "2026-08-23T01:00:01Z", "cam.toolpath.generate"),
            event(
                "source-b",
                0,
                "2026-08-23T01:00:02Z",
                "cam.operation.create",
                explicit_marker="second",
            ),
            event(
                "source-c",
                0,
                "2026-08-23T03:00:00Z",
                "cam.operation.create",
                project_id="project-b",
            ),
            event(
                "source-d",
                0,
                "2026-08-23T04:00:00Z",
                "cam.operation.create",
            ),
        ]
        sessions = SessionService(inactivity_gap=timedelta(minutes=30)).build(events)

        self.assertEqual(4, len(sessions))
        self.assertTrue(sessions[0].explicit_marker)
        self.assertTrue(sessions[1].explicit_marker)
        self.assertEqual({"project-a", "project-b"}, {item.project_id for item in sessions})

    def test_merge_and_split_are_stable_and_scoped(self) -> None:
        events = [
            event(
                "source-a",
                0,
                "2026-08-23T01:00:00Z",
                "cam.operation.create",
                explicit_marker="first",
            ),
            event("source-a", 1, "2026-08-23T01:00:01Z", "cam.toolpath.generate"),
            event(
                "source-b",
                0,
                "2026-08-23T01:00:02Z",
                "cam.operation.create",
                explicit_marker="second",
            ),
            event("source-b", 1, "2026-08-23T01:00:03Z", "cam.toolpath.generate"),
        ]
        service = SessionService()
        built = service.build(events)
        merged = service.merge(item.session_id for item in reversed(built))
        split = service.split(merged.session_id, 2)

        self.assertEqual(4, merged.event_count)
        self.assertEqual([2, 2], [item.event_count for item in split])
        self.assertEqual(
            [item.to_dict() for item in split],
            [item.to_dict() for item in service.split(merged.session_id, [2])],
        )

    def test_compare_two_to_five_sessions_reports_steps_parameters_and_duration(
        self,
    ) -> None:
        base = datetime(2026, 8, 23, 1, tzinfo=timezone.utc)
        events = []
        for index in range(5):
            started = base + timedelta(hours=index)
            source = f"source-{index}"
            marker = f"marker-{index}"
            events.extend(
                [
                    event(
                        source,
                        0,
                        started.isoformat(),
                        "cam.operation.create",
                        params={"operation_type": "mill_planar"},
                        explicit_marker=marker,
                        duration_ms=10,
                    ),
                    event(
                        source,
                        1,
                        (started + timedelta(seconds=1)).isoformat(),
                        "cam.parameter.set",
                        params={"tolerance": 0.01 + index * 0.001},
                        duration_ms=20,
                    ),
                    event(
                        source,
                        2,
                        (started + timedelta(seconds=2)).isoformat(),
                        "cam.toolpath.generate",
                        duration_ms=30,
                    ),
                ]
            )
        service = SessionService()
        sessions = service.build(events)
        ids = [item.session_id for item in sessions]

        first = service.compare(ids)
        second = service.compare(reversed(ids))

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(5, len(first.session_ids))
        self.assertEqual(3, len(first.common_steps))
        self.assertTrue(
            any(item["parameter"] == "tolerance" for item in first.parameter_differences)
        )
        self.assertEqual({60}, set(first.duration_ms_by_session.values()))
        with self.assertRaisesRegex(ValueError, "2 to 5"):
            service.compare(ids[:1])

    def test_shuffle_smoke_preserves_session_ids(self) -> None:
        events = [
            event(
                f"source-{index}",
                step,
                (
                    datetime(2026, 8, 23, tzinfo=timezone.utc)
                    + timedelta(hours=index, seconds=step)
                ).isoformat(),
                f"cam.step.{step}",
                explicit_marker=f"marker-{index}" if step == 0 else None,
            )
            for index in range(40)
            for step in range(8)
        ]
        service = SessionService()
        expected = [item.session_id for item in service.build(events)]
        random.Random(42).shuffle(events)

        self.assertEqual(expected, [item.session_id for item in service.build(events)])


if __name__ == "__main__":
    unittest.main()
