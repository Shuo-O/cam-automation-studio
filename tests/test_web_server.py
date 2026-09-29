from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode
from unittest.mock import patch

from cam_automation.connection_monitor import InstanceDescriptor
from cam_automation.integrations import ApiServices
from cam_automation.recorder import RecorderService
from cam_automation.web_server import _WorkflowHandler, _WorkflowServer


ROOT = Path(__file__).resolve().parents[1]
BARRIER_FIXTURE = ROOT / "tests" / "fixtures" / "barrier1-events.json"
RECIPE_FIXTURE = ROOT / "examples" / "contracts" / "recipe.json"
WAVE2_FIXTURES = ROOT / "tests" / "fixtures"


class _FixtureConnections:
    def list_instances(self, *, refresh=True):
        return [
            InstanceDescriptor(
                instance_id=f"{product}:fixture:{suffix}",
                product=product,
                pid=pid,
                process_name="ugraf.exe" if product == "nx" else "PowerMill.exe",
                window_handle=f"0x{pid:X}",
                window_title=f"Fixture {suffix}",
                is_foreground=suffix == "A",
                window_state="foreground" if suffix == "A" else "visible",
                target_version=version,
                project_id=project,
                connection_status=status,
            )
            for product, pid, suffix, version, project, status in (
                ("nx", 101, "A", "NX 2406", "nx-project-a", "connected"),
                ("nx", 102, "B", "NX 2406", "nx-project-b", "disconnected"),
                (
                    "powermill",
                    201,
                    "A",
                    "PowerMill 2026",
                    "pm-project-a",
                    "connected",
                ),
                (
                    "powermill",
                    202,
                    "B",
                    "PowerMill 2025",
                    "pm-project-b",
                    "detected",
                ),
            )
        ]


class _FixtureTasks:
    def __init__(self):
        self.tasks = {}

    def submit(self, task):
        value = {
            **task,
            "schema_version": 1,
            "task_id": "task:wave2:fixture",
            "status": "queued",
        }
        self.tasks[value["task_id"]] = value
        return value

    def get(self, task_id):
        return self.tasks[task_id]

    def cancel(self, task_id):
        value = {**self.tasks[task_id], "status": "cancelled"}
        self.tasks[task_id] = value
        return value


class _FixtureDiagnostics:
    def snapshot(self):
        return {
            "schema_version": 1,
            "captured_at": "2026-08-24T00:00:00Z",
            "instance_heartbeats": [
                {"instance_id": "nx:fixture:A", "status": "connected"}
            ],
            "queue_depth": 1,
            "log_latency_ms": 12,
            "task_metrics": {"queued": 1, "running": 0},
        }


class _RaisingTasks:
    def __init__(self, error):
        self.error = error

    def submit(self, task):
        raise self.error

    def get(self, task_id):
        raise self.error

    def cancel(self, task_id):
        raise self.error


class _ServiceProblem(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"fixture {code}")


class RecorderHttpTests(unittest.TestCase):
    def test_recorder_and_execution_apis_are_integrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "CAM_APP_DATA_DIR": os.path.join(directory, "app"),
                    "CAM_CAPTURE_DIR": os.path.join(directory, "capture"),
                },
            ):
                server = _WorkflowServer(("127.0.0.1", 0), _WorkflowHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                self.assertIsNone(server.recorder)
                self.assertIsNone(server.execution)

                status, plugins = self._request(connection, "GET", "/api/plugins")
                self.assertEqual(200, status)
                self.assertEqual(0, plugins["installed_count"])

                status, missing = self._request(connection, "GET", "/api/recorder")
                self.assertEqual(409, status)
                self.assertEqual("cam-local-capture", missing["plugin_id"])

                status, plugins = self._request(
                    connection,
                    "POST",
                    "/api/plugins/install",
                    {"plugin_id": "cam-local-capture"},
                )
                self.assertEqual(200, status)
                self.assertEqual(["cam-local-capture"], plugins["changed"])
                self.assertIsNotNone(server.recorder)
                capture_plugin = next(
                    item
                    for item in plugins["plugins"]
                    if item["id"] == "cam-local-capture"
                )
                self.assertEqual("recording", capture_plugin["runtime"]["status"])
                self.assertIn("instances", capture_plugin["runtime"])

                status, plugins = self._request(
                    connection,
                    "POST",
                    "/api/plugins/install",
                    {"plugin_id": "powermill-cam-copilot"},
                )
                self.assertEqual(200, status)
                self.assertEqual(["powermill-cam-copilot"], plugins["changed"])

                status, plugins = self._request(
                    connection,
                    "POST",
                    "/api/plugins/install",
                    {"plugin_id": "cam-execution-gateway"},
                )
                self.assertEqual(200, status)
                self.assertEqual(["cam-execution-gateway"], plugins["changed"])
                self.assertIsNotNone(server.execution)
                execution_plugin = next(
                    item
                    for item in plugins["plugins"]
                    if item["id"] == "cam-execution-gateway"
                )
                self.assertEqual("ready", execution_plugin["runtime"]["status"])

                status, recorder = self._request(connection, "GET", "/api/recorder")
                self.assertEqual(200, status)
                self.assertEqual("recording", recorder["state"])
                self.assertEqual("auto_install", recorder["consent_source"])
                self.assertTrue(recorder["auto_authorized"])
                self.assertEqual(
                    {"logs": True, "instances": True, "execution_audit": True},
                    recorder["categories"],
                )

                status, recorder = self._request(
                    connection,
                    "POST",
                    "/api/recorder/settings",
                    {"detect_instances": False},
                )
                self.assertEqual(200, status)
                self.assertFalse(recorder["categories"]["instances"])
                self.assertEqual([], recorder["instances"]["nx"])

                status, connections = self._request(
                    connection, "GET", "/api/connections?include_uninstalled=1"
                )
                self.assertEqual(200, status)
                self.assertEqual(
                    {"codex", "nx", "powermill", "cimatron"},
                    {item["key"] for item in connections["connections"]},
                )

                status, invalid = self._request(
                    connection, "GET", "/api/recorder/events?limit=invalid"
                )
                self.assertEqual(400, status)
                self.assertIn("limit", invalid["error"])

                status, recorder = self._request(
                    connection,
                    "POST",
                    "/api/recorder/consent",
                    {"accepted": True},
                )
                self.assertEqual(200, status)
                self.assertEqual("recording", recorder["state"])
                status, recorder = self._request(
                    connection,
                    "POST",
                    "/api/recorder/settings",
                    {"detect_instances": True},
                )
                self.assertEqual(200, status)
                self.assertTrue(recorder["categories"]["instances"])

                status, connections = self._request(
                    connection, "GET", "/api/connections"
                )
                self.assertEqual(200, status)
                powermill = next(
                    item
                    for item in connections["connections"]
                    if item["key"] == "powermill"
                )
                self.assertEqual("connected", powermill["status"])
                self.assertEqual(2, powermill["instance_count"])
                self.assertIsNone(powermill["active_instance_id"])
                self.assertEqual(
                    {"powermill:4101:C1", "powermill:4102:process"},
                    {
                        item["instance_id"]
                        for item in powermill["instances"]
                    },
                )

                status, plugins = self._request(connection, "GET", "/api/plugins")
                self.assertEqual(200, status)
                power_plugin = next(
                    item
                    for item in plugins["plugins"]
                    if item["id"] == "powermill-cam-copilot"
                )
                self.assertEqual(2, power_plugin["runtime"]["instance_count"])

                request = {
                    "product": "powermill",
                    "action": "cam.model.import",
                    "command": "IMPORT MODEL 'C:\\Users\\Operator\\part.dmt'",
                    "risk": "safe",
                    "recipe_hash": "reviewed-hash",
                    "target_version": "PowerMill 2026",
                    "test_project": True,
                    "mode": "dry-run",
                }
                status, execution = self._request(
                    connection, "POST", "/api/execution/run", request
                )
                self.assertEqual(200, status)
                self.assertEqual("dry_run", execution["status"])

                status, captured = self._request(
                    connection,
                    "GET",
                    "/api/recorder/events?action=cam.model.import&limit=10",
                )
                self.assertEqual(200, status)
                self.assertEqual(1, captured["returned"])
                serialized = json.dumps(captured, ensure_ascii=False)
                self.assertNotIn("Operator", serialized)
                self.assertIn("<PATH_", serialized)

                request["mode"] = "live"
                status, rejected = self._request(
                    connection, "POST", "/api/execution/run", request
                )
                self.assertEqual(422, status)
                self.assertEqual("rejected", rejected["status"])
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_revoked_capture_stays_revoked_after_reinstall(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "CAM_APP_DATA_DIR": os.path.join(directory, "app"),
                    "CAM_CAPTURE_DIR": os.path.join(directory, "capture"),
                },
            ):
                server = _WorkflowServer(("127.0.0.1", 0), _WorkflowHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                status, installed = self._request(
                    connection,
                    "POST",
                    "/api/plugins/install",
                    {"plugin_id": "cam-local-capture"},
                )
                self.assertEqual(200, status)
                self.assertEqual("recording", server.recorder.status()["state"])

                status, revoked = self._request(
                    connection,
                    "POST",
                    "/api/recorder/consent",
                    {"accepted": False},
                )
                self.assertEqual(200, status)
                self.assertEqual("revoked", revoked["consent_source"])
                self.assertFalse(revoked["consent"])

                status, plugin_status = self._request(
                    connection,
                    "GET",
                    "/api/plugins",
                )
                self.assertEqual(200, status)
                capture = next(
                    item
                    for item in plugin_status["plugins"]
                    if item["id"] == "cam-local-capture"
                )
                self.assertTrue(capture["authorization"]["revoked"])
                self.assertTrue(
                    all(
                        item["revoked"]
                        for item in capture["authorization"]["categories"]
                        if item["classification"] == "advanced"
                    )
                )

                status, removed = self._request(
                    connection,
                    "POST",
                    "/api/plugins/uninstall",
                    {"plugin_id": "cam-local-capture"},
                )
                self.assertEqual(200, status)
                self.assertEqual(["cam-local-capture"], removed["changed"])

                status, reinstalled = self._request(
                    connection,
                    "POST",
                    "/api/plugins/install",
                    {"plugin_id": "cam-local-capture"},
                )
                self.assertEqual(200, status)
                self.assertEqual(["cam-local-capture"], reinstalled["changed"])
                self.assertEqual("awaiting_consent", server.recorder.status()["state"])
                self.assertEqual("revoked", server.recorder.status()["consent_source"])
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    @staticmethod
    def _request(
        connection: http.client.HTTPConnection,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        decoded = json.loads(response.read().decode("utf-8"))
        return response.status, decoded


class Wave2ApiContractTests(unittest.TestCase):
    def test_wave2_json_fixtures_cover_required_ui_and_service_states(self) -> None:
        paths = sorted(WAVE2_FIXTURES.glob("wave2-api*.json"))
        self.assertEqual(4, len(paths))
        fixtures = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in paths
        }

        states = fixtures["wave2-api-states"]
        self.assertTrue(states["loading"]["pending"])
        self.assertEqual([], states["empty"]["response"]["events"])
        self.assertEqual(
            {
                "plugin_not_installed",
                "instance_not_connected",
                "invalid_parameters",
                "timeout",
                "task_cancelled",
                "unavailable_capability",
                "conflict",
            },
            {item["response"]["code"] for item in states["errors"]},
        )

        connection_fixture = fixtures["wave2-api-connections-events"]
        instances = connection_fixture["connections_response"]["instances"]
        events = connection_fixture["event_page_response"]["events"]
        self.assertEqual(
            {"nx": 2, "powermill": 2},
            {
                product: sum(item["product"] == product for item in instances)
                for product in ("nx", "powermill")
            },
        )
        self.assertIsNone(
            connection_fixture["connections_response"]["selected_instance_id"]
        )
        self.assertEqual(
            {"L0", "L1", "L2", "L3", "L4"},
            {item["view_level"] for item in events},
        )
        self.assertEqual({"manual"}, {item["source_mode"] for item in events})

        learning = fixtures["wave2-api-learning"]
        self.assertTrue(learning["sessions_response"]["sessions"])
        self.assertEqual(
            "review_required",
            learning["recipe_response"]["recipe"]["status"],
        )
        execution = fixtures["wave2-api-execution"]
        self.assertEqual(
            {"submit", "get", "cancel"},
            set(execution["service_injection"]["commands"]),
        )
        self.assertEqual(
            "dry_run",
            execution["run_response"]["response"]["task"]["execution_mode"],
        )
        self.assertIn(
            "queue_depth",
            execution["diagnostics_response"]["response"]["diagnostics"],
        )

    def test_wave2_routes_assemble_frozen_services_over_real_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = json.loads(BARRIER_FIXTURE.read_text(encoding="utf-8"))
            events = [dict(item) for item in fixture["events"]]
            events[0]["source_file"] = r"C:\Users\Operator\private\nx-journal.log"
            source = root / "barrier1-events.jsonl"
            source.write_text(
                "".join(
                    json.dumps(event, separators=(",", ":")) + "\n"
                    for event in events
                ),
                encoding="utf-8",
            )
            recorder = RecorderService(
                root / "capture",
                source_paths={"nx": [source]},
                start_background=False,
            )
            tasks = _FixtureTasks()
            services = ApiServices(
                recorder=recorder,
                connections=_FixtureConnections(),
                commands=tasks,
                diagnostics=_FixtureDiagnostics(),
            )
            with patch.dict(
                os.environ,
                {
                    "CAM_APP_DATA_DIR": str(root / "app"),
                    "CAM_CAPTURE_DIR": str(root / "capture"),
                },
            ):
                server = _WorkflowServer(
                    ("127.0.0.1", 0),
                    _WorkflowHandler,
                    services=services,
                )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                for plugin_id in (
                    "cam-local-capture",
                    "ug-cam-copilot",
                    "powermill-cam-copilot",
                    "cam-execution-gateway",
                ):
                    status, _ = self._request(
                        connection,
                        "POST",
                        "/api/plugins/install",
                        {"plugin_id": plugin_id},
                    )
                    self.assertEqual(200, status)

                status, scanned = self._request(
                    connection,
                    "POST",
                    "/api/recorder/scan",
                    {},
                )
                self.assertEqual(200, status)
                self.assertEqual(len(events), scanned["inserted"])

                status, connections = self._request(
                    connection,
                    "GET",
                    "/api/connections?refresh=1",
                )
                self.assertEqual(200, status)
                self.assertEqual(4, connections["instance_count"])
                self.assertIsNone(connections["selected_instance_id"])
                self.assertTrue(connections["selection_required"])
                self.assertEqual(
                    {"nx:fixture:A", "nx:fixture:B"},
                    {
                        item["instance_id"]
                        for item in connections["instances"]
                        if item["product"] == "nx"
                    },
                )

                def concurrent_query():
                    current = http.client.HTTPConnection(
                        "127.0.0.1",
                        server.server_port,
                        timeout=5,
                    )
                    try:
                        current.request(
                            "GET",
                            "/api/recorder/events?source_mode=manual&limit=2",
                        )
                        response = current.getresponse()
                        payload = json.loads(response.read().decode("utf-8"))
                        return response.status, payload["returned_count"]
                    finally:
                        current.close()

                with ThreadPoolExecutor(max_workers=4) as pool:
                    concurrent_results = list(
                        pool.map(lambda _: concurrent_query(), range(12))
                    )
                self.assertEqual({(200, 2)}, set(concurrent_results))

                event_parameters = [
                    ("source_mode", "manual"),
                    ("view_level", "L2"),
                    ("product", "nx"),
                    ("instance_id", "nx:fixture:A"),
                    ("project_id", "nx-project-a"),
                    ("action", "cam.parameter.set"),
                    ("from_time", "2026-08-23T08:00:00Z"),
                    ("to_time", "2026-08-23T12:00:00Z"),
                    ("text", "tolerance"),
                    ("limit", "1"),
                    ("sort", "asc"),
                ]
                status, first_page = self._request(
                    connection,
                    "GET",
                    "/api/recorder/events?" + urlencode(event_parameters),
                )
                self.assertEqual(200, status)
                self.assertEqual(1, first_page["returned_count"])
                self.assertEqual(1, first_page["returned"])
                self.assertTrue(first_page["has_more"])
                self.assertIsNotNone(first_page["next_cursor"])
                self.assertEqual("L2", first_page["events"][0]["view_level"])
                self.assertEqual("manual", first_page["events"][0]["source_mode"])

                connection.request("GET", "/api/recorder/export")
                export_response = connection.getresponse()
                exported = export_response.read().decode("utf-8")
                self.assertEqual(200, export_response.status)
                self.assertNotIn("Operator", exported)
                self.assertNotIn(r"C:\Users", exported)

                second_parameters = [
                    *event_parameters,
                    ("cursor", first_page["next_cursor"]),
                ]
                status, second_page = self._request(
                    connection,
                    "GET",
                    "/api/recorder/events?" + urlencode(second_parameters),
                )
                self.assertEqual(200, status)
                self.assertEqual(1, second_page["returned_count"])
                self.assertFalse(second_page["has_more"])

                mismatched_parameters = [
                    (key, "powermill" if key == "product" else value)
                    for key, value in second_parameters
                ]
                status, mismatch = self._request(
                    connection,
                    "GET",
                    "/api/recorder/events?" + urlencode(mismatched_parameters),
                )
                self.assertEqual(400, status)
                self.assertEqual("invalid_parameters", mismatch["code"])

                status, session_page = self._request(
                    connection,
                    "GET",
                    "/api/sessions?limit=10000&sort=asc",
                )
                self.assertEqual(200, status)
                nx_manual = [
                    item["session_id"]
                    for item in session_page["sessions"]
                    if item["product"] == "nx"
                    and item["source_modes"] == ["manual"]
                ]
                self.assertEqual(3, len(nx_manual))

                status, compared = self._request(
                    connection,
                    "POST",
                    "/api/sessions/compare",
                    {
                        "session_ids": nx_manual[:2],
                        "baseline_session_id": nx_manual[0],
                    },
                )
                self.assertEqual(200, status)
                self.assertEqual(2, len(compared["diff"]["session_ids"]))

                status, learned = self._request(
                    connection,
                    "POST",
                    "/api/workflows/mine",
                    {"session_ids": nx_manual},
                )
                self.assertEqual(200, status)
                self.assertGreaterEqual(learned["returned_count"], 1)
                self.assertEqual("nx", learned["candidates"][0]["product"])

                recipe = json.loads(RECIPE_FIXTURE.read_text(encoding="utf-8"))
                status, saved = self._request(
                    connection,
                    "POST",
                    "/api/recipes",
                    {"recipe": recipe},
                )
                self.assertEqual(201, status)
                recipe_id = saved["recipe"]["recipe_id"]
                status, loaded = self._request(
                    connection,
                    "GET",
                    "/api/recipes/" + quote(recipe_id, safe=""),
                )
                self.assertEqual(200, status)
                self.assertEqual(recipe_id, loaded["recipe"]["recipe_id"])

                task_request = {
                    "schema_version": 1,
                    "task_type": "recipe_preview",
                    "execution_mode": "dry_run",
                    "product": "nx",
                    "target_version": "NX 2406",
                    "target_instance_id": "nx:fixture:A",
                    "project_id": "nx-project-a",
                    "recipe_hash": recipe["recipe_hash"],
                    "operation": "nx.recipe.preview",
                    "arguments": {},
                    "status": "queued",
                    "submitted_at": "2026-08-24T00:00:00Z",
                    "timeout_ms": 5000,
                    "requested_by": "operator:fixture",
                    "review": {"reviewer": "operator:fixture"},
                }
                status, submitted = self._request(
                    connection,
                    "POST",
                    "/api/execution/run",
                    task_request,
                )
                self.assertEqual(202, status)
                task_id = submitted["task"]["task_id"]
                self.assertEqual("queued", submitted["task"]["status"])

                status, task = self._request(
                    connection,
                    "GET",
                    "/api/execution/tasks/" + quote(task_id, safe=""),
                )
                self.assertEqual(200, status)
                self.assertEqual(task_id, task["task"]["task_id"])

                connection.request(
                    "POST",
                    "/api/execution/tasks/" + quote(task_id, safe="") + "/cancel",
                )
                cancel_response = connection.getresponse()
                cancelled = json.loads(cancel_response.read().decode("utf-8"))
                self.assertEqual(200, cancel_response.status)
                self.assertEqual("cancelled", cancelled["task"]["status"])

                status, diagnostics = self._request(
                    connection,
                    "GET",
                    "/api/diagnostics",
                )
                self.assertEqual(200, status)
                self.assertEqual(1, diagnostics["diagnostics"]["queue_depth"])

                missing_target = dict(task_request)
                missing_target.pop("target_instance_id")
                status, invalid = self._request(
                    connection,
                    "POST",
                    "/api/execution/run",
                    missing_target,
                )
                self.assertEqual(400, status)
                self.assertEqual("invalid_parameters", invalid["code"])

                status, invalid = self._request(
                    connection,
                    "GET",
                    "/api/recorder/events?unknown=value",
                )
                self.assertEqual(400, status)
                self.assertEqual("invalid_parameters", invalid["code"])

                server.services.commands = _RaisingTasks(TimeoutError())
                status, timeout = self._request(
                    connection,
                    "POST",
                    "/api/execution/run",
                    task_request,
                )
                self.assertEqual(504, status)
                self.assertEqual("timeout", timeout["code"])

                server.services.commands = _RaisingTasks(ConnectionError())
                status, disconnected = self._request(
                    connection,
                    "POST",
                    "/api/execution/run",
                    task_request,
                )
                self.assertEqual(409, status)
                self.assertEqual("instance_not_connected", disconnected["code"])

                server.services.commands = _RaisingTasks(CancelledError())
                status, task_cancelled = self._request(
                    connection,
                    "GET",
                    "/api/execution/tasks/task%3Awave2%3Afixture",
                )
                self.assertEqual(409, status)
                self.assertEqual("task_cancelled", task_cancelled["code"])

                server.services.commands = _RaisingTasks(_ServiceProblem("conflict"))
                status, conflict = self._request(
                    connection,
                    "POST",
                    "/api/execution/run",
                    task_request,
                )
                self.assertEqual(409, status)
                self.assertEqual("conflict", conflict["code"])

                serialized = json.dumps(
                    {
                        "connections": connections,
                        "events": first_page,
                        "sessions": session_page,
                        "diagnostics": diagnostics,
                    },
                    ensure_ascii=False,
                )
                self.assertNotIn("Operator", serialized)
                self.assertNotIn(r"C:\Users", serialized)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                recorder.close()
                thread.join(timeout=3)

    def test_default_server_runs_fixture_chain_through_real_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {
                    "CAM_APP_DATA_DIR": str(root / "app"),
                    "CAM_CAPTURE_DIR": str(root / "capture"),
                },
            ):
                server = _WorkflowServer(("127.0.0.1", 0), _WorkflowHandler)
            self.assertEqual(
                "CommandTaskService",
                type(server.services.commands).__name__,
            )
            self.assertEqual(
                "DiagnosticsService",
                type(server.services.diagnostics).__name__,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=5,
            )
            try:
                status, plugins = self._request(connection, "GET", "/api/plugins")
                self.assertEqual(200, status)
                self.assertEqual(0, plugins["installed_count"])

                for plugin_id in (
                    "cam-local-capture",
                    "ug-cam-copilot",
                    "powermill-cam-copilot",
                    "cam-execution-gateway",
                    "cam-codex-review",
                ):
                    status, plugins = self._request(
                        connection,
                        "POST",
                        "/api/plugins/install",
                        {"plugin_id": plugin_id},
                    )
                    self.assertEqual(200, status)
                self.assertEqual(5, plugins["installed_count"])

                status, initial_connections = self._request(
                    connection,
                    "GET",
                    "/api/connections",
                )
                self.assertEqual(200, status)
                self.assertEqual(
                    {"nx:3101:A1", "powermill:4101:C1"},
                    {
                        item["instance_id"]
                        for item in initial_connections["instances"]
                        if item["connection_status"] == "connected"
                    },
                )

                status, connections = self._request(
                    connection,
                    "GET",
                    "/api/connections?refresh=1",
                )
                self.assertEqual(200, status)
                self.assertEqual(4, connections["instance_count"])
                self.assertEqual(
                    {
                        "nx:3101:A1",
                        "nx:3102:B2",
                        "powermill:4101:C1",
                        "powermill:4102:process",
                    },
                    {item["instance_id"] for item in connections["instances"]},
                )
                self.assertEqual(
                    {"nx:3101:A1", "powermill:4101:C1"},
                    {
                        item["instance_id"]
                        for item in connections["instances"]
                        if item["connection_status"] == "connected"
                    },
                )
                self.assertTrue(
                    all(
                        not item["live_connected"]
                        for item in connections["instances"]
                    )
                )

                status, first_page = self._request(
                    connection,
                    "GET",
                    "/api/recorder/events"
                    "?source_mode=manual&view_level=L2&limit=2&sort=asc",
                )
                self.assertEqual(200, status)
                self.assertEqual(2, first_page["returned_count"])
                self.assertTrue(first_page["has_more"])
                self.assertEqual(
                    {"manual"},
                    {item["source_mode"] for item in first_page["events"]},
                )
                self.assertEqual(
                    {"L2"},
                    {item["view_level"] for item in first_page["events"]},
                )
                status, second_page = self._request(
                    connection,
                    "GET",
                    "/api/recorder/events?"
                    + urlencode(
                        {
                            "source_mode": "manual",
                            "view_level": "L2",
                            "limit": 2,
                            "sort": "asc",
                            "cursor": first_page["next_cursor"],
                        }
                    ),
                )
                self.assertEqual(200, status)
                self.assertGreaterEqual(second_page["returned_count"], 1)

                status, session_page = self._request(
                    connection,
                    "GET",
                    "/api/sessions?limit=10000&sort=asc",
                )
                self.assertEqual(200, status)
                manual = [
                    item
                    for item in session_page["sessions"]
                    if item["source_modes"] == ["manual"]
                ]
                nx_manual = [
                    item["session_id"]
                    for item in manual
                    if item["product"] == "nx"
                ]
                self.assertEqual(5, len(nx_manual))
                self.assertEqual(7, len(manual))

                for selected in (nx_manual[:3], nx_manual):
                    status, compared = self._request(
                        connection,
                        "POST",
                        "/api/sessions/compare",
                        {
                            "session_ids": selected,
                            "baseline_session_id": selected[0],
                        },
                    )
                    self.assertEqual(200, status, compared)
                    self.assertEqual(
                        len(selected),
                        len(compared["diff"]["session_ids"]),
                    )

                status, learned = self._request(
                    connection,
                    "POST",
                    "/api/workflows/mine",
                    {"session_ids": nx_manual},
                )
                self.assertEqual(200, status)
                self.assertGreaterEqual(learned["returned_count"], 1)

                recipe = json.loads(RECIPE_FIXTURE.read_text(encoding="utf-8"))
                status, saved = self._request(
                    connection,
                    "POST",
                    "/api/recipes",
                    {"recipe": recipe},
                )
                self.assertEqual(201, status)
                recipe_hash = saved["recipe"]["recipe_hash"]

                context = json.loads(
                    (
                        ROOT
                        / "examples"
                        / "contracts"
                        / "codex-review-request.json"
                    ).read_text(encoding="utf-8")
                )
                status, request = self._request(
                    connection,
                    "POST",
                    "/api/codex/context",
                    {"context": context},
                )
                self.assertEqual(200, status)
                review = json.loads(
                    (
                        ROOT
                        / "examples"
                        / "contracts"
                        / "codex-review-result.json"
                    ).read_text(encoding="utf-8")
                )
                review["request_id"] = request["request_id"]
                status, reviewed = self._request(
                    connection,
                    "POST",
                    "/api/codex/review",
                    {"review": review},
                )
                self.assertEqual(200, status)
                self.assertEqual(request["request_id"], reviewed["request_id"])

                task_request = {
                    "schema_version": 1,
                    "task_id": "task:final-release:preview",
                    "task_type": "recipe_preview",
                    "execution_mode": "dry_run",
                    "product": "nx",
                    "target_version": "NX 2406",
                    "target_instance_id": "nx:3101:A1",
                    "project_id": "nx-project-a",
                    "recipe_hash": recipe_hash,
                    "operation": "cam.recipe.preview",
                    "arguments": {"parameters": {}},
                    "status": "queued",
                    "submitted_at": "2026-08-24T00:00:00Z",
                    "timeout_ms": 5000,
                    "requested_by": "operator:browser-fixture",
                    "review": {
                        "status": "accepted",
                        "reviewer": "operator:browser-fixture",
                        "scope": "dry_run_only",
                    },
                }
                status, submitted = self._request(
                    connection,
                    "POST",
                    "/api/execution/run",
                    task_request,
                )
                self.assertEqual(202, status)
                task_id = submitted["task"]["task_id"]
                task = submitted["task"]
                for _ in range(100):
                    status, payload = self._request(
                        connection,
                        "GET",
                        "/api/execution/tasks/" + quote(task_id, safe=""),
                    )
                    self.assertEqual(200, status)
                    task = payload["task"]
                    if task["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.01)
                self.assertEqual("succeeded", task["status"])
                diff = task["response"]["diff_report"]
                self.assertEqual("nx:3101:A1", diff["target_instance_id"])
                self.assertEqual(recipe_hash, diff["recipe_hash"])
                self.assertEqual(
                    {"not_run", "required"},
                    {
                        item["status"]
                        for item in diff["gate_results"]
                        if item["gate"]
                        in {"cam_simulation", "collision_check", "shop_approval"}
                    },
                )

                status, diagnostics = self._request(
                    connection,
                    "GET",
                    "/api/diagnostics",
                )
                self.assertEqual(200, status)
                self.assertEqual(
                    4,
                    len(diagnostics["diagnostics"]["instances"]),
                )
                self.assertTrue(
                    any(
                        item["submitted_count"] == 1
                        for item in diagnostics["diagnostics"]["instances"]
                    )
                )
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_missing_a_cmd_is_explicitly_unavailable_and_body_limit_is_enforced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "CAM_APP_DATA_DIR": os.path.join(directory, "app"),
                    "CAM_CAPTURE_DIR": os.path.join(directory, "capture"),
                },
            ):
                server = _WorkflowServer(
                    ("127.0.0.1", 0),
                    _WorkflowHandler,
                    services=ApiServices(),
                )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                status, plugins = self._request(connection, "GET", "/api/plugins")
                self.assertEqual(200, status)
                self.assertEqual(0, plugins["installed_count"])
                self.assertTrue(
                    all(
                        not item["installed"]
                        and not item["enabled"]
                        and not item["update"]["available"]
                        for item in plugins["plugins"]
                    )
                )

                status, installed = self._request(
                    connection,
                    "POST",
                    "/api/plugins/install",
                    {"plugin_id": "cam-execution-gateway"},
                )
                self.assertEqual(200, status)
                capture = next(
                    item
                    for item in installed["plugins"]
                    if item["id"] == "cam-local-capture"
                )
                self.assertEqual("ON_INSTALL", capture["authorization"]["policy"])
                self.assertTrue(capture["authorization"]["granted_once"])
                self.assertFalse(capture["local_data"]["uploads_enabled"])

                status, update = self._request(
                    connection,
                    "POST",
                    "/api/plugins/update",
                    {"plugin_id": "cam-execution-gateway"},
                )
                self.assertEqual(200, status)
                self.assertEqual("current", update["update_result"]["status"])
                self.assertEqual([], update["changed"])
                request = {
                    "task_type": "query",
                    "execution_mode": "read_only",
                    "product": "nx",
                    "target_version": "NX 2406",
                    "target_instance_id": "nx:fixture:A",
                    "project_id": "nx-project-a",
                    "recipe_hash": None,
                    "operation": "nx.project.inspect",
                    "arguments": {},
                }
                status, unavailable = self._request(
                    connection,
                    "POST",
                    "/api/execution/run",
                    request,
                )
                self.assertEqual(503, status)
                self.assertEqual("unavailable_capability", unavailable["code"])

                status, unavailable = self._request(
                    connection,
                    "GET",
                    "/api/execution/tasks/task%3Amissing",
                )
                self.assertEqual(503, status)
                self.assertEqual("unavailable_capability", unavailable["code"])

                status, unavailable = self._request(
                    connection,
                    "GET",
                    "/api/diagnostics",
                )
                self.assertEqual(503, status)
                self.assertEqual("unavailable_capability", unavailable["code"])

                with socket.create_connection(
                    ("127.0.0.1", server.server_port),
                    timeout=5,
                ) as raw:
                    raw.sendall(
                        (
                            "POST /api/plugins/install HTTP/1.1\r\n"
                            "Host: 127.0.0.1\r\n"
                            "Content-Type: application/json\r\n"
                            f"Content-Length: {2 * 1024 * 1024 + 1}\r\n"
                            "Connection: close\r\n\r\n"
                        ).encode("ascii")
                    )
                    chunks = []
                    while True:
                        chunk = raw.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                raw_response = b"".join(chunks)
                head, payload = raw_response.split(b"\r\n\r\n", 1)
                oversized = json.loads(payload.decode("utf-8"))
                self.assertIn(
                    b" 413 ",
                    head.split(b"\r\n", 1)[0],
                    oversized,
                )
                self.assertEqual("invalid_parameters", oversized["code"])
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    @staticmethod
    def _request(
        connection: http.client.HTTPConnection,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        decoded = json.loads(response.read().decode("utf-8"))
        return response.status, decoded


if __name__ == "__main__":
    unittest.main()
