from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cam_automation.recorder import CaptureService


class CaptureServiceTests(unittest.TestCase):
    def test_consent_gates_redacted_cross_product_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            power_log = root / "operator.mac"
            power_log.write_text(
                "\n".join(
                    [
                        "# session: expert-a",
                        "PowerMill> IMPORT MODEL 'C:\\Users\\Alice\\Client-X\\part.dmt'",
                        "IMPORT MODEL 'D:\\Automation\\template.dmt'",
                        "PowerMill> PRINT 'system'",
                    ]
                ),
                encoding="utf-8",
            )
            nx_journal = root / "recorded.py"
            nx_journal.write_text(
                "\n".join(
                    [
                        "session = NXOpen.Session.GetSession()",
                        "part = session.Parts.Work",
                        "operation = part.CAMSetup.CAMOperationCollection.Create('mill_planar')",
                        "operation.GenerateToolPath()",
                    ]
                ),
                encoding="utf-8",
            )

            service = CaptureService(
                root / "capture",
                source_paths={"powermill": [power_log], "nx": [nx_journal]},
                start_background=False,
            )
            try:
                service.config.auto_connect = False
                service._save_config()
                self.assertEqual("awaiting_consent", service.status()["state"])
                self.assertEqual(0, service.scan_once())

                service.configure(consent=True, operator_label="expert")
                inserted = service.scan_once()
                events = service.recent_events(limit=100)
                serialized = json.dumps(events, ensure_ascii=False)

                self.assertGreaterEqual(inserted, 5)
                self.assertEqual({"nx", "powermill"}, {event["product"] for event in events})
                self.assertNotIn("Alice", serialized)
                self.assertNotIn("Client-X", serialized)
                self.assertNotIn("C:\\\\Users", serialized)
                self.assertIn("<PATH_", serialized)
                self.assertTrue(
                    all(
                        event["params"]["capture"]["operator_label"] == "expert"
                        for event in events
                    )
                )
                self.assertEqual(0, service.scan_once())

                execution_added = service.record_execution(
                    request={
                        "product": "powermill",
                        "action": "cam.model.import",
                        "command": "IMPORT MODEL 'C:\\Users\\Alice\\secret.dmt'",
                        "risk": "safe",
                        "recipe_hash": "abc123",
                    },
                    result={"status": "dry_run", "duration_ms": 1.5},
                )
                self.assertEqual(1, execution_added)
                latest = service.recent_events(limit=1)[0]
                self.assertEqual("execution", latest["category"])
                self.assertNotIn("Alice", json.dumps(latest))

                removed = service.clear()
                self.assertEqual(inserted + 1, removed)
                self.assertEqual(0, service.status()["counts"]["total"])
            finally:
                service.close()
                service.close()

    def test_enable_requires_consent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CaptureService(directory, start_background=False)
            try:
                with self.assertRaisesRegex(ValueError, "explicit consent"):
                    service.configure(enabled=True)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
