from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .asset_registry import AssetRegistryError
from .capability_registry import CapabilityRegistryError
from .flow_service import (
    CancellationToken,
    FlowRequestContext,
    FlowService,
    FlowServiceError,
)


FLOW_API_PREFIX = "/api/flow"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_REQUEST_TARGET_BYTES = 8 * 1024
MAX_QUERY_FIELDS = 64

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_RESOURCE_ID = r"([^/]+)"
_COMMON_CREATE_FIELDS = frozenset({"idempotency_key"})
_ASSET_FIELDS = frozenset(
    {
        "content",
        "content_encoding",
        "asset_id",
        "product",
        "asset_type",
        "display_name",
        "source_locator",
        "source_origin",
        "rights",
        "target_versions",
        "runtime_modes",
        "dependencies",
        "protected_binary",
        "extensions",
        "idempotency_key",
    }
)
_VERSION_FIELDS = frozenset(
    {
        "graph_id",
        "revision_id",
        "parent_version_ids",
        "status",
        "author_ref",
        "message",
        "round_trip_report_id",
        "compatibility_report_id",
        "reviewed_recipe_hash",
        "version_id",
        "extensions",
        "idempotency_key",
        "version",
    }
)
_PREVIEW_FIELDS = frozenset(
    {
        "plan_id",
        "graph_id",
        "revision_id",
        "flow_version_id",
        "execution_mode",
        "transport",
        "target",
        "hashes",
        "idempotency_key",
    }
)
_SAFE_DETAIL_FIELDS = frozenset(
    {
        "actual_revision_id",
        "allowed_states",
        "asset_revision_id",
        "candidate_count",
        "category",
        "expected_revision_id",
        "expected_state",
        "field",
        "fields",
        "graph_id",
        "manifest_id",
        "max_bytes",
        "max_items",
        "object_ref",
        "operation",
        "plan_id",
        "product",
        "project_id",
        "reason",
        "report_id",
        "resource",
        "resource_id",
        "revision_id",
        "source_mapping_ids",
        "state",
        "target_instance_id",
        "target_state",
        "target_version",
        "version_id",
    }
)


@dataclass(frozen=True, slots=True)
class FlowApiRequest:
    method: str
    path: str
    body: Mapping[str, Any] | str | bytes | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    deadline: datetime | None = None
    cancellation: CancellationToken | None = None
    body_size: int | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FlowApiRequest:
        return cls(
            method=str(value.get("method", "")),
            path=str(value.get("path", "")),
            body=value.get("body"),
            headers=dict(value.get("headers", {})),
            query=dict(value.get("query", {})),
            correlation_id=value.get("correlation_id"),
            deadline=value.get("deadline"),
            cancellation=value.get("cancellation"),
            body_size=value.get("body_size"),
        )


@dataclass(frozen=True, slots=True)
class FlowApiResponse:
    status: int
    body: Mapping[str, Any] | None
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def http_status(self) -> int:
        return self.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "body": _json_copy(self.body) if self.body is not None else None,
            "headers": dict(self.headers),
        }


@dataclass(frozen=True, slots=True)
class FlowRoute:
    method: str
    path: str
    name: str


FLOW_ROUTES = (
    FlowRoute("GET", f"{FLOW_API_PREFIX}/assets", "flow.assets.list"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/assets", "flow.assets.create"),
    FlowRoute("GET", f"{FLOW_API_PREFIX}/assets/:asset_revision_id", "flow.assets.get"),
    FlowRoute("GET", f"{FLOW_API_PREFIX}/capabilities", "flow.capabilities.list"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/capabilities", "flow.capabilities.create"),
    FlowRoute("GET", f"{FLOW_API_PREFIX}/capabilities/:manifest_id", "flow.capabilities.get"),
    FlowRoute("GET", f"{FLOW_API_PREFIX}/graphs", "flow.graphs.list"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/graphs", "flow.graphs.create"),
    FlowRoute("GET", f"{FLOW_API_PREFIX}/graphs/:graph_id", "flow.graphs.get"),
    FlowRoute(
        "GET",
        f"{FLOW_API_PREFIX}/graphs/:graph_id/revisions",
        "flow.graphs.revisions",
    ),
    FlowRoute("GET", f"{FLOW_API_PREFIX}/versions", "flow.versions.list"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/versions", "flow.versions.create"),
    FlowRoute("GET", f"{FLOW_API_PREFIX}/versions/:version_id", "flow.versions.get"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/validate", "flow.validate"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/diff", "flow.diff"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/test", "flow.test"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/compatibility", "flow.compatibility"),
    FlowRoute(
        "GET",
        f"{FLOW_API_PREFIX}/compatibility/:report_id",
        "flow.compatibility.get",
    ),
    FlowRoute("GET", f"{FLOW_API_PREFIX}/preview-plans", "flow.preview.list"),
    FlowRoute("POST", f"{FLOW_API_PREFIX}/preview-plans", "flow.preview.create"),
    FlowRoute(
        "GET",
        f"{FLOW_API_PREFIX}/preview-plans/:plan_id",
        "flow.preview.get",
    ),
    FlowRoute(
        "POST",
        f"{FLOW_API_PREFIX}/preview-plans/:plan_id/run",
        "flow.preview.run",
    ),
    FlowRoute(
        "POST",
        f"{FLOW_API_PREFIX}/preview-plans/:plan_id/cancel",
        "flow.preview.cancel",
    ),
)


def _json_copy(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == expected:
            return str(value)
    return None


def _if_none_match(headers: Mapping[str, str], etag: str) -> bool:
    value = _header(headers, "If-None-Match")
    if value is None:
        return False
    for candidate in value.split(","):
        normalized = candidate.strip()
        if normalized == "*":
            return True
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        if normalized == etag:
            return True
    return False


def _strict_json(payload: str | bytes) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"invalid numeric constant: {value}")

    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    value = json.loads(
        text,
        object_pairs_hook=pairs,
        parse_constant=invalid_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("request body must be an object")
    return value


def _safe_details(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        if name not in _SAFE_DETAIL_FIELDS:
            continue
        if isinstance(item, str):
            if len(item) <= 256 and not any(ord(character) < 0x20 for character in item):
                result[name] = item
        elif item is None or isinstance(item, (bool, int, float)):
            result[name] = item
        elif isinstance(item, (list, tuple)):
            safe_items = [
                current
                for current in item
                if (
                    current is None
                    or isinstance(current, (bool, int, float))
                    or (
                        isinstance(current, str)
                        and len(current) <= 256
                        and not any(ord(character) < 0x20 for character in current)
                    )
                )
            ]
            result[name] = safe_items[:128]
    return result


class FlowApi:
    """Pure request/response dispatcher. It has no HTTP server side effects."""

    routes = FLOW_ROUTES

    def __init__(
        self,
        service: FlowService,
        *,
        max_request_bytes: int = MAX_REQUEST_BYTES,
    ) -> None:
        self.service = service
        self.max_request_bytes = int(max_request_bytes)
        if self.max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be positive.")

    def handle(
        self,
        request: FlowApiRequest | Mapping[str, Any] | str,
        path: str | None = None,
        *,
        body: Mapping[str, Any] | str | bytes | None = None,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
        deadline: datetime | None = None,
        cancellation: CancellationToken | None = None,
        body_size: int | None = None,
    ) -> FlowApiResponse:
        if isinstance(request, FlowApiRequest):
            current = request
        elif isinstance(request, Mapping):
            current = FlowApiRequest.from_dict(request)
        else:
            current = FlowApiRequest(
                method=str(request),
                path=str(path or ""),
                body=body,
                headers=dict(headers or {}),
                query=dict(query or {}),
                correlation_id=correlation_id,
                deadline=deadline,
                cancellation=cancellation,
                body_size=body_size,
            )

        fallback_correlation = f"corr:{uuid.uuid4().hex}"
        try:
            correlation = self._correlation_id(current, fallback_correlation)
            context = self._context(current, correlation)
            return self._dispatch(current, context)
        except FlowServiceError as error:
            correlation = locals().get("correlation", fallback_correlation)
            return self._problem(
                error.http_status,
                error.code,
                str(error),
                correlation,
                error.details,
            )
        except (AssetRegistryError, CapabilityRegistryError) as error:
            correlation = locals().get("correlation", fallback_correlation)
            return self._problem(
                self._registry_status(error),
                error.code,
                self._registry_message(error),
                correlation,
                getattr(error, "details", None),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            correlation = locals().get("correlation", fallback_correlation)
            return self._problem(
                400,
                "FLOW_REQUEST_INVALID",
                "The flow API request is invalid.",
                correlation,
            )
        except Exception:
            correlation = locals().get("correlation", fallback_correlation)
            return self._problem(
                500,
                "FLOW_INTERNAL_ERROR",
                "The flow service failed without exposing rejected payload data.",
                correlation,
            )

    dispatch = handle
    handle_request = handle

    def _correlation_id(
        self,
        request: FlowApiRequest,
        fallback: str,
    ) -> str:
        candidate = (
            request.correlation_id
            or _header(request.headers, "X-Correlation-ID")
            or fallback
        )
        if not isinstance(candidate, str) or _CORRELATION_ID.fullmatch(candidate) is None:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The correlation ID is invalid.",
                http_status=400,
                details={"field": "correlation_id"},
            )
        return candidate

    def _context(
        self,
        request: FlowApiRequest,
        correlation_id: str,
    ) -> FlowRequestContext:
        deadline = request.deadline
        timeout_header = _header(request.headers, "X-Request-Timeout-Ms")
        if timeout_header is not None:
            if deadline is not None:
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "Specify either a deadline or request timeout, not both.",
                    http_status=400,
                    details={"field": "deadline"},
                )
            try:
                timeout_ms = int(timeout_header)
            except ValueError as error:
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "X-Request-Timeout-Ms must be a positive integer.",
                    http_status=400,
                    details={"field": "X-Request-Timeout-Ms"},
                ) from error
            if timeout_ms <= 0:
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "X-Request-Timeout-Ms must be a positive integer.",
                    http_status=400,
                    details={"field": "X-Request-Timeout-Ms"},
                )
            deadline = datetime.now(timezone.utc) + timedelta(milliseconds=timeout_ms)
        return FlowRequestContext(
            correlation_id=correlation_id,
            deadline=deadline,
            cancellation=request.cancellation,
        )

    def _path_and_query(
        self,
        request: FlowApiRequest,
    ) -> tuple[str, dict[str, list[str]]]:
        if len(request.path.encode("utf-8")) > MAX_REQUEST_TARGET_BYTES:
            raise FlowServiceError(
                "RESOURCE_LIMIT_EXCEEDED",
                "The request target exceeds the flow API limit.",
                http_status=413,
                details={"max_bytes": MAX_REQUEST_TARGET_BYTES},
            )
        parsed = urlsplit(request.path)
        path = unquote(parsed.path)
        if "\x00" in path or "\\" in path or not path.startswith("/"):
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The request path is invalid.",
                http_status=400,
                details={"field": "path"},
            )
        try:
            values = parse_qs(
                parsed.query,
                keep_blank_values=True,
                max_num_fields=MAX_QUERY_FIELDS,
            )
        except ValueError as error:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The query string contains too many fields.",
                http_status=400,
            ) from error
        for key, value in request.query.items():
            if isinstance(value, (list, tuple)):
                values[str(key)] = [str(item) for item in value]
            elif value is None:
                values[str(key)] = [""]
            else:
                values[str(key)] = [str(value)]
        if len(values) > MAX_QUERY_FIELDS:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The query contains too many fields.",
                http_status=400,
            )
        return path, values

    def _body(self, request: FlowApiRequest, *, required: bool = True) -> dict[str, Any]:
        raw = request.body
        if raw is None:
            if required:
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "A non-empty JSON object is required.",
                    http_status=400,
                    details={"field": "body"},
                )
            return {}
        if isinstance(raw, bytes):
            size = len(raw)
        elif isinstance(raw, str):
            size = len(raw.encode("utf-8"))
        elif isinstance(raw, Mapping):
            try:
                size = len(
                    json.dumps(
                        raw,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
            except (TypeError, ValueError) as error:
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "The request body must contain finite JSON data.",
                    http_status=400,
                ) from error
        else:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The request body must be a JSON object.",
                http_status=400,
                details={"field": "body"},
            )
        if request.body_size is not None:
            if isinstance(request.body_size, bool) or int(request.body_size) < 0:
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "body_size must be a non-negative integer.",
                    http_status=400,
                    details={"field": "body_size"},
                )
            size = max(size, int(request.body_size))
        if size > self.max_request_bytes:
            raise FlowServiceError(
                "RESOURCE_LIMIT_EXCEEDED",
                "The request body exceeds the flow API limit.",
                http_status=413,
                details={"max_bytes": self.max_request_bytes},
            )
        try:
            value = _strict_json(raw) if isinstance(raw, (str, bytes)) else _json_copy(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The request body is not strict UTF-8 JSON.",
                http_status=400,
                details={"field": "body"},
            ) from error
        if not isinstance(value, dict):
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The request body must be a JSON object.",
                http_status=400,
                details={"field": "body"},
            )
        return value

    @staticmethod
    def _known_fields(value: Mapping[str, Any], allowed: set[str] | frozenset[str]) -> None:
        unknown = sorted(set(value) - set(allowed))
        if unknown:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The request contains unknown fields.",
                http_status=400,
                details={"fields": unknown},
            )

    @staticmethod
    def _query(
        query: Mapping[str, list[str]],
        *,
        allowed: set[str] | frozenset[str],
    ) -> None:
        unknown = sorted(set(query) - set(allowed))
        if unknown:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The query contains unknown fields.",
                http_status=400,
                details={"fields": unknown},
            )

    @staticmethod
    def _scalar(
        query: Mapping[str, list[str]],
        name: str,
        default: str | None = None,
    ) -> str | None:
        values = query.get(name)
        if values is None:
            return default
        if len(values) != 1:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                f"{name} must be provided once.",
                http_status=400,
                details={"field": name},
            )
        return values[0] or default

    def _page_args(self, query: Mapping[str, list[str]]) -> tuple[str | None, int]:
        cursor = self._scalar(query, "cursor")
        raw_limit = self._scalar(query, "limit", "100")
        try:
            limit = int(raw_limit or "100")
        except ValueError as error:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "limit must be an integer.",
                http_status=400,
                details={"field": "limit"},
            ) from error
        return cursor, limit

    @staticmethod
    def _idempotency_key(
        request: FlowApiRequest,
        body: Mapping[str, Any],
    ) -> str | None:
        header_value = _header(request.headers, "Idempotency-Key")
        body_value = body.get("idempotency_key")
        if header_value and body_value and header_value != body_value:
            raise FlowServiceError(
                "FLOW_IDEMPOTENCY_CONFLICT",
                "Header and body idempotency keys do not match.",
                http_status=409,
            )
        value = header_value or body_value
        return str(value) if value is not None else None

    def _success(
        self,
        status: int,
        payload: Mapping[str, Any],
        context: FlowRequestContext,
        *,
        etag: str | None = None,
    ) -> FlowApiResponse:
        body = _json_copy(payload)
        body.setdefault("schema_version", 1)
        body["correlation_id"] = context.correlation_id
        headers = {
            "Cache-Control": "no-store",
            "X-Correlation-ID": context.correlation_id,
        }
        if etag is not None:
            headers["ETag"] = etag
        return FlowApiResponse(status, body, headers)

    def _not_modified(
        self,
        context: FlowRequestContext,
        etag: str,
    ) -> FlowApiResponse:
        return FlowApiResponse(
            304,
            None,
            {
                "Cache-Control": "no-store",
                "X-Correlation-ID": context.correlation_id,
                "ETag": etag,
            },
        )

    def _problem(
        self,
        status: int,
        code: str,
        message: str,
        correlation_id: str,
        details: Mapping[str, Any] | None = None,
    ) -> FlowApiResponse:
        body = {
            "schema_version": 1,
            "error": {
                "code": code,
                "message": message,
                "correlation_id": correlation_id,
                "details": _safe_details(details),
            },
        }
        return FlowApiResponse(
            int(status),
            body,
            {
                "Cache-Control": "no-store",
                "X-Correlation-ID": correlation_id,
            },
        )

    @staticmethod
    def _registry_status(error: Exception) -> int:
        code = str(getattr(error, "code", ""))
        if code in {"ASSET_NOT_FOUND", "CAPABILITY_MISSING"}:
            return 404
        if code == "RESOURCE_LIMIT_EXCEEDED":
            return 413
        if code in {
            "ASSET_RIGHTS_UNKNOWN",
            "ASSET_RIGHTS_RESTRICTED",
            "ASSET_SHARING_FORBIDDEN",
            "ASSET_REDISTRIBUTION_FORBIDDEN",
            "ASSET_NETWORK_EGRESS_FORBIDDEN",
            "ASSET_BINARY_INSPECTION_FORBIDDEN",
            "CAPABILITY_REVOKED",
            "CAPABILITY_PERMISSION_NOT_DECLARED",
            "PREVIEW_PERMISSION_REVOKED",
        }:
            return 403
        if code in {
            "CAPABILITY_VERSION_MISMATCH",
            "CAPABILITY_MANIFEST_IMMUTABLE",
        }:
            return 409
        if code.endswith("_CORRUPT") or code in {
            "ASSET_CONTENT_MISSING",
            "ASSET_CONTENT_TAMPERED",
        }:
            return 500
        return 400

    @staticmethod
    def _registry_message(error: Exception) -> str:
        code = str(getattr(error, "code", ""))
        if code.startswith("ASSET_"):
            return "The asset registry rejected the request."
        return "The capability registry rejected the request."

    def _dispatch(
        self,
        request: FlowApiRequest,
        context: FlowRequestContext,
    ) -> FlowApiResponse:
        method = request.method.strip().upper()
        self.service.check_request(context)
        path, query = self._path_and_query(request)
        if method not in {"GET", "POST"}:
            raise FlowServiceError(
                "FLOW_REQUEST_INVALID",
                "The flow API method is not supported.",
                http_status=400,
                details={"field": "method"},
            )

        if path == f"{FLOW_API_PREFIX}/assets":
            if method == "GET":
                self._query(query, allowed={"asset_id", "cursor", "limit"})
                cursor, limit = self._page_args(query)
                page = self.service.list_assets(
                    asset_id=self._scalar(query, "asset_id"),
                    cursor=cursor,
                    limit=limit,
                )
                payload = page.to_dict("assets")
                return self._success(
                    200,
                    payload,
                    context,
                    etag=self.service.resource_etag(payload),
                )
            self._query(query, allowed=set())
            value = self._body(request)
            self._known_fields(value, _ASSET_FIELDS)
            if value.get("content_encoding", "utf-8") != "utf-8":
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "Asset content_encoding must be utf-8.",
                    http_status=400,
                    details={"field": "content_encoding"},
                )
            content = value.get("content")
            if not isinstance(content, str):
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "Asset content must be a UTF-8 string.",
                    http_status=400,
                    details={"field": "content"},
                )
            metadata = {
                key: item
                for key, item in value.items()
                if key not in {"content", "content_encoding", "idempotency_key"}
            }
            asset = self.service.register_asset(
                content.encode("utf-8"),
                idempotency_key=self._idempotency_key(request, value),
                context=context,
                **metadata,
            )
            return self._success(
                201,
                {"asset": asset},
                context,
                etag=self.service.resource_etag(asset),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/assets/{_RESOURCE_ID}",
            path,
        )
        if match and method == "GET":
            self._query(query, allowed=set())
            asset = self.service.get_asset(match.group(1))
            etag = self.service.resource_etag(asset)
            if _if_none_match(request.headers, etag):
                return self._not_modified(context, etag)
            return self._success(200, {"asset": asset}, context, etag=etag)

        if path == f"{FLOW_API_PREFIX}/capabilities":
            if method == "GET":
                self._query(query, allowed={"cursor", "limit"})
                cursor, limit = self._page_args(query)
                page = self.service.list_capabilities(cursor=cursor, limit=limit)
                payload = page.to_dict("capabilities")
                return self._success(
                    200,
                    payload,
                    context,
                    etag=self.service.resource_etag(payload),
                )
            self._query(query, allowed=set())
            value = self._body(request)
            if "manifest" in value:
                self._known_fields(value, {"manifest", "idempotency_key"})
                manifest = value["manifest"]
            else:
                manifest = {
                    key: item for key, item in value.items() if key != "idempotency_key"
                }
            if not isinstance(manifest, Mapping):
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "manifest must be a JSON object.",
                    http_status=400,
                    details={"field": "manifest"},
                )
            capability = self.service.register_capability(
                manifest,
                idempotency_key=self._idempotency_key(request, value),
                context=context,
            )
            return self._success(
                201,
                {"capability": capability},
                context,
                etag=self.service.resource_etag(capability),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/capabilities/{_RESOURCE_ID}",
            path,
        )
        if match and method == "GET":
            self._query(query, allowed={"manifest_version", "manifest_hash"})
            capability = self.service.get_capability(
                match.group(1),
                manifest_version=self._scalar(query, "manifest_version"),
                manifest_hash=self._scalar(query, "manifest_hash"),
            )
            etag = self.service.resource_etag(capability)
            if _if_none_match(request.headers, etag):
                return self._not_modified(context, etag)
            return self._success(
                200,
                {"capability": capability},
                context,
                etag=etag,
            )

        if path == f"{FLOW_API_PREFIX}/graphs":
            if method == "GET":
                self._query(query, allowed={"cursor", "limit"})
                cursor, limit = self._page_args(query)
                page = self.service.list_graphs(cursor=cursor, limit=limit)
                payload = page.to_dict("graphs")
                return self._success(
                    200,
                    payload,
                    context,
                    etag=self.service.resource_etag(payload),
                )
            self._query(query, allowed=set())
            value = self._body(request)
            if "graph" in value:
                self._known_fields(
                    value,
                    {
                        "graph",
                        "expected_revision_id",
                        "idempotency_key",
                    },
                )
                graph = value["graph"]
                expected_revision_id = value.get("expected_revision_id")
            else:
                graph = {
                    key: item for key, item in value.items() if key != "idempotency_key"
                }
                expected_revision_id = None
            if not isinstance(graph, Mapping):
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "graph must be a JSON object.",
                    http_status=400,
                    details={"field": "graph"},
                )
            saved = self.service.save_graph(
                graph,
                expected_revision_id=expected_revision_id,
                if_match=_header(request.headers, "If-Match"),
                idempotency_key=self._idempotency_key(request, value),
                context=context,
            )
            return self._success(
                201,
                {"graph": saved},
                context,
                etag=self.service.resource_etag(saved),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/graphs/{_RESOURCE_ID}/revisions",
            path,
        )
        if match and method == "GET":
            self._query(query, allowed={"cursor", "limit"})
            cursor, limit = self._page_args(query)
            page = self.service.list_graph_revisions(
                match.group(1),
                cursor=cursor,
                limit=limit,
            )
            payload = page.to_dict("revisions")
            return self._success(
                200,
                payload,
                context,
                etag=self.service.resource_etag(payload),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/graphs/{_RESOURCE_ID}",
            path,
        )
        if match and method == "GET":
            self._query(query, allowed={"revision_id"})
            graph = self.service.get_graph(
                match.group(1),
                self._scalar(query, "revision_id"),
            )
            etag = self.service.resource_etag(graph)
            if _if_none_match(request.headers, etag):
                return self._not_modified(context, etag)
            return self._success(200, {"graph": graph}, context, etag=etag)

        if path == f"{FLOW_API_PREFIX}/versions":
            if method == "GET":
                self._query(query, allowed={"graph_id", "cursor", "limit"})
                cursor, limit = self._page_args(query)
                page = self.service.list_versions(
                    graph_id=self._scalar(query, "graph_id"),
                    cursor=cursor,
                    limit=limit,
                )
                payload = page.to_dict("versions")
                return self._success(
                    200,
                    payload,
                    context,
                    etag=self.service.resource_etag(payload),
                )
            self._query(query, allowed=set())
            value = self._body(request)
            self._known_fields(value, _VERSION_FIELDS)
            key = self._idempotency_key(request, value)
            if "version" in value:
                version = self.service.create_version(
                    value["version"],
                    idempotency_key=key,
                    context=context,
                )
            else:
                arguments = {
                    item: current
                    for item, current in value.items()
                    if item != "idempotency_key"
                }
                version = self.service.create_version(
                    idempotency_key=key,
                    context=context,
                    **arguments,
                )
            return self._success(
                201,
                {"version": version},
                context,
                etag=self.service.resource_etag(version),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/versions/{_RESOURCE_ID}",
            path,
        )
        if match and method == "GET":
            self._query(query, allowed=set())
            version = self.service.get_version(match.group(1))
            etag = self.service.resource_etag(version)
            if _if_none_match(request.headers, etag):
                return self._not_modified(context, etag)
            return self._success(200, {"version": version}, context, etag=etag)

        if path == f"{FLOW_API_PREFIX}/validate" and method == "POST":
            self._query(query, allowed=set())
            value = self._body(request)
            self._known_fields(
                value,
                {
                    "graph",
                    "graph_id",
                    "revision_id",
                    "require_known_semantics",
                },
            )
            if ("graph" in value) == ("graph_id" in value):
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "Specify exactly one of graph or graph_id.",
                    http_status=400,
                )
            result = self.service.validate(
                value.get("graph"),
                graph_id=value.get("graph_id"),
                revision_id=value.get("revision_id"),
                require_known_semantics=bool(
                    value.get("require_known_semantics", False)
                ),
                context=context,
            )
            return self._success(200, {"validation": result}, context)

        if path == f"{FLOW_API_PREFIX}/diff" and method == "POST":
            self._query(query, allowed=set())
            value = self._body(request)
            self._known_fields(
                value,
                {
                    "graph_id",
                    "from_revision_id",
                    "to_revision_id",
                    "include_layout",
                },
            )
            result = self.service.diff_graphs(
                str(value.get("graph_id", "")),
                str(value.get("from_revision_id", "")),
                str(value.get("to_revision_id", "")),
                include_layout=bool(value.get("include_layout", False)),
                context=context,
            )
            return self._success(
                200,
                {"diff": result},
                context,
                etag=self.service.resource_etag(result),
            )

        if path == f"{FLOW_API_PREFIX}/test" and method == "POST":
            self._query(query, allowed=set())
            value = self._body(request)
            self._known_fields(value, {"graph_id", "revision_id"})
            result = self.service.test_graph(
                str(value.get("graph_id", "")),
                value.get("revision_id"),
                context=context,
            )
            return self._success(
                200,
                {"test": result},
                context,
                etag=self.service.resource_etag(result),
            )

        if path == f"{FLOW_API_PREFIX}/compatibility" and method == "POST":
            self._query(query, allowed=set())
            value = self._body(request)
            self._known_fields(value, {"graph_id", "revision_id", "target"})
            target = value.get("target")
            if not isinstance(target, Mapping):
                raise FlowServiceError(
                    "FLOW_REQUEST_INVALID",
                    "target must be a JSON object.",
                    http_status=400,
                    details={"field": "target"},
                )
            report = self.service.check_compatibility(
                str(value.get("graph_id", "")),
                target,
                revision_id=value.get("revision_id"),
                context=context,
            )
            return self._success(
                200,
                {"compatibility": report},
                context,
                etag=self.service.resource_etag(report),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/compatibility/{_RESOURCE_ID}",
            path,
        )
        if match and method == "GET":
            self._query(query, allowed=set())
            report = self.service.get_compatibility_report(match.group(1))
            return self._success(
                200,
                {"compatibility": report},
                context,
                etag=self.service.resource_etag(report),
            )

        if path == f"{FLOW_API_PREFIX}/preview-plans":
            if method == "GET":
                self._query(query, allowed={"cursor", "limit"})
                cursor, limit = self._page_args(query)
                page = self.service.list_preview_plans(
                    cursor=cursor,
                    limit=limit,
                )
                payload = page.to_dict("preview_plans")
                return self._success(
                    200,
                    payload,
                    context,
                    etag=self.service.resource_etag(payload),
                )
            self._query(query, allowed=set())
            value = self._body(request)
            self._known_fields(value, _PREVIEW_FIELDS)
            plan_request = {
                key: item for key, item in value.items() if key != "idempotency_key"
            }
            plan = self.service.create_preview_plan(
                plan_request,
                idempotency_key=self._idempotency_key(request, value),
                context=context,
            )
            return self._success(
                201,
                {"preview_plan": plan},
                context,
                etag=self.service.resource_etag(plan),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/preview-plans/{_RESOURCE_ID}/run",
            path,
        )
        if match and method == "POST":
            self._query(query, allowed=set())
            self._known_fields(self._body(request, required=False), set())
            plan = self.service.execute_preview_plan(match.group(1), context=context)
            return self._success(
                200,
                {"preview_plan": plan},
                context,
                etag=self.service.resource_etag(plan),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/preview-plans/{_RESOURCE_ID}/cancel",
            path,
        )
        if match and method == "POST":
            self._query(query, allowed=set())
            self._known_fields(self._body(request, required=False), set())
            plan = self.service.cancel_preview_plan(match.group(1))
            return self._success(
                200,
                {"preview_plan": plan},
                context,
                etag=self.service.resource_etag(plan),
            )

        match = re.fullmatch(
            rf"{re.escape(FLOW_API_PREFIX)}/preview-plans/{_RESOURCE_ID}",
            path,
        )
        if match and method == "GET":
            self._query(query, allowed=set())
            plan = self.service.get_preview_plan(match.group(1))
            etag = self.service.resource_etag(plan)
            if _if_none_match(request.headers, etag):
                return self._not_modified(context, etag)
            return self._success(200, {"preview_plan": plan}, context, etag=etag)

        raise FlowServiceError(
            "FLOW_ROUTE_NOT_FOUND",
            "The flow API route does not exist.",
            http_status=404,
            details={"resource": "route"},
        )


def register_flow_routes(
    service: FlowService,
    registrar: Any | None = None,
    *,
    max_request_bytes: int = MAX_REQUEST_BYTES,
) -> FlowApi:
    """Build the dispatcher and optionally expose all route templates to one registrar."""

    api = FlowApi(service, max_request_bytes=max_request_bytes)
    if registrar is None:
        return api
    add_route = getattr(registrar, "add_route", None)
    if not callable(add_route):
        add_route = getattr(registrar, "register", None)
    if callable(add_route):
        for route in FLOW_ROUTES:
            add_route(route.method, route.path, api.handle)
        return api
    if callable(registrar):
        callback: Callable[..., Any] = registrar
        for route in FLOW_ROUTES:
            callback(route.method, route.path, api.handle)
        return api
    raise TypeError("registrar must be callable or expose add_route/register.")


def create_flow_api(
    service: FlowService,
    *,
    max_request_bytes: int = MAX_REQUEST_BYTES,
) -> FlowApi:
    return register_flow_routes(service, max_request_bytes=max_request_bytes)


build_flow_api = create_flow_api


__all__ = [
    "FLOW_API_PREFIX",
    "FLOW_ROUTES",
    "FlowApi",
    "FlowApiRequest",
    "FlowApiResponse",
    "FlowRoute",
    "MAX_QUERY_FIELDS",
    "MAX_REQUEST_BYTES",
    "MAX_REQUEST_TARGET_BYTES",
    "build_flow_api",
    "create_flow_api",
    "register_flow_routes",
]
