from __future__ import annotations

import copy
import hashlib
import inspect
import json
import queue
import re
import threading
import time
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .execution import (
    classify_command,
    contains_machine_ready_output,
    redact_for_audit,
    transport_operation_allowed,
)


__all__ = [
    "CommandResponse",
    "CommandTask",
    "CommandTaskService",
    "TASK_STATUSES",
    "TERMINAL_STATUSES",
    "TaskDescriptor",
    "TaskStatus",
]

SCHEMA_VERSION = 1
TASK_STATUSES = frozenset(
    {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "disconnected",
        "rejected",
    }
)
TERMINAL_STATUSES = TASK_STATUSES - {"queued", "running"}
_RECIPE_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_STOP = object()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


class _FrozenDict(dict):
    def _immutable(self, *_: Any, **__: Any) -> None:
        raise TypeError("contract mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenDict(
            {
                str(item_key): _freeze_json(item)
                for item_key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _required_string(value: Any, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


@dataclass(frozen=True, slots=True)
class CommandTask:
    task_id: str
    task_type: str
    execution_mode: str
    product: str
    target_version: str
    target_instance_id: str
    project_id: str
    recipe_hash: str | None
    operation: str
    arguments: Mapping[str, Any]
    status: str
    submitted_at: str
    timeout_ms: int
    requested_by: str
    review: Mapping[str, Any] | None
    queue_timeout_ms: int | None = None
    execution_timeout_ms: int | None = None
    idempotency_key: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("CommandTask schema_version must be 1")
        object.__setattr__(self, "task_id", _required_string(self.task_id, "task_id"))
        task_type = _required_string(self.task_type, "task_type").lower()
        if task_type not in {"query", "recipe_preview"}:
            raise ValueError("task_type must be query or recipe_preview")
        object.__setattr__(self, "task_type", task_type)
        execution_mode = (
            _required_string(self.execution_mode, "execution_mode")
            .lower()
            .replace("-", "_")
        )
        if execution_mode not in {"read_only", "dry_run"}:
            raise ValueError("execution_mode must be read_only or dry_run")
        object.__setattr__(self, "execution_mode", execution_mode)
        product = _required_string(self.product, "product").lower()
        if product not in {"nx", "powermill"}:
            raise ValueError("product must be nx or powermill")
        object.__setattr__(self, "product", product)
        object.__setattr__(
            self,
            "target_version",
            _required_string(self.target_version, "target_version"),
        )
        object.__setattr__(
            self,
            "target_instance_id",
            _required_string(self.target_instance_id, "target_instance_id"),
        )
        object.__setattr__(
            self,
            "project_id",
            _required_string(self.project_id, "project_id"),
        )
        object.__setattr__(
            self,
            "operation",
            _required_string(self.operation, "operation"),
        )
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be an object")
        object.__setattr__(
            self,
            "arguments",
            _freeze_json(_json_copy(dict(self.arguments))),
        )
        status = _required_string(self.status, "status").lower()
        if status != "queued":
            raise ValueError("new CommandTask status must be queued")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "submitted_at",
            _required_string(self.submitted_at, "submitted_at"),
        )
        if isinstance(self.timeout_ms, bool) or int(self.timeout_ms) <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        object.__setattr__(self, "timeout_ms", int(self.timeout_ms))
        for field_name in ("queue_timeout_ms", "execution_timeout_ms"):
            value = getattr(self, field_name)
            if value is not None:
                if isinstance(value, bool) or int(value) <= 0:
                    raise ValueError(f"{field_name} must be a positive integer")
                object.__setattr__(self, field_name, int(value))
        object.__setattr__(
            self,
            "requested_by",
            _required_string(self.requested_by, "requested_by"),
        )
        if self.review is not None:
            if not isinstance(self.review, Mapping):
                raise TypeError("review must be an object or null")
            object.__setattr__(
                self,
                "review",
                _freeze_json(_json_copy(dict(self.review))),
            )
        if self.idempotency_key is not None:
            object.__setattr__(
                self,
                "idempotency_key",
                _required_string(self.idempotency_key, "idempotency_key"),
            )
        if self.recipe_hash is not None:
            recipe_hash = str(self.recipe_hash).strip()
            if not _RECIPE_HASH.fullmatch(recipe_hash):
                raise ValueError("recipe_hash must be null or sha256:<64 lowercase hex>")
            object.__setattr__(self, "recipe_hash", recipe_hash)
        if self.task_type == "query":
            if self.execution_mode != "read_only":
                raise ValueError("query tasks require read_only execution")
        else:
            if self.execution_mode != "dry_run":
                raise ValueError("recipe_preview tasks require dry_run execution")
            if self.recipe_hash is None:
                raise ValueError("recipe_preview requires recipe_hash")
            if self.review is None:
                raise ValueError("recipe_preview requires an identified review")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CommandTask:
        required = {
            "task_id",
            "task_type",
            "execution_mode",
            "product",
            "target_version",
            "target_instance_id",
            "project_id",
            "recipe_hash",
            "operation",
            "arguments",
            "status",
            "submitted_at",
            "timeout_ms",
            "requested_by",
            "review",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError("CommandTask is missing required fields: " + ", ".join(missing))
        return cls(
            schema_version=int(value.get("schema_version", SCHEMA_VERSION)),
            task_id=value["task_id"],
            task_type=value["task_type"],
            execution_mode=value["execution_mode"],
            product=value["product"],
            target_version=value["target_version"],
            target_instance_id=value["target_instance_id"],
            project_id=value["project_id"],
            recipe_hash=value["recipe_hash"],
            operation=value["operation"],
            arguments=value["arguments"],
            status=value["status"],
            submitted_at=value["submitted_at"],
            timeout_ms=value["timeout_ms"],
            requested_by=value["requested_by"],
            review=value["review"],
            queue_timeout_ms=value.get("queue_timeout_ms"),
            execution_timeout_ms=value.get("execution_timeout_ms"),
            idempotency_key=value.get("idempotency_key"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "execution_mode": self.execution_mode,
            "product": self.product,
            "target_version": self.target_version,
            "target_instance_id": self.target_instance_id,
            "project_id": self.project_id,
            "recipe_hash": self.recipe_hash,
            "operation": self.operation,
            "arguments": _json_copy(self.arguments),
            "status": self.status,
            "submitted_at": self.submitted_at,
            "timeout_ms": self.timeout_ms,
            "requested_by": self.requested_by,
            "review": _json_copy(self.review),
            "queue_timeout_ms": self.queue_timeout_ms,
            "execution_timeout_ms": self.execution_timeout_ms,
            "idempotency_key": self.idempotency_key,
        }

    @property
    def effective_queue_timeout_ms(self) -> int:
        return self.queue_timeout_ms or self.timeout_ms

    @property
    def effective_execution_timeout_ms(self) -> int:
        return self.execution_timeout_ms or self.timeout_ms

    def fingerprint(self) -> str:
        value = self.to_dict()
        for name in ("task_id", "status", "submitted_at", "idempotency_key"):
            value.pop(name, None)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CommandResponse:
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
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("structured_response", "diff_report", "error"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _freeze_json(_json_copy(dict(value))),
                )

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
            "structured_response": _json_copy(self.structured_response),
            "diff_report": _json_copy(self.diff_report),
            "error": _json_copy(self.error),
        }

    @property
    def raw(self) -> str | None:
        return self.raw_response

    @property
    def structured(self) -> Mapping[str, Any] | None:
        return self.structured_response

    @property
    def elapsed_ms(self) -> int:
        return self.duration_ms


@dataclass(frozen=True, slots=True)
class TaskStatus:
    task: CommandTask
    status: str
    progress: int
    queued_at: str
    started_at: str | None
    completed_at: str | None
    elapsed_ms: int | None
    response: CommandResponse | None
    error: Mapping[str, Any] | None
    status_history: tuple[Mapping[str, Any], ...]

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def product(self) -> str:
        return self.task.product

    @property
    def target_instance_id(self) -> str:
        return self.task.target_instance_id

    def to_dict(self) -> dict[str, Any]:
        value = self.task.to_dict()
        value.update(
            {
                "status": self.status,
                "progress": self.progress,
                "queued_at": self.queued_at,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "elapsed_ms": self.elapsed_ms,
                "response": self.response.to_dict() if self.response else None,
                "error": _json_copy(self.error),
                "status_history": _json_copy(list(self.status_history)),
            }
        )
        return value


TaskDescriptor = TaskStatus


@dataclass(slots=True)
class _TaskRecord:
    task: CommandTask
    fingerprint: str
    status: str
    progress: int
    queued_at: str
    queued_monotonic: float
    queue_deadline: float
    started_at: str | None = None
    started_monotonic: float | None = None
    execution_deadline: float | None = None
    completed_at: str | None = None
    completed_monotonic: float | None = None
    response: CommandResponse | None = None
    error: Mapping[str, Any] | None = None
    history: list[Mapping[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _Worker:
    instance_id: str
    items: queue.Queue[Any]
    thread: threading.Thread


class CommandTaskService:
    """Per-instance serial command queues with immutable terminal ownership."""

    def __init__(
        self,
        transports: Mapping[str, Any] | None = None,
        *,
        instances: Mapping[str, Any] | None = None,
        instance_provider: Any | None = None,
        recipe_service: Any | None = None,
        audit_sink: Any | None = None,
        result_cache_size: int = 256,
        audit_cache_size: int = 1024,
        timeout_poll_interval: float = 0.01,
        close_timeout: float = 1.0,
        clock: Callable[[], str] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if result_cache_size < 1:
            raise ValueError("result_cache_size must be positive")
        if audit_cache_size < 1:
            raise ValueError("audit_cache_size must be positive")
        if timeout_poll_interval <= 0:
            raise ValueError("timeout_poll_interval must be positive")
        self._transports = {
            str(product).strip().lower(): transport
            for product, transport in (transports or {}).items()
        }
        self._instances = dict(instances or {})
        self._instance_provider = instance_provider
        self._recipe_service = recipe_service
        self._audit_sink = audit_sink
        self._result_cache_size = result_cache_size
        self._timeout_poll_interval = timeout_poll_interval
        self._close_timeout = max(0.0, close_timeout)
        self._clock = clock
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._records: dict[str, _TaskRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._terminal_order: OrderedDict[str, None] = OrderedDict()
        self._evicted: OrderedDict[str, tuple[str, str | None]] = OrderedDict()
        self._workers: dict[str, _Worker] = {}
        self._closed = False
        self._stop = threading.Event()
        self._metrics: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "submitted_count": 0,
                "terminal_counts": Counter(),
                "durations_ms": deque(maxlen=max(32, result_cache_size)),
            }
        )
        self._diagnostic_lock = threading.Lock()
        self._diagnostic_cache: dict[str, Mapping[str, Any]] = {}
        self._audit_events: deque[Mapping[str, Any]] = deque(maxlen=audit_cache_size)
        self._audit_items: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._audit_thread = threading.Thread(
            target=self._audit_loop,
            name="cam-command-audit",
            daemon=True,
        )
        self._audit_thread.start()
        self._timeout_thread = threading.Thread(
            target=self._timeout_loop,
            name="cam-command-timeouts",
            daemon=True,
        )
        self._timeout_thread.start()

    def register_transport(self, product: str, transport: Any) -> None:
        normalized = product.strip().lower()
        if normalized not in {"nx", "powermill"}:
            raise ValueError("unsupported transport product")
        with self._lock:
            if self._closed:
                raise RuntimeError("CommandTaskService is closed")
            self._transports[normalized] = transport

    def register_instance(self, descriptor: Any) -> None:
        value = self._descriptor_mapping(descriptor)
        instance_id = _required_string(value.get("instance_id"), "instance_id")
        with self._lock:
            if self._closed:
                raise RuntimeError("CommandTaskService is closed")
            self._instances[instance_id] = descriptor

    def submit(self, task: CommandTask | Mapping[str, Any]) -> TaskDescriptor:
        current = task if isinstance(task, CommandTask) else CommandTask.from_dict(task)
        fingerprint = current.fingerprint()
        descriptor, descriptor_error = self._resolve_and_validate_target(current)
        rejection = self._submission_rejection(current, descriptor, descriptor_error)
        now_mono = self._monotonic()
        now = self._clock()
        with self._condition:
            if self._closed:
                raise RuntimeError("CommandTaskService is closed")
            existing = self._records.get(current.task_id)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise ValueError("task_id idempotency conflict")
                return self._snapshot_locked(existing)
            evicted = self._evicted.get(current.task_id)
            if evicted is not None:
                if evicted[0] != fingerprint:
                    raise ValueError("task_id idempotency conflict")
                raise KeyError(
                    "The idempotent result was evicted and will not be replayed."
                )
            if current.idempotency_key:
                existing_id = self._idempotency.get(current.idempotency_key)
                if existing_id:
                    existing = self._records.get(existing_id)
                    if existing is None:
                        evicted = self._evicted.get(existing_id)
                        if evicted is not None:
                            if evicted[0] != fingerprint:
                                raise ValueError("idempotency_key payload conflict")
                            raise KeyError(
                                "The idempotent result was evicted and will not be replayed."
                            )
                        self._idempotency.pop(current.idempotency_key, None)
                    elif existing.fingerprint != fingerprint:
                        raise ValueError("idempotency_key payload conflict")
                    else:
                        return self._snapshot_locked(existing)
            record = _TaskRecord(
                task=current,
                fingerprint=fingerprint,
                status="queued",
                progress=0,
                queued_at=now,
                queued_monotonic=now_mono,
                queue_deadline=(
                    now_mono + current.effective_queue_timeout_ms / 1000.0
                ),
            )
            record.history.append({"status": "queued", "at": now, "progress": 0})
            self._records[current.task_id] = record
            if current.idempotency_key:
                self._idempotency[current.idempotency_key] = current.task_id
            self._metrics[current.target_instance_id]["submitted_count"] += 1
            self._emit_audit_locked(record)
            if rejection:
                response = self._error_response(
                    current,
                    "rejected",
                    "command_rejected",
                    "; ".join(rejection),
                    started_at=now,
                )
                self._transition_locked(record, "rejected", response=response)
            else:
                worker = self._ensure_worker_locked(current.target_instance_id)
                worker.items.put(current.task_id)
                self._publish_diagnostics_locked(current.target_instance_id)
                self._condition.notify_all()
            return self._snapshot_locked(record)

    def get(self, task_id: str) -> TaskStatus:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(f"Unknown or evicted command task: {task_id}")
            return self._snapshot_locked(record)

    def wait(self, task_id: str, timeout: float | None = None) -> TaskStatus:
        deadline = None if timeout is None else self._monotonic() + max(0.0, timeout)
        with self._condition:
            while True:
                record = self._records.get(task_id)
                if record is None:
                    raise KeyError(f"Unknown or evicted command task: {task_id}")
                if record.status in TERMINAL_STATUSES:
                    return self._snapshot_locked(record)
                remaining = (
                    None if deadline is None else max(0.0, deadline - self._monotonic())
                )
                if remaining == 0:
                    return self._snapshot_locked(record)
                self._condition.wait(remaining)

    def cancel(self, task_id: str) -> TaskStatus:
        with self._condition:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(f"Unknown or evicted command task: {task_id}")
            if record.status in {"queued", "running"}:
                response = self._error_response(
                    record.task,
                    "cancelled",
                    "task_cancelled",
                    "The task was cancelled; any late transport result will be discarded.",
                    started_at=record.started_at or self._clock(),
                )
                self._transition_locked(record, "cancelled", response=response)
            return self._snapshot_locked(record)

    def update_progress(self, task_id: str, progress: int) -> TaskStatus:
        if isinstance(progress, bool) or not 1 <= int(progress) <= 99:
            raise ValueError("running progress must be an integer from 1 to 99")
        with self._condition:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(f"Unknown or evicted command task: {task_id}")
            if record.status != "running":
                raise ValueError("progress can only be updated for a running task")
            record.progress = max(record.progress, int(progress))
            record.history.append(
                {
                    "status": "running",
                    "at": self._clock(),
                    "progress": record.progress,
                }
            )
            self._emit_audit_locked(record)
            self._condition.notify_all()
            return self._snapshot_locked(record)

    def audit_events(self) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            return tuple(copy.deepcopy(list(self._audit_events)))

    def diagnostics_snapshot(self) -> Mapping[str, Mapping[str, Any]]:
        with self._diagnostic_lock:
            return copy.deepcopy(self._diagnostic_cache)

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            for record in list(self._records.values()):
                if record.status in {"queued", "running"}:
                    response = self._error_response(
                        record.task,
                        "cancelled",
                        "service_closed",
                        "The command task service closed before completion.",
                        started_at=record.started_at or self._clock(),
                    )
                    self._transition_locked(record, "cancelled", response=response)
            workers = list(self._workers.values())
            for worker in workers:
                worker.items.put(_STOP)
            self._condition.notify_all()
        deadline = self._monotonic() + self._close_timeout
        for worker in workers:
            worker.thread.join(max(0.0, deadline - self._monotonic()))
        self._timeout_thread.join(max(0.0, deadline - self._monotonic()))
        closed: set[int] = set()
        for transport in self._transports.values():
            if id(transport) in closed:
                continue
            closed.add(id(transport))
            close = getattr(transport, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._audit_items.put(_STOP)
        self._audit_thread.join(max(0.0, deadline - self._monotonic()))

    def __enter__(self) -> CommandTaskService:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _ensure_worker_locked(self, instance_id: str) -> _Worker:
        worker = self._workers.get(instance_id)
        if worker is not None:
            return worker
        items: queue.Queue[Any] = queue.Queue()
        thread = threading.Thread(
            target=self._worker_loop,
            args=(instance_id, items),
            name=f"cam-command-{hashlib.sha256(instance_id.encode()).hexdigest()[:8]}",
            daemon=True,
        )
        worker = _Worker(instance_id, items, thread)
        self._workers[instance_id] = worker
        thread.start()
        return worker

    def _worker_loop(self, instance_id: str, items: queue.Queue[Any]) -> None:
        while True:
            task_id = items.get()
            if task_id is _STOP:
                items.task_done()
                return
            try:
                with self._lock:
                    record = self._records.get(str(task_id))
                    if record is None or record.status != "queued":
                        continue
                    task = record.task
                descriptor, target_error = self._resolve_and_validate_target(task)
                preflight = self._preflight(task, descriptor, target_error)
                with self._condition:
                    record = self._records.get(task.task_id)
                    if record is None or record.status != "queued":
                        continue
                    if preflight is not None:
                        status, code, message = preflight
                        response = self._error_response(
                            task,
                            status,
                            code,
                            message,
                            started_at=self._clock(),
                        )
                        self._transition_locked(record, status, response=response)
                        continue
                    now_mono = self._monotonic()
                    record.started_at = self._clock()
                    record.started_monotonic = now_mono
                    record.execution_deadline = (
                        now_mono + task.effective_execution_timeout_ms / 1000.0
                    )
                    self._transition_locked(record, "running")
                response = self._execute_task(task)
                with self._condition:
                    record = self._records.get(task.task_id)
                    if record is None or record.status != "running":
                        continue
                    self._transition_locked(record, response.status, response=response)
            except Exception as error:
                with self._condition:
                    record = self._records.get(str(task_id))
                    if record is not None and record.status in {"queued", "running"}:
                        response = self._error_response(
                            record.task,
                            "failed",
                            "worker_error",
                            str(error) or type(error).__name__,
                            started_at=record.started_at or self._clock(),
                        )
                        self._transition_locked(record, "failed", response=response)
            finally:
                items.task_done()

    def _timeout_loop(self) -> None:
        while not self._stop.wait(self._timeout_poll_interval):
            now = self._monotonic()
            with self._condition:
                for record in list(self._records.values()):
                    if record.status == "queued" and now >= record.queue_deadline:
                        response = self._error_response(
                            record.task,
                            "timed_out",
                            "queue_timeout",
                            "The task exceeded its queue timeout before execution.",
                            started_at=self._clock(),
                        )
                        self._transition_locked(record, "timed_out", response=response)
                    elif (
                        record.status == "running"
                        and record.execution_deadline is not None
                        and now >= record.execution_deadline
                    ):
                        response = self._error_response(
                            record.task,
                            "timed_out",
                            "execution_timeout",
                            (
                                "The task exceeded its execution timeout; any late "
                                "transport result will be discarded."
                            ),
                            started_at=record.started_at or self._clock(),
                        )
                        self._transition_locked(record, "timed_out", response=response)

    def _execute_task(self, task: CommandTask) -> CommandResponse:
        started_at = self._clock()
        started = self._monotonic()
        try:
            if task.task_type == "query":
                transport = self._transports[task.product]
                raw = self._invoke_query(transport, task)
            else:
                parameters = task.arguments.get("parameters", task.arguments)
                report = self._recipe_service.preview(
                    task.recipe_hash,
                    parameters,
                    target_version=task.target_version,
                    target_instance_id=task.target_instance_id,
                    project_id=task.project_id,
                    reviewer=str(task.review.get("reviewer", "")),
                )
                diff_report = (
                    report.to_dict()
                    if callable(getattr(report, "to_dict", None))
                    else dict(report)
                )
                raw = {
                    "schema_version": 1,
                    "task_id": task.task_id,
                    "product": task.product,
                    "target_version": task.target_version,
                    "target_instance_id": task.target_instance_id,
                    "project_id": task.project_id,
                    "status": "succeeded",
                    "raw_response": None,
                    "structured_response": {
                        "operation": task.operation,
                        "execution_mode": "dry_run",
                        "commands_sent": 0,
                        "review_first": True,
                        "production_approved": False,
                    },
                    "diff_report": diff_report,
                    "error": None,
                }
        except TimeoutError:
            return self._error_response(
                task,
                "timed_out",
                "transport_timeout",
                "The transport reported a timeout.",
                started_at=started_at,
                duration_ms=round((self._monotonic() - started) * 1000),
            )
        except (ConnectionError, OSError) as error:
            return self._error_response(
                task,
                "disconnected",
                "transport_disconnected",
                str(error) or "The target transport disconnected.",
                started_at=started_at,
                duration_ms=round((self._monotonic() - started) * 1000),
            )
        except Exception as error:
            return self._error_response(
                task,
                "failed",
                "transport_error",
                str(error) or type(error).__name__,
                started_at=started_at,
                duration_ms=round((self._monotonic() - started) * 1000),
            )
        elapsed = max(0, round((self._monotonic() - started) * 1000))
        return self._normalize_response(task, raw, started_at, elapsed)

    @staticmethod
    def _invoke_query(transport: Any, task: CommandTask) -> Any:
        query = getattr(transport, "query", None)
        if not callable(query):
            raise TypeError("transport must implement query")
        parameters = inspect.signature(query).parameters
        if "operation" in parameters:
            return query(
                task.target_instance_id,
                task.operation,
                task.arguments,
                task_id=task.task_id,
            )
        return query(
            task.target_instance_id,
            {
                "task_id": task.task_id,
                "operation": task.operation,
                "arguments": dict(task.arguments),
            },
        )

    def _normalize_response(
        self,
        task: CommandTask,
        raw: Any,
        started_at: str,
        elapsed_ms: int,
    ) -> CommandResponse:
        if callable(getattr(raw, "to_dict", None)):
            value = dict(raw.to_dict())
        elif isinstance(raw, Mapping):
            value = dict(raw)
        else:
            return self._error_response(
                task,
                "failed",
                "invalid_transport_response",
                "Transport response must be an object.",
                started_at=started_at,
                duration_ms=elapsed_ms,
            )
        mismatch = []
        for name, expected in (
            ("task_id", task.task_id),
            ("product", task.product),
            ("target_version", task.target_version),
            ("target_instance_id", task.target_instance_id),
            ("project_id", task.project_id),
        ):
            if name in value and str(value[name]) != expected:
                mismatch.append(name)
        if mismatch:
            return self._error_response(
                task,
                "failed",
                "response_attribution_mismatch",
                "Transport response ownership mismatch: " + ", ".join(mismatch),
                started_at=started_at,
                duration_ms=elapsed_ms,
            )
        status = str(value.get("status", "failed")).lower()
        if status in {"dry_run", "dry_run_complete"}:
            status = "succeeded"
        if status not in TERMINAL_STATUSES:
            status = "failed"
            value["error"] = {
                "code": "invalid_transport_status",
                "message": "Transport returned a non-terminal or unknown status.",
            }
        response_payload = {
            "raw_response": value.get("raw_response", value.get("raw")),
            "structured_response": value.get(
                "structured_response",
                value.get("structured"),
            ),
            "diff_report": value.get("diff_report"),
        }
        if response_payload["raw_response"] is not None and not isinstance(
            response_payload["raw_response"],
            str,
        ):
            return self._error_response(
                task,
                "failed",
                "invalid_transport_response",
                "raw_response must be a string or null.",
                started_at=started_at,
                duration_ms=elapsed_ms,
            )
        for name in ("structured_response", "diff_report"):
            if response_payload[name] is not None and not isinstance(
                response_payload[name],
                Mapping,
            ):
                return self._error_response(
                    task,
                    "failed",
                    "invalid_transport_response",
                    f"{name} must be an object or null.",
                    started_at=started_at,
                    duration_ms=elapsed_ms,
                )
        if contains_machine_ready_output(response_payload):
            return self._error_response(
                task,
                "rejected",
                "machine_ready_output_blocked",
                "Transport output was discarded because it resembled machine-ready output.",
                started_at=started_at,
                duration_ms=elapsed_ms,
            )
        error = value.get("error")
        if error is not None and not isinstance(error, Mapping):
            error = {"code": "transport_error", "message": str(error)}
        response_id = str(value.get("response_id") or self._response_id(task.task_id))
        return CommandResponse(
            response_id=response_id,
            task_id=task.task_id,
            product=task.product,
            target_version=task.target_version,
            target_instance_id=task.target_instance_id,
            project_id=task.project_id,
            status=status,
            started_at=str(value.get("started_at") or started_at),
            completed_at=str(value.get("completed_at") or self._clock()),
            duration_ms=elapsed_ms,
            raw_response=(
                str(response_payload["raw_response"])
                if response_payload["raw_response"] is not None
                else None
            ),
            structured_response=(
                _json_copy(response_payload["structured_response"])
                if isinstance(response_payload["structured_response"], Mapping)
                else None
            ),
            diff_report=(
                _json_copy(response_payload["diff_report"])
                if isinstance(response_payload["diff_report"], Mapping)
                else None
            ),
            error=_json_copy(error) if isinstance(error, Mapping) else None,
        )

    def _submission_rejection(
        self,
        task: CommandTask,
        descriptor: Mapping[str, Any] | None,
        descriptor_error: str | None,
    ) -> tuple[str, ...]:
        reasons = list(
            classify_command(
                product=task.product,
                task_type=task.task_type,
                execution_mode=task.execution_mode,
                operation=task.operation,
                arguments=task.arguments,
            )
        )
        if not transport_operation_allowed(
            product=task.product,
            task_type=task.task_type,
            operation=task.operation,
        ):
            reasons.append("operation is not on the transport allowlist")
        if descriptor_error:
            reasons.append(descriptor_error)
        if task.task_type == "query" and task.product not in self._transports:
            reasons.append("no query transport is registered for the product")
        if task.task_type == "recipe_preview":
            if self._recipe_service is None:
                reasons.append("no recipe preview service is configured")
            reasons.extend(self._review_reasons(task))
            reasons.extend(self._recipe_reasons(task))
        return tuple(dict.fromkeys(reasons))

    def _preflight(
        self,
        task: CommandTask,
        descriptor: Mapping[str, Any] | None,
        target_error: str | None,
    ) -> tuple[str, str, str] | None:
        if target_error:
            return "rejected", "target_mismatch", target_error
        assert descriptor is not None
        connection = str(descriptor.get("connection_status", "")).lower()
        if connection != "connected":
            return (
                "disconnected",
                "instance_disconnected",
                "The exact target instance is not connected.",
            )
        reasons = self._submission_rejection(task, descriptor, None)
        if reasons:
            return "rejected", "preflight_rejected", "; ".join(reasons)
        return None

    def _resolve_and_validate_target(
        self,
        task: CommandTask,
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        try:
            descriptor = self._resolve_instance(task.target_instance_id)
        except (KeyError, LookupError, ValueError, TypeError):
            return None, "target_instance_id is not registered"
        if descriptor is None:
            return None, "target_instance_id is not registered"
        value = self._descriptor_mapping(descriptor)
        expected = {
            "instance_id": task.target_instance_id,
            "product": task.product,
            "target_version": task.target_version,
            "project_id": task.project_id,
        }
        mismatches = [
            name
            for name, expected_value in expected.items()
            if str(value.get(name) or "") != expected_value
        ]
        if mismatches:
            return value, "target binding mismatch: " + ", ".join(mismatches)
        return value, None

    def _resolve_instance(self, instance_id: str) -> Any:
        if instance_id in self._instances:
            return self._instances[instance_id]
        provider = self._instance_provider
        if provider is None:
            raise KeyError(instance_id)
        if isinstance(provider, Mapping):
            return provider[instance_id]
        if callable(provider):
            return provider(instance_id)
        getter = getattr(provider, "get_instance", None)
        if callable(getter):
            return getter(instance_id)
        lister = getattr(provider, "list_instances", None)
        if callable(lister):
            try:
                descriptors = lister(refresh=False)
            except TypeError:
                descriptors = lister()
            for descriptor in descriptors:
                value = self._descriptor_mapping(descriptor)
                if str(value.get("instance_id", "")) == instance_id:
                    return descriptor
        raise KeyError(instance_id)

    @staticmethod
    def _descriptor_mapping(descriptor: Any) -> Mapping[str, Any]:
        if isinstance(descriptor, Mapping):
            return descriptor
        to_dict = getattr(descriptor, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        return {
            name: getattr(descriptor, name, None)
            for name in (
                "instance_id",
                "product",
                "target_version",
                "project_id",
                "connection_status",
                "last_seen_at",
            )
        }

    def _review_reasons(self, task: CommandTask) -> list[str]:
        assert task.task_type == "recipe_preview"
        review = task.review or {}
        reasons = []
        if str(review.get("status", "")).lower() != "accepted":
            reasons.append("recipe preview review status must be accepted")
        if not str(review.get("reviewer", "")).strip():
            reasons.append("recipe preview requires an identified reviewer")
        scope = str(review.get("scope", "dry_run_only")).lower().replace("-", "_")
        if scope != "dry_run_only":
            reasons.append("recipe preview review scope must be dry_run_only")
        return reasons

    def _recipe_reasons(self, task: CommandTask) -> list[str]:
        if self._recipe_service is None or task.recipe_hash is None:
            return []
        try:
            version = self._recipe_service.get(task.recipe_hash)
            recipe = version.recipe
            from .recipes import compute_recipe_hash

            calculated = compute_recipe_hash(recipe)
        except Exception as error:
            return [str(error) or "recipe_hash is not registered"]
        reasons = []
        if str(getattr(recipe, "recipe_hash", "")) != task.recipe_hash:
            reasons.append("stored recipe_hash does not match the requested hash")
        if calculated != task.recipe_hash:
            reasons.append("recipe semantics no longer match recipe_hash")
        if str(getattr(recipe, "product", "")).lower() != task.product:
            reasons.append("recipe product does not match the target product")
        versions = tuple(getattr(recipe, "target_versions", ()))
        if versions and task.target_version not in versions:
            reasons.append("target version is outside the reviewed recipe versions")
        return reasons

    def _transition_locked(
        self,
        record: _TaskRecord,
        status: str,
        *,
        response: CommandResponse | None = None,
    ) -> None:
        if status not in TASK_STATUSES:
            raise ValueError(f"invalid task status: {status}")
        if record.status in TERMINAL_STATUSES:
            return
        if record.status == "queued" and status not in TERMINAL_STATUSES | {"running"}:
            raise RuntimeError(f"invalid queued transition to {status}")
        if record.status == "running" and status not in TERMINAL_STATUSES:
            raise RuntimeError(f"invalid running transition to {status}")
        now = self._clock()
        now_mono = self._monotonic()
        record.status = status
        record.progress = 10 if status == "running" else (100 if status in TERMINAL_STATUSES else 0)
        if status == "running":
            record.started_at = record.started_at or now
            record.started_monotonic = record.started_monotonic or now_mono
        if status in TERMINAL_STATUSES:
            record.completed_at = now
            record.completed_monotonic = now_mono
            elapsed = self._elapsed_ms(record)
            if response is not None and response.duration_ms == 0 and elapsed > 0:
                response = replace(
                    response,
                    completed_at=now,
                    duration_ms=elapsed,
                )
            record.response = response
            record.error = response.error if response else None
            metrics = self._metrics[record.task.target_instance_id]
            metrics["terminal_counts"][status] += 1
            metrics["durations_ms"].append(elapsed)
            self._terminal_order[record.task.task_id] = None
        record.history.append(
            {"status": status, "at": now, "progress": record.progress}
        )
        self._emit_audit_locked(record)
        self._publish_diagnostics_locked(record.task.target_instance_id)
        if status in TERMINAL_STATUSES:
            self._evict_locked()
        self._condition.notify_all()

    def _snapshot_locked(self, record: _TaskRecord) -> TaskStatus:
        return TaskStatus(
            task=record.task,
            status=record.status,
            progress=record.progress,
            queued_at=record.queued_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            elapsed_ms=(
                self._elapsed_ms(record)
                if record.status in TERMINAL_STATUSES
                else None
            ),
            response=record.response,
            error=_json_copy(record.error),
            status_history=tuple(_json_copy(record.history)),
        )

    def _elapsed_ms(self, record: _TaskRecord) -> int:
        end = record.completed_monotonic or self._monotonic()
        start = record.started_monotonic or record.queued_monotonic
        return max(0, round((end - start) * 1000))

    def _evict_locked(self) -> None:
        while len(self._terminal_order) > self._result_cache_size:
            task_id, _ = self._terminal_order.popitem(last=False)
            record = self._records.get(task_id)
            if record is None or record.status not in TERMINAL_STATUSES:
                continue
            self._records.pop(task_id, None)
            self._evicted[task_id] = (
                record.fingerprint,
                record.task.idempotency_key,
            )
            while len(self._evicted) > self._result_cache_size:
                evicted_id, (_, idempotency_key) = self._evicted.popitem(last=False)
                if (
                    idempotency_key
                    and self._idempotency.get(idempotency_key) == evicted_id
                ):
                    self._idempotency.pop(idempotency_key, None)
            self._publish_diagnostics_locked(record.task.target_instance_id)

    def _publish_diagnostics_locked(self, instance_id: str) -> None:
        now = self._monotonic()
        current = [
            record
            for record in self._records.values()
            if record.task.target_instance_id == instance_id
        ]
        queued = [record for record in current if record.status == "queued"]
        running = [record for record in current if record.status == "running"]
        metrics = self._metrics[instance_id]
        durations = list(metrics["durations_ms"])
        task_counts = {status: 0 for status in TASK_STATUSES}
        for record in current:
            task_counts[record.status] += 1
        for status, count in metrics["terminal_counts"].items():
            task_counts[f"total_{status}"] = count
        duration_summary = {
            "count": len(durations),
            "average_ms": (
                round(sum(durations) / len(durations), 3) if durations else 0
            ),
            "max_ms": max(durations, default=0),
        }
        snapshot = {
            "schema_version": 1,
            "instance_id": instance_id,
            "queue_length": len(queued),
            "running_count": len(running),
            "oldest_wait_ms": (
                max(0, round((now - min(item.queued_monotonic for item in queued)) * 1000))
                if queued
                else 0
            ),
            "task_counts": task_counts,
            "submitted_count": metrics["submitted_count"],
            "duration": duration_summary,
            "updated_at": self._clock(),
        }
        with self._diagnostic_lock:
            self._diagnostic_cache[instance_id] = snapshot

    def _emit_audit_locked(self, record: _TaskRecord) -> None:
        response = record.response.to_dict() if record.response else None
        task = record.task
        event: dict[str, Any] = {
            "schema_version": 1,
            "session_id": f"{task.product}:execution:{task.task_id}",
            "seq": len(record.history) - 1,
            "product": task.product,
            "action": "cam.execution.task.status",
            "category": "execution",
            "mode": "automation",
            "source_mode": "execution_audit",
            "view_level": "L4",
            "instance_id": task.target_instance_id,
            "project_id": task.project_id,
            "target_version": task.target_version,
            "recipe_hash": task.recipe_hash,
            "timestamp": self._clock(),
            "duration_ms": (
                response.get("duration_ms")
                if response is not None
                else (self._elapsed_ms(record) if record.status == "running" else 0)
            ),
            "source_file": "local:execution-audit",
            "source_line": 0,
            "params": {
                "task_id": task.task_id,
                "task_type": task.task_type,
                "execution_mode": task.execution_mode,
                "operation": task.operation,
                "arguments": redact_for_audit(task.arguments),
                "status": record.status,
                "progress": record.progress,
                "requested_by": task.requested_by,
            },
            "command_response": redact_for_audit(response),
        }
        safe_event = _json_copy(redact_for_audit(event))
        self._audit_events.append(safe_event)
        if self._audit_sink is not None:
            self._audit_items.put((safe_event, task.to_dict(), response))

    def _audit_loop(self) -> None:
        while True:
            item = self._audit_items.get()
            if item is _STOP:
                return
            event, task, response = item
            sink = self._audit_sink
            try:
                if callable(getattr(sink, "record_execution", None)):
                    request = dict(task)
                    request["action"] = event["action"]
                    sink.record_execution(
                        request=redact_for_audit(request),
                        result=redact_for_audit(
                            response or {"status": event["params"]["status"]}
                        ),
                    )
                elif callable(sink):
                    sink(copy.deepcopy(event))
                elif callable(getattr(sink, "append", None)):
                    sink.append(copy.deepcopy(event))
            except Exception:
                continue

    def _error_response(
        self,
        task: CommandTask,
        status: str,
        code: str,
        message: str,
        *,
        started_at: str,
        duration_ms: int = 0,
    ) -> CommandResponse:
        return CommandResponse(
            response_id=self._response_id(task.task_id),
            task_id=task.task_id,
            product=task.product,
            target_version=task.target_version,
            target_instance_id=task.target_instance_id,
            project_id=task.project_id,
            status=status,
            started_at=started_at,
            completed_at=self._clock(),
            duration_ms=max(0, int(duration_ms)),
            raw_response=None,
            structured_response=None,
            diff_report=None,
            error={"code": code, "message": message},
        )

    def _response_id(self, task_id: str) -> str:
        token = f"{task_id}:{self._clock()}:{self._monotonic()}".encode("utf-8")
        return "response:" + hashlib.sha256(token).hexdigest()[:24]
