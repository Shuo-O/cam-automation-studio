from __future__ import annotations

import copy
import http.client
import json
import os
import subprocess
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from cam_automation.flow_contracts import compute_capability_lock_hash
from cam_automation.flow_service import FlowConflictError
from cam_automation.fixture_runtime import build_fixture_flow_service
from cam_automation.integrations import (
    ApiServices,
    OfflineFlowIntegration,
    register_product_flow_capability,
)
from cam_automation.web_server import _WorkflowHandler, _WorkflowServer


ROOT = Path(__file__).resolve().parents[2]
POWERMILL_SOURCE = (
    ROOT
    / "plugins"
    / "powermill-cam-copilot"
    / "fixtures"
    / "flow"
    / "command-envelope.log"
)
CLI_PREVIEW_FIXTURE = (
    ROOT / "tests" / "fixtures" / "cam-flow" / "api" / "flow-api-fixture.json"
)
NO_BODY = object()
RIGHTS = {
    "status": "legal_reviewed",
    "evidence_ref": "rights:t09:fixture",
    "sharing_scope": "private",
    "redistribution_allowed": False,
    "network_egress_allowed": False,
    "binary_inspection": False,
}
ZERO_EXECUTION = {
    "transport": "none",
    "commands_sent": 0,
    "journal_executed": False,
    "macro_executed": False,
    "machine_output_count": 0,
}


def assert_zero_execution(
    case: unittest.TestCase,
    value: dict[str, Any],
) -> None:
    for field, expected in ZERO_EXECUTION.items():
        case.assertEqual(expected, value[field], field)


def import_reviewed_powermill(service: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    register_product_flow_capability(service, "powermill")
    imported = OfflineFlowIntegration(service).import_source(
        product="powermill",
        source=POWERMILL_SOURCE.read_bytes(),
        source_name=POWERMILL_SOURCE.name,
        target_versions=["PowerMill 2025"],
        rights=RIGHTS,
    )
    target = next(
        item
        for item in service.list_fixture_targets()
        if item["target_instance_id"] == "powermill:4102:process"
    )
    return imported, target


def prepare_preview(
    service: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    imported, target = import_reviewed_powermill(service)
    graph = imported["graph"]
    compatibility = service.check_compatibility(
        graph["graph_id"],
        target,
        revision_id=graph["revision_id"],
    )
    version = service.create_version(
        graph_id=graph["graph_id"],
        revision_id=graph["revision_id"],
        status="reviewed_for_fixture",
        author_ref="operator:t09",
        message="Reviewed for the offline T09 fixture.",
        round_trip_report_id="roundtrip:t09:fixture",
        compatibility_report_id=compatibility["report_id"],
    )
    request = {
        "graph_id": graph["graph_id"],
        "revision_id": graph["revision_id"],
        "flow_version_id": version["version_id"],
        "execution_mode": "fixture_dry_run",
        "transport": "none",
        "target": {
            key: target[key]
            for key in (
                "product",
                "target_version",
                "target_instance_id",
                "project_id",
                "project_snapshot_hash",
                "target_kind",
            )
        },
        "hashes": {
            "source_snapshot_hash": graph["source_snapshot_hash"],
            "semantic_hash": graph["semantic_hash"],
            "capability_lock_hash": compute_capability_lock_hash(graph),
            "reviewed_recipe_hash": None,
        },
    }
    return graph, target, request


def request(
    server: _WorkflowServer,
    method: str,
    path: str,
    body: Any = NO_BODY,
    *,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, Any, dict[str, str]]:
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_port,
        timeout=5,
    )
    headers = {"Accept": "application/json"}
    payload = None
    if body is not NO_BODY:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers.update(extra_headers or {})
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {
            name.casefold(): value for name, value in response.getheaders()
        }
        content_type = response_headers.get("content-type", "")
        value = (
            json.loads(raw.decode("utf-8"))
            if "application/json" in content_type and raw
            else raw.decode("utf-8")
        )
        return response.status, value, response_headers
    finally:
        connection.close()


@contextmanager
def running_server() -> Iterator[_WorkflowServer]:
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    service = build_fixture_flow_service(root / "flow")
    services = ApiServices(
        flow=service,
        flow_imports=OfflineFlowIntegration(service),
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
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        directory.cleanup()


class FlowServiceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.service = build_fixture_flow_service(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_fixture_runtime_exposes_exactly_two_targets_per_product(self) -> None:
        targets = self.service.list_fixture_targets()
        self.assertEqual(4, len(targets))
        self.assertEqual(
            {
                "nx:3101:A1",
                "nx:3102:B2",
                "powermill:4101:C1",
                "powermill:4102:process",
            },
            {item["target_instance_id"] for item in targets},
        )
        self.assertEqual(
            {"nx": 2, "powermill": 2},
            {
                product: sum(item["product"] == product for item in targets)
                for product in ("nx", "powermill")
            },
        )
        self.assertEqual(4, len({item["project_id"] for item in targets}))
        for target in targets:
            self.assertEqual("fixture", target["target_kind"])
            self.assertEqual(
                "none",
                target["extensions"]["cam.flow.fixture"]["transport"],
            )

    def test_adapter_to_service_to_version_to_preview_is_transport_free(self) -> None:
        graph, _target, preview_request = prepare_preview(self.service)
        self.assertEqual("cam.flowgraph.v1", graph["contract"])
        plan = self.service.create_preview_plan(preview_request)
        self.assertEqual("ready", plan["status"])
        result = self.service.execute_preview_plan(plan["plan_id"])
        self.assertEqual("succeeded", result["status"])
        assert_zero_execution(self, result)

    def test_cancel_and_stale_hash_are_deterministic(self) -> None:
        _graph, _target, preview_request = prepare_preview(self.service)
        stale = copy.deepcopy(preview_request)
        stale["hashes"]["semantic_hash"] = "sha256:" + ("0" * 64)
        with self.assertRaises(FlowConflictError) as mismatch:
            self.service.create_preview_plan(stale)
        self.assertEqual("PREVIEW_HASH_MISMATCH", mismatch.exception.code)

        plan = self.service.create_preview_plan(preview_request)
        cancelled = self.service.cancel_preview_plan(plan["plan_id"])
        self.assertEqual("cancelled", cancelled["status"])
        assert_zero_execution(self, cancelled)
        with self.assertRaises(FlowConflictError) as terminal:
            self.service.execute_preview_plan(plan["plan_id"])
        self.assertEqual("FLOW_STATE_CONFLICT", terminal.exception.code)

    def test_ambiguous_target_is_never_selected_by_order(self) -> None:
        imported, _target = import_reviewed_powermill(self.service)
        with self.assertRaises(FlowConflictError) as ambiguous:
            self.service.check_compatibility(
                imported["graph"]["graph_id"],
                {"product": "powermill", "target_kind": "fixture"},
            )
        self.assertEqual("PREVIEW_TARGET_AMBIGUOUS", ambiguous.exception.code)

    def test_opaque_asset_is_preserved_but_preview_ineligible(self) -> None:
        register_product_flow_capability(self.service, "powermill")
        imported = OfflineFlowIntegration(self.service).import_source(
            product="powermill",
            source='VENDOR.Gizmo APPLY "part"\n',
            source_name="opaque.mac",
            target_versions=["PowerMill 2025"],
            rights=RIGHTS,
        )
        graph = imported["graph"]
        opaque_nodes = [
            node
            for flow in graph["flows"]
            for node in flow["nodes"]
            if node["opaque"] is not None
        ]
        self.assertEqual(1, len(opaque_nodes))
        target = next(
            item
            for item in self.service.list_fixture_targets()
            if item["target_instance_id"] == "powermill:4102:process"
        )
        report = self.service.check_compatibility(graph["graph_id"], target)
        self.assertFalse(report["preview_eligible"])
        self.assertIn("CAPABILITY_MISSING", report["blocker_codes"])
        assert_zero_execution(self, imported)


class FlowHttpIntegrationTests(unittest.TestCase):
    def test_product_plugin_is_explicit_and_product_specific(self) -> None:
        with running_server() as server:
            status, missing, _headers = request(
                server,
                "POST",
                "/api/flow/import",
                {
                    "product": "powermill",
                    "source": "PRINT 1\n",
                    "source_name": "query.mac",
                    "target_versions": ["PowerMill 2025"],
                },
            )
            self.assertEqual(409, status)
            self.assertEqual("plugin_not_installed", missing["code"])
            self.assertEqual("powermill-cam-copilot", missing["plugin_id"])

            status, _installed, _headers = request(
                server,
                "POST",
                "/api/plugins/install",
                {"plugin_id": "powermill-cam-copilot"},
            )
            self.assertEqual(200, status)
            status, missing_nx, _headers = request(
                server,
                "POST",
                "/api/flow/import",
                {
                    "product": "nx",
                    "source": "import NXOpen\n",
                    "source_name": "journal.py",
                    "target_versions": ["NX 2406"],
                },
            )
            self.assertEqual(409, status)
            self.assertEqual("ug-cam-copilot", missing_nx["plugin_id"])

    def test_http_adapter_version_preview_and_cancel_chain(self) -> None:
        with running_server() as server:
            status, _installed, _headers = request(
                server,
                "POST",
                "/api/plugins/install",
                {"plugin_id": "powermill-cam-copilot"},
            )
            self.assertEqual(200, status)
            status, imported, _headers = request(
                server,
                "POST",
                "/api/flow/import",
                {
                    "product": "powermill",
                    "source": POWERMILL_SOURCE.read_text(encoding="utf-8"),
                    "source_name": POWERMILL_SOURCE.name,
                    "target_versions": ["PowerMill 2025"],
                    "rights": RIGHTS,
                },
            )
            self.assertEqual(201, status)
            assert_zero_execution(self, imported)
            graph = imported["graph"]
            status, fetched, graph_headers = request(
                server,
                "GET",
                f"/api/flow/graphs/{graph['graph_id']}",
            )
            self.assertEqual(200, status)
            self.assertEqual(graph["revision_id"], fetched["graph"]["revision_id"])
            status, empty, not_modified_headers = request(
                server,
                "GET",
                f"/api/flow/graphs/{graph['graph_id']}",
                extra_headers={"If-None-Match": graph_headers["etag"]},
            )
            self.assertEqual(304, status)
            self.assertEqual("", empty)
            self.assertEqual("0", not_modified_headers["content-length"])
            target = next(
                item
                for item in server.services.flow.list_fixture_targets()
                if item["target_instance_id"] == "powermill:4102:process"
            )
            status, compatible, headers = request(
                server,
                "POST",
                "/api/flow/compatibility",
                {
                    "graph_id": graph["graph_id"],
                    "revision_id": graph["revision_id"],
                    "target": target,
                },
            )
            self.assertEqual(200, status)
            self.assertEqual("compatible", compatible["compatibility"]["status"])
            self.assertIn("x-correlation-id", headers)

            status, version_payload, _headers = request(
                server,
                "POST",
                "/api/flow/versions",
                {
                    "graph_id": graph["graph_id"],
                    "revision_id": graph["revision_id"],
                    "status": "reviewed_for_fixture",
                    "author_ref": "operator:t09:http",
                    "message": "Reviewed fixture over HTTP.",
                    "round_trip_report_id": "roundtrip:t09:http",
                    "compatibility_report_id": compatible["compatibility"]["report_id"],
                },
            )
            self.assertEqual(201, status)
            version = version_payload["version"]
            preview_body = {
                "graph_id": graph["graph_id"],
                "revision_id": graph["revision_id"],
                "flow_version_id": version["version_id"],
                "execution_mode": "fixture_dry_run",
                "transport": "none",
                "target": {
                    key: target[key]
                    for key in (
                        "product",
                        "target_version",
                        "target_instance_id",
                        "project_id",
                        "project_snapshot_hash",
                        "target_kind",
                    )
                },
                "hashes": {
                    "source_snapshot_hash": graph["source_snapshot_hash"],
                    "semantic_hash": graph["semantic_hash"],
                    "capability_lock_hash": compute_capability_lock_hash(graph),
                    "reviewed_recipe_hash": None,
                },
            }
            status, created, _headers = request(
                server,
                "POST",
                "/api/flow/preview-plans",
                preview_body,
            )
            self.assertEqual(201, status)
            plan = created["preview_plan"]
            status, completed, _headers = request(
                server,
                "POST",
                f"/api/flow/preview-plans/{plan['plan_id']}/run",
            )
            self.assertEqual(200, status)
            self.assertEqual("succeeded", completed["preview_plan"]["status"])
            assert_zero_execution(self, completed["preview_plan"])

            preview_body["plan_id"] = "preview:t09:http:cancel"
            status, created, _headers = request(
                server,
                "POST",
                "/api/flow/preview-plans",
                preview_body,
            )
            self.assertEqual(201, status)
            status, cancelled, _headers = request(
                server,
                "POST",
                f"/api/flow/preview-plans/{created['preview_plan']['plan_id']}/cancel",
            )
            self.assertEqual(200, status)
            self.assertEqual("cancelled", cancelled["preview_plan"]["status"])
            assert_zero_execution(self, cancelled["preview_plan"])

    def test_legacy_routes_and_static_flow_workspace_remain_available(self) -> None:
        with running_server() as server:
            status, index, _headers = request(server, "GET", "/")
            self.assertEqual(200, status)
            self.assertIn("/flow/flow-studio.js", index)
            self.assertIn('id="flowStudio"', index)
            status, app, _headers = request(server, "GET", "/app.js")
            self.assertEqual(200, status)
            self.assertIn("CAM_FLOW_STUDIO", app)
            self.assertIn('state.installed.has("powermill-cam-copilot")', app)

            status, _installed, _headers = request(
                server,
                "POST",
                "/api/plugins/install",
                {"plugin_id": "powermill-cam-copilot"},
            )
            self.assertEqual(200, status)
            status, learned, _headers = request(
                server,
                "POST",
                "/api/learn",
                {
                    "product": "powermill",
                    "source": "PRINT 1\n",
                    "name": "legacy-t09",
                },
            )
            self.assertEqual(200, status)
            self.assertEqual("powermill", learned["product"])
            status, health, _headers = request(server, "GET", "/api/health")
            self.assertEqual(200, status)
            self.assertEqual("ok", health["status"])

    def test_server_close_cancels_inflight_flow_request(self) -> None:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        service = build_fixture_flow_service(root / "flow")
        services = ApiServices(
            flow=service,
            flow_imports=OfflineFlowIntegration(service),
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
        server.install_plugin("powermill-cam-copilot")
        imported, _target = import_reviewed_powermill(service)
        started = threading.Event()

        def slow_validate(
            graph: Any = None,
            *,
            graph_id: str | None = None,
            revision_id: str | None = None,
            require_known_semantics: bool = False,
            context: Any = None,
        ) -> dict[str, Any]:
            del graph, graph_id, revision_id, require_known_semantics
            started.set()
            while not context.cancellation.cancelled:
                threading.Event().wait(0.01)
            context.cancellation.raise_if_cancelled()
            raise AssertionError("cancelled request resumed")

        service.validate = slow_validate
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        response: dict[str, Any] = {}

        def call_validate() -> None:
            response["value"] = request(
                server,
                "POST",
                "/api/flow/validate",
                {"graph_id": imported["graph"]["graph_id"]},
            )

        request_thread = threading.Thread(target=call_validate)
        request_thread.start()
        self.assertTrue(started.wait(2))
        server.shutdown()
        server_thread.join(timeout=5)
        server.server_close()
        request_thread.join(timeout=5)
        try:
            self.assertFalse(request_thread.is_alive())
            status, payload, _headers = response["value"]
            self.assertEqual(409, status)
            self.assertEqual("REQUEST_CANCELLED", payload["error"]["code"])
            self.assertEqual(0, len(server._flow_requests))
        finally:
            directory.cleanup()


class FlowCliIntegrationTests(unittest.TestCase):
    def test_cli_exposes_only_offline_flow_commands(self) -> None:
        result = subprocess.run(
            [os.sys.executable, "-m", "cam_automation.cli", "flow", "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        help_text = result.stdout.casefold()
        for command in ("import", "validate", "test", "preview"):
            self.assertIn(command, help_text)
        for forbidden in ("--live", "--attach", "--execute", "--postprocess"):
            self.assertNotIn(forbidden, help_text)

    def test_cli_preview_preserves_zero_execution_contract(self) -> None:
        result = subprocess.run(
            [
                os.sys.executable,
                "-m",
                "cam_automation.cli",
                "flow",
                "preview",
                str(CLI_PREVIEW_FIXTURE),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        plan = json.loads(result.stdout)
        self.assertEqual("succeeded", plan["status"])
        assert_zero_execution(self, plan)


if __name__ == "__main__":
    unittest.main()
