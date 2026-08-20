from __future__ import annotations

import unittest

from cam_automation.connection_monitor import _build_instances


class ConnectionMonitorTests(unittest.TestCase):
    def test_multiple_processes_and_windows_get_stable_runtime_ids(self) -> None:
        snapshot = _build_instances(
            [
                ("ugraf.exe", 101),
                ("ugraf.exe", 102),
                ("PowerMill.exe", 201),
                ("explorer.exe", 999),
            ],
            [
                {"pid": 101, "handle": 0xA1, "title": "Part A - NX"},
                {"pid": 101, "handle": 0xA2, "title": "Tool dialog - NX"},
                {"pid": 102, "handle": 0xB1, "title": "Part B - NX"},
                {"pid": 201, "handle": 0xC1, "title": "Project C - PowerMill"},
                {"pid": 999, "handle": 0xD1, "title": "Files"},
            ],
            foreground_handle=0xB1,
        )

        self.assertEqual(3, len(snapshot["nx"]))
        self.assertEqual(1, len(snapshot["powermill"]))
        self.assertEqual("nx:102:B1", snapshot["nx"][0]["instance_id"])
        self.assertTrue(snapshot["nx"][0]["is_foreground"])
        self.assertEqual(
            {"nx:101:A1", "nx:101:A2", "nx:102:B1"},
            {item["instance_id"] for item in snapshot["nx"]},
        )

    def test_headless_cam_process_remains_visible_as_process_instance(self) -> None:
        snapshot = _build_instances(
            [("pmill.exe", 301)],
            [],
        )

        self.assertEqual("powermill:301:process", snapshot["powermill"][0]["instance_id"])
        self.assertIsNone(snapshot["powermill"][0]["window_handle"])


if __name__ == "__main__":
    unittest.main()
