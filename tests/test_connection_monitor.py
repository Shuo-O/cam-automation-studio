from __future__ import annotations

import unittest

from cam_automation.adapters.powermill_macro import FixturePowerMillTransport
from cam_automation.connection_monitor import ConnectionMonitor, _build_instances


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

    def test_double_pm_double_nx_same_titles_and_headless_processes(self) -> None:
        snapshot = _build_instances(
            [
                ("ugraf.exe", 101, "nx-a"),
                ("ugraf.exe", 102, "nx-b"),
                ("PowerMill.exe", 201, "pm-a"),
                ("pmill.exe", 202, "pm-b"),
            ],
            [
                {"pid": 101, "handle": 0xA1, "title": "Same project"},
                {"pid": 102, "handle": 0xA2, "title": "Same project"},
                {"pid": 201, "handle": 0xB1, "title": "Same project"},
            ],
        )

        self.assertEqual(2, len(snapshot["nx"]))
        self.assertEqual(2, len(snapshot["powermill"]))
        self.assertEqual(
            4,
            len(
                {
                    instance["instance_id"]
                    for instances in snapshot.values()
                    for instance in instances
                }
            ),
        )
        headless = next(item for item in snapshot["powermill"] if item["pid"] == 202)
        self.assertIsNone(headless["window_handle"])
        self.assertTrue(headless["metadata"]["headless"])

    def test_process_start_token_prevents_pid_reuse_aliasing(self) -> None:
        first = _build_instances(
            [("PowerMill.exe", 401, "created-at-a")],
            [{"pid": 401, "handle": 0xC1, "title": "Project - PowerMill"}],
        )
        reused = _build_instances(
            [("PowerMill.exe", 401, "created-at-b")],
            [{"pid": 401, "handle": 0xC1, "title": "Project - PowerMill"}],
        )

        self.assertNotEqual(
            first["powermill"][0]["instance_id"],
            reused["powermill"][0]["instance_id"],
        )

    def test_authorized_fixture_connections_are_explicitly_not_live(self) -> None:
        processes = [("PowerMill.exe", 501, "pm-501")]
        windows = [
            {
                "pid": 501,
                "handle": 0xD1,
                "title": "Cavity - PowerMill 2026",
                "project_id": "pm-project",
            }
        ]
        transport = FixturePowerMillTransport()
        monitor = ConnectionMonitor(
            process_provider=lambda: processes,
            window_provider=lambda: (windows, 0),
            transports={"powermill": transport},
        )

        detected = monitor.list_instances()[0]
        self.assertEqual("detected", detected.connection_status)
        self.assertFalse(detected.live_connected)

        monitor.authorize_local_sources(True)
        connected = monitor.connect(detected.instance_id)
        self.assertEqual("connected", connected.connection_status)
        self.assertEqual("fixture", connected.transport_kind)
        self.assertFalse(connected.live_connected)
        self.assertEqual("fixture", connected.metadata["connection_mode"])

    def test_per_instance_responses_disconnects_and_project_switches_do_not_cross(self) -> None:
        processes = [
            ("PowerMill.exe", 601, "pm-601"),
            ("PowerMill.exe", 602, "pm-602"),
            ("ugraf.exe", 701, "nx-701"),
            ("ugraf.exe", 702, "nx-702"),
        ]
        windows = [
            {
                "pid": 601,
                "handle": 0xE1,
                "title": "Same title",
                "project_id": "pm-a",
                "project_name": "A",
                "target_version": "PowerMill 2026",
            },
            {
                "pid": 602,
                "handle": 0xE2,
                "title": "Same title",
                "project_id": "pm-b",
                "project_name": "B",
                "target_version": "PowerMill 2026",
            },
            {"pid": 701, "handle": 0xF1, "title": "Same title"},
            {"pid": 702, "handle": 0xF2, "title": "Same title"},
        ]
        built = _build_instances(processes, windows)
        ids_by_pid = {
            item["pid"]: item["instance_id"] for item in built["powermill"]
        }
        fixtures = {
            ids_by_pid[601]: {
                "project_id": "pm-a",
                "target_version": "PowerMill 2026",
                "responses": {
                    "powermill.project.info": {
                        "structured_response": {"fixture_marker": "A"}
                    }
                },
            },
            ids_by_pid[602]: {
                "project_id": "pm-b",
                "target_version": "PowerMill 2026",
                "responses": {
                    "powermill.project.info": {
                        "structured_response": {"fixture_marker": "B"}
                    }
                },
            },
        }
        transport = FixturePowerMillTransport(fixtures)
        monitor = ConnectionMonitor(
            process_provider=lambda: processes,
            window_provider=lambda: (windows, 0),
            transports={"powermill": transport},
            authorized_local_sources=True,
        )
        instances = monitor.list_instances()
        pm_instances = {item.pid: item for item in instances if item.product == "powermill"}
        self.assertEqual(2, len(pm_instances))
        self.assertEqual(2, len([item for item in instances if item.product == "nx"]))

        first_id = pm_instances[601].instance_id
        second_id = pm_instances[602].instance_id
        first = monitor.query(first_id, {"operation": "powermill.project.info"})
        second = monitor.query(second_id, {"operation": "powermill.project.info"})

        self.assertEqual(first_id, first["target_instance_id"])
        self.assertEqual(second_id, second["target_instance_id"])
        self.assertEqual("A", first["structured_response"]["fixture_marker"])
        self.assertEqual("B", second["structured_response"]["fixture_marker"])
        states = {item.instance_id: item for item in monitor.list_instances(refresh=False)}
        self.assertEqual(first["response_id"], states[first_id].last_response["response_id"])
        self.assertEqual(second["response_id"], states[second_id].last_response["response_id"])

        windows[0]["title"] = "Renamed window"
        renamed = {item.pid: item for item in monitor.refresh()}
        self.assertEqual(first_id, renamed[601].instance_id)

        monitor.disconnect(first_id)
        self.assertEqual("disconnected", monitor.query(first_id, "PRINT 1")["status"])
        self.assertEqual(
            "disconnected",
            {item.pid: item for item in monitor.refresh()}[601].connection_status,
        )
        self.assertEqual(
            "succeeded",
            monitor.query(second_id, {"operation": "powermill.project.info"})["status"],
        )

        windows[1]["project_id"] = "pm-b-next"
        switched = {item.pid: item for item in monitor.refresh()}
        self.assertIsNone(switched[602].last_response)
        stale_project = monitor.query(
            second_id,
            {"operation": "powermill.project.info"},
        )
        self.assertEqual("failed", stale_project["status"])
        self.assertEqual(
            "response_attribution_mismatch",
            stale_project["error"]["code"],
        )

        processes[:] = [item for item in processes if item[1] != 601]
        windows[:] = [item for item in windows if item["pid"] != 601]
        remaining = monitor.refresh()
        self.assertNotIn(first_id, {item.instance_id for item in remaining})
        self.assertFalse(transport.is_connected(first_id))


if __name__ == "__main__":
    unittest.main()
