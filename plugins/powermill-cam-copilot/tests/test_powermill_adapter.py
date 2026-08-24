from __future__ import annotations

import unittest
from pathlib import Path

from cam_automation.adapters.powermill_macro import (
    FixturePowerMillTransport,
    PowerMillAdapter,
    PowerMillTransport,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class PowerMillAdapterTests(unittest.TestCase):
    def test_macro_preserves_raw_text_argument_order_and_default_gaps(self) -> None:
        events = PowerMillAdapter().parse(str(FIXTURES / "macro-fidelity.mac"))

        self.assertEqual(3, len(events))
        function, edit, query = events
        self.assertEqual("powermill.macro.function.define", function["action"])
        self.assertEqual(["$tolerance"], function["params"]["default_value_gaps"])
        self.assertEqual(
            'FUNCTION Main(STRING $project = "C:\\Jobs\\Cavity A", REAL $tolerance)',
            function["params"]["raw_line"],
        )
        self.assertEqual('"Rough   A"', edit["params"]["argument_order"][0])
        self.assertEqual("Rough   A", edit["params"]["arguments"][0]["value"])
        self.assertEqual(
            ['"Rough   A"', "TOLERANCE", "0.05", "STEPDOWN", "2"],
            edit["params"]["argument_order"],
        )
        self.assertEqual("review", edit["params"]["risk"])
        self.assertEqual("needs_review", edit["review_status"])
        self.assertEqual(
            "PRINT $entity('toolpath';'Rough   A').Name",
            query["params"]["normalized_command"],
        )
        self.assertEqual("safe", query["params"]["risk"])
        self.assertTrue(
            all(event["action"].startswith(("cam.", "powermill.")) for event in events)
        )

    def test_jsonl_keeps_execution_audit_compatibility_and_rejects_nx_input(self) -> None:
        adapter = PowerMillAdapter()
        events = adapter.parse(FIXTURES / "events.jsonl")

        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("execution_audit", event["source_mode"])
        self.assertEqual("automation", event["mode"])
        self.assertEqual(["selector"], event["params"]["default_value_gaps"])
        self.assertEqual("powermill:fixture:A", event["instance_id"])
        self.assertTrue(
            any("non-PowerMill" in item["message"] for item in adapter.diagnostics)
        )

    def test_timestamped_log_keeps_quoted_whitespace(self) -> None:
        event = PowerMillAdapter().parse(FIXTURES / "command-fidelity.log")[0]

        self.assertEqual("2026-08-23T10:05:00Z", event["timestamp"])
        self.assertEqual('"Rough   B"', event["params"]["argument_order"][0])
        self.assertEqual("cam.toolpath.bounds.query", event["action"])


class FixturePowerMillTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FixturePowerMillTransport.from_file(FIXTURES / "transport.json")
        for instance_id, version, project_id in (
            ("powermill:fixture:A", "PowerMill 2026", "pm-a"),
            ("powermill:fixture:B", "PowerMill 2025", "pm-b"),
        ):
            self.transport.connect(
                {
                    "instance_id": instance_id,
                    "target_version": version,
                    "project_id": project_id,
                }
            )

    def test_transport_protocol_is_offline_and_responses_are_isolated(self) -> None:
        self.assertIsInstance(self.transport, PowerMillTransport)
        self.assertTrue(self.transport.offline)

        first = self.transport.query(
            "powermill:fixture:A",
            {"task_id": "task-a", "operation": "powermill.project.info"},
        )
        second = self.transport.query(
            "powermill:fixture:B",
            {"task_id": "task-b", "operation": "powermill.project.info"},
        )

        self.assertEqual("powermill:fixture:A", first["target_instance_id"])
        self.assertEqual("pm-a", first["project_id"])
        self.assertEqual("A", first["structured_response"]["fixture_marker"])
        self.assertEqual("powermill:fixture:B", second["target_instance_id"])
        self.assertEqual("pm-b", second["project_id"])
        self.assertEqual("B", second["structured_response"]["fixture_marker"])
        self.assertNotEqual(first["response_id"], second["response_id"])
        self.assertEqual(0, first["structured_response"]["commands_sent"])

        first_snapshot = self.transport.snapshot("powermill:fixture:A")
        second_snapshot = self.transport.snapshot("powermill:fixture:B")
        self.assertEqual("fixture-snapshot-a", first_snapshot["snapshot_id"])
        self.assertEqual("fixture-snapshot-b", second_snapshot["snapshot_id"])
        self.assertNotEqual(first_snapshot["content_hash"], second_snapshot["content_hash"])

    def test_disconnect_and_unsafe_queries_fail_closed(self) -> None:
        for command in (
            "DELETE TOOLPATH 'rough'",
            "NCPROGRAM WRITE 'out.tap'",
            "POSTPROCESS NCProgram",
            "MACHINE RUN",
            "G1 X20",
        ):
            response = self.transport.query("powermill:fixture:A", command)
            self.assertEqual("rejected", response["status"], command)
            self.assertEqual("unsafe_query", response["error"]["code"])

        smuggled = self.transport.query(
            "powermill:fixture:A",
            {
                "operation": "cam.model.bounds.query",
                "arguments": {"macro": "PROJECT SAVE"},
            },
        )
        self.assertEqual("rejected", smuggled["status"])
        self.assertEqual("unsafe_query_arguments", smuggled["error"]["code"])

        self.transport.disconnect("powermill:fixture:A")
        disconnected = self.transport.query("powermill:fixture:A", "PRINT 1")
        self.assertEqual("disconnected", disconnected["status"])
        self.assertEqual(
            "succeeded",
            self.transport.query("powermill:fixture:B", "PRINT 1")["status"],
        )

    def test_fixture_can_model_timeout_without_claiming_live_connectivity(self) -> None:
        transport = FixturePowerMillTransport(
            {
                "powermill:fixture:timeout": {
                    "project_id": "pm-timeout",
                    "target_version": "PowerMill 2026",
                    "responses": {
                        "powermill.project.info": {
                            "status": "timed_out",
                            "error": {
                                "code": "fixture_timeout",
                                "message": "Offline timeout fixture.",
                            },
                        }
                    },
                }
            }
        )
        transport.connect(
            {
                "instance_id": "powermill:fixture:timeout",
                "project_id": "pm-timeout",
                "target_version": "PowerMill 2026",
            }
        )

        response = transport.query(
            "powermill:fixture:timeout",
            {"operation": "powermill.project.info"},
        )

        self.assertEqual("timed_out", response["status"])
        self.assertEqual("fixture_timeout", response["error"]["code"])
        self.assertTrue(transport.offline)


if __name__ == "__main__":
    unittest.main()
