from __future__ import annotations

import json
import mimetypes
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .plugin_manager import PRODUCT_PLUGINS, PluginManager, PluginNotInstalled


_WEB_ROOT = Path(__file__).with_name("web")
_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins"
_MAX_BODY = 2 * 1024 * 1024


class _WorkflowHandler(BaseHTTPRequestHandler):
    server_version = "CamAutomationStudio/0.4"

    def _json(self, status: HTTPStatus, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        *,
        content_type: str,
        filename: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(payload)

    def _local_api_allowed(self, path: str) -> bool:
        if not path.startswith(("/api/plugins", "/api/recorder", "/api/execution")):
            return True
        if self.client_address[0] in {"127.0.0.1", "::1"}:
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "Plugin APIs are local-only."})
        return False

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > _MAX_BODY:
            raise ValueError("Request body must be between 1 byte and 2 MiB.")
        decoded = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Request body must be a JSON object.")
        return decoded

    def _require_plugin(self, plugin_id: str) -> bool:
        try:
            self.server.plugins.require(plugin_id)
            return True
        except PluginNotInstalled as error:
            self._json(
                HTTPStatus.CONFLICT,
                {
                    "error": str(error),
                    "code": "plugin_not_installed",
                    "plugin_id": error.plugin_id,
                },
            )
            return False

    def _require_product(self, product: str) -> bool:
        plugin_id = PRODUCT_PLUGINS.get(product)
        if not plugin_id:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Unsupported product: {product}"},
            )
            return False
        return self._require_plugin(plugin_id)

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if not self._local_api_allowed(path):
            return
        if path == "/api/health":
            plugins = self.server.plugins.status()
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "version": "0.4.0",
                    "module": "CAM Automation Studio Core",
                    "installed_plugins": plugins["installed_count"],
                },
            )
            return
        if path == "/api/plugins":
            self._json(HTTPStatus.OK, self.server.plugins.status())
            return
        if path == "/api/sample":
            query = parse_qs(urlparse(self.path).query)
            product = query.get("product", ["powermill"])[0]
            if not self._require_product(product):
                return
            from .integrations import sample_for

            source = sample_for(product)
            self._json(HTTPStatus.OK, {"product": product, "source": source, "log": source})
            return
        if path == "/api/capabilities":
            from .integrations import capability_manifest

            self._json(
                HTTPStatus.OK,
                capability_manifest(self.server.plugins.installed_ids()),
            )
            return
        if path == "/api/connections":
            from .integrations import connection_statuses

            self._json(
                HTTPStatus.OK,
                {
                    "connections": connection_statuses(
                        self.server.plugins.installed_ids()
                    )
                },
            )
            return
        if path == "/api/recorder":
            if not self._require_plugin("cam-local-capture"):
                return
            self._json(HTTPStatus.OK, self.server.recorder.status())
            return
        if path == "/api/recorder/events":
            if not self._require_plugin("cam-local-capture"):
                return
            query = parse_qs(urlparse(self.path).query)
            try:
                limit = int(query.get("limit", ["500"])[0])
            except (TypeError, ValueError):
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "limit must be an integer between 1 and 10000."},
                )
                return
            product = query.get("product", [None])[0]
            events = self.server.recorder.recent_events(limit=limit, product=product)
            self._json(
                HTTPStatus.OK,
                {
                    "events": events,
                    "returned": len(events),
                    "counts": self.server.recorder.status()["counts"],
                },
            )
            return
        if path == "/api/recorder/export":
            if not self._require_plugin("cam-local-capture"):
                return
            self._bytes(
                HTTPStatus.OK,
                self.server.recorder.export_jsonl(),
                content_type="application/x-ndjson; charset=utf-8",
                filename="cam-captured-events.jsonl",
            )
            return
        if path == "/api/state":
            self._json(HTTPStatus.OK, self.server.state)
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._local_api_allowed(path):
            return
        if path not in {
            "/api/learn",
            "/api/analyze",
            "/api/codex/context",
            "/api/codex/review",
            "/api/plugins/install",
            "/api/plugins/uninstall",
            "/api/recorder/consent",
            "/api/recorder/control",
            "/api/recorder/label",
            "/api/recorder/scan",
            "/api/recorder/clear",
            "/api/execution/run",
        }:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        try:
            body = self._read_json()
            if path == "/api/plugins/install":
                plugin_id = str(body.get("plugin_id", "")).strip()
                if not plugin_id:
                    raise ValueError("plugin_id is required.")
                self._json(
                    HTTPStatus.OK,
                    self.server.install_plugin(plugin_id),
                )
                return
            if path == "/api/plugins/uninstall":
                plugin_id = str(body.get("plugin_id", "")).strip()
                if not plugin_id:
                    raise ValueError("plugin_id is required.")
                self._json(
                    HTTPStatus.OK,
                    self.server.uninstall_plugin(plugin_id),
                )
                return
            if path == "/api/recorder/consent":
                self.server.plugins.require("cam-local-capture")
                if not isinstance(body.get("accepted"), bool):
                    raise ValueError("accepted must be a boolean.")
                self._json(
                    HTTPStatus.OK,
                    self.server.recorder.configure(consent=body["accepted"]),
                )
                return
            if path == "/api/recorder/control":
                self.server.plugins.require("cam-local-capture")
                if not isinstance(body.get("enabled"), bool):
                    raise ValueError("enabled must be a boolean.")
                self._json(
                    HTTPStatus.OK,
                    self.server.recorder.configure(enabled=body["enabled"]),
                )
                return
            if path == "/api/recorder/label":
                self.server.plugins.require("cam-local-capture")
                self._json(
                    HTTPStatus.OK,
                    self.server.recorder.configure(
                        operator_label=str(body.get("operator_label", ""))
                    ),
                )
                return
            if path == "/api/recorder/scan":
                self.server.plugins.require("cam-local-capture")
                inserted = self.server.recorder.scan_once()
                self._json(
                    HTTPStatus.OK,
                    {
                        "inserted": inserted,
                        "recorder": self.server.recorder.status(),
                    },
                )
                return
            if path == "/api/recorder/clear":
                self.server.plugins.require("cam-local-capture")
                if body.get("confirm") != "CLEAR_CAPTURED_EVENTS":
                    raise ValueError("Captured event deletion requires the confirmation token.")
                removed = self.server.recorder.clear()
                self._json(
                    HTTPStatus.OK,
                    {"removed": removed, "recorder": self.server.recorder.status()},
                )
                return
            if path == "/api/execution/run":
                self.server.plugins.require("cam-execution-gateway")
                from .execution import ExecutionRequest

                request = ExecutionRequest.from_dict(body)
                result = self.server.execution.execute(request)
                self.server.recorder.record_execution(request=body, result=result)
                status = (
                    HTTPStatus.OK
                    if result["status"] == "dry_run"
                    else HTTPStatus.UNPROCESSABLE_ENTITY
                )
                self._json(status, result)
                return
            if path == "/api/codex/review":
                self.server.plugins.require("cam-codex-review")
                self._handle_codex_review(body)
                return
            if path == "/api/codex/context":
                self.server.plugins.require("cam-codex-review")
                from .codex_bridge import review_request, write_exchange

                context = body.get("context") or self.server.state.get("analysis", {}).get(
                    "codex_context"
                )
                if not isinstance(context, dict):
                    raise ValueError("No analysis context is available for Codex review.")
                request = review_request(context)
                self.server.state["codex_request"] = request
                if body.get("persist", False):
                    paths = write_exchange(
                        Path("build") / "codex-exchange",
                        context=context,
                    )
                    request["files"] = {key: str(value.resolve()) for key, value in paths.items()}
                self._json(HTTPStatus.OK, request)
                return

            product = body.get("product", "powermill" if path == "/api/learn" else "powermill")
            plugin_id = PRODUCT_PLUGINS.get(str(product))
            if not plugin_id:
                raise ValueError(f"Unsupported product: {product}")
            self.server.plugins.require(plugin_id)
            source = body.get("source", body.get("log"))
            if not isinstance(source, str) or not source.strip():
                raise ValueError("A non-empty source or log string is required.")
            name = body.get("name", f"{product}-workflow")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Recipe name must be a non-empty string.")
            parameters = body.get("parameters", {})
            if not isinstance(parameters, dict):
                raise ValueError("Parameters must be a JSON object.")
            from .integrations import analyze

            result = analyze(
                product=product,
                source=source,
                source_format=body.get("source_format"),
                name=name.strip(),
                parameters=parameters,
                allow_review_steps=bool(body.get("allow_review_steps", False)),
                source_name=str(body.get("source_name", "pasted-input")),
            )
            self.server.state["analysis"] = result
            self._json(HTTPStatus.OK, result)
        except PluginNotInstalled as error:
            self._json(
                HTTPStatus.CONFLICT,
                {
                    "error": str(error),
                    "code": "plugin_not_installed",
                    "plugin_id": error.plugin_id,
                },
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def _handle_codex_review(self, body: dict[str, Any]) -> None:
        from .codex_bridge import validate_review

        value = body.get("review", body)
        if not isinstance(value, dict):
            raise ValueError("Codex review must be a JSON object.")
        review = validate_review(value)
        self.server.state["codex_review"] = review
        self._json(HTTPStatus.OK, review)

    def _serve_static(self, request_path: str) -> None:
        relative = request_path.lstrip("/") or "index.html"
        candidate = (_WEB_ROOT / relative).resolve()
        if _WEB_ROOT.resolve() not in candidate.parents and candidate != _WEB_ROOT.resolve():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        if not candidate.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        payload = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


class _WorkflowServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(address, handler)
        configured_app_data_dir = os.environ.get("CAM_APP_DATA_DIR")
        self.app_data_dir = (
            Path(configured_app_data_dir)
            if configured_app_data_dir
            else Path.cwd() / "build" / "app-data"
        )
        self.plugins = PluginManager(self.app_data_dir, _PLUGIN_ROOT)
        configured_capture_dir = os.environ.get("CAM_CAPTURE_DIR")
        self.capture_dir = (
            Path(configured_capture_dir)
            if configured_capture_dir
            else self.app_data_dir / "capture"
        )
        self.recorder: Any | None = None
        self.execution: Any | None = None
        self._module_lock = threading.RLock()
        self.state: dict[str, Any] = {
            "analysis": None,
            "codex_request": None,
            "codex_review": None,
        }
        self._sync_modules()

    def install_plugin(self, plugin_id: str) -> dict[str, Any]:
        with self._module_lock:
            result = self.plugins.install(plugin_id)
            self._sync_modules()
            return result

    def uninstall_plugin(self, plugin_id: str) -> dict[str, Any]:
        with self._module_lock:
            result = self.plugins.uninstall(plugin_id)
            self._sync_modules()
            return result

    def _sync_modules(self) -> None:
        with self._module_lock:
            capture_installed = self.plugins.is_installed("cam-local-capture")
            if capture_installed and self.recorder is None:
                from .recorder import CaptureService

                self.recorder = CaptureService(self.capture_dir)
            elif not capture_installed and self.recorder is not None:
                self.recorder.close()
                self.recorder = None

            execution_installed = self.plugins.is_installed(
                "cam-execution-gateway"
            )
            if execution_installed and self.execution is None:
                from .execution import ExecutionGateway

                self.execution = ExecutionGateway()
            elif not execution_installed:
                self.execution = None

    def server_close(self) -> None:
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None
        super().server_close()


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = _WorkflowServer((host, port), _WorkflowHandler)
    print(f"CAM Automation Studio: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
