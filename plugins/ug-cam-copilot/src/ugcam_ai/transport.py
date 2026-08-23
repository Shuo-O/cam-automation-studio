from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


READ_ONLY_NX_OPERATIONS = frozenset(
    {
        "nx.cam.operations.list",
        "nx.cam.setup.describe",
        "nx.cam.tools.list",
        "nx.part.describe",
        "nx.project.describe",
        "nx.selection.preview",
        "nx.session.describe",
    }
)
_DANGEROUS_OPERATION_MARKERS = (
    "commit",
    "create",
    "destroy",
    "execute",
    "gcode",
    "journal",
    "machine",
    "nc.",
    "post",
    "run",
    "save",
    "write",
)
_DANGEROUS_PAYLOAD_KEYS = frozenset(
    {
        "gcode",
        "g_code",
        "journal_code",
        "journal_source",
        "machine_command",
        "machine_ready",
        "nc",
        "nc_code",
        "nc_output",
        "postprocess",
        "postprocessor",
    }
)
_MACHINE_CODE_LINE = re.compile(r"(?im)^\s*(?:G0?[0-3]|G8[0-9]|M0?[0-9]|N[0-9]+)\b")


class NxTransportError(RuntimeError):
    pass


class NxTransportUnavailableError(NxTransportError):
    pass


class NxInstanceNotFoundError(NxTransportError):
    pass


class NxTransportSafetyError(NxTransportError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResponse:
    schema_version: int
    response_id: str
    task_id: str
    product: str
    target_version: str
    target_instance_id: str
    project_id: str
    status: str
    started_at: str
    completed_at: str
    duration_ms: int
    raw_response: str | None
    structured_response: Mapping[str, Any] | None
    diff_report: Mapping[str, Any] | None
    error: Mapping[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "response_id": self.response_id,
            "task_id": self.task_id,
            "product": self.product,
            "target_version": self.target_version,
            "target_instance_id": self.target_instance_id,
            "project_id": self.project_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "raw_response": self.raw_response,
            "structured_response": (
                copy.deepcopy(dict(self.structured_response))
                if self.structured_response is not None
                else None
            ),
            "diff_report": (
                copy.deepcopy(dict(self.diff_report))
                if self.diff_report is not None
                else None
            ),
            "error": copy.deepcopy(dict(self.error)) if self.error is not None else None,
        }


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    schema_version: int
    snapshot_id: str
    product: str
    target_instance_id: str
    target_version: str
    project_id: str
    captured_at: str
    content_hash: str
    transport: str
    read_only: bool
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "product": self.product,
            "target_instance_id": self.target_instance_id,
            "target_version": self.target_version,
            "project_id": self.project_id,
            "captured_at": self.captured_at,
            "content_hash": self.content_hash,
            "transport": self.transport,
            "read_only": self.read_only,
            "metadata": copy.deepcopy(dict(self.metadata)),
        }


class NxTransport:
    """Fail-closed NX query boundary; no live connector is configured by default."""

    def query(
        self,
        instance_id: str,
        operation: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
    ) -> CommandResponse:
        raise NxTransportUnavailableError(
            "live NX transport is not configured; use an explicit fixture transport"
        )

    def snapshot(self, instance_id: str) -> SnapshotMetadata:
        raise NxTransportUnavailableError(
            "live NX transport is not configured; use an explicit fixture transport"
        )


@dataclass(frozen=True, slots=True)
class _FixtureInstance:
    instance_id: str
    target_version: str
    project_id: str
    queries: Mapping[str, Any]
    snapshot_payload: Mapping[str, Any]


class FixtureNxTransport(NxTransport):
    """Pure in-memory transport with per-instance fixtures and no NX connection."""

    def __init__(self, instances: Mapping[str, _FixtureInstance]) -> None:
        self._instances = copy.deepcopy(dict(instances))
        self._sequence = {instance_id: 0 for instance_id in instances}
        self._lock = threading.Lock()

    @classmethod
    def from_path(cls, path: str | Path) -> "FixtureNxTransport":
        source = Path(path)
        try:
            value = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise NxTransportError(f"invalid NX transport fixture {source}: {error}") from error
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixtureNxTransport":
        if int(value.get("schema_version", 1)) != 1:
            raise NxTransportError("NX transport fixture schema_version must be 1")
        raw_instances = value.get("instances")
        if not isinstance(raw_instances, list) or not raw_instances:
            raise NxTransportError("NX transport fixture requires a non-empty instances list")
        instances: dict[str, _FixtureInstance] = {}
        for raw in raw_instances:
            if not isinstance(raw, Mapping):
                raise NxTransportError("each NX fixture instance must be an object")
            if str(raw.get("product", "nx")).lower() != "nx":
                raise NxTransportError("PowerMill fixture data is not accepted by NxTransport")
            instance_id = str(raw.get("instance_id", "")).strip()
            target_version = str(raw.get("target_version", "")).strip()
            project_id = str(raw.get("project_id", "")).strip()
            queries = raw.get("queries", {})
            snapshot_payload = raw.get("snapshot", {})
            if not instance_id or not target_version or not project_id:
                raise NxTransportError(
                    "fixture instance_id, target_version and project_id are required"
                )
            if instance_id in instances:
                raise NxTransportError(f"duplicate NX fixture instance: {instance_id}")
            if not isinstance(queries, Mapping) or not isinstance(
                snapshot_payload, Mapping
            ):
                raise NxTransportError("fixture queries and snapshot must be objects")
            instances[instance_id] = _FixtureInstance(
                instance_id=instance_id,
                target_version=target_version,
                project_id=project_id,
                queries=copy.deepcopy(dict(queries)),
                snapshot_payload=copy.deepcopy(dict(snapshot_payload)),
            )
        return cls(instances)

    def query(
        self,
        instance_id: str,
        operation: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
    ) -> CommandResponse:
        fixture = self._instance(instance_id)
        arguments = copy.deepcopy(dict(arguments or {}))
        response_id, resolved_task_id = self._next_ids(instance_id, task_id)
        now = _utc_now()
        rejection = self._validate_query(operation, arguments)
        if rejection is not None:
            code, message = rejection
            return self._response(
                fixture,
                response_id=response_id,
                task_id=resolved_task_id,
                status="rejected",
                started_at=now,
                structured_response=None,
                error={"code": code, "message": message},
            )
        if operation not in fixture.queries:
            return self._response(
                fixture,
                response_id=response_id,
                task_id=resolved_task_id,
                status="rejected",
                started_at=now,
                structured_response=None,
                error={
                    "code": "nx.transport.fixture_query_missing",
                    "message": (
                        f"read-only operation {operation!r} has no fixture response "
                        f"for instance {instance_id!r}"
                    ),
                },
            )
        payload = copy.deepcopy(fixture.queries[operation])
        if _contains_machine_ready_content(payload):
            return self._response(
                fixture,
                response_id=response_id,
                task_id=resolved_task_id,
                status="rejected",
                started_at=now,
                structured_response=None,
                error={
                    "code": "nx.transport.machine_ready_output_blocked",
                    "message": "fixture payload was discarded because it resembles machine-ready output",
                },
            )
        structured = {
            "transport": "fixture",
            "execution_mode": "read_only",
            "operation": operation,
            "arguments": arguments,
            "result": payload,
            "commands_sent": 0,
            "journal_executed": False,
            "machine_ready_output_included": False,
            "review_first": True,
            "production_approved": False,
        }
        return self._response(
            fixture,
            response_id=response_id,
            task_id=resolved_task_id,
            status="succeeded",
            started_at=now,
            structured_response=structured,
            error=None,
        )

    def snapshot(self, instance_id: str) -> SnapshotMetadata:
        fixture = self._instance(instance_id)
        payload = copy.deepcopy(dict(fixture.snapshot_payload))
        if _contains_machine_ready_content(payload):
            raise NxTransportSafetyError(
                "snapshot payload was discarded because it resembles machine-ready output"
            )
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        digest = hashlib.sha256(serialized).hexdigest()
        return SnapshotMetadata(
            schema_version=1,
            snapshot_id=f"nx-snapshot:{_short_instance(instance_id)}:{digest[:16]}",
            product="nx",
            target_instance_id=fixture.instance_id,
            target_version=fixture.target_version,
            project_id=fixture.project_id,
            captured_at=_utc_now(),
            content_hash=f"sha256:{digest}",
            transport="fixture",
            read_only=True,
            metadata={
                "result": payload,
                "commands_sent": 0,
                "journal_executed": False,
                "machine_ready_output_included": False,
                "review_first": True,
                "production_approved": False,
            },
        )

    def _instance(self, instance_id: str) -> _FixtureInstance:
        try:
            return self._instances[instance_id]
        except KeyError as error:
            raise NxInstanceNotFoundError(
                f"NX fixture instance is not configured: {instance_id}"
            ) from error

    def _next_ids(self, instance_id: str, task_id: str | None) -> tuple[str, str]:
        with self._lock:
            self._sequence[instance_id] += 1
            sequence = self._sequence[instance_id]
        instance_token = _short_instance(instance_id)
        resolved_task_id = task_id or f"nx-query:{instance_token}:{sequence}"
        return f"nx-response:{instance_token}:{sequence}", resolved_task_id

    @staticmethod
    def _validate_query(
        operation: str, arguments: Mapping[str, Any]
    ) -> tuple[str, str] | None:
        lowered = operation.strip().lower()
        if any(marker in lowered for marker in _DANGEROUS_OPERATION_MARKERS):
            return (
                "nx.transport.dangerous_operation",
                "Journal execution, CAM writes, NC/G-code, postprocessing and machine "
                "control are forbidden",
            )
        if operation not in READ_ONLY_NX_OPERATIONS:
            return (
                "nx.transport.operation_not_allowed",
                f"operation {operation!r} is not in the read-only NX allowlist",
            )
        product = arguments.get("product")
        if product is not None and str(product).lower() != "nx":
            return (
                "nx.transport.product_mismatch",
                "PowerMill arguments are not accepted by NxTransport",
            )
        if _contains_machine_ready_content(arguments):
            return (
                "nx.transport.dangerous_payload",
                "query arguments contain Journal, NC/G-code, postprocess or machine-control content",
            )
        return None

    @staticmethod
    def _response(
        fixture: _FixtureInstance,
        *,
        response_id: str,
        task_id: str,
        status: str,
        started_at: str,
        structured_response: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None,
    ) -> CommandResponse:
        return CommandResponse(
            schema_version=1,
            response_id=response_id,
            task_id=task_id,
            product="nx",
            target_version=fixture.target_version,
            target_instance_id=fixture.instance_id,
            project_id=fixture.project_id,
            status=status,
            started_at=started_at,
            completed_at=_utc_now(),
            duration_ms=0,
            raw_response=None,
            structured_response=copy.deepcopy(structured_response),
            diff_report=None,
            error=copy.deepcopy(error),
        )


def _contains_machine_ready_content(value: Any, *, key: str = "") -> bool:
    normalized_key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    if normalized_key in _DANGEROUS_PAYLOAD_KEYS:
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_machine_ready_content(item, key=str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_machine_ready_content(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        if _MACHINE_CODE_LINE.search(value):
            return True
        return any(
            marker in lowered
            for marker in (
                "g-code",
                "machine-ready",
                "postprocess(",
                "run_journal",
            )
        )
    return False


def _short_instance(instance_id: str) -> str:
    return hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:12]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
