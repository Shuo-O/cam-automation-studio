from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
from concurrent.futures import CancelledError
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from .integrations import ApiServices, plugin_api_status, service_payload
from .models import EventQuery
from .plugin_manager import PRODUCT_PLUGINS, PluginManager, PluginNotInstalled


_WEB_ROOT = Path(__file__).with_name("web")
_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins"
_MAX_BODY = 2 * 1024 * 1024
_MAX_REQUEST_TARGET = 8 * 1024
_MAX_QUERY_FIELDS = 64
_VALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_RECIPE_ROUTE = re.compile(r"^/api/recipes/([^/]+)$")
_TASK_ROUTE = re.compile(r"^/api/execution/tasks/([^/]+)$")
_TASK_CANCEL_ROUTE = re.compile(r"^/api/execution/tasks/([^/]+)/cancel$")
_EVENT_QUERY_KEYS = frozenset(
    {
        "schema_version",
        "source_mode",
        "source_modes",
        "view_level",
        "view_levels",
        "product",
        "products",
        "instance_id",
        "instance_ids",
        "project_id",
        "project_ids",
        "session_id",
        "session_ids",
        "action",
        "actions",
        "category",
        "categories",
        "from_time",
        "to_time",
        "text",
        "cursor",
        "limit",
        "sort",
    }
)
_ARRAY_QUERY_FIELDS = {
    "source_modes": ("source_modes", "source_mode"),
    "view_levels": ("view_levels", "view_level"),
    "products": ("products", "product"),
    "instance_ids": ("instance_ids", "instance_id"),
    "project_ids": ("project_ids", "project_id"),
    "session_ids": ("session_ids", "session_id"),
    "actions": ("actions", "action"),
    "categories": ("categories", "category"),
}


class _ApiError(Exception):
    def __init__(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _runtime_connection_label(connection: dict[str, Any]) -> str:
    count = int(connection.get("instance_count", 0) or 0)
    if count:
        prefix = "监测中" if connection.get("monitoring") else "已检测"
        return f"{prefix} · {count} 个实例"
    return {
        "ready": "已就绪",
        "available": "可连接",
        "awaiting_consent": "待授权",
        "disconnected": "未连接",
        "unconfigured": "未配置",
    }.get(str(connection.get("status", "")), str(connection.get("status", "未知")))


class _WorkflowHandler(BaseHTTPRequestHandler):
    server_version = "CamAutomationStudio/0.6"

    def _json(self, status: HTTPStatus, value: Any) -> None:
        payload = json.dumps(
            service_payload(value),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _problem(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
        **details: Any,
    ) -> None:
        value: dict[str, Any] = {
            "schema_version": 1,
            "error": message,
            "code": code,
        }
        if details:
            value["details"] = details
            for key, item in details.items():
                if key not in value:
                    value[key] = item
        self._json(status, value)

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
        if not path.startswith(
            (
                "/api/plugins",
                "/api/connections",
                "/api/recorder",
                "/api/sessions",
                "/api/workflows",
                "/api/recipes",
                "/api/execution",
                "/api/diagnostics",
            )
        ):
            return True
        if self.client_address[0] in {"127.0.0.1", "::1"}:
            return True
        self._problem(
            HTTPStatus.FORBIDDEN,
            "conflict",
            "This API is available only to local clients.",
        )
        return False

    def _parsed_request(self) -> Any:
        if len(self.path) > _MAX_REQUEST_TARGET:
            raise _ApiError(
                HTTPStatus.REQUEST_URI_TOO_LONG,
                "invalid_parameters",
                "Request target is too long.",
            )
        if _VALID_PERCENT_ESCAPE.search(self.path):
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Request target contains an invalid percent escape.",
            )
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if "\x00" in path or "\\" in path:
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Request path is invalid.",
            )
        return parsed._replace(path=path)

    def _query(
        self,
        parsed: Any,
        *,
        allowed: set[str] | frozenset[str],
    ) -> dict[str, list[str]]:
        try:
            query = parse_qs(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=False,
                max_num_fields=_MAX_QUERY_FIELDS,
            )
        except ValueError as error:
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Query string is invalid or contains too many fields.",
            ) from error
        unknown = sorted(set(query) - set(allowed))
        if unknown:
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Unknown query parameters: " + ", ".join(unknown) + ".",
                details={"parameters": unknown},
            )
        return query

    @staticmethod
    def _scalar_query(
        query: dict[str, list[str]],
        name: str,
        default: str | None = None,
    ) -> str | None:
        values = query.get(name)
        if values is None:
            return default
        if len(values) != 1:
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                f"{name} must be provided once.",
            )
        return values[0]

    def _event_query(self, parsed: Any) -> EventQuery:
        query = self._query(parsed, allowed=_EVENT_QUERY_KEYS)
        value: dict[str, Any] = {}
        for target, aliases in _ARRAY_QUERY_FIELDS.items():
            values: list[str] = []
            for alias in aliases:
                for raw in query.get(alias, []):
                    values.extend(item.strip() for item in raw.split(",") if item.strip())
            if values:
                value[target] = values
        for name in (
            "schema_version",
            "from_time",
            "to_time",
            "text",
            "cursor",
            "limit",
            "sort",
        ):
            candidate = self._scalar_query(query, name)
            if candidate not in (None, ""):
                value[name] = candidate
        for name in ("schema_version", "limit"):
            if name in value:
                try:
                    value[name] = int(value[name])
                except ValueError as error:
                    raise _ApiError(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_parameters",
                        f"{name} must be an integer.",
                    ) from error
        return EventQuery.from_dict(value)

    def _read_json(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Transfer-Encoding is not supported.",
            )
        content_type = self.headers.get("Content-Type", "application/json")
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            raise _ApiError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "invalid_parameters",
                "Content-Type must be application/json.",
            )
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Content-Length must be an integer.",
            ) from error
        if length <= 0:
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Request body must be a non-empty JSON object.",
            )
        if length > _MAX_BODY:
            raise _ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "invalid_parameters",
                "Request body exceeds the 2 MiB limit.",
                details={"max_bytes": _MAX_BODY},
            )
        try:
            decoded = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Request body is not valid UTF-8 JSON.",
            ) from error
        if not isinstance(decoded, dict):
            raise _ApiError(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                "Request body must be a JSON object.",
            )
        return decoded

    def _require_plugin(self, plugin_id: str) -> bool:
        try:
            self.server.plugins.require(plugin_id)
            return True
        except PluginNotInstalled as error:
            self._problem(
                HTTPStatus.CONFLICT,
                "plugin_not_installed",
                str(error),
                plugin_id=error.plugin_id,
            )
            return False

    def _require_product(self, product: str) -> bool:
        plugin_id = PRODUCT_PLUGINS.get(product)
        if not plugin_id:
            self._problem(
                HTTPStatus.BAD_REQUEST,
                "invalid_parameters",
                f"Unsupported product: {product}",
            )
            return False
        return self._require_plugin(plugin_id)

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._dispatch_get()
        except Exception as error:  # noqa: BLE001
            self._handle_exception(error)

    def _dispatch_get(self) -> None:
        parsed = self._parsed_request()
        path = parsed.path
        if not self._local_api_allowed(path):
            return
        if path == "/api/health":
            plugins = self.server.plugin_status()
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "version": "0.6.0",
                    "module": "CAM Automation Studio Core",
                    "installed_plugins": plugins["installed_count"],
                },
            )
            return
        if path == "/api/plugins":
            self._query(parsed, allowed=frozenset())
            self._json(HTTPStatus.OK, self.server.plugin_status())
            return
        if path == "/api/sample":
            query = self._query(parsed, allowed={"product"})
            product = self._scalar_query(query, "product", "powermill") or "powermill"
            if not self._require_product(product):
                return
            from .integrations import sample_for

            source = sample_for(product)
            self._json(HTTPStatus.OK, {"product": product, "source": source, "log": source})
            return
        if path == "/api/capabilities":
            self._query(parsed, allowed=frozenset())
            from .integrations import capability_manifest

            self._json(
                HTTPStatus.OK,
                capability_manifest(self.server.plugins.installed_ids()),
            )
            return
        if path == "/api/connections":
            query = self._query(
                parsed,
                allowed={"refresh", "include_uninstalled"},
            )
            refresh = self._scalar_query(query, "refresh", "0")
            include_uninstalled = self._scalar_query(
                query,
                "include_uninstalled",
                "0",
            )
            if refresh not in {"0", "1"} or include_uninstalled not in {"0", "1"}:
                raise _ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_parameters",
                    "refresh and include_uninstalled must be 0 or 1.",
                )
            self._json(
                HTTPStatus.OK,
                self.server.connections_payload(
                    force=refresh == "1",
                    include_uninstalled=include_uninstalled == "1",
                ),
            )
            return
        if path == "/api/recorder":
            self._query(parsed, allowed=frozenset())
            if not self._require_plugin("cam-local-capture"):
                return
            self._json(HTTPStatus.OK, self.server.recorder.status())
            return
        if path == "/api/recorder/events":
            if not self._require_plugin("cam-local-capture"):
                return
            event_query = self._event_query(parsed)
            self._json(HTTPStatus.OK, self.server.event_page(event_query))
            return
        if path == "/api/recorder/export":
            self._query(parsed, allowed=frozenset())
            if not self._require_plugin("cam-local-capture"):
                return
            self._bytes(
                HTTPStatus.OK,
                self.server.export_events(),
                content_type="application/x-ndjson; charset=utf-8",
                filename="cam-captured-events.jsonl",
            )
            return
        if path == "/api/sessions":
            event_query = self._event_query(parsed)
            self._json(
                HTTPStatus.OK,
                self.server.sessions_payload(event_query),
            )
            return
        recipe_match = _RECIPE_ROUTE.fullmatch(path)
        if recipe_match:
            self._query(parsed, allowed=frozenset())
            self._json(
                HTTPStatus.OK,
                self.server.recipe_payload(recipe_match.group(1)),
            )
            return
        task_match = _TASK_ROUTE.fullmatch(path)
        if task_match:
            self._query(parsed, allowed=frozenset())
            if not self._require_plugin("cam-execution-gateway"):
                return
            self._json(
                HTTPStatus.OK,
                self.server.command_task(task_match.group(1)),
            )
            return
        if path == "/api/diagnostics":
            self._query(parsed, allowed=frozenset())
            self._json(HTTPStatus.OK, self.server.diagnostics_payload())
            return
        if path == "/api/state":
            self._query(parsed, allowed=frozenset())
            self._json(HTTPStatus.OK, self.server.state_snapshot())
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._dispatch_post()
        except Exception as error:  # noqa: BLE001
            self._handle_exception(error)

    def _dispatch_post(self) -> None:
        parsed = self._parsed_request()
        path = parsed.path
        if not self._local_api_allowed(path):
            return
        exact_routes = {
            "/api/learn",
            "/api/analyze",
            "/api/codex/context",
            "/api/codex/review",
            "/api/plugins/install",
            "/api/plugins/uninstall",
            "/api/plugins/update",
            "/api/recorder/consent",
            "/api/recorder/control",
            "/api/recorder/settings",
            "/api/recorder/label",
            "/api/recorder/scan",
            "/api/recorder/clear",
            "/api/sessions/compare",
            "/api/workflows/mine",
            "/api/recipes",
            "/api/execution/run",
        }
        cancel_match = _TASK_CANCEL_ROUTE.fullmatch(path)
        if path not in exact_routes and cancel_match is None:
            self._problem(HTTPStatus.NOT_FOUND, "not_found", "Not found.")
            return
        self._query(parsed, allowed=frozenset())
        content_length = self.headers.get("Content-Length", "0")
        body = (
            {}
            if cancel_match is not None and content_length in {"", "0"}
            else self._read_json()
        )
        if path == "/api/plugins/install":
            plugin_id = str(body.get("plugin_id", "")).strip()
            if not plugin_id:
                raise ValueError("plugin_id is required.")
            self._json(HTTPStatus.OK, self.server.install_plugin(plugin_id))
            return
        if path == "/api/plugins/uninstall":
            plugin_id = str(body.get("plugin_id", "")).strip()
            if not plugin_id:
                raise ValueError("plugin_id is required.")
            self._json(HTTPStatus.OK, self.server.uninstall_plugin(plugin_id))
            return
        if path == "/api/plugins/update":
            plugin_id = str(body.get("plugin_id", "")).strip()
            if not plugin_id:
                raise ValueError("plugin_id is required.")
            self._json(HTTPStatus.OK, self.server.update_plugin(plugin_id))
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
            if isinstance(body.get("enabled"), bool):
                result = self.server.recorder.configure(enabled=body["enabled"])
            else:
                action = str(body.get("action", "")).casefold()
                category = body.get("category")
                if category is not None and not isinstance(category, str):
                    raise ValueError("category must be a string.")
                if action == "pause":
                    result = self.server.recorder.pause(category or None)
                elif action == "resume":
                    result = self.server.recorder.resume(category or None)
                else:
                    raise ValueError("enabled or action=pause|resume is required.")
            self._json(HTTPStatus.OK, result)
            return
        if path == "/api/recorder/settings":
            self.server.plugins.require("cam-local-capture")
            settings = {
                key: body[key]
                for key in ("capture_logs", "detect_instances", "audit_execution")
                if key in body
            }
            if not settings or not all(
                isinstance(value, bool) for value in settings.values()
            ):
                raise ValueError(
                    "settings must include boolean capture_logs, detect_instances, or audit_execution."
                )
            self._json(
                HTTPStatus.OK,
                self.server.recorder.configure(**settings),
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
            scan = getattr(self.server.recorder, "scan_now", None)
            inserted = scan() if callable(scan) else self.server.recorder.scan_once()
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
        if path == "/api/sessions/compare":
            self._json(HTTPStatus.OK, self.server.compare_sessions(body))
            return
        if path == "/api/workflows/mine":
            self._json(HTTPStatus.OK, self.server.mine_workflows(body))
            return
        if path == "/api/recipes":
            recipe = body.get("recipe", body)
            if not isinstance(recipe, dict):
                raise ValueError("recipe must be a JSON object.")
            product = str(recipe.get("product", "")).casefold()
            if not self._require_product(product):
                return
            self._json(HTTPStatus.CREATED, self.server.save_recipe(recipe))
            return
        if path == "/api/execution/run":
            self.server.plugins.require("cam-execution-gateway")
            if (
                self.server.services.commands is not None
                and self.server.is_command_task_request(body)
            ):
                self._json(
                    HTTPStatus.ACCEPTED,
                    self.server.submit_command(body),
                )
                return
            if self.server.is_command_task_request(body):
                raise _ApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "unavailable_capability",
                    "Command task service is not available.",
                    details={"capability": "command_tasks"},
                )
            from .execution import ExecutionRequest

            request = ExecutionRequest.from_dict(body)
            result = self.server.execution.execute(request)
            if self.server.recorder is not None:
                self.server.recorder.record_execution(request=body, result=result)
            status = (
                HTTPStatus.OK
                if result["status"] == "dry_run"
                else HTTPStatus.UNPROCESSABLE_ENTITY
            )
            self._json(status, result)
            return
        if cancel_match is not None:
            self.server.plugins.require("cam-execution-gateway")
            self._json(
                HTTPStatus.OK,
                self.server.cancel_command(cancel_match.group(1)),
            )
            return
        if path == "/api/codex/review":
            self.server.plugins.require("cam-codex-review")
            self._handle_codex_review(body)
            return
        if path == "/api/codex/context":
            self.server.plugins.require("cam-codex-review")
            from .codex_bridge import review_request, write_exchange

            current_analysis = self.server.get_state("analysis") or {}
            context = body.get("context") or current_analysis.get("codex_context")
            if not isinstance(context, dict):
                raise ValueError("No analysis context is available for Codex review.")
            request = review_request(context)
            self.server.set_state("codex_request", request)
            if body.get("persist", False):
                paths = write_exchange(
                    Path("build") / "codex-exchange",
                    context=context,
                )
                request["files"] = {
                    key: str(value.resolve()) for key, value in paths.items()
                }
            self._json(HTTPStatus.OK, request)
            return

        product = body.get("product", "powermill")
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
            product=str(product),
            source=source,
            source_format=body.get("source_format"),
            name=name.strip(),
            parameters=parameters,
            allow_review_steps=bool(body.get("allow_review_steps", False)),
            source_name=str(body.get("source_name", "pasted-input")),
        )
        self.server.set_state("analysis", result)
        self._json(HTTPStatus.OK, result)

    def _handle_exception(self, error: Exception) -> None:
        if isinstance(error, _ApiError):
            self._problem(
                error.status,
                error.code,
                error.message,
                **error.details,
            )
            return
        service_code = str(getattr(error, "code", ""))
        if service_code in {
            "plugin_not_installed",
            "instance_not_connected",
            "invalid_parameters",
            "timeout",
            "task_cancelled",
            "unavailable_capability",
            "conflict",
        }:
            status = {
                "plugin_not_installed": HTTPStatus.CONFLICT,
                "instance_not_connected": HTTPStatus.CONFLICT,
                "invalid_parameters": HTTPStatus.BAD_REQUEST,
                "timeout": HTTPStatus.GATEWAY_TIMEOUT,
                "task_cancelled": HTTPStatus.CONFLICT,
                "unavailable_capability": HTTPStatus.SERVICE_UNAVAILABLE,
                "conflict": HTTPStatus.CONFLICT,
            }[service_code]
            self._problem(status, service_code, str(error))
            return
        if isinstance(error, PluginNotInstalled):
            self._problem(
                HTTPStatus.CONFLICT,
                "plugin_not_installed",
                str(error),
                plugin_id=error.plugin_id,
            )
            return
        if isinstance(error, CancelledError):
            self._problem(
                HTTPStatus.CONFLICT,
                "task_cancelled",
                "The command task was cancelled.",
            )
            return
        if isinstance(error, TimeoutError):
            self._problem(
                HTTPStatus.GATEWAY_TIMEOUT,
                "timeout",
                "The service operation timed out.",
            )
            return
        if isinstance(error, ConnectionError):
            self._problem(
                HTTPStatus.CONFLICT,
                "instance_not_connected",
                "The target CAM instance is not connected.",
            )
            return
        if isinstance(error, KeyError):
            self._problem(HTTPStatus.NOT_FOUND, "not_found", str(error).strip("'"))
            return
        if isinstance(error, PermissionError):
            self._problem(HTTPStatus.CONFLICT, "conflict", str(error))
            return
        if isinstance(error, (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError)):
            self._problem(HTTPStatus.BAD_REQUEST, "invalid_parameters", str(error))
            return
        self._problem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "unavailable_capability",
            "The requested service capability is unavailable.",
        )

    def _handle_codex_review(self, body: dict[str, Any]) -> None:
        from .codex_bridge import validate_review

        value = body.get("review", body)
        if not isinstance(value, dict):
            raise ValueError("Codex review must be a JSON object.")
        request = self.server.get_state("codex_request")
        request_id = (
            str(request.get("request_id"))
            if isinstance(request, Mapping) and request.get("request_id")
            else None
        )
        review = validate_review(value, request_id=request_id)
        self.server.set_state("codex_review", review)
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

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        services: ApiServices | None = None,
    ) -> None:
        super().__init__(address, handler)
        configured_app_data_dir = os.environ.get("CAM_APP_DATA_DIR")
        self.app_data_dir = (
            Path(configured_app_data_dir)
            if configured_app_data_dir
            else Path.cwd() / "build" / "app-data"
        )
        configured_capture_dir = os.environ.get("CAM_CAPTURE_DIR")
        self.capture_dir = (
            Path(configured_capture_dir)
            if configured_capture_dir
            else self.app_data_dir / "capture"
        )
        self._owns_services = services is None
        if services is None:
            from .fixture_runtime import build_fixture_api_services

            services = build_fixture_api_services(self.capture_dir)
        self.services = services
        if self.services.sessions is None:
            from .sessions import SessionService

            self.services.sessions = SessionService()
        if self.services.recipes is None:
            from .recipes import RecipeService

            self.services.recipes = RecipeService()
        self.plugins = PluginManager(self.app_data_dir, _PLUGIN_ROOT)
        self.recorder: Any | None = None
        self.execution: Any | None = None
        self._injected_recorder = self.services.recorder
        self._owns_recorder = False
        self._connection_authorized: bool | None = None
        self._module_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self.state: dict[str, Any] = {
            "analysis": None,
            "codex_request": None,
            "codex_review": None,
        }
        self._sync_modules()

    def install_plugin(self, plugin_id: str) -> dict[str, Any]:
        with self._module_lock:
            result = self.plugins.install(plugin_id)
            changed = list(result.get("changed", []))
            self._sync_modules()
            if "cam-local-capture" in changed and self.recorder is not None:
                recorder_status = self.recorder.status()
                if (
                    not recorder_status.get("consent", False)
                    and recorder_status.get("consent_source") != "revoked"
                ):
                    configure = getattr(self.recorder, "configure", None)
                    if not callable(configure):
                        raise _ApiError(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            "unavailable_capability",
                            "Recorder authorization is not available.",
                            details={"capability": "recorder.configure"},
                        )
                    configure(consent=True, consent_source="auto_install")
                scan = getattr(self.recorder, "scan_now", None)
                if self._owns_services and callable(scan):
                    scan()
            status = self.plugin_status()
            status["changed"] = changed
            return status

    def uninstall_plugin(self, plugin_id: str) -> dict[str, Any]:
        with self._module_lock:
            capture = self.recorder if plugin_id == "cam-local-capture" else None
            try:
                result = self.plugins.uninstall(plugin_id)
            except ValueError as error:
                if str(error).startswith("Uninstall dependent plugins first:"):
                    raise _ApiError(
                        HTTPStatus.CONFLICT,
                        "conflict",
                        str(error),
                        details={"plugin_id": plugin_id},
                    ) from error
                raise
            changed = list(result.get("changed", []))
            if "cam-local-capture" in changed and capture is not None:
                configure = getattr(capture, "configure", None)
                if callable(configure):
                    configure(consent=False)
            self._sync_modules()
            status = self.plugin_status()
            status["changed"] = changed
            return status

    def update_plugin(self, plugin_id: str) -> dict[str, Any]:
        with self._module_lock:
            self.plugins.require(plugin_id)
            status = self.plugin_status()
            plugin = next(
                item for item in status["plugins"] if item["id"] == plugin_id
            )
            status["changed"] = []
            status["update_result"] = {
                "plugin_id": plugin_id,
                "status": "current",
                "installed_version": plugin["version"],
                "available_version": plugin["version"],
            }
            return status

    def _sync_modules(self) -> None:
        with self._module_lock:
            capture_installed = self.plugins.is_installed("cam-local-capture")
            if capture_installed and self.recorder is None:
                if self._injected_recorder is not None:
                    self.recorder = self._injected_recorder
                    self._owns_recorder = False
                else:
                    from .recorder import CaptureService

                    self.recorder = CaptureService(self.capture_dir)
                    self._owns_recorder = True
            elif not capture_installed and self.recorder is not None:
                if self._owns_recorder:
                    self.recorder.close()
                self.recorder = None
                self._owns_recorder = False

            execution_installed = self.plugins.is_installed(
                "cam-execution-gateway"
            )
            if execution_installed and self.execution is None:
                from .execution import ExecutionGateway

                self.execution = ExecutionGateway()
            elif not execution_installed:
                self.execution = None

    def connection_statuses(
        self,
        *,
        force: bool = False,
        include_uninstalled: bool = False,
    ) -> list[dict[str, Any]]:
        from .integrations import connection_statuses

        capture_status = (
            self.recorder.status(refresh_connections=force)
            if self.recorder is not None
            else None
        )
        connection_service = self.services.connections
        authorize = getattr(connection_service, "authorize_local_sources", None)
        if callable(authorize):
            enabled = bool(
                capture_status
                and capture_status.get("consent")
                and capture_status.get("categories", {}).get("instances", True)
            )
            if enabled != self._connection_authorized:
                list_instances = getattr(connection_service, "list_instances", None)
                discovered = (
                    list_instances(refresh=enabled)
                    if callable(list_instances)
                    else ()
                )
                targets = tuple(
                    str(item)
                    for item in getattr(
                        connection_service,
                        "default_authorized_instance_ids",
                        (),
                    )
                )
                known_instance_ids = {
                    str(
                        item.get("instance_id")
                        if isinstance(item, Mapping)
                        else getattr(item, "instance_id", "")
                    )
                    for item in discovered
                }
                known_instance_ids.update(targets)
                known_instance_ids.discard("")
                authorize(False)
                if known_instance_ids:
                    authorize(False, instance_ids=known_instance_ids)
                if enabled:
                    authorize(True, instance_ids=targets)
                    if callable(list_instances):
                        list_instances(refresh=True)
                self._connection_authorized = enabled
        return connection_statuses(
            self.plugins.installed_ids(),
            capture_status=capture_status,
            connection_service=connection_service,
            refresh=force,
            include_uninstalled=include_uninstalled,
        )

    def connections_payload(
        self,
        *,
        force: bool = False,
        include_uninstalled: bool = False,
    ) -> dict[str, Any]:
        connections = self.connection_statuses(
            force=force,
            include_uninstalled=include_uninstalled,
        )
        instances = [
            dict(instance)
            for connection in connections
            for instance in connection.get("instances", [])
            if isinstance(instance, dict)
        ]
        return {
            "schema_version": 1,
            "connections": connections,
            "instances": instances,
            "instance_count": len(instances),
            "selected_instance_id": None,
            "selection_required": bool(instances),
        }

    def plugin_status(self) -> dict[str, Any]:
        status = self.plugins.status()
        connections = {
            connection["plugin_id"]: connection
            for connection in self.connection_statuses()
        }
        recorder_status = (
            self.recorder.status()
            if self.recorder is not None
            else None
        )
        for plugin in status["plugins"]:
            plugin_id = plugin["id"]
            if not plugin["installed"]:
                plugin["runtime"] = {
                    "status": "not_installed",
                    "label": "未安装",
                    "detail": "安装后可用",
                    "instance_count": 0,
                    "instances": [],
                }
                continue
            if plugin_id in connections:
                connection = connections[plugin_id]
                plugin["runtime"] = {
                    **connection,
                    "label": _runtime_connection_label(connection),
                }
            elif plugin_id == "cam-local-capture" and recorder_status:
                instance_count = sum(
                    len(instances)
                    for instances in recorder_status.get("instances", {}).values()
                    if isinstance(instances, list)
                )
                plugin["runtime"] = {
                    "status": recorder_status["state"],
                    "label": {
                        "awaiting_consent": "待授权",
                        "recording": "记录中",
                        "paused": "已暂停",
                    }.get(recorder_status["state"], recorder_status["state"]),
                    "detail": (
                        f"本机监测 {instance_count} 个窗口/进程"
                        if recorder_status["consent"]
                        else "授权后才会检测 CAM 软件窗口"
                    ),
                    "instance_count": instance_count,
                    "instances": [
                        dict(instance)
                        for instances in recorder_status.get("instances", {}).values()
                        if isinstance(instances, list)
                        for instance in instances
                    ],
                }
            elif plugin_id == "cam-execution-gateway":
                plugin["runtime"] = {
                    "status": "ready",
                    "label": "dry-run 就绪",
                    "detail": "实时传输未配置；live 必须明确指定目标实例",
                    "instance_count": 0,
                    "instances": [],
                }
            else:
                plugin["runtime"] = {
                    "status": "ready",
                    "label": "已就绪",
                    "detail": "插件已加载",
                    "instance_count": 0,
                    "instances": [],
                }
        return plugin_api_status(status, recorder_status=recorder_status)

    def event_page(self, query: EventQuery) -> dict[str, Any]:
        if self.recorder is None:
            raise _ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "unavailable_capability",
                "Recorder service is not available.",
                details={"capability": "recorder.query_events"},
            )
        page = service_payload(self.recorder.query_events(query))
        if not isinstance(page, dict):
            raise TypeError("Recorder query_events() must return an EventPage.")
        page["returned"] = int(page.get("returned_count", len(page.get("events", []))))
        page["counts"] = service_payload(self.recorder.status().get("counts", {}))
        page["query"] = query.to_dict()
        return page

    def export_events(self) -> bytes:
        if self.recorder is None:
            raise _ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "unavailable_capability",
                "Recorder export is not available.",
                details={"capability": "recorder.export"},
            )
        raw = self.recorder.export_jsonl()
        lines: list[str] = []
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"Recorder export line {line_number} must be a JSON object."
                )
            lines.append(
                json.dumps(
                    service_payload(value),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")

    def _refresh_sessions(self, query: EventQuery | None = None) -> list[Any]:
        with self.services.lock:
            session_service = self.services.sessions
            if session_service is None:
                raise _ApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "unavailable_capability",
                    "Session service is not available.",
                    details={"capability": "sessions"},
                )
            if self.recorder is not None:
                page = self.recorder.query_events(
                    query or EventQuery(limit=10_000, sort="asc")
                )
                return list(session_service.build(page))
            sessions = getattr(session_service, "sessions", ())
            if callable(sessions):
                sessions = sessions()
            return list(sessions)

    def sessions_payload(self, query: EventQuery) -> dict[str, Any]:
        sessions = [service_payload(item) for item in self._refresh_sessions(query)]
        return {
            "schema_version": 1,
            "sessions": sessions,
            "returned_count": len(sessions),
            "query": query.to_dict(),
        }

    def compare_sessions(self, body: dict[str, Any]) -> dict[str, Any]:
        session_ids = body.get("session_ids")
        if not isinstance(session_ids, list) or any(
            not isinstance(item, str) or not item.strip() for item in session_ids
        ):
            raise ValueError("session_ids must be an array of non-empty strings.")
        baseline = body.get("baseline_session_id")
        if baseline is not None and not isinstance(baseline, str):
            raise ValueError("baseline_session_id must be a string.")
        with self.services.lock:
            self._refresh_sessions()
            compare = getattr(self.services.sessions, "compare", None)
            if not callable(compare):
                raise _ApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "unavailable_capability",
                    "Session comparison is not available.",
                    details={"capability": "sessions.compare"},
                )
            result = compare(
                session_ids,
                baseline_session_id=baseline or None,
            )
        return {"schema_version": 1, "diff": service_payload(result)}

    def mine_workflows(self, body: dict[str, Any]) -> dict[str, Any]:
        requested = body.get("session_ids")
        if requested is not None and (
            not isinstance(requested, list)
            or any(not isinstance(item, str) or not item.strip() for item in requested)
        ):
            raise ValueError("session_ids must be an array of non-empty strings.")
        min_support = body.get("min_support", 2)
        if not isinstance(min_support, int) or isinstance(min_support, bool):
            raise ValueError("min_support must be an integer.")
        with self.services.lock:
            self._refresh_sessions()
            learner = self.services.workflows
            if learner is None:
                if self.services.workflow_factory is not None:
                    learner = self.services.workflow_factory(self.services.sessions)
                else:
                    from .learning import WorkflowLearner

                    learner = WorkflowLearner(
                        self.services.sessions,
                        min_support=min_support,
                    )
            mine = getattr(learner, "mine", None)
            if not callable(mine):
                raise _ApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "unavailable_capability",
                    "Workflow mining is not available.",
                    details={"capability": "workflows.mine"},
                )
            candidates = list(mine(requested))
        return {
            "schema_version": 1,
            "candidates": [service_payload(item) for item in candidates],
            "returned_count": len(candidates),
        }

    def save_recipe(self, recipe: dict[str, Any]) -> dict[str, Any]:
        save = getattr(self.services.recipes, "save", None)
        if not callable(save):
            raise _ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "unavailable_capability",
                "Recipe storage is not available.",
                details={"capability": "recipes.save"},
            )
        version = service_payload(save(recipe))
        if not isinstance(version, dict):
            raise TypeError("Recipe save() must return a RecipeVersion.")
        return {
            "schema_version": 1,
            "recipe_version": version,
            "recipe": version.get("recipe"),
        }

    def recipe_payload(self, identifier: str) -> dict[str, Any]:
        if not identifier or len(identifier) > 512:
            raise ValueError("Recipe identifier is invalid.")
        recipe_service = self.services.recipes
        with self.services.lock:
            if identifier.startswith("sha256:"):
                get = getattr(recipe_service, "get", None)
                if not callable(get):
                    raise _ApiError(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "unavailable_capability",
                        "Recipe lookup is not available.",
                        details={"capability": "recipes.get"},
                    )
                versions = [get(identifier)]
            else:
                list_versions = getattr(recipe_service, "versions", None)
                if not callable(list_versions):
                    raise _ApiError(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "unavailable_capability",
                        "Recipe version lookup is not available.",
                        details={"capability": "recipes.versions"},
                    )
                versions = list(list_versions(identifier))
            if not versions:
                raise KeyError(f"Unknown recipe: {identifier}")
        serialized = [service_payload(item) for item in versions]
        latest = serialized[-1]
        return {
            "schema_version": 1,
            "recipe_version": latest,
            "recipe": latest.get("recipe") if isinstance(latest, dict) else None,
            "versions": serialized,
        }

    @staticmethod
    def is_command_task_request(body: Mapping[str, Any]) -> bool:
        return bool(
            {
                "task_type",
                "execution_mode",
                "target_instance_id",
                "project_id",
                "operation",
            }
            & set(body)
        )

    def submit_command(self, body: dict[str, Any]) -> dict[str, Any]:
        target_instance_id = str(body.get("target_instance_id", "")).strip()
        if not target_instance_id:
            raise ValueError("target_instance_id is required; no instance is selected implicitly.")
        submit = getattr(self.services.commands, "submit", None)
        if not callable(submit):
            raise _ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "unavailable_capability",
                "Command task service is not available.",
                details={"capability": "command_tasks.submit"},
            )
        return {
            "schema_version": 1,
            "task": service_payload(submit(body)),
        }

    def command_task(self, task_id: str) -> dict[str, Any]:
        if not task_id or len(task_id) > 512:
            raise ValueError("task_id is invalid.")
        get = getattr(self.services.commands, "get", None)
        if not callable(get):
            raise _ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "unavailable_capability",
                "Command task service is not available.",
                details={"capability": "command_tasks.get"},
            )
        return {"schema_version": 1, "task": service_payload(get(task_id))}

    def cancel_command(self, task_id: str) -> dict[str, Any]:
        if not task_id or len(task_id) > 512:
            raise ValueError("task_id is invalid.")
        cancel = getattr(self.services.commands, "cancel", None)
        if not callable(cancel):
            raise _ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "unavailable_capability",
                "Command task service is not available.",
                details={"capability": "command_tasks.cancel"},
            )
        return {"schema_version": 1, "task": service_payload(cancel(task_id))}

    def diagnostics_payload(self) -> dict[str, Any]:
        snapshot = getattr(self.services.diagnostics, "snapshot", None)
        if not callable(snapshot):
            raise _ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "unavailable_capability",
                "Diagnostics service is not available.",
                details={"capability": "diagnostics.snapshot"},
            )
        return {
            "schema_version": 1,
            "diagnostics": service_payload(snapshot()),
        }

    def get_state(self, key: str) -> Any:
        with self._state_lock:
            return self.state.get(key)

    def set_state(self, key: str, value: Any) -> None:
        with self._state_lock:
            self.state[key] = value

    def state_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return service_payload(self.state)

    def server_close(self) -> None:
        if self._owns_services:
            commands = self.services.commands
            close_commands = getattr(commands, "close", None)
            if callable(close_commands):
                close_commands()
            injected_recorder = self._injected_recorder
            close_recorder = getattr(injected_recorder, "close", None)
            if callable(close_recorder):
                close_recorder()
        if self.recorder is not None and self._owns_recorder:
            self.recorder.close()
        self.recorder = None
        self._owns_recorder = False
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
