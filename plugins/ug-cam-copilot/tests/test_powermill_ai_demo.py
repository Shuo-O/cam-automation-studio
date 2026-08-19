import json
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from powermill_ai_demo import (  # noqa: E402
    SAMPLE_LOG,
    analyze_payload,
    mine_recipes,
    parse_events,
    render_powermill_macro,
)


class WorkflowLearningTests(unittest.TestCase):
    def test_json_sample_finds_repeated_workflow(self):
        events = parse_events(SAMPLE_LOG, "powermill", "json")
        recipes = mine_recipes(events, min_support=2)
        self.assertEqual(len(events), 12)
        self.assertTrue(recipes)
        self.assertGreaterEqual(recipes[0].support, 2)
        self.assertIn("calculate", recipes[0].title)

    def test_macro_parser_skips_comments_and_preserves_raw_commands(self):
        events = parse_events(
            """
            // recorded
            EDIT PAR TOLERANCE "0.05"
            CALCULATE TOOLPATH "ROUGH_A"
            """,
            "powermill",
            "powermill-macro",
        )
        self.assertEqual([event.action for event in events], ["EDIT", "CALCULATE"])
        self.assertEqual(events[0].raw, 'EDIT PAR TOLERANCE "0.05"')

    def test_analysis_contains_safe_exports(self):
        result = analyze_payload({"text": SAMPLE_LOG, "format": "json"})
        self.assertEqual(result["summary"]["event_count"], 12)
        self.assertTrue(result["recipes"][0]["powermill_macro"].startswith("// Generated"))
        self.assertIn("REVIEW", result["recipes"][0]["powermill_macro"])

    def test_jsonl_is_supported(self):
        text = "\n".join(
            json.dumps({"app": "nx", "action": "create", "target": "operation"})
            for _ in range(2)
        )
        self.assertEqual(len(parse_events(text, "nx", "jsonl")), 2)

    def test_events_expose_manual_automation_and_system_modes(self):
        events = parse_events(
            json.dumps(
                [
                    {
                        "app": "nx",
                        "action": "cam.operation.create",
                        "category": "operation",
                        "mode": "operator",
                    },
                    {
                        "app": "nx",
                        "action": "cam.toolpath.generate",
                        "category": "toolpath",
                        "source": "recipe-replay",
                    },
                    {
                        "app": "nx",
                        "action": "heartbeat",
                        "category": "service",
                    },
                ]
            ),
            "nx",
            "json",
        )

        self.assertEqual(
            [event.mode for event in events],
            ["manual", "automation", "system"],
        )
        self.assertEqual(
            [event.target for event in events],
            ["operation", "toolpath", "service"],
        )

    def test_analysis_serializes_mode_for_client_side_filtering(self):
        result = analyze_payload({"text": SAMPLE_LOG, "format": "json"})

        self.assertTrue(all(event["mode"] == "manual" for event in result["events"]))


if __name__ == "__main__":
    unittest.main()
