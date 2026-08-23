import json
import time
import unittest

from cam_automation.models import ActivityEvent, EventQuery
from cam_automation.parser import normalize_command, parse_log


class ParserTests(unittest.TestCase):
    def test_parses_session_markers_timestamps_and_prompts(self) -> None:
        result = parse_log(
            """
# session: first
[2026-08-20T10:00:00+08:00] PowerMill> IMPORT   MODEL   'a b.dmt'
DEBUG: ignored
# session: second
2026-08-20 10:01:00 | PMILL> SIZE MODEL 'b'
"""
        )

        self.assertEqual(["first", "second"], [session.name for session in result.sessions])
        self.assertEqual(2, result.event_count)
        self.assertEqual("IMPORT MODEL 'a b.dmt'", result.sessions[0].events[0].normalized)
        self.assertEqual("IMPORT MODEL", result.sessions[0].events[0].operation)
        self.assertEqual("cam.model.import", result.sessions[0].events[0].action)
        self.assertEqual("manual", result.sessions[0].events[0].mode)
        self.assertTrue(
            all(
                event["action"].startswith(("cam.", "powermill."))
                for event in result.to_activity_events()
            )
        )

    def test_parses_jsonl_and_reports_invalid_events(self) -> None:
        valid = json.dumps(
            {
                "timestamp": "2026-08-20T10:00:00Z",
                "source": "recorder",
                "command": "SIZE TOOLPATH 'rough'",
            }
        )
        result = parse_log(valid + "\n" + '{"event":"click"}')

        self.assertEqual(1, result.event_count)
        self.assertEqual("recorder", result.sessions[0].events[0].source)
        self.assertEqual(1, len(result.diagnostics))

    def test_accepts_shared_activity_event_jsonl(self) -> None:
        event = {
            "schema_version": 1,
            "session_id": "session-a",
            "seq": 0,
            "product": "powermill",
            "action": "cam.model.bounds.query",
            "category": "model",
            "params": {
                "command": "SIZE MODEL 'part-a'",
                "mode": "operator",
            },
        }
        result = parse_log(json.dumps(event))

        self.assertEqual(1, result.event_count)
        self.assertEqual("session-a", result.sessions[0].name)
        self.assertEqual("cam.model.bounds.query", result.sessions[0].events[0].action)
        self.assertEqual("manual", result.sessions[0].events[0].mode)

    def test_activity_event_round_trips_extensions_and_unknown_fields(self) -> None:
        event = {
            "schema_version": 1,
            "session_id": "session-a",
            "seq": 7,
            "product": "powermill",
            "action": "part.save",
            "category": "data",
            "mode": "manual",
            "params": {"command": "SAVE PROJECT", "future_param": {"enabled": True}},
            "source_file": "fixture.jsonl",
            "source_line": 11,
            "duration_ms": None,
            "timestamp": "2026-08-23T09:00:00Z",
            "source_mode": "manual",
            "view_level": "L2",
            "expertise_label": "expert",
            "instance_id": "powermill:1:A",
            "project_id": None,
            "target_version": "PowerMill 2026",
            "command_response": None,
            "review_status": "unreviewed",
            "recipe_hash": None,
            "future_contract_field": {"nested": [1, "two"]},
        }

        restored = ActivityEvent.from_dict(event).to_dict()
        projected = parse_log(json.dumps(event)).to_activity_events()[0]

        self.assertEqual(event, restored)
        self.assertEqual(event, projected)
        self.assertEqual("part.save", projected["action"])

    def test_execution_audit_mirrors_legacy_automation_mode(self) -> None:
        event = ActivityEvent.from_dict(
            {
                "session_id": "audit-a",
                "seq": 0,
                "product": "nx",
                "action": "cam.recipe.preview",
                "category": "execution",
                "source_mode": "execution_audit",
                "params": {"command": "PREVIEW"},
            }
        )

        self.assertEqual("execution_audit", event.effective_source_mode)
        self.assertEqual("automation", event.mode)
        self.assertEqual("automation", event.to_dict()["mode"])

    def test_event_query_rejects_invalid_contract_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_modes"):
            EventQuery(source_modes=("operator",))
        with self.assertRaisesRegex(ValueError, "view_levels"):
            EventQuery(view_levels=("L5",))
        with self.assertRaisesRegex(ValueError, "from_time"):
            EventQuery(
                from_time="2026-08-23T10:00:00Z",
                to_time="2026-08-23T09:00:00Z",
            )
        with self.assertRaisesRegex(ValueError, "limit"):
            EventQuery(limit=0)

    def test_classifies_manual_automation_and_system_modes(self) -> None:
        result = parse_log(
            "\n".join(
                [
                    "PowerMill> IMPORT MODEL 'manual.dmt'",
                    "IMPORT MODEL 'macro.dmt'",
                    "PowerMill> DIALOGS MESSAGE OFF",
                    json.dumps(
                        {
                            "command": "SIZE MODEL 'api-model'",
                            "source": "plugin-api",
                            "mode": "script",
                        }
                    ),
                ]
            )
        )

        events = result.sessions[0].events
        self.assertEqual(
            ["manual", "automation", "system", "automation"],
            [event.mode for event in events],
        )
        projected = result.to_activity_events()
        self.assertEqual("manual", projected[0]["params"]["mode"])
        self.assertEqual(
            ["powermill", "model", "cam.model.import"],
            projected[0]["params"]["hierarchy"],
        )

    def test_normalization_preserves_quoted_whitespace(self) -> None:
        self.assertEqual(
            "PRINT 'a   b' VALUE",
            normalize_command("  PRINT   'a   b'   VALUE  "),
        )

    def test_linear_parser_handles_large_logs(self) -> None:
        text = "\n".join(f"PRINT 'tool-{index}'" for index in range(20_000))
        started = time.perf_counter()
        result = parse_log(text)
        elapsed = time.perf_counter() - started

        self.assertEqual(20_000, result.event_count)
        self.assertLess(elapsed, 3.0)


if __name__ == "__main__":
    unittest.main()
