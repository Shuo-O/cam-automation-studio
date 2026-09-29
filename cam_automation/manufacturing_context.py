"""Manufacturing context v1: immutable, review-first snapshots.

The service deliberately stores JSON rather than attempting to open a CAD/CAM
document.  A snapshot is evidence about a host or an imported export; it is
not proof of a live connection and never authorizes execution.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
SUPPORTED_PRODUCTS = frozenset({"nx", "powermill"})
SUPPORTED_UNITS = frozenset({"mm", "inch"})
_REQUIRED = ("product", "instance_id", "project_id", "target_version", "units", "material", "machine", "objects")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def canonical_json(value: Any) -> bytes:
    """Return the byte representation used by all context hashes."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _verified_host_evidence(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    required = ("evidence_type", "instance_id", "project_id", "target_version")
    return (
        value.get("verified") is True
        and value.get("verification_status") == "independently_verified"
        and value.get("evidence_type") in {"official_api_runtime", "vendor_export"}
        and all(isinstance(value.get(key), str) and value[key].strip() for key in required)
    )


def resolve_snapshot_selector(
    snapshot: Mapping[str, Any], selector: Mapping[str, Any]
) -> dict[str, Any]:
    """Resolve one selector against an already-loaded snapshot.

    This pure helper is intentionally shared by case/proposal code.  It does
    not read files or mutate the snapshot, and returns ``ambiguous`` rather
    than picking the first matching object.
    """

    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot must be an object")
    if not isinstance(selector, Mapping) or not selector:
        raise ValueError("selector must be a non-empty object")
    allowed = {"object_id", "name", "kind", "attributes"}
    unknown = set(selector) - allowed
    if unknown:
        raise ValueError(f"unsupported selector fields: {sorted(unknown)}")
    criteria_attributes = selector.get("attributes")
    if criteria_attributes is not None and not isinstance(criteria_attributes, Mapping):
        raise ValueError("selector.attributes must be an object")
    objects = snapshot.get("objects")
    if not isinstance(objects, list):
        raise ValueError("snapshot.objects must be a list")

    def attributes_match(actual: Any, expected: Mapping[str, Any]) -> bool:
        if not isinstance(actual, Mapping):
            return False
        for key, value in expected.items():
            if key not in actual:
                return False
            candidate = actual[key]
            if isinstance(value, Mapping):
                if not attributes_match(candidate, value):
                    return False
            elif candidate != value:
                return False
        return True

    matches: list[dict[str, Any]] = []
    for item in objects:
        if not isinstance(item, Mapping):
            continue
        if "object_id" in selector and item.get("object_id") != selector["object_id"]:
            continue
        if "name" in selector and item.get("name") != selector["name"]:
            continue
        if "kind" in selector and item.get("kind") != selector["kind"]:
            continue
        if criteria_attributes is not None and not attributes_match(
            item.get("attributes"), criteria_attributes
        ):
            continue
        matches.append(_json_copy(item))
    status = "resolved" if len(matches) == 1 else ("ambiguous" if matches else "unresolved")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "selector": _json_copy(selector),
        "matches": matches,
        "snapshot_hash": snapshot.get("content_hash"),
        "snapshot_id": snapshot.get("snapshot_id"),
    }


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _validate_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _validate_nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


class ManufacturingContextService:
    """Thread-safe local persistence and selector resolution for context v1."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._snapshot_dir = self.root / "snapshots"
        self._lock = threading.RLock()
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)

    def import_snapshot(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate, hash, and atomically persist an imported/host snapshot."""

        if not isinstance(payload, Mapping):
            raise ValueError("snapshot payload must be an object")
        raw_schema_version = payload.get("schema_version", SCHEMA_VERSION)
        if isinstance(raw_schema_version, bool) or not isinstance(raw_schema_version, int):
            raise ValueError("snapshot schema_version must be 1")
        schema_version = raw_schema_version
        if schema_version != SCHEMA_VERSION:
            raise ValueError("snapshot schema_version must be 1")

        normalized = self._normalize_payload(payload)
        source = normalized["source"]
        warnings = list(normalized["warnings"])
        if source == "fixture" and not normalized.get("fixture_id"):
            fixture_identity = {
                key: value
                for key, value in normalized.items()
                if key not in {"fixture_id", "warnings", "host_evidence"}
            }
            normalized["fixture_id"] = (
                "fixture-" + content_hash(fixture_identity).removeprefix("sha256:")[:24]
            )
            warnings.append("fixture_id was generated from the immutable snapshot contents")
        if source not in {"imported", "fixture", "host_read_only"}:
            raise ValueError("source must be imported, fixture, or host_read_only")
        if source == "host_read_only" and not normalized.get("host_evidence"):
            raise ValueError("host_read_only snapshots require host_evidence")
        if source == "imported":
            warnings.append("imported snapshot is not proof of a live host connection")
        if source == "fixture":
            warnings.append("fixture snapshot is synthetic evidence and requires real-host revalidation")
        if source == "host_read_only":
            evidence = normalized.get("host_evidence") or {}
            if not _verified_host_evidence(evidence):
                warnings.append("host API evidence is not independently verified on this machine")

        hash_payload = {
            key: value
            for key, value in normalized.items()
            if key not in {"snapshot_id", "content_hash", "captured_at", "warnings"}
        }
        digest = content_hash(hash_payload)
        snapshot_id = str(
            payload.get("snapshot_id")
            or f"snapshot-{digest.removeprefix('sha256:')[:24]}"
        )
        if not snapshot_id.startswith("snapshot-"):
            raise ValueError("snapshot_id must use the snapshot- prefix")
        snapshot = dict(normalized)
        snapshot.update(
            {
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": snapshot_id,
                "content_hash": digest,
                "captured_at": str(payload.get("captured_at") or _utc_now()),
                "warnings": list(dict.fromkeys(warnings)),
            }
        )
        target = self._path_for(snapshot_id)
        with self._lock:
            if target.is_file():
                try:
                    existing = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(f"persisted snapshot is unreadable: {snapshot_id}") from error
                if not isinstance(existing, Mapping):
                    raise ValueError(f"persisted snapshot is invalid: {snapshot_id}")
                self._verify_record(existing, snapshot_id)
                if existing.get("content_hash") != snapshot["content_hash"]:
                    raise ValueError(
                        f"snapshot_id already exists with different content: {snapshot_id}"
                    )
                return _json_copy(existing)
            _atomic_json_write(target, snapshot)
        return _json_copy(snapshot)

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            records: list[dict[str, Any]] = []
            for path in sorted(self._snapshot_dir.glob("snapshot-*.json")):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise ValueError(f"persisted snapshot is unreadable: {path.name}") from error
                if isinstance(value, dict):
                    self._verify_record(value, str(value.get("snapshot_id") or path.stem))
                    records.append(value)
            return _json_copy(records)

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("snapshot_id must be a non-empty string")
        path = self._path_for(snapshot_id)
        with self._lock:
            if not path.is_file():
                raise KeyError(snapshot_id)
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(f"persisted snapshot is unreadable: {snapshot_id}") from error
        if not isinstance(value, dict):
            raise KeyError(snapshot_id)
        self._verify_record(value, snapshot_id)
        return _json_copy(value)

    def resolve_selector(self, snapshot_id: str, selector: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self.get_snapshot(snapshot_id)
        return resolve_snapshot_selector(snapshot, selector)

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "service": "manufacturing_context",
            "products": {
                "nx": {"snapshot_import": True, "host_export": "unverified", "live_connection": False},
                "powermill": {
                    "snapshot_import": True,
                    "host_export": "unverified",
                    "live_connection": False,
                },
            },
            "persistence": {"format": "json", "atomic_writes": True, "thread_safe": True},
            "selectors": {
                "supported": ["object_id", "name", "kind", "attributes"],
                "unique_binding_required": True,
            },
            "safety": {
                "dry_run": True,
                "machine_output": False,
                "execution": False,
                "live_connection_claim_requires_evidence": True,
            },
        }

    def _path_for(self, snapshot_id: str) -> Path:
        if (
            "/" in snapshot_id
            or "\\" in snapshot_id
            or snapshot_id in {".", ".."}
            or any(character in snapshot_id for character in '<>:"|?*')
            or snapshot_id.endswith((" ", "."))
        ):
            raise ValueError("snapshot_id must be a portable filename without path separators")
        return self._snapshot_dir / f"{snapshot_id}.json"

    def _normalize_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        product = _validate_nonempty_text(payload.get("product"), "product").casefold()
        if product not in SUPPORTED_PRODUCTS:
            raise ValueError(f"unsupported product: {product}")
        instance_id = _validate_nonempty_text(payload.get("instance_id"), "instance_id")
        project_id = _validate_nonempty_text(payload.get("project_id"), "project_id")
        target_version = _validate_nonempty_text(payload.get("target_version"), "target_version")
        units = _validate_nonempty_text(payload.get("units"), "units").casefold()
        if units not in SUPPORTED_UNITS:
            raise ValueError("units must be mm or inch")
        material = _validate_nonempty_text(payload.get("material"), "material")
        machine = _validate_mapping(payload.get("machine"), "machine")
        axes = machine.get("axes")
        if type(axes) is not int or axes != 3:
            raise ValueError("machine.axes must be 3")
        objects = payload.get("objects")
        if not isinstance(objects, list):
            raise ValueError("objects must be a list")
        normalized_objects: list[dict[str, Any]] = []
        object_ids: set[str] = set()
        for index, raw in enumerate(objects):
            item = _validate_mapping(raw, f"objects[{index}]")
            object_id = _validate_nonempty_text(item.get("object_id"), f"objects[{index}].object_id")
            if object_id in object_ids:
                raise ValueError(f"duplicate object_id: {object_id}")
            object_ids.add(object_id)
            kind = _validate_nonempty_text(item.get("kind"), f"objects[{index}].kind")
            name = _validate_nonempty_text(item.get("name"), f"objects[{index}].name")
            attributes = item.get("attributes", {})
            if not isinstance(attributes, Mapping):
                raise ValueError(f"objects[{index}].attributes must be an object")
            base = {
                "object_id": object_id,
                "kind": kind,
                "name": name,
                "attributes": _json_copy(attributes),
            }
            # Preserve optional geometry/manufacturing evidence before hashing it.
            for key, value in item.items():
                if key not in base and key != "object_hash":
                    base[str(key)] = _json_copy(value)
            base["object_hash"] = content_hash(base)
            normalized_objects.append(base)

        known = {
            "schema_version",
            "product",
            "instance_id",
            "project_id",
            "target_version",
            "units",
            "material",
            "machine",
            "objects",
            "stock",
            "tools",
            "fixtures",
            "geometry_digest",
            "source",
            "fixture_id",
            "host_evidence",
            "warnings",
            "snapshot_id",
            "captured_at",
            "content_hash",
            "extensions",
        }
        raw_warnings = payload.get("warnings", [])
        if not isinstance(raw_warnings, list) or any(
            not isinstance(item, str) for item in raw_warnings
        ):
            raise ValueError("warnings must be a list of strings")
        if "host_evidence" in payload and not isinstance(payload["host_evidence"], Mapping):
            raise ValueError("host_evidence must be an object")
        if "fixture_id" in payload and not isinstance(payload["fixture_id"], str):
            raise ValueError("fixture_id must be a string")
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "product": product,
            "instance_id": instance_id,
            "project_id": project_id,
            "target_version": target_version,
            "units": units,
            "material": material,
            "machine": _json_copy(machine),
            "objects": normalized_objects,
            "source": str(payload.get("source") or "imported"),
            "warnings": list(raw_warnings),
        }
        for key in ("stock", "tools", "fixtures", "geometry_digest", "fixture_id", "host_evidence"):
            if key in payload:
                result[key] = _json_copy(payload[key])
        if isinstance(payload.get("extensions"), Mapping):
            result["extensions"] = _json_copy(payload["extensions"])
        for key, value in payload.items():
            if key not in known:
                result.setdefault("extensions", {})[str(key)] = _json_copy(value)
        return result

    @staticmethod
    def _verify_record(value: Mapping[str, Any], snapshot_id: str) -> None:
        if value.get("snapshot_id") != snapshot_id:
            raise ValueError(f"persisted snapshot id mismatch: {snapshot_id}")
        digest = value.get("content_hash")
        if not isinstance(digest, str):
            raise ValueError(f"persisted snapshot has no content_hash: {snapshot_id}")
        for item in value.get("objects", []):
            if not isinstance(item, Mapping):
                raise ValueError(f"persisted snapshot has invalid object: {snapshot_id}")
            object_hash = item.get("object_hash")
            object_body = {key: item[key] for key in item if key != "object_hash"}
            if object_hash != content_hash(object_body):
                raise ValueError(f"persisted object hash mismatch: {snapshot_id}")
        hash_payload = {
            key: item
            for key, item in value.items()
            if key not in {"snapshot_id", "content_hash", "captured_at", "warnings"}
        }
        if digest != content_hash(hash_payload):
            raise ValueError(f"persisted snapshot hash mismatch: {snapshot_id}")


__all__ = [
    "ManufacturingContextService",
    "canonical_json",
    "content_hash",
    "resolve_snapshot_selector",
    "SCHEMA_VERSION",
]
