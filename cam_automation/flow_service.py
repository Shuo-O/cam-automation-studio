from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import hmac
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .asset_registry import AssetAccessDenied, AssetRegistryError
from .capability_registry import CapabilityRegistryError
from .flow_contracts import (
    canonical_hash,
    canonicalize,
    compute_artifact_hash,
    compute_capability_lock_hash,
)
from .flow_validation import (
    DEFAULT_RESOURCE_LIMITS,
    ResourceLimits,
    ValidationResult,
    validate_contract,
    validate_flow_graph,
)
from .version_compatibility import (
    classify_target_version,
    normalize_target_version,
)


SCHEMA_VERSION = 1
DEFAULT_MAX_PAGE_SIZE = 200
DEFAULT_MAX_DIFF_CHANGES = 5_000
ZERO_EXECUTION_FIELDS = {
    "commands_sent": 0,
    "journal_executed": False,
    "macro_executed": False,
    "machine_output_count": 0,
}

_HASH_FIELDS = (
    "source_snapshot_hash",
    "semantic_hash",
    "capability_lock_hash",
    "reviewed_recipe_hash",
)
_PREVIEW_TERMINAL_STATES = frozenset(
    {"blocked", "succeeded", "failed", "cancelled"}
)
_PREVIEW_TRANSITIONS = {
    "draft": frozenset({"validating", "cancelled"}),
    "validating": frozenset({"blocked", "ready", "failed", "cancelled"}),
    "ready": frozenset({"previewing", "cancelled"}),
    "previewing": frozenset({"succeeded", "failed", "cancelled"}),
    "blocked": frozenset(),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_SAVEABLE_CAPABILITY_CODES = frozenset(
    {
        "CAPABILITY_MISSING",
        "CAPABILITY_REVOKED",
        "CAPABILITY_VERSION_MISMATCH",
    }
)
_PROTECTED_GATES = frozenset(
    {
        "cam_simulation",
        "collision_check",
        "gouge_check",
        "machine_simulation",
        "shop_approval",
    }
)


def _json_copy(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise FlowServiceError(
            "FLOW_SCHEMA_INVALID",
            "The value must contain only finite JSON data.",
            http_status=400,
        ) from error
    return json.loads(encoded)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FlowServiceError(
            "FLOW_SCHEMA_INVALID",
            f"{field_name} must be a non-empty string.",
            http_status=400,
            details={"field": field_name},
        )
    result = value.strip()
    if any(ord(character) < 0x20 for character in result):
        raise FlowServiceError(
            "FLOW_SCHEMA_INVALID",
            f"{field_name} contains control characters.",
            http_status=400,
            details={"field": field_name},
        )
    return result


def _status_for_code(code: str) -> int:
    if code == "RESOURCE_LIMIT_EXCEEDED":
        return 413
    if code in {"FLOW_DEADLINE_EXCEEDED", "REQUEST_TIMEOUT"}:
        return 408
    if code.endswith("_NOT_FOUND") or code in {
        "ASSET_NOT_FOUND",
        "CAPABILITY_MISSING_RESOURCE",
        "FLOW_GRAPH_NOT_FOUND",
        "FLOW_VERSION_NOT_FOUND",
        "PREVIEW_PLAN_NOT_FOUND",
        "FIXTURE_TARGET_NOT_FOUND",
    }:
        return 404
    if code.startswith("ASSET_") and code not in {
        "ASSET_CONTENT_MISSING",
        "ASSET_CONTENT_TAMPERED",
    }:
        return 403
    if code in {
        "PREVIEW_PERMISSION_REVOKED",
        "CAPABILITY_REVOKED",
        "CAPABILITY_PERMISSION_NOT_DECLARED",
    }:
        return 403
    if code in {
        "FLOW_REVISION_CONFLICT",
        "FLOW_VERSION_CONFLICT",
        "FLOW_IDEMPOTENCY_CONFLICT",
        "FLOW_STATE_CONFLICT",
        "PREVIEW_HASH_MISMATCH",
        "PREVIEW_TARGET_AMBIGUOUS",
        "CAPABILITY_MISSING",
        "CAPABILITY_VERSION_MISMATCH",
        "REQUEST_CANCELLED",
    }:
        return 409
    if code in {
        "FLOW_SCHEMA_INVALID",
        "FLOW_UNKNOWN_SEMANTICS",
        "FLOW_CURSOR_INVALID",
        "FLOW_REQUEST_INVALID",
    }:
        return 400
    return 409


class FlowServiceError(ValueError):
    """A route-neutral service error with a stable code and HTTP recommendation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.http_status = int(http_status or _status_for_code(code))
        self.details = _json_copy(dict(details or {}))
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "http_status": self.http_status,
            "details": _json_copy(self.details),
        }


class FlowNotFoundError(FlowServiceError):
    pass


class FlowConflictError(FlowServiceError):
    pass


class FlowPermissionError(FlowServiceError):
    pass


class FlowResourceLimitError(FlowServiceError):
    pass


class FlowDeadlineExceeded(FlowServiceError):
    def __init__(self) -> None:
        super().__init__(
            "FLOW_DEADLINE_EXCEEDED",
            "The flow service deadline expired.",
            http_status=408,
        )


class FlowCancelledError(FlowServiceError):
    def __init__(self, reason: str = "request_cancelled") -> None:
        super().__init__(
            "REQUEST_CANCELLED",
            "The flow service request was cancelled.",
            http_status=409,
            details={"reason": reason},
        )


class CancellationToken:
    """A small cooperative cancellation token with no worker or transport handle."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = "request_cancelled"
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def cancel(self, reason: str = "request_cancelled") -> bool:
        normalized = _required_text(reason, "reason")
        with self._lock:
            changed = not self._event.is_set()
            if changed:
                self._reason = normalized
                self._event.set()
            return changed

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise FlowCancelledError(self.reason)


FlowCancellationToken = CancellationToken


@dataclass(frozen=True, slots=True)
class FlowRequestContext:
    correlation_id: str = ""
    deadline: datetime | None = None
    cancellation: CancellationToken | None = None


RequestContext = FlowRequestContext


@dataclass(frozen=True, slots=True)
class FixtureTarget:
    product: str
    target_version: str
    target_instance_id: str
    project_id: str
    project_snapshot_hash: str
    permissions: tuple[str, ...] = ()
    target_kind: str = "fixture"
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.product not in {"nx", "powermill"}:
            raise FlowServiceError(
                "FLOW_SCHEMA_INVALID",
                "Fixture target product must be nx or powermill.",
                http_status=400,
            )
        for field_name in (
            "target_version",
            "target_instance_id",
            "project_id",
            "project_snapshot_hash",
        ):
            _required_text(getattr(self, field_name), field_name)
        if self.target_kind != "fixture":
            raise FlowServiceError(
                "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                "Only explicit fixture targets are supported.",
                http_status=409,
            )
        object.__setattr__(
            self,
            "permissions",
            tuple(sorted({_required_text(item, "permissions") for item in self.permissions})),
        )
        object.__setattr__(self, "extensions", _json_copy(dict(self.extensions)))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FixtureTarget:
        if not isinstance(value, Mapping):
            raise FlowServiceError(
                "FLOW_SCHEMA_INVALID",
                "Fixture target must be a JSON object.",
                http_status=400,
            )
        return cls(
            product=str(value.get("product", "")),
            target_version=str(value.get("target_version", "")),
            target_instance_id=str(value.get("target_instance_id", "")),
            project_id=str(value.get("project_id", "")),
            project_snapshot_hash=str(value.get("project_snapshot_hash", "")),
            permissions=tuple(
                value.get("permissions", value.get("permission_categories", ()))
            ),
            target_kind=str(value.get("target_kind", "fixture")),
            extensions=dict(value.get("extensions", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "target_version": self.target_version,
            "target_instance_id": self.target_instance_id,
            "project_id": self.project_id,
            "project_snapshot_hash": self.project_snapshot_hash,
            "target_kind": "fixture",
            "permissions": list(self.permissions),
            "extensions": _json_copy(self.extensions),
        }


@dataclass(frozen=True, slots=True)
class ServicePage:
    items: tuple[Mapping[str, Any], ...]
    returned_count: int
    has_more: bool
    next_cursor: str | None

    def to_dict(self, item_name: str = "items") -> dict[str, Any]:
        return {
            "schema_version": 1,
            item_name: [_json_copy(item) for item in self.items],
            "returned_count": self.returned_count,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
        }


class FlowService:
    """Route-neutral CAM flow orchestration with no HTTP server or CAM transport."""

    def __init__(
        self,
        *,
        asset_registry: Any | None = None,
        capability_registry: Any | None = None,
        fixture_targets: Sequence[Mapping[str, Any] | FixtureTarget] = (),
        clock: Callable[[], datetime] | Any | None = None,
        limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
        max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
        max_diff_changes: int = DEFAULT_MAX_DIFF_CHANGES,
        cursor_secret: bytes | str = b"cam-flow-service-v1",
    ) -> None:
        self.asset_registry = asset_registry
        self.capability_registry = capability_registry
        self.limits = limits
        self.max_page_size = int(max_page_size)
        self.max_diff_changes = int(max_diff_changes)
        if self.max_page_size <= 0 or self.max_diff_changes <= 0:
            raise ValueError("Flow service limits must be positive.")
        self._clock = clock
        secret = cursor_secret.encode("utf-8") if isinstance(cursor_secret, str) else bytes(
            cursor_secret
        )
        if not secret:
            raise ValueError("cursor_secret must not be empty.")
        self._cursor_secret = secret
        self._lock = threading.RLock()
        self._idempotency: dict[tuple[str, str], tuple[str, Any]] = {}
        self._graph_revisions: dict[tuple[str, str], dict[str, Any]] = {}
        self._graph_heads: dict[str, str] = {}
        self._graph_order: list[str] = []
        self._graph_revision_order: list[tuple[str, str]] = []
        self._versions: dict[str, dict[str, Any]] = {}
        self._version_order: list[str] = []
        self._compatibility_reports: dict[str, dict[str, Any]] = {}
        self._compatibility_cache: dict[str, str] = {}
        self._preview_plans: dict[str, dict[str, Any]] = {}
        self._preview_plan_order: list[str] = []
        self._targets: dict[str, FixtureTarget] = {}
        for target in fixture_targets:
            self.register_fixture_target(target)

    def _now(self) -> datetime:
        source = self._clock
        if source is None:
            value = datetime.now(timezone.utc)
        elif hasattr(source, "now"):
            value = source.now()
        else:
            value = source()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise TypeError("Flow service clock must return a timezone-aware datetime.")
        return value.astimezone(timezone.utc)

    def _timestamp(self) -> str:
        return self._now().isoformat().replace("+00:00", "Z")

    def _checkpoint(self, context: FlowRequestContext | None) -> None:
        if context is None:
            return
        if context.cancellation is not None:
            context.cancellation.raise_if_cancelled()
        if context.deadline is not None:
            deadline = context.deadline
            if deadline.tzinfo is None:
                raise FlowServiceError(
                    "FLOW_SCHEMA_INVALID",
                    "Request deadline must include a timezone.",
                    http_status=400,
                    details={"field": "deadline"},
                )
            if self._now() >= deadline.astimezone(timezone.utc):
                raise FlowDeadlineExceeded()

    def check_request(self, context: FlowRequestContext | None = None) -> None:
        self._checkpoint(context)

    @staticmethod
    def resource_etag(value: Mapping[str, Any]) -> str:
        return f'"{compute_artifact_hash(value)}"'

    def _matches_etag(self, expected: str, actual: str) -> bool:
        normalized = expected.strip()
        if normalized == "*":
            return True
        if normalized == actual:
            return True
        if not normalized.startswith('"'):
            return f'"{normalized}"' == actual
        return False

    def _idempotent(
        self,
        operation: str,
        idempotency_key: str | None,
        payload: Mapping[str, Any],
        create: Callable[[], Any],
    ) -> Any:
        if idempotency_key is None:
            return create()
        key = _required_text(idempotency_key, "idempotency_key")
        fingerprint = canonical_hash(payload)
        identity = (operation, key)
        with self._lock:
            existing = self._idempotency.get(identity)
            if existing is not None:
                if existing[0] != fingerprint:
                    raise FlowConflictError(
                        "FLOW_IDEMPOTENCY_CONFLICT",
                        "The idempotency key was already used for another payload.",
                        http_status=409,
                        details={"operation": operation},
                    )
                return _json_copy(existing[1])
            value = create()
            self._idempotency[identity] = (fingerprint, _json_copy(value))
            return _json_copy(value)

    def _encode_cursor(
        self,
        collection: str,
        *,
        offset: int,
        snapshot_size: int,
        query_key: str,
    ) -> str:
        payload = canonicalize(
            {
                "v": 1,
                "collection": collection,
                "offset": offset,
                "snapshot_size": snapshot_size,
                "query_key": query_key,
            }
        )
        signature = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(payload + b"." + signature).decode("ascii")
        return token.rstrip("=")

    def _decode_cursor(
        self,
        cursor: str,
        *,
        collection: str,
        query_key: str,
    ) -> tuple[int, int]:
        try:
            padding = "=" * (-len(cursor) % 4)
            encoded = base64.urlsafe_b64decode(cursor + padding)
            payload, signature = encoded.rsplit(b".", 1)
            expected = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            value = json.loads(payload.decode("utf-8"))
            if (
                value.get("v") != 1
                or value.get("collection") != collection
                or value.get("query_key") != query_key
            ):
                raise ValueError("scope")
            offset = int(value["offset"])
            snapshot_size = int(value["snapshot_size"])
            if offset < 0 or snapshot_size < offset:
                raise ValueError("range")
            return offset, snapshot_size
        except (
            binascii.Error,
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise FlowServiceError(
                "FLOW_CURSOR_INVALID",
                "The cursor is invalid or belongs to another query.",
                http_status=400,
            ) from error

    def _page(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        collection: str,
        cursor: str | None,
        limit: int,
        query: Mapping[str, Any] | None = None,
    ) -> ServicePage:
        if isinstance(limit, bool) or not 1 <= int(limit) <= self.max_page_size:
            raise FlowServiceError(
                "FLOW_SCHEMA_INVALID",
                f"limit must be between 1 and {self.max_page_size}.",
                http_status=400,
                details={"field": "limit", "max_items": self.max_page_size},
            )
        page_size = int(limit)
        query_key = canonical_hash(dict(query or {}))
        offset = 0
        snapshot_size = len(items)
        if cursor:
            offset, snapshot_size = self._decode_cursor(
                cursor,
                collection=collection,
                query_key=query_key,
            )
            if snapshot_size > len(items):
                raise FlowServiceError(
                    "FLOW_CURSOR_INVALID",
                    "The cursor snapshot is no longer available.",
                    http_status=400,
                )
        stop = min(offset + page_size, snapshot_size)
        page_items = tuple(_json_copy(item) for item in items[offset:stop])
        has_more = stop < snapshot_size
        next_cursor = (
            self._encode_cursor(
                collection,
                offset=stop,
                snapshot_size=snapshot_size,
                query_key=query_key,
            )
            if has_more
            else None
        )
        return ServicePage(page_items, len(page_items), has_more, next_cursor)

    def register_fixture_target(
        self,
        target: Mapping[str, Any] | FixtureTarget,
    ) -> dict[str, Any]:
        descriptor = (
            target if isinstance(target, FixtureTarget) else FixtureTarget.from_dict(target)
        )
        with self._lock:
            existing = self._targets.get(descriptor.target_instance_id)
            if existing is not None and existing != descriptor:
                raise FlowConflictError(
                    "FLOW_STATE_CONFLICT",
                    "A fixture target identity cannot be overwritten.",
                    http_status=409,
                    details={"target_instance_id": descriptor.target_instance_id},
                )
            self._targets[descriptor.target_instance_id] = descriptor
            return descriptor.to_dict()

    def list_fixture_targets(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(
                self._targets[key].to_dict() for key in sorted(self._targets)
            )

    def revoke_fixture_permission(
        self,
        target_instance_id: str,
        category: str,
    ) -> dict[str, Any]:
        instance_id = _required_text(target_instance_id, "target_instance_id")
        permission = _required_text(category, "category")
        with self._lock:
            target = self._targets.get(instance_id)
            if target is None:
                raise FlowNotFoundError(
                    "FIXTURE_TARGET_NOT_FOUND",
                    "The fixture target does not exist.",
                    http_status=404,
                    details={"target_instance_id": instance_id},
                )
            permissions = tuple(item for item in target.permissions if item != permission)
            changed = permissions != target.permissions
            self._targets[instance_id] = FixtureTarget(
                product=target.product,
                target_version=target.target_version,
                target_instance_id=target.target_instance_id,
                project_id=target.project_id,
                project_snapshot_hash=target.project_snapshot_hash,
                permissions=permissions,
                extensions=target.extensions,
            )
            cancelled: list[str] = []
            if changed:
                for plan_id in self._preview_plan_order:
                    plan = self._preview_plans[plan_id]
                    if (
                        plan["target"]["target_instance_id"] == instance_id
                        and plan["status"] not in _PREVIEW_TERMINAL_STATES
                    ):
                        self._set_plan_status_locked(
                            plan_id,
                            "cancelled",
                            expected_status=plan["status"],
                            diagnostic_code="PREVIEW_PERMISSION_REVOKED",
                        )
                        cancelled.append(plan_id)
            return {
                "schema_version": 1,
                "target_instance_id": instance_id,
                "category": permission,
                "changed": changed,
                "cancelled_plan_ids": cancelled,
                "auto_resumed": False,
            }

    def grant_fixture_permission(
        self,
        target_instance_id: str,
        category: str,
    ) -> dict[str, Any]:
        instance_id = _required_text(target_instance_id, "target_instance_id")
        permission = _required_text(category, "category")
        with self._lock:
            target = self._targets.get(instance_id)
            if target is None:
                raise FlowNotFoundError(
                    "FIXTURE_TARGET_NOT_FOUND",
                    "The fixture target does not exist.",
                    http_status=404,
                    details={"target_instance_id": instance_id},
                )
            permissions = tuple(sorted(set(target.permissions) | {permission}))
            changed = permissions != target.permissions
            self._targets[instance_id] = FixtureTarget(
                product=target.product,
                target_version=target.target_version,
                target_instance_id=target.target_instance_id,
                project_id=target.project_id,
                project_snapshot_hash=target.project_snapshot_hash,
                permissions=permissions,
                extensions=target.extensions,
            )
            return {
                "schema_version": 1,
                "target_instance_id": instance_id,
                "category": permission,
                "changed": changed,
                "resumed_plan_ids": [],
                "auto_resumed": False,
            }

    def _resolve_target(self, value: Mapping[str, Any]) -> FixtureTarget:
        if not isinstance(value, Mapping):
            raise FlowServiceError(
                "FLOW_SCHEMA_INVALID",
                "target must be a JSON object.",
                http_status=400,
                details={"field": "target"},
            )
        instance_id = value.get("target_instance_id")
        if not isinstance(instance_id, str) or not instance_id.strip():
            candidates = [
                target
                for target in self._targets.values()
                if (
                    (not value.get("product") or target.product == value.get("product"))
                    and (
                        not value.get("target_version")
                        or target.target_version == value.get("target_version")
                    )
                    and (
                        not value.get("project_id")
                        or target.project_id == value.get("project_id")
                    )
                )
            ]
            raise FlowConflictError(
                "PREVIEW_TARGET_AMBIGUOUS",
                "One explicit fixture target instance is required.",
                http_status=409,
                details={"candidate_count": len(candidates)},
            )
        with self._lock:
            target = self._targets.get(instance_id.strip())
        if target is None:
            raise FlowNotFoundError(
                "FIXTURE_TARGET_NOT_FOUND",
                "The fixture target does not exist.",
                http_status=404,
                details={"target_instance_id": instance_id.strip()},
            )
        supplied = dict(value)
        expected = target.to_dict()
        for field_name in (
            "product",
            "target_version",
            "target_instance_id",
            "project_id",
            "project_snapshot_hash",
            "target_kind",
        ):
            if supplied.get(field_name) != expected[field_name]:
                raise FlowConflictError(
                    "PREVIEW_TARGET_AMBIGUOUS",
                    "The fixture target binding does not match the registered snapshot.",
                    http_status=409,
                    details={"field": field_name},
                )
        return target

    def _require_asset_registry(self) -> Any:
        if self.asset_registry is None:
            raise FlowServiceError(
                "FLOW_SERVICE_UNAVAILABLE",
                "The asset registry is not configured.",
                http_status=500,
            )
        return self.asset_registry

    def _require_capability_registry(self) -> Any:
        if self.capability_registry is None:
            raise FlowServiceError(
                "FLOW_SERVICE_UNAVAILABLE",
                "The capability registry is not configured.",
                http_status=500,
            )
        return self.capability_registry

    def register_asset(
        self,
        content: bytes | bytearray | memoryview,
        *,
        idempotency_key: str | None = None,
        context: FlowRequestContext | None = None,
        **metadata: Any,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        raw = bytes(content)
        payload = {"content_hash": hashlib.sha256(raw).hexdigest(), **_json_copy(metadata)}

        def create() -> dict[str, Any]:
            self._checkpoint(context)
            return self._require_asset_registry().register_asset(raw, **metadata)

        return self._idempotent("asset.create", idempotency_key, payload, create)

    create_asset = register_asset

    def get_asset(self, asset_revision_id: str) -> dict[str, Any]:
        return self._require_asset_registry().get_revision(asset_revision_id)

    def list_assets(
        self,
        *,
        asset_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ServicePage:
        registry = self._require_asset_registry()
        items = registry.list_revisions(asset_id)
        return self._page(
            items,
            collection="assets",
            cursor=cursor,
            limit=limit,
            query={"asset_id": asset_id},
        )

    def register_capability(
        self,
        manifest: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        payload = _json_copy(manifest)

        def create() -> dict[str, Any]:
            self._checkpoint(context)
            return self._require_capability_registry().register_manifest(manifest)

        return self._idempotent(
            "capability.create",
            idempotency_key,
            payload,
            create,
        )

    create_capability = register_capability

    def get_capability(
        self,
        manifest_id: str,
        *,
        manifest_version: str | None = None,
        manifest_hash: str | None = None,
    ) -> dict[str, Any]:
        return self._require_capability_registry().get_manifest(
            manifest_id,
            manifest_version=manifest_version,
            manifest_hash=manifest_hash,
        )

    def list_capabilities(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ServicePage:
        items = self._require_capability_registry().list_manifests()
        return self._page(
            items,
            collection="capabilities",
            cursor=cursor,
            limit=limit,
        )

    def _manifests(self) -> list[dict[str, Any]] | None:
        if self.capability_registry is None:
            return None
        return self.capability_registry.list_manifests()

    def _raise_for_validation(
        self,
        result: ValidationResult,
        *,
        allowed_codes: frozenset[str] = frozenset(),
    ) -> None:
        diagnostics = [
            item
            for item in result.diagnostics
            if item.severity in {"blocker", "error"} and item.code not in allowed_codes
        ]
        if not diagnostics:
            return
        first = diagnostics[0]
        raise FlowServiceError(
            first.code,
            first.message,
            http_status=_status_for_code(first.code),
            details={
                "object_ref": first.object_ref,
                "source_mapping_ids": list(first.source_mapping_ids),
            },
        )

    def save_graph(
        self,
        graph: Mapping[str, Any],
        *,
        expected_revision_id: str | None = None,
        if_match: str | None = None,
        idempotency_key: str | None = None,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        value = _json_copy(graph)
        if not isinstance(value, Mapping):
            raise FlowServiceError(
                "FLOW_SCHEMA_INVALID",
                "FlowGraph must be a JSON object.",
                http_status=400,
            )
        validation = validate_flow_graph(
            value,
            capability_manifests=self._manifests(),
            limits=self.limits,
        )
        self._raise_for_validation(
            validation,
            allowed_codes=_SAVEABLE_CAPABILITY_CODES,
        )
        graph_id = _required_text(value.get("graph_id"), "graph_id")
        revision_id = _required_text(value.get("revision_id"), "revision_id")
        payload = {
            "graph": value,
            "expected_revision_id": expected_revision_id,
            "if_match": if_match,
        }

        def create() -> dict[str, Any]:
            self._checkpoint(context)
            identity = (graph_id, revision_id)
            with self._lock:
                duplicate = self._graph_revisions.get(identity)
                if duplicate is not None:
                    if canonicalize(duplicate) != canonicalize(value):
                        raise FlowConflictError(
                            "FLOW_REVISION_CONFLICT",
                            "An immutable graph revision cannot be overwritten.",
                            http_status=409,
                            details={"graph_id": graph_id, "revision_id": revision_id},
                        )
                    return _json_copy(duplicate)

                head_id = self._graph_heads.get(graph_id)
                parent_id = value.get("parent_revision_id")
                if head_id is None:
                    if parent_id is not None:
                        raise FlowConflictError(
                            "FLOW_REVISION_CONFLICT",
                            "A new graph must have a null parent revision.",
                            http_status=409,
                            details={"graph_id": graph_id},
                        )
                    if expected_revision_id is not None or if_match not in {None, "*"}:
                        raise FlowConflictError(
                            "FLOW_REVISION_CONFLICT",
                            "The graph does not yet have a matching revision.",
                            http_status=409,
                            details={"graph_id": graph_id},
                        )
                    self._graph_order.append(graph_id)
                else:
                    head = self._graph_revisions[(graph_id, head_id)]
                    head_etag = self.resource_etag(head)
                    if expected_revision_id is not None and expected_revision_id != head_id:
                        raise FlowConflictError(
                            "FLOW_REVISION_CONFLICT",
                            "The expected graph revision is stale.",
                            http_status=409,
                            details={
                                "graph_id": graph_id,
                                "expected_revision_id": expected_revision_id,
                                "actual_revision_id": head_id,
                            },
                        )
                    if if_match is not None and not self._matches_etag(if_match, head_etag):
                        raise FlowConflictError(
                            "FLOW_REVISION_CONFLICT",
                            "The If-Match ETag is stale.",
                            http_status=409,
                            details={"graph_id": graph_id, "actual_revision_id": head_id},
                        )
                    if parent_id != head_id:
                        raise FlowConflictError(
                            "FLOW_REVISION_CONFLICT",
                            "The graph parent revision is not the current head.",
                            http_status=409,
                            details={
                                "graph_id": graph_id,
                                "expected_revision_id": head_id,
                                "actual_revision_id": parent_id,
                            },
                        )
                self._graph_revisions[identity] = _json_copy(value)
                self._graph_heads[graph_id] = revision_id
                self._graph_revision_order.append(identity)
                return _json_copy(value)

        return self._idempotent("graph.save", idempotency_key, payload, create)

    create_graph = save_graph
    put_graph = save_graph

    def get_graph(
        self,
        graph_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        logical_id = _required_text(graph_id, "graph_id")
        with self._lock:
            resolved_revision = revision_id or self._graph_heads.get(logical_id)
            value = (
                self._graph_revisions.get((logical_id, resolved_revision))
                if resolved_revision is not None
                else None
            )
            if value is None:
                raise FlowNotFoundError(
                    "FLOW_GRAPH_NOT_FOUND",
                    "The requested graph revision does not exist.",
                    http_status=404,
                    details={
                        "graph_id": logical_id,
                        "revision_id": revision_id,
                    },
                )
            return _json_copy(value)

    def graph_etag(self, graph_id: str, revision_id: str | None = None) -> str:
        return self.resource_etag(self.get_graph(graph_id, revision_id))

    def list_graphs(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ServicePage:
        with self._lock:
            items = [
                {
                    "graph_id": graph_id,
                    "revision_id": self._graph_heads[graph_id],
                    "etag": self.resource_etag(
                        self._graph_revisions[
                            (graph_id, self._graph_heads[graph_id])
                        ]
                    ),
                    "graph": _json_copy(
                        self._graph_revisions[
                            (graph_id, self._graph_heads[graph_id])
                        ]
                    ),
                }
                for graph_id in self._graph_order
            ]
        return self._page(
            items,
            collection="graphs",
            cursor=cursor,
            limit=limit,
        )

    def list_graph_revisions(
        self,
        graph_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ServicePage:
        logical_id = _required_text(graph_id, "graph_id")
        with self._lock:
            items = [
                self._graph_revisions[identity]
                for identity in self._graph_revision_order
                if identity[0] == logical_id
            ]
        if not items and logical_id not in self._graph_heads:
            raise FlowNotFoundError(
                "FLOW_GRAPH_NOT_FOUND",
                "The requested graph does not exist.",
                http_status=404,
                details={"graph_id": logical_id},
            )
        return self._page(
            items,
            collection="graph_revisions",
            cursor=cursor,
            limit=limit,
            query={"graph_id": logical_id},
        )

    def validate(
        self,
        graph: Mapping[str, Any] | None = None,
        *,
        graph_id: str | None = None,
        revision_id: str | None = None,
        require_known_semantics: bool = False,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        value = (
            self.get_graph(_required_text(graph_id, "graph_id"), revision_id)
            if graph is None
            else _json_copy(graph)
        )
        result = validate_flow_graph(
            value,
            capability_manifests=self._manifests(),
            require_known_semantics=require_known_semantics,
            limits=self.limits,
        )
        self._checkpoint(context)
        return result.to_dict()

    validate_graph = validate

    def test_graph(
        self,
        graph_id: str,
        revision_id: str | None = None,
        *,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        graph = self.get_graph(graph_id, revision_id)
        validation = self.validate(graph, context=context)
        for flow in graph.get("flows", []):
            self._checkpoint(context)
            if isinstance(flow, Mapping):
                for _node in flow.get("nodes", []):
                    self._checkpoint(context)
        status = "passed" if validation["valid"] else "blocked"
        return {
            "schema_version": 1,
            "graph_id": graph["graph_id"],
            "revision_id": graph["revision_id"],
            "status": status,
            "deterministic": True,
            "checks": [
                {
                    "name": "schema_graph_safety",
                    "status": status,
                },
                {
                    "name": "fixture_transport_absent",
                    "status": "passed",
                },
            ],
            "diagnostics": validation["diagnostics"],
            "transport": "none",
            **ZERO_EXECUTION_FIELDS,
        }

    run_test = test_graph

    def _diff_values(
        self,
        before: Any,
        after: Any,
        *,
        path: str,
        changes: list[dict[str, Any]],
    ) -> None:
        def append(change: dict[str, Any]) -> None:
            if len(changes) >= self.max_diff_changes:
                raise FlowResourceLimitError(
                    "RESOURCE_LIMIT_EXCEEDED",
                    "The diff exceeds the configured change limit.",
                    http_status=413,
                    details={"max_items": self.max_diff_changes},
                )
            changes.append(change)

        if isinstance(before, Mapping) and isinstance(after, Mapping):
            for key in sorted(set(before) | set(after), key=str):
                token = str(key).replace("~", "~0").replace("/", "~1")
                child_path = f"{path}/{token}"
                if key not in before:
                    append(
                        {"path": child_path, "kind": "added", "after": _json_copy(after[key])}
                    )
                elif key not in after:
                    append(
                        {
                            "path": child_path,
                            "kind": "removed",
                            "before": _json_copy(before[key]),
                        }
                    )
                else:
                    self._diff_values(
                        before[key],
                        after[key],
                        path=child_path,
                        changes=changes,
                    )
            return
        if isinstance(before, list) and isinstance(after, list):
            for index in range(max(len(before), len(after))):
                child_path = f"{path}/{index}"
                if index >= len(before):
                    append(
                        {
                            "path": child_path,
                            "kind": "added",
                            "after": _json_copy(after[index]),
                        }
                    )
                elif index >= len(after):
                    append(
                        {
                            "path": child_path,
                            "kind": "removed",
                            "before": _json_copy(before[index]),
                        }
                    )
                else:
                    self._diff_values(
                        before[index],
                        after[index],
                        path=child_path,
                        changes=changes,
                    )
            return
        if before != after:
            append(
                {
                    "path": path or "/",
                    "kind": "changed",
                    "before": _json_copy(before),
                    "after": _json_copy(after),
                }
            )

    def diff_graphs(
        self,
        graph_id: str,
        from_revision_id: str,
        to_revision_id: str,
        *,
        include_layout: bool = False,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        before = self.get_graph(graph_id, from_revision_id)
        after = self.get_graph(graph_id, to_revision_id)
        if not include_layout:
            before.pop("layout", None)
            after.pop("layout", None)
        changes: list[dict[str, Any]] = []
        self._diff_values(before, after, path="", changes=changes)
        self._checkpoint(context)
        fingerprint = canonical_hash(
            {
                "graph_id": graph_id,
                "from_revision_id": from_revision_id,
                "to_revision_id": to_revision_id,
                "include_layout": include_layout,
                "changes": changes,
            }
        )
        return {
            "schema_version": 1,
            "diff_id": f"flow-diff:{fingerprint[7:31]}",
            "graph_id": graph_id,
            "from_revision_id": from_revision_id,
            "to_revision_id": to_revision_id,
            "include_layout": include_layout,
            "status": "no_change" if not changes else "changes_detected",
            "changes": changes,
        }

    diff = diff_graphs

    def create_version(
        self,
        version: Mapping[str, Any] | None = None,
        *,
        graph_id: str | None = None,
        revision_id: str | None = None,
        parent_version_ids: Sequence[str] = (),
        status: str = "draft",
        author_ref: str = "local:operator",
        message: str = "",
        round_trip_report_id: str | None = None,
        compatibility_report_id: str | None = None,
        reviewed_recipe_hash: str | None = None,
        version_id: str | None = None,
        extensions: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        if version is not None:
            value = _json_copy(version)
            graph_id = _required_text(value.get("graph_id"), "graph_id")
            revision_id = _required_text(value.get("revision_id"), "revision_id")
        else:
            logical_id = _required_text(graph_id, "graph_id")
            graph = self.get_graph(logical_id, revision_id)
            revision_id = graph["revision_id"]
            normalized_status = _required_text(status, "status")
            if normalized_status not in {
                "draft",
                "review_required",
                "reviewed_for_fixture",
                "retired",
            }:
                raise FlowServiceError(
                    "FLOW_SCHEMA_INVALID",
                    "FlowVersion status is invalid.",
                    http_status=400,
                    details={"field": "status"},
                )
            if normalized_status == "reviewed_for_fixture" and (
                not round_trip_report_id or not compatibility_report_id
            ):
                raise FlowServiceError(
                    "FLOW_SCHEMA_INVALID",
                    "Reviewed fixture versions require report references.",
                    http_status=400,
                )
            extension_value = _json_copy(dict(extensions or {}))
            if reviewed_recipe_hash is not None:
                extension_value["cam.flow.api/reviewed_recipe_hash"] = {
                    "semantic": True,
                    "value": reviewed_recipe_hash,
                }
            semantics = {
                "graph_id": logical_id,
                "revision_id": revision_id,
                "parent_version_ids": sorted(set(parent_version_ids)),
                "semantic_hash": graph["semantic_hash"],
                "artifact_hash": compute_artifact_hash(graph),
                "source_snapshot_hash": graph["source_snapshot_hash"],
                "capability_lock_hash": compute_capability_lock_hash(graph),
                "status": normalized_status,
                "author_ref": _required_text(author_ref, "author_ref"),
                "message": str(message),
                "round_trip_report_id": (
                    round_trip_report_id
                    or f"roundtrip:not-checked:{revision_id}"
                ),
                "compatibility_report_id": (
                    compatibility_report_id
                    or f"compat:not-checked:{revision_id}"
                ),
                "extensions": extension_value,
            }
            resolved_version_id = version_id or (
                "flow-version:" + canonical_hash(semantics)[7:31]
            )
            value = {
                "schema_version": 1,
                "contract": "cam.flow_version.v1",
                "version_id": resolved_version_id,
                **semantics,
                "created_at": self._timestamp(),
                "immutable": True,
            }
            graph_id = logical_id
        validation = validate_contract(value, "flow_version", limits=self.limits)
        self._raise_for_validation(validation)
        graph = self.get_graph(str(graph_id), str(revision_id))
        expected_hashes = {
            "semantic_hash": graph["semantic_hash"],
            "artifact_hash": compute_artifact_hash(graph),
            "source_snapshot_hash": graph["source_snapshot_hash"],
            "capability_lock_hash": compute_capability_lock_hash(graph),
        }
        mismatched = sorted(
            key for key, expected in expected_hashes.items() if value.get(key) != expected
        )
        if mismatched:
            raise FlowConflictError(
                "PREVIEW_HASH_MISMATCH",
                "FlowVersion hashes do not match the immutable graph revision.",
                http_status=409,
                details={"fields": mismatched},
            )
        payload = {"version": value}

        def create() -> dict[str, Any]:
            identity = _required_text(value.get("version_id"), "version_id")
            with self._lock:
                existing = self._versions.get(identity)
                if existing is not None:
                    if canonicalize(existing) != canonicalize(value):
                        raise FlowConflictError(
                            "FLOW_VERSION_CONFLICT",
                            "An immutable flow version cannot be overwritten.",
                            http_status=409,
                            details={"version_id": identity},
                        )
                    return _json_copy(existing)
                self._versions[identity] = _json_copy(value)
                self._version_order.append(identity)
                return _json_copy(value)

        return self._idempotent("version.create", idempotency_key, payload, create)

    save_version = create_version

    def get_version(self, version_id: str) -> dict[str, Any]:
        identity = _required_text(version_id, "version_id")
        with self._lock:
            value = self._versions.get(identity)
            if value is None:
                raise FlowNotFoundError(
                    "FLOW_VERSION_NOT_FOUND",
                    "The requested flow version does not exist.",
                    http_status=404,
                    details={"version_id": identity},
                )
            return _json_copy(value)

    def list_versions(
        self,
        *,
        graph_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ServicePage:
        with self._lock:
            items = [
                self._versions[version_id]
                for version_id in self._version_order
                if graph_id is None or self._versions[version_id]["graph_id"] == graph_id
            ]
        return self._page(
            items,
            collection="versions",
            cursor=cursor,
            limit=limit,
            query={"graph_id": graph_id},
        )

    def _capability_state(
        self,
        graph: Mapping[str, Any],
        target: FixtureTarget,
    ) -> tuple[
        list[dict[str, Any]],
        set[str],
        set[str],
        list[dict[str, Any]],
    ]:
        issues: list[dict[str, Any]] = []
        checked_hashes: set[str] = set()
        required_permissions: set[str] = set()
        version_results: list[dict[str, Any]] = []
        registry = self.capability_registry
        for lock in graph.get("capability_lock", []):
            if not isinstance(lock, Mapping):
                continue
            manifest_hash = str(lock.get("manifest_hash", ""))
            if manifest_hash:
                checked_hashes.add(manifest_hash)
            if registry is None:
                issues.append(
                    {
                        "code": "CAPABILITY_MISSING",
                        "severity": "blocker",
                        "message": "The exact capability registry is unavailable.",
                        "object_ref": f"manifest:{lock.get('manifest_id', 'unknown')}",
                        "source_mapping_ids": [],
                        "remediation": "Register the exact fixture capability lock.",
                    }
                )
                continue
            state = registry.lock_status(lock)
            if not state["available"]:
                issues.append(
                    {
                        "code": state["code"],
                        "severity": "blocker",
                        "message": "The exact capability lock is unavailable.",
                        "object_ref": f"manifest:{lock.get('manifest_id', 'unknown')}",
                        "source_mapping_ids": [],
                        "remediation": "Re-register and explicitly authorize the exact lock.",
                    }
                )
                continue
            try:
                manifest = registry.get_manifest(
                    str(lock.get("manifest_id", "")),
                    manifest_version=str(lock.get("manifest_version", "")),
                    manifest_hash=manifest_hash,
                )
            except CapabilityRegistryError:
                continue
            required_permissions.update(str(item) for item in manifest.get("permissions", []))
            version_result = classify_target_version(
                target.target_version,
                manifest,
            )
            version_results.append(
                {
                    "manifest_id": str(lock.get("manifest_id", "")),
                    **version_result.to_dict(),
                }
            )
            if version_result.tier == "review_required":
                issues.append(
                    {
                        "code": "COMPAT_TARGET_VERSION_REVIEW_REQUIRED",
                        "severity": "warning",
                        "message": (
                            "The target version is readable but has not passed "
                            "fixture verification for this capability."
                        ),
                        "object_ref": f"manifest:{lock.get('manifest_id', 'unknown')}",
                        "source_mapping_ids": [],
                        "remediation": (
                            "Review version-specific symbols, selectors, units, "
                            "round-trip evidence, and fixture results."
                        ),
                        "evidence": [
                            target.target_version,
                            version_result.normalized_version,
                            version_result.matched_range,
                        ],
                    }
                )
            elif version_result.tier == "opaque_only":
                issues.append(
                    {
                        "code": "COMPAT_TARGET_VERSION_OPAQUE_ONLY",
                        "severity": "blocker",
                        "message": (
                            "The target version is limited to opaque source "
                            "preservation and cannot be previewed."
                        ),
                        "object_ref": f"manifest:{lock.get('manifest_id', 'unknown')}",
                        "source_mapping_ids": [],
                        "remediation": (
                            "Keep the source readable and collect version-specific "
                            "fixtures before semantic editing."
                        ),
                    }
                )
                issues.append(
                    {
                        "code": "CAPABILITY_VERSION_MISMATCH",
                        "severity": "blocker",
                        "message": (
                            "The target version is outside the capability's "
                            "verified ranges."
                        ),
                        "object_ref": f"manifest:{lock.get('manifest_id', 'unknown')}",
                        "source_mapping_ids": [],
                        "remediation": "Use an explicitly verified target version.",
                    }
                )
            elif version_result.tier in {"unsupported", "unknown"}:
                issues.append(
                    {
                        "code": "COMPAT_TARGET_VERSION_UNSUPPORTED",
                        "severity": "blocker",
                        "message": (
                            "The target version is outside every declared "
                            "compatibility tier."
                        ),
                        "object_ref": f"manifest:{lock.get('manifest_id', 'unknown')}",
                        "source_mapping_ids": [],
                        "remediation": (
                            "Add reviewed version evidence or use an explicitly "
                            "classified target version."
                        ),
                    }
                )
                issues.append(
                    {
                        "code": "CAPABILITY_VERSION_MISMATCH",
                        "severity": "blocker",
                        "message": (
                            "The target version is outside the capability's "
                            "verified ranges."
                        ),
                        "object_ref": f"manifest:{lock.get('manifest_id', 'unknown')}",
                        "source_mapping_ids": [],
                        "remediation": "Use an explicitly verified target version.",
                    }
                )
        missing_permissions = required_permissions - set(target.permissions)
        if missing_permissions:
            issues.append(
                {
                    "code": "PREVIEW_PERMISSION_REVOKED",
                    "severity": "blocker",
                    "message": "A required fixture permission is not active.",
                    "object_ref": f"target:{target.target_instance_id}",
                    "source_mapping_ids": [],
                    "remediation": "Create a new explicit grant before another request.",
                }
            )
        return (
            issues,
            checked_hashes,
            required_permissions,
            sorted(
                version_results,
                key=lambda item: (
                    str(item.get("manifest_id", "")),
                    str(item.get("normalized_version", "")),
                ),
            ),
        )

    @staticmethod
    def _selector_results(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for flow in graph.get("flows", []):
            if not isinstance(flow, Mapping):
                continue
            for node in flow.get("nodes", []):
                if not isinstance(node, Mapping):
                    continue
                for binding in node.get("bindings", []):
                    if not isinstance(binding, Mapping):
                        continue
                    literal = binding.get("literal")
                    if not isinstance(literal, Mapping):
                        continue
                    if literal.get("type_ref") != "cam.object_selector":
                        continue
                    selector = literal.get("value")
                    count = (
                        selector.get(
                            "match_count",
                            selector.get("resolved_count", selector.get("matches")),
                        )
                        if isinstance(selector, Mapping)
                        else None
                    )
                    status = "supported" if count == 1 else "ambiguous"
                    results.append(
                        {
                            "node_id": node.get("node_id"),
                            "binding_id": binding.get("binding_id"),
                            "status": status,
                            "match_count": count,
                        }
                    )
        return sorted(
            results,
            key=lambda item: (
                str(item.get("node_id", "")),
                str(item.get("binding_id", "")),
            ),
        )

    def check_compatibility(
        self,
        graph_id: str,
        target: Mapping[str, Any],
        *,
        revision_id: str | None = None,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        graph = self.get_graph(graph_id, revision_id)
        fixture = self._resolve_target(target)
        issues: list[dict[str, Any]] = []
        if graph["product"] != fixture.product:
            issues.append(
                {
                    "code": "FLOW_PRODUCT_MIXED",
                    "severity": "blocker",
                    "message": "The graph and target products do not match.",
                    "object_ref": f"graph:{graph['graph_id']}",
                    "source_mapping_ids": [],
                    "remediation": "Select a fixture target for the graph product.",
                }
            )
        target_versions = graph.get("target_versions", [])
        if not target_versions:
            issues.append(
                {
                    "code": "COMPAT_TARGET_VERSION_UNKNOWN",
                    "severity": "blocker",
                    "message": "The graph does not bind a reviewed target version.",
                    "object_ref": f"graph:{graph['graph_id']}",
                    "source_mapping_ids": [],
                    "remediation": "Create a revision with an explicit target version.",
                }
            )
        elif normalize_target_version(
            fixture.target_version,
            fixture.product,
        ) not in {
            normalize_target_version(str(item), fixture.product)
            for item in target_versions
        }:
            issues.append(
                {
                    "code": "CAPABILITY_VERSION_MISMATCH",
                    "severity": "blocker",
                    "message": "The fixture target version is outside the graph revision.",
                    "object_ref": f"graph:{graph['graph_id']}",
                    "source_mapping_ids": [],
                    "remediation": "Select a reviewed graph and target version pair.",
                }
            )

        validation = validate_flow_graph(
            graph,
            capability_manifests=self._manifests(),
            limits=self.limits,
        )
        issues.extend(item.to_dict() for item in validation.diagnostics)
        (
            capability_issues,
            checked_hashes,
            _permissions,
            version_results,
        ) = self._capability_state(graph, fixture)
        issues.extend(capability_issues)
        selector_results = self._selector_results(graph)
        if any(item["status"] != "supported" for item in selector_results):
            issues.append(
                {
                    "code": "COMPAT_SELECTOR_UNRESOLVED",
                    "severity": "blocker",
                    "message": "A fixture selector does not resolve to exactly one object.",
                    "object_ref": f"graph:{graph['graph_id']}",
                    "source_mapping_ids": [],
                    "remediation": "Resolve one typed selector target without guessing.",
                }
            )

        node_results: list[dict[str, Any]] = []
        tier_order = {
            "verified": 0,
            "review_required": 1,
            "opaque_only": 2,
            "unsupported": 3,
            "unknown": 4,
        }
        target_version_tier = max(
            (
                str(item.get("tier", "unknown"))
                for item in version_results
            ),
            key=lambda item: tier_order.get(item, tier_order["unknown"]),
            default="verified",
        )
        issue_refs = {
            str(item.get("object_ref", ""))
            for item in issues
            if item.get("severity") in {"blocker", "error"}
        }
        for flow in graph.get("flows", []):
            if not isinstance(flow, Mapping):
                continue
            for node in flow.get("nodes", []):
                if not isinstance(node, Mapping):
                    continue
                node_id = str(node.get("node_id", ""))
                incompatible = (
                    f"node:{node_id}" in issue_refs
                    or node.get("risk") == "blocked"
                    or node.get("compatibility_status")
                    in {"unsupported", "capability_unavailable"}
                    or node.get("opaque") is not None
                )
                status = "unsupported" if incompatible else "supported"
                if (
                    status == "supported"
                    and target_version_tier == "review_required"
                ):
                    status = "needs_review"
                elif target_version_tier in {
                    "opaque_only",
                    "unsupported",
                    "unknown",
                }:
                    status = "unsupported"
                node_results.append({"node_id": node_id, "status": status})
        node_results.sort(key=lambda item: item["node_id"])
        blocker_codes = sorted(
            {
                str(item.get("code"))
                for item in issues
                if item.get("severity") in {"blocker", "error"}
            }
        )
        report_status = (
            "incompatible"
            if blocker_codes
            else (
                "needs_review"
                if (
                    any(item.get("severity") == "warning" for item in issues)
                    or any(
                        item.get("status") == "needs_review"
                        for item in node_results
                    )
                )
                else "compatible"
            )
        )
        stable_issues = sorted(
            {_stable_issue_key(item): _json_copy(item) for item in issues}.values(),
            key=_stable_issue_key,
        )
        state = {
            "graph": graph,
            "target": fixture.to_dict(),
            "manifests": self._manifests() or [],
            "issues": stable_issues,
            "node_results": node_results,
            "selector_results": selector_results,
            "version_results": version_results,
        }
        state_hash = canonical_hash(state)
        with self._lock:
            cached_id = self._compatibility_cache.get(state_hash)
            if cached_id is not None:
                return _json_copy(self._compatibility_reports[cached_id])

        report = {
            "schema_version": 1,
            "contract": "cam.compatibility_report.v1",
            "report_id": f"compat:{state_hash[7:31]}",
            "graph_id": graph["graph_id"],
            "revision_id": graph["revision_id"],
            "product": graph["product"],
            "source_profile": {
                "target_version": (
                    target_versions[0] if len(target_versions) == 1 else "unknown"
                )
            },
            "target_profile": {
                "target_version": fixture.target_version,
                "target_instance_id": fixture.target_instance_id,
                "project_id": fixture.project_id,
                "project_snapshot_hash": fixture.project_snapshot_hash,
                "target_kind": "fixture",
            },
            "capability_lock_hash": compute_capability_lock_hash(graph),
            "checked_manifest_hashes": sorted(checked_hashes),
            "status": report_status,
            "node_results": node_results,
            "symbol_results": [],
            "selector_results": selector_results,
            "coverage_gaps": [
                {
                    "object_ref": f"manifest:{item.get('manifest_id', 'unknown')}",
                    "reason": (
                        "target_version_opaque_only"
                        if item.get("tier") == "opaque_only"
                        else "target_version_unclassified"
                    ),
                    "target_version": fixture.target_version,
                }
                for item in version_results
                if item.get("tier") in {
                    "opaque_only",
                    "unsupported",
                    "unknown",
                }
            ],
            "issues": stable_issues,
            "blocker_codes": blocker_codes,
            "preview_eligible": report_status == "compatible",
            "checked_at": self._timestamp(),
            "extensions": {
                "cam.flow_compatibility": {
                    "semantic": False,
                    "static_only": True,
                    "transport": "none",
                    "simulation_claimed": False,
                    "version_results": version_results,
                }
            },
        }
        report_validation = validate_contract(
            report,
            "compatibility_report",
            limits=self.limits,
        )
        self._raise_for_validation(report_validation)
        with self._lock:
            self._compatibility_reports[report["report_id"]] = _json_copy(report)
            self._compatibility_cache[state_hash] = report["report_id"]
        self._checkpoint(context)
        return _json_copy(report)

    compatibility = check_compatibility

    def get_compatibility_report(self, report_id: str) -> dict[str, Any]:
        identity = _required_text(report_id, "report_id")
        with self._lock:
            report = self._compatibility_reports.get(identity)
            if report is None:
                raise FlowNotFoundError(
                    "COMPATIBILITY_REPORT_NOT_FOUND",
                    "The compatibility report does not exist.",
                    http_status=404,
                    details={"report_id": identity},
                )
            return _json_copy(report)

    @staticmethod
    def _reviewed_recipe_hash(version: Mapping[str, Any]) -> str | None:
        extension = version.get("extensions", {}).get(
            "cam.flow.api/reviewed_recipe_hash"
        )
        if isinstance(extension, Mapping):
            value = extension.get("value")
            return str(value) if value is not None else None
        return None

    def _expected_preview_hashes(
        self,
        graph: Mapping[str, Any],
        version: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "source_snapshot_hash": graph["source_snapshot_hash"],
            "semantic_hash": graph["semantic_hash"],
            "capability_lock_hash": compute_capability_lock_hash(graph),
            "reviewed_recipe_hash": self._reviewed_recipe_hash(version),
        }

    def _require_preview_hashes(
        self,
        provided: Any,
        expected: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(provided, Mapping):
            raise FlowServiceError(
                "FLOW_SCHEMA_INVALID",
                "Preview hashes must be an explicit JSON object.",
                http_status=400,
                details={"field": "hashes"},
            )
        missing = [field_name for field_name in _HASH_FIELDS if field_name not in provided]
        if missing:
            raise FlowServiceError(
                "FLOW_SCHEMA_INVALID",
                "Preview hashes are incomplete.",
                http_status=400,
                details={"fields": missing},
            )
        mismatched = [
            field_name
            for field_name in _HASH_FIELDS
            if provided.get(field_name) != expected.get(field_name)
        ]
        if mismatched:
            raise FlowConflictError(
                "PREVIEW_HASH_MISMATCH",
                "Preview hashes do not match the reviewed immutable inputs.",
                http_status=409,
                details={"fields": mismatched},
            )
        return {field_name: _json_copy(provided.get(field_name)) for field_name in _HASH_FIELDS}

    def _require_asset_preview_rights(self, graph: Mapping[str, Any]) -> None:
        refs = [
            item
            for item in graph.get("asset_refs", [])
            if isinstance(item, Mapping)
        ]
        if not refs:
            return
        if self.asset_registry is None:
            raise FlowPermissionError(
                "ASSET_RIGHTS_UNKNOWN",
                "Asset rights cannot be verified for preview.",
                http_status=403,
            )
        for item in refs:
            revision_id = str(item.get("asset_revision_id", ""))
            try:
                asset = self.asset_registry.get_revision(revision_id)
                if asset.get("content_hash") != item.get("content_hash"):
                    raise FlowConflictError(
                        "PREVIEW_HASH_MISMATCH",
                        "The asset content hash does not match the graph snapshot.",
                        http_status=409,
                        details={"asset_revision_id": revision_id},
                    )
                self.asset_registry.require_operation(revision_id, "preview")
            except AssetAccessDenied as error:
                raise FlowPermissionError(
                    error.code,
                    "The asset rights state does not permit preview.",
                    http_status=403,
                    details={"asset_revision_id": revision_id},
                ) from error
            except AssetRegistryError as error:
                status = 404 if error.code == "ASSET_NOT_FOUND" else _status_for_code(error.code)
                raise FlowServiceError(
                    error.code,
                    "The asset revision cannot be verified for preview.",
                    http_status=status,
                    details={"asset_revision_id": revision_id},
                ) from error

    def _ordered_nodes(self, graph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        ordered: list[Mapping[str, Any]] = []
        flows = sorted(
            (
                flow
                for flow in graph.get("flows", [])
                if isinstance(flow, Mapping)
            ),
            key=lambda flow: (
                0 if flow.get("flow_id") == graph.get("entry_flow_id") else 1,
                str(flow.get("flow_id", "")),
            ),
        )
        for flow in flows:
            nodes = {
                str(node.get("node_id", "")): node
                for node in flow.get("nodes", [])
                if isinstance(node, Mapping)
            }
            outgoing: dict[str, set[str]] = {node_id: set() for node_id in nodes}
            incoming: dict[str, int] = {node_id: 0 for node_id in nodes}
            for edge in flow.get("edges", []):
                if not isinstance(edge, Mapping) or edge.get("kind") != "control":
                    continue
                source = edge.get("source", {})
                target = edge.get("target", {})
                if not isinstance(source, Mapping) or not isinstance(target, Mapping):
                    continue
                source_id = str(source.get("node_id", ""))
                target_id = str(target.get("node_id", ""))
                if (
                    source_id in nodes
                    and target_id in nodes
                    and target_id not in outgoing[source_id]
                ):
                    outgoing[source_id].add(target_id)
                    incoming[target_id] += 1
            ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
            emitted: set[str] = set()
            while ready:
                node_id = ready.pop(0)
                emitted.add(node_id)
                ordered.append(nodes[node_id])
                for target_id in sorted(outgoing[node_id]):
                    incoming[target_id] -= 1
                    if incoming[target_id] == 0:
                        ready.append(target_id)
                        ready.sort()
            ordered.extend(nodes[node_id] for node_id in sorted(nodes) if node_id not in emitted)
        return ordered

    @staticmethod
    def _preview_parameters(node: Mapping[str, Any]) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "configuration": _json_copy(node.get("configuration", {}))
        }
        bindings: list[dict[str, Any]] = []
        for binding in node.get("bindings", []):
            if not isinstance(binding, Mapping):
                continue
            item = {
                "binding_id": binding.get("binding_id"),
                "target": _json_copy(binding.get("target")),
                "kind": binding.get("kind"),
            }
            if binding.get("kind") == "literal":
                item["literal"] = _json_copy(binding.get("literal"))
            elif binding.get("kind") == "graph_parameter":
                item["graph_parameter_id"] = binding.get("graph_parameter_id")
            elif binding.get("kind") == "node_output":
                item["source_output"] = _json_copy(binding.get("source_output"))
            elif binding.get("kind") == "secret_ref":
                item["secret_ref_present"] = bool(binding.get("secret_ref"))
            bindings.append(item)
        if bindings:
            parameters["bindings"] = sorted(
                bindings,
                key=lambda item: str(item.get("binding_id", "")),
            )
        return parameters

    def _preview_steps(self, graph: Mapping[str, Any]) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for node in self._ordered_nodes(graph):
            if not node.get("enabled", False):
                continue
            action = str(node.get("node_type", ""))
            if action.startswith(("flow.", "opaque.")):
                continue
            steps.append(
                {
                    "order": len(steps) + 1,
                    "node_id": node["node_id"],
                    "action": action,
                    "parameters": self._preview_parameters(node),
                    "source_mapping_ids": sorted(
                        set(node.get("source_mapping_ids", []))
                    ),
                    "effects": ["unknown"],
                    "risk": node["risk"],
                    "compatibility_status": node["compatibility_status"],
                    "approval_required": (
                        node["risk"] != "safe"
                        or node.get("review_status") != "accepted"
                    ),
                }
            )
        return steps

    @staticmethod
    def _gate_results(graph: Mapping[str, Any]) -> list[dict[str, str]]:
        gates = set(str(item) for item in graph.get("required_gates", []))
        gates.update(
            {
                "recipe_review",
                "target_version_validation",
                "cam_simulation",
                "collision_check",
                "shop_approval",
            }
        )
        results: list[dict[str, str]] = []
        for gate in sorted(gates):
            if gate in _PROTECTED_GATES:
                status = (
                    "not_run"
                    if gate in {"cam_simulation", "machine_simulation"}
                    else "required"
                )
            elif gate in {"recipe_review", "target_version_validation"}:
                status = "passed"
            else:
                status = "required"
            results.append({"gate": gate, "status": status})
        return results

    def _initial_plan(
        self,
        *,
        plan_id: str,
        graph: Mapping[str, Any],
        version: Mapping[str, Any],
        target: FixtureTarget,
        hashes: Mapping[str, Any],
        compatibility: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = self._timestamp()
        return {
            "schema_version": 1,
            "contract": "cam.preview_plan.v1",
            "plan_id": plan_id,
            "graph_id": graph["graph_id"],
            "revision_id": graph["revision_id"],
            "flow_version_id": version["version_id"],
            "status": "draft",
            "execution_mode": "fixture_dry_run",
            "transport": "none",
            "target": {
                key: value
                for key, value in target.to_dict().items()
                if key
                in {
                    "product",
                    "target_version",
                    "target_instance_id",
                    "project_id",
                    "project_snapshot_hash",
                    "target_kind",
                }
            },
            "hashes": _json_copy(hashes),
            "steps": self._preview_steps(graph),
            "gate_results": self._gate_results(graph),
            "round_trip_report_id": version["round_trip_report_id"],
            "compatibility_report_id": compatibility["report_id"],
            "diagnostics": _json_copy(compatibility["issues"]),
            **ZERO_EXECUTION_FIELDS,
            "created_at": now,
            "completed_at": None,
            "extensions": {
                "cam.flow.api/state_history": {
                    "semantic": False,
                    "value": [{"status": "draft", "at": now}],
                }
            },
        }

    def create_preview_plan(
        self,
        request: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        self._checkpoint(context)
        if not isinstance(request, Mapping):
            raise FlowServiceError(
                "FLOW_SCHEMA_INVALID",
                "Preview request must be a JSON object.",
                http_status=400,
            )
        payload = _json_copy(request)
        if payload.get("execution_mode", "fixture_dry_run") != "fixture_dry_run":
            raise FlowConflictError(
                "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                "Preview execution_mode must be fixture_dry_run.",
                http_status=409,
            )
        if payload.get("transport", "none") != "none":
            raise FlowConflictError(
                "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                "Preview transport must be none.",
                http_status=409,
            )
        graph_id = _required_text(payload.get("graph_id"), "graph_id")
        revision_id = _required_text(payload.get("revision_id"), "revision_id")
        version_id = _required_text(payload.get("flow_version_id"), "flow_version_id")

        def create() -> dict[str, Any]:
            self._checkpoint(context)
            graph = self.get_graph(graph_id, revision_id)
            version = self.get_version(version_id)
            if (
                version["graph_id"] != graph_id
                or version["revision_id"] != revision_id
            ):
                raise FlowConflictError(
                    "PREVIEW_HASH_MISMATCH",
                    "FlowVersion does not bind the requested graph revision.",
                    http_status=409,
                )
            if version["status"] != "reviewed_for_fixture":
                raise FlowConflictError(
                    "FLOW_STATE_CONFLICT",
                    "Only a reviewed_for_fixture FlowVersion may enter preview.",
                    http_status=409,
                    details={"state": version["status"]},
                )
            target = self._resolve_target(payload.get("target", {}))
            expected_hashes = self._expected_preview_hashes(graph, version)
            hashes = self._require_preview_hashes(
                payload.get("hashes"),
                expected_hashes,
            )
            self._require_asset_preview_rights(graph)
            compatibility = self.check_compatibility(
                graph_id,
                target.to_dict(),
                revision_id=revision_id,
                context=context,
            )
            plan_semantics = {
                "graph_id": graph_id,
                "revision_id": revision_id,
                "flow_version_id": version_id,
                "target": target.to_dict(),
                "hashes": hashes,
            }
            plan_id = str(
                payload.get("plan_id")
                or f"preview:{canonical_hash(plan_semantics)[7:31]}"
            )
            plan = self._initial_plan(
                plan_id=plan_id,
                graph=graph,
                version=version,
                target=target,
                hashes=hashes,
                compatibility=compatibility,
            )
            with self._lock:
                existing = self._preview_plans.get(plan_id)
                if existing is not None:
                    comparable = _json_copy(existing)
                    comparable["status"] = "draft"
                    comparable["completed_at"] = None
                    comparable["extensions"] = plan["extensions"]
                    if canonical_hash(comparable) != canonical_hash(plan):
                        raise FlowConflictError(
                            "FLOW_STATE_CONFLICT",
                            "A PreviewPlan identity cannot be reused.",
                            http_status=409,
                            details={"plan_id": plan_id},
                        )
                    return _json_copy(existing)
                self._preview_plans[plan_id] = plan
                self._preview_plan_order.append(plan_id)
                self._set_plan_status_locked(
                    plan_id,
                    "validating",
                    expected_status="draft",
                )
                if compatibility["preview_eligible"]:
                    self._set_plan_status_locked(
                        plan_id,
                        "ready",
                        expected_status="validating",
                    )
                    return _json_copy(self._preview_plans[plan_id])
                self._set_plan_status_locked(
                    plan_id,
                    "blocked",
                    expected_status="validating",
                )
            first_code = (
                compatibility["blocker_codes"][0]
                if compatibility["blocker_codes"]
                else "FLOW_STATE_CONFLICT"
            )
            raise FlowServiceError(
                first_code,
                "The PreviewPlan is blocked by deterministic validation.",
                http_status=_status_for_code(first_code),
                details={"plan_id": plan_id},
            )

        return self._idempotent("preview.create", idempotency_key, payload, create)

    create_preview = create_preview_plan

    def _set_plan_status_locked(
        self,
        plan_id: str,
        target_status: str,
        *,
        expected_status: str | None = None,
        diagnostic_code: str | None = None,
    ) -> dict[str, Any]:
        plan = self._preview_plans.get(plan_id)
        if plan is None:
            raise FlowNotFoundError(
                "PREVIEW_PLAN_NOT_FOUND",
                "The PreviewPlan does not exist.",
                http_status=404,
                details={"plan_id": plan_id},
            )
        current = str(plan["status"])
        if expected_status is not None and current != expected_status:
            raise FlowConflictError(
                "FLOW_STATE_CONFLICT",
                "The PreviewPlan state changed before the requested transition.",
                http_status=409,
                details={
                    "plan_id": plan_id,
                    "state": current,
                    "expected_state": expected_status,
                },
            )
        if target_status not in _PREVIEW_TRANSITIONS.get(current, frozenset()):
            raise FlowConflictError(
                "FLOW_STATE_CONFLICT",
                "The PreviewPlan state transition is invalid.",
                http_status=409,
                details={
                    "plan_id": plan_id,
                    "state": current,
                    "target_state": target_status,
                },
            )
        updated = _json_copy(plan)
        updated["status"] = target_status
        updated.update(ZERO_EXECUTION_FIELDS)
        now = self._timestamp()
        if target_status in _PREVIEW_TERMINAL_STATES:
            updated["completed_at"] = now
        history = updated["extensions"]["cam.flow.api/state_history"]["value"]
        history.append({"status": target_status, "at": now})
        if diagnostic_code is not None:
            updated["diagnostics"].append(
                {
                    "code": diagnostic_code,
                    "severity": "blocker",
                    "message": "The PreviewPlan was stopped by a changed prerequisite.",
                    "object_ref": f"plan:{plan_id}",
                    "source_mapping_ids": [],
                    "remediation": "Create a new request after explicit review.",
                }
            )
        validation = validate_contract(updated, "preview_plan", limits=self.limits)
        self._raise_for_validation(validation)
        self._preview_plans[plan_id] = updated
        return _json_copy(updated)

    def transition_preview_plan(
        self,
        plan_id: str,
        target_status: str,
        *,
        expected_status: str | None = None,
    ) -> dict[str, Any]:
        target = _required_text(target_status, "target_status")
        with self._lock:
            return self._set_plan_status_locked(
                _required_text(plan_id, "plan_id"),
                target,
                expected_status=expected_status,
            )

    def get_preview_plan(self, plan_id: str) -> dict[str, Any]:
        identity = _required_text(plan_id, "plan_id")
        with self._lock:
            plan = self._preview_plans.get(identity)
            if plan is None:
                raise FlowNotFoundError(
                    "PREVIEW_PLAN_NOT_FOUND",
                    "The PreviewPlan does not exist.",
                    http_status=404,
                    details={"plan_id": identity},
                )
            return _json_copy(plan)

    def list_preview_plans(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ServicePage:
        with self._lock:
            items = [self._preview_plans[plan_id] for plan_id in self._preview_plan_order]
        return self._page(
            items,
            collection="preview_plans",
            cursor=cursor,
            limit=limit,
        )

    def cancel_preview_plan(self, plan_id: str) -> dict[str, Any]:
        identity = _required_text(plan_id, "plan_id")
        with self._lock:
            plan = self._preview_plans.get(identity)
            if plan is None:
                raise FlowNotFoundError(
                    "PREVIEW_PLAN_NOT_FOUND",
                    "The PreviewPlan does not exist.",
                    http_status=404,
                    details={"plan_id": identity},
                )
            if plan["status"] == "cancelled":
                return _json_copy(plan)
            if plan["status"] in {"blocked", "succeeded", "failed"}:
                raise FlowConflictError(
                    "FLOW_STATE_CONFLICT",
                    "A terminal PreviewPlan cannot be cancelled.",
                    http_status=409,
                    details={"plan_id": identity, "state": plan["status"]},
                )
            return self._set_plan_status_locked(
                identity,
                "cancelled",
                expected_status=plan["status"],
            )

    cancel_preview = cancel_preview_plan

    def execute_preview_plan(
        self,
        plan_id: str,
        *,
        context: FlowRequestContext | None = None,
    ) -> dict[str, Any]:
        identity = _required_text(plan_id, "plan_id")
        self._checkpoint(context)
        with self._lock:
            self._set_plan_status_locked(
                identity,
                "previewing",
                expected_status="ready",
            )
        try:
            self._checkpoint(context)
            plan = self.get_preview_plan(identity)
            graph = self.get_graph(plan["graph_id"], plan["revision_id"])
            version = self.get_version(plan["flow_version_id"])
            target = self._resolve_target(plan["target"])
            self._require_preview_hashes(
                plan["hashes"],
                self._expected_preview_hashes(graph, version),
            )
            self._require_asset_preview_rights(graph)
            compatibility = self.check_compatibility(
                graph["graph_id"],
                target.to_dict(),
                revision_id=graph["revision_id"],
                context=context,
            )
            if not compatibility["preview_eligible"]:
                first_code = (
                    compatibility["blocker_codes"][0]
                    if compatibility["blocker_codes"]
                    else "FLOW_STATE_CONFLICT"
                )
                raise FlowServiceError(
                    first_code,
                    "Preview prerequisites changed after plan creation.",
                    http_status=_status_for_code(first_code),
                )
            self._checkpoint(context)
            with self._lock:
                result = self._set_plan_status_locked(
                    identity,
                    "succeeded",
                    expected_status="previewing",
                )
            return result
        except FlowCancelledError:
            with self._lock:
                current = self._preview_plans.get(identity)
                if current is not None and current["status"] == "previewing":
                    self._set_plan_status_locked(
                        identity,
                        "cancelled",
                        expected_status="previewing",
                    )
            raise
        except FlowDeadlineExceeded:
            with self._lock:
                current = self._preview_plans.get(identity)
                if current is not None and current["status"] == "previewing":
                    self._set_plan_status_locked(
                        identity,
                        "failed",
                        expected_status="previewing",
                    )
            raise
        except FlowServiceError as error:
            with self._lock:
                current = self._preview_plans.get(identity)
                if current is not None and current["status"] == "previewing":
                    target_status = (
                        "cancelled"
                        if error.code == "PREVIEW_PERMISSION_REVOKED"
                        else "failed"
                    )
                    self._set_plan_status_locked(
                        identity,
                        target_status,
                        expected_status="previewing",
                        diagnostic_code=error.code,
                    )
            raise
        except Exception:
            with self._lock:
                current = self._preview_plans.get(identity)
                if current is not None and current["status"] == "previewing":
                    self._set_plan_status_locked(
                        identity,
                        "failed",
                        expected_status="previewing",
                        diagnostic_code="FLOW_INTERNAL_ERROR",
                    )
            raise

    run_preview = execute_preview_plan


def _stable_issue_key(value: Mapping[str, Any]) -> tuple[str, str, str, tuple[str, ...], str]:
    return (
        str(value.get("severity", "")),
        str(value.get("code", "")),
        str(value.get("object_ref", "")),
        tuple(str(item) for item in value.get("source_mapping_ids", [])),
        str(value.get("message", "")),
    )


__all__ = [
    "CancellationToken",
    "DEFAULT_MAX_DIFF_CHANGES",
    "DEFAULT_MAX_PAGE_SIZE",
    "FixtureTarget",
    "FlowCancellationToken",
    "FlowCancelledError",
    "FlowConflictError",
    "FlowDeadlineExceeded",
    "FlowNotFoundError",
    "FlowPermissionError",
    "FlowRequestContext",
    "FlowResourceLimitError",
    "FlowService",
    "FlowServiceError",
    "RequestContext",
    "SCHEMA_VERSION",
    "ServicePage",
    "ZERO_EXECUTION_FIELDS",
]
