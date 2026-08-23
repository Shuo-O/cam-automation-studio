from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .models import EventPage

__all__ = ["EventRef", "Session", "SessionDiff", "SessionService"]


SCHEMA_VERSION = 1
DEFAULT_INACTIVITY_GAP = timedelta(minutes=30)
MAX_COMPARE_EVENTS = 10_000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SOURCE_MODE_ORDER = {
    "manual": 0,
    "automation": 1,
    "system": 2,
    "execution_audit": 3,
}
_NOISE_CATEGORIES = {"connection", "evidence", "navigation", "selection", "system", "ui"}
_NOISE_ACTION_PARTS = (
    ".cursor.",
    ".heartbeat",
    ".hover",
    ".instance.discovered",
    ".selection.",
    ".session.observed",
    ".undo_mark",
    ".view.",
)


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _normalize_timestamp(value: Any) -> tuple[datetime | None, str | None]:
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith(("Z", "z")):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as error:
            raise ValueError(f"Invalid RFC 3339 timestamp: {value!r}") from error
    else:
        raise ValueError(f"Invalid timestamp type: {type(value).__name__}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    rendered = parsed.isoformat().replace("+00:00", "Z")
    return parsed, rendered


def _type_shape(value: Any) -> Any:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return {
            str(key): _type_shape(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return ["array", sorted({_stable_json(_type_shape(item)) for item in value})]
    return type(value).__name__


def _flatten_json(value: Any, prefix: str = "", *, limit: int = 128) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    pending: list[tuple[str, Any]] = [(prefix, value)]
    while pending and len(flattened) < limit:
        path, item = pending.pop()
        if isinstance(item, Mapping):
            for key in sorted(item, key=str, reverse=True):
                child = f"{path}.{key}" if path else str(key)
                pending.append((child, item[key]))
        else:
            flattened[path or "value"] = item
    return flattened


@dataclass(frozen=True, order=True)
class EventRef:
    event_session_id: str
    seq: int

    def to_dict(self) -> dict[str, Any]:
        return {"event_session_id": self.event_session_id, "seq": self.seq}

    @classmethod
    def from_value(cls, value: EventRef | Mapping[str, Any]) -> EventRef:
        if isinstance(value, cls):
            return value
        return cls(str(value["event_session_id"]), int(value["seq"]))


@dataclass(frozen=True)
class _EventRecord:
    source: Any
    ref: EventRef
    product: str
    instance_id: str
    project_id: str | None
    action: str
    category: str
    params: Mapping[str, Any]
    source_mode: str
    target_version: str
    timestamp: datetime | None
    timestamp_text: str | None
    duration_ms: int | None
    marker_id: str | None
    split_before: bool
    split_after: bool
    explicit: bool
    labels: tuple[str, ...]
    fingerprint: str

    @property
    def sort_key(self) -> tuple[Any, ...]:
        return (
            self.timestamp is None,
            self.timestamp or _EPOCH,
            self.ref.event_session_id,
            self.ref.seq,
            self.action,
            self.fingerprint,
        )


def _marker_details(event: Any, params: Mapping[str, Any], action: str) -> tuple[
    str | None, bool, bool, bool
]:
    raw_marker = None
    for name in ("session_marker", "explicit_session_id", "marker_id", "explicit_marker"):
        raw_marker = _value(event, name)
        if raw_marker not in (None, "", False):
            break
        raw_marker = params.get(name)
        if raw_marker not in (None, "", False):
            break

    marker_id: str | None = None
    marker_kind = ""
    explicit = False
    if isinstance(raw_marker, Mapping):
        marker_id = str(
            raw_marker.get("id")
            or raw_marker.get("session_id")
            or raw_marker.get("name")
            or ""
        ).strip() or None
        marker_kind = str(raw_marker.get("kind") or raw_marker.get("type") or "").lower()
        explicit = True
    elif isinstance(raw_marker, str):
        marker_id = raw_marker.strip() or None
        explicit = marker_id is not None
    elif raw_marker is True:
        marker_id = str(_value(event, "session_id", "")).strip() or None
        explicit = True

    lower_action = action.lower()
    action_start = lower_action.endswith((".session.start", ".session.begin"))
    action_end = lower_action.endswith((".session.end", ".session.stop"))
    split_before = bool(
        _value(event, "split_before", False)
        or params.get("split_before", False)
        or marker_kind in {"start", "begin"}
        or action_start
    )
    split_after = bool(
        _value(event, "split_after", False)
        or params.get("split_after", False)
        or marker_kind in {"end", "stop"}
        or action_end
    )
    if action_start or action_end:
        explicit = True
        marker_id = marker_id or str(_value(event, "session_id", "")).strip() or action
    return marker_id, split_before, split_after, explicit


def _coerce_record(event: Any) -> _EventRecord:
    product = str(_value(event, "product", "")).strip().lower()
    if product not in {"nx", "powermill"}:
        raise ValueError(f"Unsupported or missing event product: {product!r}")
    instance_id = str(_value(event, "instance_id", "")).strip() or f"{product}:unassigned"
    project_value = _value(event, "project_id")
    project_id = None if project_value in (None, "") else str(project_value)
    action = str(_value(event, "action", "")).strip()
    if not action:
        raise ValueError("Every event must have an action.")
    category = str(_value(event, "category", "")).strip()
    raw_params = _value(event, "params", {})
    if raw_params is None:
        raw_params = {}
    if not isinstance(raw_params, Mapping):
        raise ValueError("Event params must be an object.")
    params = _json_copy(dict(raw_params))
    source_mode = str(
        _value(event, "source_mode", "") or _value(event, "mode", "manual")
    ).strip().lower()
    if source_mode not in _SOURCE_MODE_ORDER:
        source_mode = "manual"
    target_version = str(_value(event, "target_version", "") or "").strip()

    source_session_id = str(
        _value(event, "session_id", "")
        or _value(event, "event_session_id", "")
        or _value(event, "source_session_id", "")
    ).strip()
    raw_seq = _value(event, "seq", _value(event, "sequence", 0))
    try:
        seq = int(raw_seq)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid event sequence: {raw_seq!r}") from error
    if seq < 0:
        raise ValueError("Event sequence cannot be negative.")
    if not source_session_id:
        source_session_id = "source:" + _stable_hash(
            {
                "product": product,
                "instance_id": instance_id,
                "project_id": project_id,
                "action": action,
                "seq": seq,
                "source_file": _value(event, "source_file", ""),
                "source_line": _value(event, "source_line", 0),
            }
        )[:20]
    ref = EventRef(source_session_id, seq)

    timestamp, timestamp_text = _normalize_timestamp(_value(event, "timestamp"))
    raw_duration = _value(event, "duration_ms")
    duration_ms = None
    if raw_duration is not None:
        try:
            duration_ms = int(raw_duration)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid duration_ms: {raw_duration!r}") from error
        if duration_ms < 0:
            raise ValueError("duration_ms cannot be negative.")

    marker_id, split_before, split_after, explicit = _marker_details(event, params, action)
    labels: set[str] = set()
    expertise = str(_value(event, "expertise_label", "") or "").strip()
    if expertise and expertise != "unlabeled":
        labels.add(expertise)
    raw_labels = _value(event, "labels", ())
    if isinstance(raw_labels, str):
        labels.add(raw_labels)
    elif isinstance(raw_labels, Iterable):
        labels.update(str(item) for item in raw_labels if str(item))

    fingerprint = _stable_hash(
        {
            "ref": ref.to_dict(),
            "product": product,
            "instance_id": instance_id,
            "project_id": project_id,
            "action": action,
            "category": category,
            "params": params,
            "source_mode": source_mode,
            "target_version": target_version,
            "timestamp": timestamp_text,
            "duration_ms": duration_ms,
            "marker_id": marker_id,
            "split_before": split_before,
            "split_after": split_after,
        }
    )
    return _EventRecord(
        source=event,
        ref=ref,
        product=product,
        instance_id=instance_id,
        project_id=project_id,
        action=action,
        category=category,
        params=params,
        source_mode=source_mode,
        target_version=target_version,
        timestamp=timestamp,
        timestamp_text=timestamp_text,
        duration_ms=duration_ms,
        marker_id=marker_id,
        split_before=split_before,
        split_after=split_after,
        explicit=explicit,
        labels=tuple(sorted(labels)),
        fingerprint=fingerprint,
    )


def _structural_signature(record: _EventRecord) -> str:
    operation = record.params.get("operation")
    structure = {
        "action": record.action,
        "category": record.category,
        "operation": operation if isinstance(operation, str) else None,
        "params": _type_shape(record.params),
    }
    return _stable_hash(structure)


def _step_key(signature: str, occurrence: int) -> str:
    return f"step:{signature[:16]}:{occurrence}"


def _is_noise(record: _EventRecord) -> bool:
    if record.category.lower() in _NOISE_CATEGORIES:
        return True
    lower_action = record.action.lower()
    if any(part in lower_action for part in _NOISE_ACTION_PARTS):
        return True
    return bool(record.params.get("noise") or record.params.get("exploratory"))


def _workflow_records(session: Session) -> tuple[_EventRecord, ...]:
    significant = tuple(record for record in session._records if not _is_noise(record))
    return significant[:MAX_COMPARE_EVENTS]


def _align_signatures(
    baseline: Sequence[str], target: Sequence[str]
) -> tuple[dict[int, int], set[int]]:
    positions: dict[str, list[int]] = defaultdict(list)
    for index, signature in enumerate(target):
        positions[signature].append(index)
    mapping: dict[int, int] = {}
    used: set[int] = set()
    previous = -1
    for baseline_index, signature in enumerate(baseline):
        candidates = positions.get(signature, ())
        offset = bisect_right(candidates, previous)
        if offset >= len(candidates):
            continue
        target_index = candidates[offset]
        mapping[baseline_index] = target_index
        used.add(target_index)
        previous = target_index
    return mapping, used


@dataclass(frozen=True)
class Session:
    session_id: str
    product: str
    instance_id: str
    project_id: str | None
    source_modes: tuple[str, ...]
    started_at: str
    ended_at: str
    event_refs: tuple[EventRef, ...]
    explicit_marker: bool
    labels: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    view_level: str = "L2"
    events: tuple[Any, ...] = field(default=(), repr=False, compare=False)
    _records: tuple[_EventRecord, ...] = field(default=(), repr=False, compare=False)

    @property
    def event_count(self) -> int:
        return len(self.event_refs)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "product": self.product,
            "instance_id": self.instance_id,
            "project_id": self.project_id,
            "view_level": self.view_level,
            "source_modes": list(self.source_modes),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "event_count": self.event_count,
            "event_refs": [item.to_dict() for item in self.event_refs],
            "explicit_marker": self.explicit_marker,
        }
        if self.labels:
            value["labels"] = list(self.labels)
        if self.metadata:
            value["metadata"] = _json_copy(dict(self.metadata))
        return value


@dataclass(frozen=True)
class SessionDiff:
    diff_id: str
    session_ids: tuple[str, ...]
    baseline_session_id: str
    common_steps: tuple[Mapping[str, Any], ...]
    session_deltas: tuple[Mapping[str, Any], ...]
    parameter_differences: tuple[Mapping[str, Any], ...]
    duration_ms_by_session: Mapping[str, int]
    created_at: str
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "diff_id": self.diff_id,
            "session_ids": list(self.session_ids),
            "baseline_session_id": self.baseline_session_id,
            "common_steps": _json_copy(list(self.common_steps)),
            "session_deltas": _json_copy(list(self.session_deltas)),
            "parameter_differences": _json_copy(list(self.parameter_differences)),
            "duration_ms_by_session": dict(self.duration_ms_by_session),
            "created_at": self.created_at,
        }


class SessionService:
    def __init__(
        self,
        sessions: Iterable[Session] | None = None,
        *,
        inactivity_gap: timedelta = DEFAULT_INACTIVITY_GAP,
    ) -> None:
        if inactivity_gap.total_seconds() < 0:
            raise ValueError("inactivity_gap must be non-negative.")
        self.inactivity_gap = inactivity_gap
        self._sessions: dict[str, Session] = {}
        for session in sessions or ():
            self.add(session)

    @property
    def sessions(self) -> tuple[Session, ...]:
        return tuple(self._sessions[key] for key in sorted(self._sessions))

    def add(self, session: Session) -> Session:
        existing = self._sessions.get(session.session_id)
        if existing is not None:
            existing_identity = (
                existing.product,
                existing.instance_id,
                existing.project_id,
                existing.started_at,
                existing.ended_at,
                existing.event_refs,
            )
            new_identity = (
                session.product,
                session.instance_id,
                session.project_id,
                session.started_at,
                session.ended_at,
                session.event_refs,
            )
            if existing_identity != new_identity:
                raise ValueError(f"Conflicting session ID: {session.session_id}")
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise KeyError(f"Unknown session: {session_id}") from error

    def build(self, events: EventPage | Iterable[Any]) -> list[Session]:
        event_items = events.events if isinstance(events, EventPage) else events
        records_by_ref: dict[tuple[str, str, str | None, EventRef], _EventRecord] = {}
        for event in event_items:
            record = _coerce_record(event)
            identity = (
                record.product,
                record.instance_id,
                record.project_id,
                record.ref,
            )
            existing = records_by_ref.get(identity)
            if existing is not None:
                if existing.fingerprint != record.fingerprint:
                    raise ValueError(
                        "Conflicting events use the same source reference "
                        f"{record.ref.event_session_id}:{record.ref.seq}."
                    )
                continue
            records_by_ref[identity] = record

        partitions: dict[tuple[str, str, str | None], list[_EventRecord]] = defaultdict(list)
        for record in records_by_ref.values():
            partitions[(record.product, record.instance_id, record.project_id)].append(record)

        built: list[Session] = []
        for partition in sorted(partitions, key=lambda item: _stable_json(item)):
            ordered = sorted(partitions[partition], key=lambda item: item.sort_key)
            current: list[_EventRecord] = []
            current_marker: str | None = None
            previous_time: datetime | None = None

            def flush() -> None:
                nonlocal current, current_marker, previous_time
                if current:
                    built.append(self._make_session(current))
                current = []
                current_marker = None
                previous_time = None

            for record in ordered:
                marker_changed = bool(
                    current
                    and record.marker_id
                    and record.marker_id != current_marker
                )
                gap_exceeded = bool(
                    current
                    and record.timestamp is not None
                    and previous_time is not None
                    and record.timestamp - previous_time > self.inactivity_gap
                )
                if record.split_before or marker_changed or gap_exceeded:
                    flush()
                current.append(record)
                if record.marker_id:
                    current_marker = record.marker_id
                if record.timestamp is not None:
                    previous_time = record.timestamp
                if record.split_after:
                    flush()
            flush()

        built.sort(
            key=lambda item: (
                _normalize_timestamp(item.started_at)[0] or _EPOCH,
                item.product,
                item.instance_id,
                item.session_id,
            )
        )
        self._sessions = {session.session_id: session for session in built}
        return built

    def merge(self, session_ids: Iterable[str]) -> Session:
        ids = tuple(sorted(set(session_ids)))
        if len(ids) < 2:
            raise ValueError("At least two distinct sessions are required for merge.")
        sessions = [self.get(session_id) for session_id in ids]
        identity = {(item.product, item.instance_id, item.project_id) for item in sessions}
        if len(identity) != 1:
            raise ValueError("Merged sessions must share product, instance, and project.")
        records = {
            record.ref: record
            for session in sessions
            for record in session._records
        }
        merged = self._make_session(
            sorted(records.values(), key=lambda item: item.sort_key),
            metadata={"operation": "merge", "merged_from": list(ids)},
        )
        return self.add(merged)

    def split(
        self,
        session_id: str,
        split_points: int | str | EventRef | Mapping[str, Any] | Iterable[Any],
    ) -> list[Session]:
        session = self.get(session_id)
        if isinstance(split_points, (int, str, EventRef, Mapping)):
            requested = [split_points]
        else:
            requested = list(split_points)
        boundaries: set[int] = set()
        for point in requested:
            if isinstance(point, int):
                boundary = point
            elif isinstance(point, (EventRef, Mapping)):
                ref = EventRef.from_value(point)
                try:
                    boundary = session.event_refs.index(ref)
                except ValueError as error:
                    raise ValueError(f"Split event is not in session {session_id}.") from error
            elif isinstance(point, str):
                timestamp, _ = _normalize_timestamp(point)
                boundary = next(
                    (
                        index
                        for index, record in enumerate(session._records)
                        if record.timestamp is not None and record.timestamp >= timestamp
                    ),
                    len(session._records),
                )
            else:
                raise TypeError(f"Unsupported split point: {point!r}")
            if boundary <= 0 or boundary >= session.event_count:
                raise ValueError("Split points must fall between session events.")
            boundaries.add(boundary)
        if not boundaries:
            raise ValueError("At least one split point is required.")

        offsets = [0, *sorted(boundaries), session.event_count]
        results = [
            self._make_session(
                list(session._records[start:end]),
                metadata={
                    "operation": "split",
                    "split_from": session_id,
                    "segment": index + 1,
                    "segment_count": len(offsets) - 1,
                },
            )
            for index, (start, end) in enumerate(zip(offsets, offsets[1:]))
        ]
        for result in results:
            self.add(result)
        return results

    def compare(
        self,
        session_ids: Iterable[str],
        *,
        baseline_session_id: str | None = None,
    ) -> SessionDiff:
        supplied = list(session_ids)
        ids = tuple(sorted(set(supplied)))
        if len(ids) != len(supplied):
            raise ValueError("Session comparison requires distinct session IDs.")
        if not 2 <= len(ids) <= 5:
            raise ValueError("Session comparison requires 2 to 5 sessions.")
        sessions = {session_id: self.get(session_id) for session_id in ids}
        if len({session.product for session in sessions.values()}) != 1:
            raise ValueError("Sessions from different products cannot be compared.")
        baseline_id = baseline_session_id or ids[0]
        if baseline_id not in sessions:
            raise ValueError("baseline_session_id must be one of session_ids.")

        records = {key: _workflow_records(value) for key, value in sessions.items()}
        if any(
            sum(not _is_noise(record) for record in session._records) > MAX_COMPARE_EVENTS
            for session in sessions.values()
        ):
            raise ValueError(f"Session comparison is limited to {MAX_COMPARE_EVENTS} events.")
        signatures = {
            key: tuple(_structural_signature(item) for item in value)
            for key, value in records.items()
        }
        baseline_signatures = signatures[baseline_id]
        alignments: dict[str, dict[int, int]] = {baseline_id: dict(enumerate(range(
            len(baseline_signatures)
        )))}
        used_indices: dict[str, set[int]] = {baseline_id: set(range(len(baseline_signatures)))}
        for session_id in ids:
            if session_id == baseline_id:
                continue
            mapping, used = _align_signatures(baseline_signatures, signatures[session_id])
            alignments[session_id] = mapping
            used_indices[session_id] = used

        occurrence_by_signature: dict[str, int] = defaultdict(int)
        baseline_step_keys: list[str] = []
        for signature in baseline_signatures:
            occurrence_by_signature[signature] += 1
            baseline_step_keys.append(_step_key(signature, occurrence_by_signature[signature]))

        common_indices = [
            index
            for index in range(len(baseline_signatures))
            if all(index in alignments[session_id] for session_id in ids)
        ]
        common_steps = tuple(
            {
                "step_key": baseline_step_keys[index],
                "order": order,
                "action": records[baseline_id][index].action,
            }
            for order, index in enumerate(common_indices, 1)
        )

        session_deltas: list[dict[str, Any]] = []
        for session_id in ids:
            mapping = alignments[session_id]
            missing = [
                baseline_step_keys[index]
                for index in range(len(baseline_signatures))
                if index not in mapping
            ]
            extras = [
                {
                    "order": index + 1,
                    "step_key": _step_key(signatures[session_id][index], index + 1),
                    "action": record.action,
                    "classification": "exploration" if _is_noise(record) else "branch",
                    "source_event_ref": record.ref.to_dict(),
                }
                for index, record in enumerate(records[session_id])
                if index not in used_indices[session_id]
            ]
            extras.extend(
                {
                    "order": len(records[session_id]) + index + 1,
                    "step_key": _step_key(_structural_signature(record), index + 1),
                    "action": record.action,
                    "classification": "exploration",
                    "source_event_ref": record.ref.to_dict(),
                }
                for index, record in enumerate(sessions[session_id]._records)
                if _is_noise(record)
            )
            session_deltas.append(
                {
                    "session_id": session_id,
                    "missing_step_keys": missing,
                    "extra_steps": extras,
                }
            )

        parameter_differences: list[dict[str, Any]] = []
        for baseline_index in common_indices:
            values_by_path: dict[str, dict[str, Any]] = defaultdict(dict)
            for session_id in ids:
                event_index = alignments[session_id][baseline_index]
                flattened = _flatten_json(records[session_id][event_index].params)
                for path in flattened:
                    values_by_path[path][session_id] = flattened[path]
            for path in sorted(values_by_path):
                values = values_by_path[path]
                comparable = [
                    _stable_json(values.get(session_id, {"__missing__": True}))
                    for session_id in ids
                ]
                if len(set(comparable)) <= 1:
                    continue
                mapped_values = {
                    session_id: _json_copy(values[session_id])
                    if session_id in values
                    else None
                    for session_id in ids
                }
                difference: dict[str, Any] = {
                    "step_key": baseline_step_keys[baseline_index],
                    "parameter": path,
                    "values_by_session": mapped_values,
                }
                missing_sessions = [session_id for session_id in ids if session_id not in values]
                if missing_sessions:
                    difference["missing_by_session"] = missing_sessions
                parameter_differences.append(difference)

        durations = {
            session_id: self._duration_ms(session)
            for session_id, session in sorted(sessions.items())
        }
        created_at = max(
            sessions.values(),
            key=lambda session: _normalize_timestamp(session.ended_at)[0] or _EPOCH,
        ).ended_at
        diff_payload = {
            "session_ids": ids,
            "baseline_session_id": baseline_id,
            "common_steps": common_steps,
            "session_deltas": session_deltas,
            "parameter_differences": parameter_differences,
            "duration_ms_by_session": durations,
            "created_at": created_at,
        }
        return SessionDiff(
            diff_id="session-diff:" + _stable_hash(diff_payload)[:24],
            session_ids=ids,
            baseline_session_id=baseline_id,
            common_steps=common_steps,
            session_deltas=tuple(session_deltas),
            parameter_differences=tuple(parameter_differences),
            duration_ms_by_session=durations,
            created_at=created_at,
        )

    def _make_session(
        self,
        records: Sequence[_EventRecord],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Session:
        if not records:
            raise ValueError("A session requires at least one event.")
        ordered = tuple(sorted(records, key=lambda item: item.sort_key))
        identity = {(item.product, item.instance_id, item.project_id) for item in ordered}
        if len(identity) != 1:
            raise ValueError("A session cannot cross product, instance, or project.")
        product, instance_id, project_id = next(iter(identity))
        timestamps = [item.timestamp for item in ordered if item.timestamp is not None]
        started = min(timestamps) if timestamps else _EPOCH
        ended = max(timestamps) if timestamps else _EPOCH
        started_at = started.isoformat().replace("+00:00", "Z")
        ended_at = ended.isoformat().replace("+00:00", "Z")
        event_refs = tuple(item.ref for item in ordered)
        marker_ids = sorted({item.marker_id for item in ordered if item.marker_id})
        session_id = f"session:{product}:" + _stable_hash(
            {
                "product": product,
                "instance_id": instance_id,
                "project_id": project_id,
                "event_refs": [item.to_dict() for item in event_refs],
                "started_at": started_at,
                "ended_at": ended_at,
                "marker_ids": marker_ids,
            }
        )[:24]
        modes = tuple(
            sorted(
                {item.source_mode for item in ordered},
                key=lambda mode: (_SOURCE_MODE_ORDER.get(mode, 99), mode),
            )
        )
        labels = tuple(sorted({label for item in ordered for label in item.labels}))
        session_metadata: dict[str, Any] = {
            "timing_complete": len(timestamps) == len(ordered),
            "inactivity_gap_ms": int(self.inactivity_gap.total_seconds() * 1000),
        }
        if marker_ids:
            session_metadata["marker_ids"] = marker_ids
        if metadata:
            session_metadata.update(_json_copy(dict(metadata)))
        return Session(
            session_id=session_id,
            product=product,
            instance_id=instance_id,
            project_id=project_id,
            source_modes=modes,
            started_at=started_at,
            ended_at=ended_at,
            event_refs=event_refs,
            explicit_marker=any(item.explicit for item in ordered),
            labels=labels,
            metadata=session_metadata,
            events=tuple(item.source for item in ordered),
            _records=ordered,
        )

    @staticmethod
    def _duration_ms(session: Session) -> int:
        declared = session.metadata.get("duration_ms") if session.metadata else None
        if declared is not None:
            return max(0, int(declared))
        observed = [item.duration_ms for item in session._records if item.duration_ms is not None]
        if observed:
            return sum(observed)
        start, _ = _normalize_timestamp(session.started_at)
        end, _ = _normalize_timestamp(session.ended_at)
        return max(0, int(((end or _EPOCH) - (start or _EPOCH)).total_seconds() * 1000))
