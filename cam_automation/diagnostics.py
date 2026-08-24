from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .command_tasks import CommandTaskService


__all__ = ["DiagnosticsService", "DiagnosticsSnapshot"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _age_ms(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(
        0,
        round((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() * 1000),
    )


@dataclass(frozen=True, slots=True)
class DiagnosticsSnapshot:
    captured_at: str
    instances: tuple[Mapping[str, Any], ...]
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "captured_at": self.captured_at,
            "instances": copy.deepcopy(list(self.instances)),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class DiagnosticsService:
    """Merge cached task metrics with connection and log heartbeat metadata."""

    def __init__(
        self,
        task_service: CommandTaskService,
        *,
        instance_provider: Any | None = None,
        log_lag_provider: Callable[[str], int | float | None] | Mapping[str, Any] | None = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._task_service = task_service
        self._instance_provider = instance_provider
        self._log_lag_provider = log_lag_provider
        self._clock = clock

    def snapshot(self) -> DiagnosticsSnapshot:
        # CommandTaskService exposes a separately locked immutable cache. No worker
        # queue, transport lock, or transport callback is touched by this read.
        task_metrics = self._task_service.diagnostics_snapshot()
        descriptors = {
            str(value.get("instance_id", "")): value
            for value in (
                self._descriptor_mapping(item)
                for item in self._list_instances(tuple(task_metrics))
            )
            if value.get("instance_id")
        }
        instance_ids = sorted(set(task_metrics) | set(descriptors))
        instances = []
        for instance_id in instance_ids:
            descriptor = descriptors.get(instance_id, {})
            metrics = task_metrics.get(instance_id, {})
            heartbeat_at = str(
                descriptor.get("last_seen_at")
                or descriptor.get("heartbeat_at")
                or ""
            )
            instances.append(
                {
                    "schema_version": 1,
                    "instance_id": instance_id,
                    "product": descriptor.get("product"),
                    "target_version": descriptor.get("target_version"),
                    "project_id": descriptor.get("project_id"),
                    "connection_status": str(
                        descriptor.get("connection_status") or "unknown"
                    ),
                    "heartbeat_at": heartbeat_at or None,
                    "heartbeat_age_ms": _age_ms(heartbeat_at),
                    "queue_length": int(metrics.get("queue_length", 0)),
                    "running_count": int(metrics.get("running_count", 0)),
                    "oldest_wait_ms": int(metrics.get("oldest_wait_ms", 0)),
                    "log_lag_ms": self._log_lag(instance_id, descriptor),
                    "task_counts": copy.deepcopy(metrics.get("task_counts", {})),
                    "submitted_count": int(metrics.get("submitted_count", 0)),
                    "duration": copy.deepcopy(
                        metrics.get(
                            "duration",
                            {"count": 0, "average_ms": 0, "max_ms": 0},
                        )
                    ),
                    "metrics_updated_at": metrics.get("updated_at"),
                }
            )
        return DiagnosticsSnapshot(
            captured_at=self._clock(),
            instances=tuple(instances),
        )

    def _list_instances(self, known_instance_ids: tuple[str, ...]) -> list[Any]:
        provider = self._instance_provider
        if provider is None:
            return []
        if isinstance(provider, Mapping):
            return list(provider.values())
        if callable(provider):
            try:
                value = provider()
                return list(value or ())
            except TypeError:
                values = []
                for instance_id in known_instance_ids:
                    try:
                        values.append(provider(instance_id))
                    except (KeyError, LookupError, ValueError):
                        continue
                return values
        lister = getattr(provider, "list_instances", None)
        if callable(lister):
            try:
                return list(lister(refresh=False))
            except TypeError:
                return list(lister())
        return []

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
                "metadata",
            )
        }

    def _log_lag(
        self,
        instance_id: str,
        descriptor: Mapping[str, Any],
    ) -> int | float | None:
        provider = self._log_lag_provider
        value: Any = None
        if isinstance(provider, Mapping):
            value = provider.get(instance_id)
        elif callable(provider):
            try:
                value = provider(instance_id)
            except Exception:
                value = None
        if value is None:
            metadata = descriptor.get("metadata")
            if isinstance(metadata, Mapping):
                value = metadata.get("log_lag_ms")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return max(0, value)
