from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


ASSET_CONTRACT = "cam.automation_asset.v1"
MAX_ASSET_BYTES = 10 * 1024 * 1024

_PRODUCTS = frozenset({"nx", "powermill"})
_ASSET_TYPES = frozenset(
    {
        "nx_journal",
        "nx_action_log",
        "powermill_macro",
        "powermill_command_log",
        "vendor_export",
        "capability_manifest",
    }
)
_SOURCE_ORIGINS = frozenset(
    {
        "user_authored",
        "employer_owned",
        "vendor_export",
        "open_source",
        "unknown",
    }
)
_RIGHTS_STATUSES = frozenset(
    {
        "unreviewed",
        "user_asserted",
        "vendor_authorized",
        "legal_reviewed",
        "restricted",
    }
)
_SHARING_SCOPES = frozenset(
    {"private", "team", "site", "organization", "restricted", "unknown"}
)
_BINARY_SUFFIXES = frozenset(
    {
        ".bin",
        ".class",
        ".com",
        ".dll",
        ".dylib",
        ".exe",
        ".jar",
        ".lib",
        ".msi",
        ".o",
        ".obj",
        ".pyd",
        ".so",
        ".sys",
    }
)
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_OPERATION_ALIASES = {
    "local_read": "read_local",
    "read": "read_local",
    "sharing": "share",
    "export": "redistribute",
    "network": "network_egress",
    "preview_asset": "preview",
    "inspect_binary": "binary_inspection",
}


class AssetRegistryError(ValueError):
    """A fail-closed registry error with a stable contract code."""

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


class AssetAccessDenied(AssetRegistryError):
    pass


def default_rights() -> dict[str, Any]:
    """Return the conservative rights record used when no review exists."""

    return {
        "status": "unreviewed",
        "evidence_ref": "",
        "sharing_scope": "private",
        "redistribution_allowed": None,
        "network_egress_allowed": False,
        "binary_inspection": False,
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            "Asset timestamps must include a timezone.",
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any, *, field: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"{field} must contain finite JSON values.",
        ) from exc
    return json.loads(encoded)


def _nonempty(value: Any, *, field: str) -> str:
    result = str(value).strip()
    if not result or _CONTROL_RE.search(result):
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"{field} must be a non-empty string without control characters.",
        )
    return result


def _string_list(
    values: Iterable[str],
    *,
    field: str,
    allow_empty: bool = True,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _nonempty(value, field=field)
        if item not in seen:
            seen.add(item)
            result.append(item)
    if not allow_empty and not result:
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"{field} must contain at least one value.",
        )
    return result


def _display_basename(value: str) -> str:
    text = _nonempty(value, field="display_name")
    windows_name = PureWindowsPath(text).name
    posix_name = PurePosixPath(windows_name).name
    name = posix_name.strip()
    if not name or name in {".", ".."}:
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            "display_name must identify a file without exposing its path.",
        )
    return name


def _suffixes(*values: str) -> set[str]:
    suffixes: set[str] = set()
    for value in values:
        leaf = PurePosixPath(PureWindowsPath(value).name).name
        suffixes.update(suffix.lower() for suffix in Path(leaf).suffixes)
    return suffixes


def _content_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _revision_id(asset_id: str, content_hash: str) -> str:
    payload = f"{asset_id}\0{content_hash}".encode("utf-8")
    return f"asset-rev:{hashlib.sha256(payload).hexdigest()}"


def _detect_text_profile(content: bytes) -> tuple[str, str, str]:
    bom = "none"
    encoding = "unknown"
    text: str | None = None

    if content.startswith(b"\xef\xbb\xbf"):
        bom = "utf8"
        encoding = "utf-8"
        try:
            text = content[3:].decode("utf-8")
        except UnicodeDecodeError:
            text = None
    elif content.startswith(b"\xff\xfe"):
        bom = "utf16le"
        encoding = "utf-16-le"
        try:
            text = content[2:].decode("utf-16-le")
        except UnicodeDecodeError:
            text = None
    elif content.startswith(b"\xfe\xff"):
        bom = "utf16be"
        encoding = "utf-16-be"
        try:
            text = content[2:].decode("utf-16-be")
        except UnicodeDecodeError:
            text = None
    else:
        try:
            text = content.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = None

    if text is None:
        return "unknown", "unknown" if bom == "none" else bom, "unknown"

    crlf = text.count("\r\n")
    without_crlf = text.replace("\r\n", "")
    lf = without_crlf.count("\n")
    cr = without_crlf.count("\r")
    kinds = sum(count > 0 for count in (crlf, lf, cr))
    if kinds == 0:
        newline = "none"
    elif kinds > 1:
        newline = "mixed"
    elif crlf:
        newline = "crlf"
    elif lf:
        newline = "lf"
    else:
        newline = "cr"
    return encoding, bom, newline


def _normalize_rights(value: Mapping[str, Any] | None) -> dict[str, Any]:
    rights = default_rights()
    if value is not None:
        rights.update(_json_copy(dict(value), field="rights"))

    status = str(rights.get("status", ""))
    if status not in _RIGHTS_STATUSES:
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"Unsupported rights status: {status or '<empty>'}.",
        )
    scope = str(rights.get("sharing_scope", ""))
    if scope not in _SHARING_SCOPES:
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            f"Unsupported sharing scope: {scope or '<empty>'}.",
        )
    redistribution = rights.get("redistribution_allowed")
    if redistribution is not None and not isinstance(redistribution, bool):
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            "redistribution_allowed must be true, false, or null.",
        )
    if not isinstance(rights.get("network_egress_allowed"), bool):
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            "network_egress_allowed must be a boolean.",
        )
    if rights.get("binary_inspection") is not False:
        raise AssetRegistryError(
            "ASSET_BINARY_INSPECTION_FORBIDDEN",
            "Binary inspection is permanently disabled.",
        )
    if not isinstance(rights.get("evidence_ref"), str):
        raise AssetRegistryError(
            "FLOW_SCHEMA_INVALID",
            "rights.evidence_ref must be a string.",
        )
    return rights


def _normalize_dependencies(
    dependencies: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, Mapping):
            raise AssetRegistryError(
                "FLOW_SCHEMA_INVALID",
                f"dependencies[{index}] must be an object.",
            )
        item = _json_copy(dict(dependency), field=f"dependencies[{index}]")
        for key in ("path", "locator", "source_locator"):
            if key in item:
                raise AssetRegistryError(
                    "FLOW_SCHEMA_INVALID",
                    "Dependency records must use identifiers, not source paths.",
                )
        for key, value in item.items():
            if key.endswith("_hash") and value is not None:
                if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                    raise AssetRegistryError(
                        "FLOW_SCHEMA_INVALID",
                        f"dependencies[{index}].{key} must be a SHA-256 hash.",
                    )
        result.append(item)
    return result


class AssetRegistry:
    """Persistent local registry for immutable, content-addressed source assets."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        clock: Any = _now_utc,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.registry_dir = self.data_dir / "asset-registry"
        self.object_dir = self.registry_dir / "objects" / "sha256"
        self.state_path = self.registry_dir / "registry.json"
        self.object_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._revisions: dict[str, dict[str, Any]] = {}
        self._asset_revisions: dict[str, list[str]] = {}
        self._load()

    def _clock_now(self) -> datetime:
        value = self._clock.now() if hasattr(self._clock, "now") else self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return datetime")
        return value

    def _load(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AssetRegistryError(
                "ASSET_REGISTRY_CORRUPT",
                "The local asset registry cannot be read safely.",
            ) from exc
        records = value.get("revisions") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not isinstance(records, list)
        ):
            raise AssetRegistryError(
                "ASSET_REGISTRY_CORRUPT",
                "The local asset registry has an unsupported shape.",
            )
        for record in records:
            if not isinstance(record, dict):
                raise AssetRegistryError(
                    "ASSET_REGISTRY_CORRUPT",
                    "The local asset registry contains an invalid revision.",
                )
            revision_id = str(record.get("asset_revision_id", ""))
            asset_id = str(record.get("asset_id", ""))
            content_hash = str(record.get("content_hash", ""))
            if (
                not revision_id
                or not asset_id
                or not _HASH_RE.fullmatch(content_hash)
                or revision_id in self._revisions
            ):
                raise AssetRegistryError(
                    "ASSET_REGISTRY_CORRUPT",
                    "The local asset registry contains an invalid revision identity.",
                )
            self._revisions[revision_id] = _json_copy(record, field="revision")
            self._asset_revisions.setdefault(asset_id, []).append(revision_id)

    def _save(self) -> None:
        value = {
            "schema_version": 1,
            "revisions": list(self._revisions.values()),
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _object_path(self, content_hash: str) -> Path:
        digest = content_hash.removeprefix("sha256:")
        return self.object_dir / digest[:2] / digest[2:]

    def _store_content(self, content: bytes, content_hash: str) -> None:
        path = self._object_path(content_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_bytes()
            if _content_digest(existing) != content_hash:
                raise AssetRegistryError(
                    "ASSET_CONTENT_TAMPERED",
                    "A content-addressed asset object failed its hash check.",
                )
            return
        try:
            with path.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            existing = path.read_bytes()
            if _content_digest(existing) != content_hash:
                raise AssetRegistryError(
                    "ASSET_CONTENT_TAMPERED",
                    "A content-addressed asset object failed its hash check.",
                )

    def register_asset(
        self,
        content: bytes | bytearray | memoryview,
        *,
        asset_id: str,
        product: str,
        asset_type: str,
        display_name: str,
        source_locator: str = "selected-file",
        source_origin: str = "unknown",
        rights: Mapping[str, Any] | None = None,
        target_versions: Iterable[str] = (),
        runtime_modes: Iterable[str] = ("offline",),
        dependencies: Iterable[Mapping[str, Any]] = (),
        imported_at: datetime | None = None,
        protected_binary: bool = False,
        extensions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw = bytes(content)
        logical_id = _nonempty(asset_id, field="asset_id")
        product_name = str(product)
        type_name = str(asset_type)
        if product_name not in _PRODUCTS:
            raise AssetRegistryError(
                "FLOW_SCHEMA_INVALID",
                "product must be nx or powermill.",
            )
        if type_name not in _ASSET_TYPES:
            raise AssetRegistryError(
                "FLOW_SCHEMA_INVALID",
                f"Unsupported asset_type: {type_name or '<empty>'}.",
            )
        name = _display_basename(display_name)
        locator_hint = str(source_locator)
        if protected_binary or _suffixes(name, locator_hint) & _BINARY_SUFFIXES:
            raise AssetRegistryError(
                "ASSET_BINARY_INSPECTION_FORBIDDEN",
                "Protected or commercial binary assets cannot be inspected or registered.",
            )
        if len(raw) > MAX_ASSET_BYTES:
            raise AssetRegistryError(
                "RESOURCE_LIMIT_EXCEEDED",
                "The asset exceeds the 10 MiB registry limit.",
            )

        encoding, bom, newline = _detect_text_profile(raw)
        if encoding == "unknown":
            raise AssetRegistryError(
                "ASSET_BINARY_INSPECTION_FORBIDDEN",
                "Binary or undecodable asset content cannot be inspected or registered.",
            )
        origin = str(source_origin)
        if origin not in _SOURCE_ORIGINS:
            raise AssetRegistryError(
                "FLOW_SCHEMA_INVALID",
                f"Unsupported source_origin: {origin or '<empty>'}.",
            )

        normalized_rights = _normalize_rights(rights)
        normalized_targets = _string_list(target_versions, field="target_versions")
        normalized_modes = _string_list(
            runtime_modes,
            field="runtime_modes",
            allow_empty=False,
        )
        normalized_dependencies = _normalize_dependencies(dependencies)
        normalized_extensions = _json_copy(
            dict(extensions or {}),
            field="extensions",
        )
        content_hash = _content_digest(raw)
        revision_id = _revision_id(logical_id, content_hash)

        with self._lock:
            existing = self._revisions.get(revision_id)
            if existing is not None:
                self._store_content(raw, content_hash)
                return copy.deepcopy(existing)

            imported = imported_at or self._clock_now()
            record = {
                "schema_version": 1,
                "contract": ASSET_CONTRACT,
                "asset_id": logical_id,
                "asset_revision_id": revision_id,
                "product": product_name,
                "asset_type": type_name,
                "display_name": name,
                "source_locator": f"local-content:{content_hash}",
                "content_hash": content_hash,
                "byte_length": len(raw),
                "encoding": encoding,
                "bom": bom,
                "newline_profile": newline,
                "immutable": True,
                "source_origin": origin,
                "rights": normalized_rights,
                "target_versions": normalized_targets,
                "runtime_modes": normalized_modes,
                "dependencies": normalized_dependencies,
                "imported_at": _rfc3339(imported),
                "extensions": normalized_extensions,
            }
            self._store_content(raw, content_hash)
            self._revisions[revision_id] = record
            self._asset_revisions.setdefault(logical_id, []).append(revision_id)
            self._save()
            return copy.deepcopy(record)

    def register(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.register_asset(*args, **kwargs)

    def register_bytes(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.register_asset(*args, **kwargs)

    def get_revision(self, asset_revision_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._revisions.get(str(asset_revision_id))
            if record is None:
                raise AssetRegistryError(
                    "ASSET_NOT_FOUND",
                    "The requested asset revision does not exist.",
                    details={"asset_revision_id": str(asset_revision_id)},
                )
            return copy.deepcopy(record)

    def get(self, asset_revision_id: str) -> dict[str, Any]:
        return self.get_revision(asset_revision_id)

    def list_revisions(self, asset_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if asset_id is None:
                revision_ids = list(self._revisions)
            else:
                revision_ids = list(self._asset_revisions.get(str(asset_id), ()))
            return [copy.deepcopy(self._revisions[item]) for item in revision_ids]

    def read_bytes(self, asset_revision_id: str) -> bytes:
        record = self.get_revision(asset_revision_id)
        path = self._object_path(record["content_hash"])
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise AssetRegistryError(
                "ASSET_CONTENT_MISSING",
                "The immutable asset content is unavailable.",
                details={"asset_revision_id": asset_revision_id},
            ) from exc
        if (
            len(content) != record["byte_length"]
            or _content_digest(content) != record["content_hash"]
        ):
            raise AssetRegistryError(
                "ASSET_CONTENT_TAMPERED",
                "The immutable asset content failed its integrity check.",
                details={"asset_revision_id": asset_revision_id},
            )
        return content

    def access_decision(
        self,
        asset_revision_id: str,
        operation: str,
    ) -> dict[str, Any]:
        record = self.get_revision(asset_revision_id)
        normalized = _OPERATION_ALIASES.get(str(operation), str(operation))
        if normalized not in {
            "read_local",
            "share",
            "redistribute",
            "network_egress",
            "preview",
            "binary_inspection",
        }:
            raise AssetRegistryError(
                "FLOW_SCHEMA_INVALID",
                f"Unsupported asset operation: {normalized or '<empty>'}.",
            )

        rights = record["rights"]
        status = rights["status"]
        code: str | None = None
        if normalized == "read_local":
            allowed = True
        elif normalized == "binary_inspection":
            allowed = False
            code = "ASSET_BINARY_INSPECTION_FORBIDDEN"
        elif status == "unreviewed":
            allowed = False
            code = "ASSET_RIGHTS_UNKNOWN"
        elif status == "restricted" or rights["sharing_scope"] == "restricted":
            allowed = False
            code = "ASSET_RIGHTS_RESTRICTED"
        elif normalized == "share":
            allowed = rights["sharing_scope"] in {"team", "site", "organization"}
            code = None if allowed else "ASSET_SHARING_FORBIDDEN"
        elif normalized == "redistribute":
            allowed = rights["redistribution_allowed"] is True
            code = None if allowed else "ASSET_REDISTRIBUTION_FORBIDDEN"
        elif normalized == "network_egress":
            allowed = rights["network_egress_allowed"] is True
            code = None if allowed else "ASSET_NETWORK_EGRESS_FORBIDDEN"
        else:
            allowed = True

        return {
            "allowed": allowed,
            "operation": normalized,
            "asset_revision_id": asset_revision_id,
            "rights_status": status,
            "code": code,
        }

    def require_operation(
        self,
        asset_revision_id: str,
        operation: str,
    ) -> dict[str, Any]:
        decision = self.access_decision(asset_revision_id, operation)
        if not decision["allowed"]:
            raise AssetAccessDenied(
                str(decision["code"]),
                "The asset operation is not permitted by the recorded rights state.",
                details={
                    "asset_revision_id": asset_revision_id,
                    "operation": decision["operation"],
                },
            )
        return decision

    def check_rights(
        self,
        asset_revision_id: str,
        operation: str,
    ) -> dict[str, Any]:
        return self.access_decision(asset_revision_id, operation)
