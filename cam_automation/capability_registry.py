from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .product_catalog import PRODUCT_ACTION_NAMESPACES, PRODUCT_KEYS
from .version_compatibility import validate_version_compatibility_policy


CAPABILITY_CONTRACT = "cam.capability_manifest.v1"

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_PRODUCTS = PRODUCT_KEYS
_ADAPTER_KINDS = frozenset(
    {"builtin", "licensed_adapter", "public_api", "vendor_export", "manifest_only"}
)
_EXECUTION_MODES = frozenset({"offline", "read_only", "fixture_dry_run"})
_RISK_ORDER = {"safe": 0, "review": 1, "blocked": 2}
_REQUIRED_PROHIBITIONS = frozenset(
    {
        "live_journal",
        "live_macro",
        "nc",
        "gcode",
        "clsf",
        "postprocess",
        "machine_control",
    }
)


class Clock(Protocol):
    def now(self) -> datetime:
        ...


class ManifestValidator(Protocol):
    def validate(self, document: Mapping[str, Any]) -> None:
        ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class CapabilityRegistryError(ValueError):
    """A fail-closed capability or permission error with a stable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": copy.deepcopy(self.details),
        }


class CapabilityUnavailable(CapabilityRegistryError):
    pass


class AuthorizationRequired(CapabilityRegistryError):
    pass


class CapabilityHandle:
    """An in-memory permission lease. It never contains a CAM transport."""

    __slots__ = (
        "handle_id",
        "grant_id",
        "manifest_id",
        "manifest_hash",
        "category",
        "opened_at",
        "_closed_at",
        "_close_reason",
        "_lock",
    )

    def __init__(
        self,
        *,
        handle_id: str,
        grant_id: str,
        manifest_id: str,
        manifest_hash: str,
        category: str,
        opened_at: str,
    ) -> None:
        self.handle_id = handle_id
        self.grant_id = grant_id
        self.manifest_id = manifest_id
        self.manifest_hash = manifest_hash
        self.category = category
        self.opened_at = opened_at
        self._closed_at: str | None = None
        self._close_reason: str | None = None
        self._lock = threading.Lock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed_at is not None

    @property
    def closed_at(self) -> str | None:
        with self._lock:
            return self._closed_at

    @property
    def close_reason(self) -> str | None:
        with self._lock:
            return self._close_reason

    def close(self, *, at: str | None = None, reason: str = "closed") -> None:
        timestamp = at or _format_time(datetime.now(timezone.utc))
        with self._lock:
            if self._closed_at is None:
                self._closed_at = timestamp
                self._close_reason = reason

    def require_open(self) -> None:
        if self.closed:
            raise CapabilityUnavailable(
                "PREVIEW_PERMISSION_REVOKED",
                "The permission handle is closed.",
                details={"handle_id": self.handle_id},
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "handle_id": self.handle_id,
            "grant_id": self.grant_id,
            "manifest_id": self.manifest_id,
            "manifest_hash": self.manifest_hash,
            "category": self.category,
            "opened_at": self.opened_at,
            "closed": self.closed,
            "closed_at": self.closed_at,
            "close_reason": self.close_reason,
            "transport": "none",
        }


def _format_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "Capability timestamps must include a timezone.",
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any, *, field: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"{field} must contain finite JSON values.",
        ) from exc
    return json.loads(encoded)


def _stable_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _record_digest(value: Any) -> str:
    return hashlib.sha256(_stable_bytes(value)).hexdigest()


def _required_string(value: Any, *, field: str) -> str:
    result = str(value).strip()
    if not result:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"{field} must be a non-empty string.",
        )
    return result


def _required_string_list(
    value: Any,
    *,
    field: str,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"{field} must be an array.",
        )
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _required_string(item, field=field)
        if text not in seen:
            result.append(text)
            seen.add(text)
    if not allow_empty and not result:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"{field} must contain at least one value.",
        )
    return result


def _manifest_key(manifest_id: str, version: str, manifest_hash: str) -> str:
    return "\0".join((manifest_id, version, manifest_hash))


def _normalize_scope(value: Any) -> Any:
    if isinstance(value, str):
        return _required_string(value, field="scope")
    if value is None:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "scope must be an explicit JSON value.",
        )
    return _json_copy(value, field="scope")


def _validate_node_type(node: Mapping[str, Any], product: str, index: int) -> None:
    required = {
        "node_type",
        "node_type_version",
        "category",
        "display_name",
        "ports",
        "configuration_schema",
        "static_risk_floor",
        "required_gates",
        "forbidden_modes",
        "parse_support",
        "preview_support",
        "recipe_projection_support",
        "fidelity",
    }
    missing = sorted(required - set(node))
    if missing:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"node_types[{index}] is missing: {', '.join(missing)}.",
        )
    node_type = _required_string(node["node_type"], field="node_type")
    prefix = node_type.split(".", 1)[0]
    allowed_namespaces = {"cam", *PRODUCT_ACTION_NAMESPACES}
    if prefix not in allowed_namespaces:
        raise CapabilityRegistryError(
            "FLOW_NAMESPACE_INVALID",
            f"Unsupported node namespace: {prefix}.",
        )
    if prefix in PRODUCT_ACTION_NAMESPACES and prefix != product:
        raise CapabilityRegistryError(
            "FLOW_PRODUCT_MIXED",
            "A product capability cannot declare the other product's node type.",
        )
    version = _required_string(node["node_type_version"], field="node_type_version")
    if not _SEMVER_RE.fullmatch(version):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "node_type_version must be SemVer.",
        )
    risk = str(node["static_risk_floor"])
    if risk not in _RISK_ORDER:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "static_risk_floor must be safe, review, or blocked.",
        )
    if not isinstance(node["ports"], list):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "node type ports must be an array.",
        )
    if not isinstance(node["configuration_schema"], Mapping):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "configuration_schema must be an object.",
        )
    _required_string_list(
        node["required_gates"],
        field="required_gates",
        allow_empty=True,
    )
    _required_string_list(
        node["forbidden_modes"],
        field="forbidden_modes",
        allow_empty=True,
    )
    for field in (
        "parse_support",
        "preview_support",
        "recipe_projection_support",
    ):
        if not isinstance(node[field], bool):
            raise CapabilityRegistryError(
                "FLOW_SCHEMA_INVALID",
                f"{field} must be a boolean.",
            )


def _validate_manifest_shape(document: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _json_copy(dict(document), field="manifest")
    required = {
        "schema_version",
        "contract",
        "manifest_id",
        "manifest_version",
        "manifest_hash",
        "provider",
        "product",
        "adapter_kind",
        "authorization",
        "target_version_ranges",
        "execution_modes",
        "permissions",
        "node_types",
        "prohibited_operations",
        "evidence",
        "revocation",
        "extensions",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"Capability manifest is missing: {', '.join(missing)}.",
        )
    if manifest["schema_version"] != 1 or manifest["contract"] != CAPABILITY_CONTRACT:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "Capability manifest contract must be cam.capability_manifest.v1.",
        )
    manifest_id = _required_string(manifest["manifest_id"], field="manifest_id")
    version = _required_string(manifest["manifest_version"], field="manifest_version")
    if not _SEMVER_RE.fullmatch(version):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "manifest_version must be SemVer.",
        )
    manifest_hash = str(manifest["manifest_hash"])
    if not _HASH_RE.fullmatch(manifest_hash):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "manifest_hash must be a lowercase SHA-256 hash.",
        )
    product = str(manifest["product"])
    if product not in _PRODUCTS:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "Capability product is not registered.",
        )
    if manifest["adapter_kind"] not in _ADAPTER_KINDS:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "Capability adapter_kind is not supported.",
        )
    if not isinstance(manifest["provider"], Mapping):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "provider must be an object.",
        )
    for field in ("provider_id", "display_name", "claim_level"):
        _required_string(manifest["provider"].get(field), field=f"provider.{field}")
    if not isinstance(manifest["authorization"], Mapping):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "authorization must be an object.",
        )
    _required_string(
        manifest["authorization"].get("status"),
        field="authorization.status",
    )
    target_ranges = _required_string_list(
        manifest["target_version_ranges"],
        field="target_version_ranges",
    )
    if any(item.lower() == "latest" for item in target_ranges):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "target_version_ranges cannot use latest.",
        )
    modes = _required_string_list(
        manifest["execution_modes"],
        field="execution_modes",
    )
    if not set(modes) <= _EXECUTION_MODES:
        raise CapabilityRegistryError(
            "SAFETY_LIVE_EXECUTION_FORBIDDEN",
            "Capability execution modes are limited to offline/read-only fixture modes.",
        )
    _required_string_list(
        manifest["permissions"],
        field="permissions",
        allow_empty=True,
    )
    nodes = manifest["node_types"]
    if not isinstance(nodes, list) or not nodes:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "node_types must contain at least one node definition.",
        )
    seen_nodes: set[tuple[str, str]] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            raise CapabilityRegistryError(
                "FLOW_SCHEMA_INVALID",
                f"node_types[{index}] must be an object.",
            )
        _validate_node_type(node, product, index)
        identity = (str(node["node_type"]), str(node["node_type_version"]))
        if identity in seen_nodes:
            raise CapabilityRegistryError(
                "FLOW_SCHEMA_INVALID",
                "Capability node type identities must be unique.",
            )
        seen_nodes.add(identity)
    prohibited = set(
        _required_string_list(
            manifest["prohibited_operations"],
            field="prohibited_operations",
        )
    )
    if not _REQUIRED_PROHIBITIONS <= prohibited:
        raise CapabilityRegistryError(
            "SAFETY_LIVE_EXECUTION_FORBIDDEN",
            "Capability manifest omits required live or machine-output prohibitions.",
        )
    if not isinstance(manifest["evidence"], list):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "evidence must be an array.",
        )
    if any(not isinstance(item, Mapping) for item in manifest["evidence"]):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "Every capability evidence item must be an object.",
        )
    if manifest["revocation"] is not None and not isinstance(
        manifest["revocation"], Mapping
    ):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "revocation must be an object or null.",
        )
    if not isinstance(manifest["extensions"], Mapping):
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            "extensions must be an object.",
        )
    try:
        validate_version_compatibility_policy(manifest)
    except ValueError as error:
        raise CapabilityRegistryError(
            "FLOW_SCHEMA_INVALID",
            str(error),
        ) from error
    # Keep these exact values normalized for indexing without changing unknown fields.
    manifest["manifest_id"] = manifest_id
    manifest["manifest_version"] = version
    manifest["manifest_hash"] = manifest_hash
    return manifest


class CapabilityRegistry:
    """Local capability, exact grant, revocation, and manifest-lock registry."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        clock: Clock | None = None,
        validator: ManifestValidator | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.registry_dir = self.data_dir / "capability-registry"
        self.state_path = self.registry_dir / "registry.json"
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _SystemClock()
        self._validator = validator
        self._lock = threading.RLock()
        self._manifests: dict[str, dict[str, Any]] = {}
        self._current: dict[str, str] = {}
        self._revocations: list[dict[str, Any]] = []
        self._grants: list[dict[str, Any]] = []
        self._next_grant_sequence = 1
        self._next_handle_sequence = 1
        self._handles: dict[str, CapabilityHandle] = {}
        self._load()

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime):
            raise TypeError("clock.now() must return datetime")
        if value.tzinfo is None:
            raise CapabilityRegistryError(
                "FLOW_SCHEMA_INVALID",
                "clock.now() must return a timezone-aware datetime.",
            )
        return value

    def _now_text(self) -> str:
        return _format_time(self._now())

    def _load(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CapabilityRegistryError(
                "CAPABILITY_REGISTRY_CORRUPT",
                "The local capability registry cannot be read safely.",
            ) from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise CapabilityRegistryError(
                "CAPABILITY_REGISTRY_CORRUPT",
                "The local capability registry has an unsupported shape.",
            )
        manifests = value.get("manifests")
        current = value.get("current")
        revocations = value.get("revocations")
        grants = value.get("grants")
        if not all(
            (
                isinstance(manifests, list),
                isinstance(current, dict),
                isinstance(revocations, list),
                isinstance(grants, list),
            )
        ):
            raise CapabilityRegistryError(
                "CAPABILITY_REGISTRY_CORRUPT",
                "The local capability registry contains invalid collections.",
            )
        for manifest in manifests:
            if not isinstance(manifest, Mapping):
                raise CapabilityRegistryError(
                    "CAPABILITY_REGISTRY_CORRUPT",
                    "The local capability registry contains an invalid manifest.",
                )
            normalized = _validate_manifest_shape(manifest)
            key = _manifest_key(
                normalized["manifest_id"],
                normalized["manifest_version"],
                normalized["manifest_hash"],
            )
            if key in self._manifests:
                raise CapabilityRegistryError(
                    "CAPABILITY_REGISTRY_CORRUPT",
                    "The local capability registry contains duplicate manifests.",
                )
            self._manifests[key] = normalized
        self._current = {str(key): str(item) for key, item in current.items()}
        if any(key not in self._manifests for key in self._current.values()):
            raise CapabilityRegistryError(
                "CAPABILITY_REGISTRY_CORRUPT",
                "The current manifest pointer is invalid.",
            )
        self._revocations = _json_copy(revocations, field="revocations")
        self._grants = _json_copy(grants, field="grants")
        self._next_grant_sequence = max(
            1,
            int(value.get("next_grant_sequence", len(self._grants) + 1)),
        )

    def _save(self) -> None:
        value = {
            "schema_version": 1,
            "manifests": list(self._manifests.values()),
            "current": self._current,
            "revocations": self._revocations,
            "grants": self._grants,
            "next_grant_sequence": self._next_grant_sequence,
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _current_manifest(self, manifest_id: str) -> tuple[str, dict[str, Any]]:
        key = self._current.get(manifest_id)
        if key is None:
            raise CapabilityUnavailable(
                "CAPABILITY_MISSING",
                "The requested capability manifest is not registered.",
                details={"manifest_id": manifest_id},
            )
        return key, self._manifests[key]

    def _select_manifest(
        self,
        manifest_id: str,
        *,
        manifest_version: str | None = None,
        manifest_hash: str | None = None,
        current_only: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        if current_only or (manifest_version is None and manifest_hash is None):
            key, manifest = self._current_manifest(manifest_id)
            if manifest_version is not None and manifest["manifest_version"] != manifest_version:
                raise CapabilityUnavailable(
                    "CAPABILITY_VERSION_MISMATCH",
                    "The current capability version does not match the request.",
                )
            if manifest_hash is not None and manifest["manifest_hash"] != manifest_hash:
                raise CapabilityUnavailable(
                    "CAPABILITY_VERSION_MISMATCH",
                    "The current capability hash does not match the request.",
                )
            return key, manifest
        candidates = [
            (key, manifest)
            for key, manifest in self._manifests.items()
            if manifest["manifest_id"] == manifest_id
            and (
                manifest_version is None
                or manifest["manifest_version"] == manifest_version
            )
            and (manifest_hash is None or manifest["manifest_hash"] == manifest_hash)
        ]
        if len(candidates) != 1:
            raise CapabilityUnavailable(
                "CAPABILITY_MISSING",
                "The exact capability manifest lock is unavailable.",
                details={"manifest_id": manifest_id},
            )
        return candidates[0]

    def _is_revoked(self, key: str, manifest: Mapping[str, Any]) -> bool:
        if manifest.get("revocation") is not None:
            return True
        return any(item.get("manifest_key") == key for item in self._revocations)

    def _close_handles(
        self,
        predicate: Any,
        *,
        at: str,
        reason: str,
    ) -> int:
        count = 0
        for handle in self._handles.values():
            if not handle.closed and predicate(handle):
                handle.close(at=at, reason=reason)
                count += 1
        return count

    def register_manifest(self, document: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(document, Mapping):
            raise CapabilityRegistryError(
                "FLOW_SCHEMA_INVALID",
                "Capability manifest must be an object.",
            )
        if self._validator is not None:
            self._validator.validate(document)
        manifest = _validate_manifest_shape(document)
        key = _manifest_key(
            manifest["manifest_id"],
            manifest["manifest_version"],
            manifest["manifest_hash"],
        )
        with self._lock:
            existing = self._manifests.get(key)
            if existing is not None:
                if _record_digest(existing) != _record_digest(manifest):
                    raise CapabilityRegistryError(
                        "CAPABILITY_MANIFEST_IMMUTABLE",
                        "An exact manifest identity cannot be overwritten.",
                    )
                return copy.deepcopy(existing)

            previous_key = self._current.get(manifest["manifest_id"])
            now = self._now_text()
            self._manifests[key] = manifest
            self._current[manifest["manifest_id"]] = key
            if previous_key is not None and previous_key != key:
                for grant in self._grants:
                    if (
                        grant["manifest_id"] == manifest["manifest_id"]
                        and grant["status"] == "active"
                    ):
                        grant["status"] = "superseded"
                        grant["superseded_at"] = now
                self._close_handles(
                    lambda item: item.manifest_id == manifest["manifest_id"],
                    at=now,
                    reason="manifest_changed",
                )
            self._save()
            return copy.deepcopy(manifest)

    def register(self, document: Mapping[str, Any]) -> dict[str, Any]:
        return self.register_manifest(document)

    def get_manifest(
        self,
        manifest_id: str,
        *,
        manifest_version: str | None = None,
        manifest_hash: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            _, manifest = self._select_manifest(
                str(manifest_id),
                manifest_version=manifest_version,
                manifest_hash=manifest_hash,
            )
            return copy.deepcopy(manifest)

    def list_manifests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(item) for item in self._manifests.values()]

    def create_lock(
        self,
        manifest_id: str,
        *,
        manifest_version: str | None = None,
        manifest_hash: str | None = None,
    ) -> dict[str, str]:
        with self._lock:
            _, manifest = self._select_manifest(
                str(manifest_id),
                manifest_version=manifest_version,
                manifest_hash=manifest_hash,
            )
            return {
                "manifest_id": manifest["manifest_id"],
                "manifest_version": manifest["manifest_version"],
                "manifest_hash": manifest["manifest_hash"],
            }

    def lock_manifest(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        return self.create_lock(*args, **kwargs)

    def lock_status(self, lock: Mapping[str, Any]) -> dict[str, Any]:
        manifest_id = str(lock.get("manifest_id", ""))
        version = str(lock.get("manifest_version", ""))
        manifest_hash = str(lock.get("manifest_hash", ""))
        with self._lock:
            try:
                key, manifest = self._select_manifest(
                    manifest_id,
                    manifest_version=version,
                    manifest_hash=manifest_hash,
                )
            except CapabilityUnavailable:
                return {
                    "available": False,
                    "compatibility_status": "capability_unavailable",
                    "code": "CAPABILITY_MISSING",
                }
            if self._is_revoked(key, manifest):
                return {
                    "available": False,
                    "compatibility_status": "capability_unavailable",
                    "code": "CAPABILITY_REVOKED",
                }
            return {
                "available": True,
                "compatibility_status": "supported",
                "code": None,
            }

    def require_lock(self, lock: Mapping[str, Any]) -> dict[str, Any]:
        status = self.lock_status(lock)
        if not status["available"]:
            raise CapabilityUnavailable(
                str(status["code"]),
                "The exact capability lock is unavailable.",
            )
        return status

    def _node_definition(
        self,
        lock: Mapping[str, Any],
        node_type: str,
        node_type_version: str,
    ) -> dict[str, Any]:
        self.require_lock(lock)
        with self._lock:
            _, manifest = self._select_manifest(
                str(lock["manifest_id"]),
                manifest_version=str(lock["manifest_version"]),
                manifest_hash=str(lock["manifest_hash"]),
            )
            for node in manifest["node_types"]:
                if (
                    node["node_type"] == node_type
                    and node["node_type_version"] == node_type_version
                ):
                    return node
        raise CapabilityUnavailable(
            "CAPABILITY_MISSING",
            "The locked manifest does not define the requested node type.",
        )

    def enforce_risk_floor(
        self,
        lock: Mapping[str, Any],
        *,
        node_type: str,
        node_type_version: str,
        requested_risk: str,
    ) -> str:
        if requested_risk not in _RISK_ORDER:
            raise CapabilityRegistryError(
                "FLOW_SCHEMA_INVALID",
                "requested_risk must be safe, review, or blocked.",
            )
        node = self._node_definition(lock, node_type, node_type_version)
        floor = node["static_risk_floor"]
        if _RISK_ORDER[requested_risk] < _RISK_ORDER[floor]:
            raise CapabilityUnavailable(
                "CAPABILITY_RISK_DOWNGRADE",
                "Requested node risk is below the manifest risk floor.",
                details={
                    "node_type": node_type,
                    "node_type_version": node_type_version,
                },
            )
        return requested_risk

    def revoke_manifest(
        self,
        manifest_id: str,
        *,
        manifest_version: str | None = None,
        manifest_hash: str | None = None,
        reason: str,
        revoked_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            key, manifest = self._select_manifest(
                str(manifest_id),
                manifest_version=manifest_version,
                manifest_hash=manifest_hash,
            )
            if self._is_revoked(key, manifest):
                return {
                    "manifest_id": manifest["manifest_id"],
                    "manifest_hash": manifest["manifest_hash"],
                    "revoked": True,
                    "changed": False,
                    "closed_handle_count": 0,
                }
            now = self._now_text()
            event = {
                "manifest_key": key,
                "manifest_id": manifest["manifest_id"],
                "manifest_version": manifest["manifest_version"],
                "manifest_hash": manifest["manifest_hash"],
                "reason": _required_string(reason, field="reason"),
                "revoked_by": _required_string(revoked_by, field="revoked_by"),
                "revoked_at": now,
            }
            self._revocations.append(event)
            for grant in self._grants:
                if (
                    grant["manifest_id"] == manifest["manifest_id"]
                    and grant["manifest_hash"] == manifest["manifest_hash"]
                    and grant["status"] == "active"
                ):
                    grant["status"] = "revoked"
                    grant["revoked_at"] = now
                    grant["revocation_reason"] = "manifest_revoked"
            closed = self._close_handles(
                lambda item: (
                    item.manifest_id == manifest["manifest_id"]
                    and item.manifest_hash == manifest["manifest_hash"]
                ),
                at=now,
                reason="manifest_revoked",
            )
            self._save()
            return {
                "manifest_id": manifest["manifest_id"],
                "manifest_hash": manifest["manifest_hash"],
                "revoked": True,
                "changed": True,
                "closed_handle_count": closed,
                "revoked_at": now,
                "auto_resumed": False,
            }

    def _binding(
        self,
        *,
        user_id: str,
        plugin_id: str,
        plugin_version: str,
        manifest_id: str,
        manifest_hash: str,
        category: str,
        scope: Any,
        purpose: str,
    ) -> dict[str, Any]:
        return {
            "user_id": _required_string(user_id, field="user_id"),
            "plugin_id": _required_string(plugin_id, field="plugin_id"),
            "plugin_version": _required_string(
                plugin_version,
                field="plugin_version",
            ),
            "manifest_id": _required_string(manifest_id, field="manifest_id"),
            "manifest_hash": _required_string(manifest_hash, field="manifest_hash"),
            "category": _required_string(category, field="category"),
            "scope": _normalize_scope(scope),
            "purpose": _required_string(purpose, field="purpose"),
        }

    def _active_grant(self, binding: Mapping[str, Any]) -> dict[str, Any] | None:
        for grant in self._grants:
            if grant.get("status") != "active":
                continue
            if all(grant.get(key) == value for key, value in binding.items()):
                return grant
        return None

    def grant(
        self,
        *,
        user_id: str,
        plugin_id: str,
        plugin_version: str,
        manifest_id: str,
        manifest_hash: str,
        category: str,
        scope: Any,
        purpose: str,
    ) -> dict[str, Any]:
        binding = self._binding(
            user_id=user_id,
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            manifest_id=manifest_id,
            manifest_hash=manifest_hash,
            category=category,
            scope=scope,
            purpose=purpose,
        )
        with self._lock:
            key, manifest = self._select_manifest(
                binding["manifest_id"],
                manifest_hash=binding["manifest_hash"],
                current_only=True,
            )
            if self._is_revoked(key, manifest):
                raise CapabilityUnavailable(
                    "CAPABILITY_REVOKED",
                    "A revoked capability manifest cannot receive grants.",
                )
            if binding["category"] not in manifest["permissions"]:
                raise AuthorizationRequired(
                    "CAPABILITY_PERMISSION_NOT_DECLARED",
                    "The capability manifest does not declare this permission category.",
                    details={"category": binding["category"]},
                )
            existing = self._active_grant(binding)
            if existing is not None:
                return copy.deepcopy(existing)
            sequence = self._next_grant_sequence
            self._next_grant_sequence += 1
            digest = _record_digest(binding)[:24]
            record = {
                "schema_version": 1,
                "grant_id": f"grant:{digest}:{sequence}",
                **binding,
                "status": "active",
                "granted_at": self._now_text(),
                "revoked_at": None,
                "auto_resume": False,
            }
            self._grants.append(record)
            self._save()
            return copy.deepcopy(record)

    def grant_permission(self, **kwargs: Any) -> dict[str, Any]:
        return self.grant(**kwargs)

    def requires_authorization(self, **kwargs: Any) -> bool:
        try:
            binding = self._binding(**kwargs)
        except TypeError:
            raise
        with self._lock:
            try:
                key, manifest = self._select_manifest(
                    binding["manifest_id"],
                    manifest_hash=binding["manifest_hash"],
                    current_only=True,
                )
            except CapabilityUnavailable:
                return True
            if self._is_revoked(key, manifest):
                return True
            return self._active_grant(binding) is None

    def authorization_required(self, **kwargs: Any) -> bool:
        return self.requires_authorization(**kwargs)

    def open_handle(self, **kwargs: Any) -> CapabilityHandle:
        binding = self._binding(**kwargs)
        with self._lock:
            key, manifest = self._select_manifest(
                binding["manifest_id"],
                manifest_hash=binding["manifest_hash"],
                current_only=True,
            )
            if self._is_revoked(key, manifest):
                raise CapabilityUnavailable(
                    "CAPABILITY_REVOKED",
                    "The capability manifest is revoked.",
                )
            grant = self._active_grant(binding)
            if grant is None:
                raise AuthorizationRequired(
                    "PREVIEW_PERMISSION_REVOKED",
                    "An exact active permission grant is required.",
                    details={"category": binding["category"]},
                )
            sequence = self._next_handle_sequence
            self._next_handle_sequence += 1
            handle = CapabilityHandle(
                handle_id=f"permission-handle:{sequence}",
                grant_id=grant["grant_id"],
                manifest_id=grant["manifest_id"],
                manifest_hash=grant["manifest_hash"],
                category=grant["category"],
                opened_at=self._now_text(),
            )
            self._handles[handle.handle_id] = handle
            return handle

    def open_permission_handle(self, **kwargs: Any) -> CapabilityHandle:
        return self.open_handle(**kwargs)

    def revoke_category(
        self,
        *,
        user_id: str,
        plugin_id: str,
        category: str,
        reason: str,
    ) -> dict[str, Any]:
        user = _required_string(user_id, field="user_id")
        plugin = _required_string(plugin_id, field="plugin_id")
        permission = _required_string(category, field="category")
        why = _required_string(reason, field="reason")
        with self._lock:
            now = self._now_text()
            revoked_grants: list[str] = []
            for grant in self._grants:
                if (
                    grant["status"] == "active"
                    and grant["user_id"] == user
                    and grant["plugin_id"] == plugin
                    and grant["category"] == permission
                ):
                    grant["status"] = "revoked"
                    grant["revoked_at"] = now
                    grant["revocation_reason"] = why
                    revoked_grants.append(grant["grant_id"])
            grant_ids = set(revoked_grants)
            closed = self._close_handles(
                lambda item: item.grant_id in grant_ids,
                at=now,
                reason="category_revoked",
            )
            self._save()
            return {
                "user_id": user,
                "plugin_id": plugin,
                "category": permission,
                "revoked_at": now,
                "revoked_grant_ids": revoked_grants,
                "closed_handle_count": closed,
                "data_deleted": False,
                "auto_resumed": False,
            }

    def revoke_permission_category(self, **kwargs: Any) -> dict[str, Any]:
        return self.revoke_category(**kwargs)

    def list_grants(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._grants)

    def active_handle_count(self) -> int:
        with self._lock:
            return sum(not handle.closed for handle in self._handles.values())
