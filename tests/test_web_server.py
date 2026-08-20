from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from cam_automation.web_server import _WorkflowHandler, _WorkflowServer


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

                status, plugins = self._request(
                    connection,
                    "POST",
                    "/api/plugins/install",
                    {"plugin_id": "cam-execution-gateway"},
                )
                self.assertEqual(200, status)
                self.assertEqual(["cam-execution-gateway"], plugins["changed"])
                self.assertIsNotNone(server.execution)

                status, recorder = self._request(connection, "GET", "/api/recorder")
                self.assertEqual(200, status)
                self.assertEqual("awaiting_consent", recorder["state"])

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
                    connection, "GET", "/api/recorder/events?limit=10"
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
