from __future__ import annotations

import threading
import time
import unittest

from cam_automation.command_tasks import CommandTaskService
from cam_automation.diagnostics import DiagnosticsService
from tests.test_command_tasks import ControlledTransport, instance, task


class DiagnosticsServiceTests(unittest.TestCase):
    def test_snapshot_reports_connection_queue_log_lag_counts_and_duration(self) -> None:
        descriptor = instance("instance-a")
        descriptor["metadata"] = {"log_lag_ms": 125}
        transport = ControlledTransport()
        service = CommandTaskService(
            {"nx": transport},
            instances={"instance-a": descriptor},
            timeout_poll_interval=0.002,
        )
        self.addCleanup(service.close)
        diagnostics = DiagnosticsService(
            service,
            instance_provider={"instance-a": descriptor},
        )
        gate = threading.Event()
        started = threading.Event()
        transport.control("diagnostic-running", gate=gate, started=started)
        service.submit(task("diagnostic-running"))
        self.assertTrue(started.wait(1))
        service.submit(task("diagnostic-queued"))

        before = time.perf_counter()
        snapshot = diagnostics.snapshot()
        elapsed = time.perf_counter() - before
        current = snapshot["instances"][0]
        self.assertLess(elapsed, 0.1)
        self.assertEqual("connected", current["connection_status"])
        self.assertEqual(1, current["queue_length"])
        self.assertEqual(1, current["running_count"])
        self.assertGreaterEqual(current["oldest_wait_ms"], 0)
        self.assertEqual(125, current["log_lag_ms"])
        self.assertEqual(2, current["submitted_count"])
        self.assertIsNotNone(current["heartbeat_at"])

        gate.set()
        self.assertEqual("succeeded", service.wait("diagnostic-running", 1).status)
        self.assertEqual("succeeded", service.wait("diagnostic-queued", 1).status)
        final = diagnostics.snapshot()["instances"][0]
        self.assertEqual(0, final["queue_length"])
        self.assertEqual(0, final["running_count"])
        self.assertEqual(2, final["task_counts"]["total_succeeded"])
        self.assertEqual(2, final["duration"]["count"])

    def test_snapshot_does_not_call_transports_or_require_task_lock(self) -> None:
        descriptor = instance("instance-a")
        transport = ControlledTransport()
        service = CommandTaskService(
            {"nx": transport},
            instances={"instance-a": descriptor},
        )
        self.addCleanup(service.close)
        diagnostics = DiagnosticsService(
            service,
            instance_provider={"instance-a": descriptor},
            log_lag_provider=lambda _: 7,
        )

        first = diagnostics.snapshot().to_dict()
        second = diagnostics.snapshot().to_dict()
        self.assertEqual([], transport.calls)
        self.assertEqual(7, first["instances"][0]["log_lag_ms"])
        self.assertEqual("instance-a", second["instances"][0]["instance_id"])


if __name__ == "__main__":
    unittest.main()
