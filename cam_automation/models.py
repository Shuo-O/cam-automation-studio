from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


RISK_ORDER = {"safe": 0, "review": 1, "blocked": 2}
EVENT_MODES = frozenset({"manual", "automation", "system"})
SOURCE_MODES = frozenset({*EVENT_MODES, "execution_audit"})
VIEW_LEVELS = frozenset({"L0", "L1", "L2", "L3", "L4"})
EXPERTISE_LABELS = frozenset({"unlabeled", "routine", "expert"})
REVIEW_STATUSES = frozenset(
    {
        "unreviewed",
        "needs_review",
        "needs_changes",
        "approved_for_simulation",
        "rejected",
    }
)
PRODUCTS = frozenset({"nx", "powermill"})
_MODE_ALIASES = {
    "human": "manual",
    "operator": "manual",
    "recorded": "manual",
    "auto": "automation",
    "automatic": "automation",
    "automated": "automation",
    "macro": "automation",
    "replay": "automation",
    "script": "automation",
    "background": "system",
    "internal": "system",
    "telemetry": "system",
}
_ACTIVITY_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "seq",
        "product",
        "action",
        "category",
        "mode",
        "params",
        "source_file",
        "source_line",
        "duration_ms",
        "timestamp",
        "source_mode",
        "view_level",
        "expertise_label",
        "instance_id",
        "project_id",
        "target_version",
        "command_response",
        "review_status",
        "recipe_hash",
    }
)
_ACTIVITY_EVENT_EXTENSIONS = (
    "source_mode",
    "view_level",
    "expertise_label",
    "instance_id",
    "project_id",
    "target_version",
    "command_response",
    "review_status",
    "recipe_hash",
)


def normalize_event_mode(value: Any = None) -> str:
    normalized = str(value or "manual").strip().lower()
    normalized = _MODE_ALIASES.get(normalized, normalized)
    return normalized if normalized in EVENT_MODES else "manual"


def _source_mode_and_legacy_mode(
    source_mode: Any,
    mode: Any,
) -> tuple[str | None, str]:
    normalized_source = (
        str(source_mode).strip().lower() if source_mode is not None else None
    )
    if normalized_source not in SOURCE_MODES:
        normalized_source = None
    legacy_mode = normalize_event_mode(mode)
    if normalized_source == "execution_audit":
        legacy_mode = "automation"
    elif normalized_source in EVENT_MODES:
        legacy_mode = normalized_source
    return normalized_source, legacy_mode


def _rfc3339(value: str, field_name: str) -> datetime:
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"{field_name} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _query_values(value: Sequence[str] | str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """Backward-compatible ActivityEvent v1 with lossless optional extensions."""

    session_id: str
    seq: int
    product: str
    action: str
    category: str
    params: Mapping[str, Any] = field(default_factory=dict)
    source_file: str = ""
    source_line: int = 0
    duration_ms: int | None = None
    timestamp: str | None = None
    mode: str = "manual"
    schema_version: int = 1
    source_mode: str | None = None
    view_level: str | None = None
    expertise_label: str | None = None
    instance_id: str | None = None
    project_id: str | None = None
    target_version: str | None = None
    command_response: Mapping[str, Any] | None = None
    review_status: str | None = None
    recipe_hash: str | None = None
    extra_fields: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )
    _present_optional_fields: frozenset[str] = field(
        default_factory=frozenset,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        source_mode, mode = _source_mode_and_legacy_mode(self.source_mode, self.mode)
        object.__setattr__(self, "source_mode", source_mode)
        object.__setattr__(self, "mode", mode)
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")

    @property
    def effective_source_mode(self) -> str:
        return self.source_mode or self.mode

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.extra_fields)
        value.update(
            {
                "schema_version": self.schema_version,
                "session_id": self.session_id,
                "seq": self.seq,
                "product": self.product,
                "action": self.action,
                "category": self.category,
                "mode": self.mode,
                "params": dict(self.params),
                "source_file": self.source_file,
                "source_line": self.source_line,
                "duration_ms": self.duration_ms,
                "timestamp": self.timestamp,
            }
        )
        for name in _ACTIVITY_EVENT_EXTENSIONS:
            item = getattr(self, name)
            if item is not None or name in self._present_optional_fields:
                value[name] = dict(item) if isinstance(item, Mapping) else item
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActivityEvent":
        params = value.get("params", {})
        if not isinstance(params, Mapping):
            raise ValueError("ActivityEvent params must be an object")
        command_response = value.get("command_response")
        if command_response is not None and not isinstance(command_response, Mapping):
            raise ValueError("ActivityEvent command_response must be an object or null")
        source_mode, mode = _source_mode_and_legacy_mode(
            value.get("source_mode"),
            value.get("mode") or params.get("mode"),
        )
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            session_id=str(value["session_id"]),
            seq=int(value["seq"]),
            product=str(value["product"]).lower(),
            action=str(value["action"]),
            category=str(value.get("category", "other")),
            mode=mode,
            params=dict(params),
            source_file=str(value.get("source_file", "")),
            source_line=int(value.get("source_line", 0) or 0),
            duration_ms=(
                int(value["duration_ms"])
                if value.get("duration_ms") is not None
                else None
            ),
            timestamp=(
                str(value["timestamp"]) if value.get("timestamp") is not None else None
            ),
            source_mode=source_mode,
            view_level=(
                str(value["view_level"]) if value.get("view_level") is not None else None
            ),
            expertise_label=(
                str(value["expertise_label"])
                if value.get("expertise_label") is not None
                else None
            ),
            instance_id=(
                str(value["instance_id"]) if value.get("instance_id") is not None else None
            ),
            project_id=(
                str(value["project_id"]) if value.get("project_id") is not None else None
            ),
            target_version=(
                str(value["target_version"])
                if value.get("target_version") is not None
                else None
            ),
            command_response=(
                dict(command_response) if isinstance(command_response, Mapping) else None
            ),
            review_status=(
                str(value["review_status"])
                if value.get("review_status") is not None
                else None
            ),
            recipe_hash=(
                str(value["recipe_hash"]) if value.get("recipe_hash") is not None else None
            ),
            extra_fields={
                str(key): item
                for key, item in value.items()
                if key not in _ACTIVITY_EVENT_FIELDS
            },
            _present_optional_fields=frozenset(
                name for name in _ACTIVITY_EVENT_EXTENSIONS if name in value
            ),
        )


@dataclass(frozen=True, slots=True)
class EventQuery:
    schema_version: int = 1
    source_modes: tuple[str, ...] = ()
    view_levels: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    instance_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    from_time: str | None = None
    to_time: str | None = None
    text: str = ""
    cursor: str | None = None
    limit: int = 100
    sort: str = "asc"

    def __post_init__(self) -> None:
        for name in (
            "source_modes",
            "view_levels",
            "products",
            "instance_ids",
            "project_ids",
            "session_ids",
            "actions",
            "categories",
        ):
            object.__setattr__(self, name, _query_values(getattr(self, name)))
        if self.schema_version != 1:
            raise ValueError("EventQuery schema_version must be 1")
        invalid_source_modes = set(self.source_modes) - SOURCE_MODES
        if invalid_source_modes:
            raise ValueError(f"Invalid source_modes: {sorted(invalid_source_modes)}")
        invalid_view_levels = set(self.view_levels) - VIEW_LEVELS
        if invalid_view_levels:
            raise ValueError(f"Invalid view_levels: {sorted(invalid_view_levels)}")
        invalid_products = set(self.products) - PRODUCTS
        if invalid_products:
            raise ValueError(f"Invalid products: {sorted(invalid_products)}")
        if not 1 <= int(self.limit) <= 10_000:
            raise ValueError("EventQuery limit must be between 1 and 10000")
        object.__setattr__(self, "limit", int(self.limit))
        if self.sort not in {"asc", "desc"}:
            raise ValueError("EventQuery sort must be asc or desc")
        from_value = _rfc3339(self.from_time, "from_time") if self.from_time else None
        to_value = _rfc3339(self.to_time, "to_time") if self.to_time else None
        if from_value and to_value and from_value > to_value:
            raise ValueError("EventQuery from_time must not be later than to_time")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EventQuery":
        def values(plural: str, singular: str) -> tuple[str, ...]:
            candidate = value.get(plural)
            if candidate is None and singular in value:
                candidate = value.get(singular)
            return _query_values(candidate)

        return cls(
            schema_version=int(value.get("schema_version", 1)),
            source_modes=values("source_modes", "source_mode"),
            view_levels=values("view_levels", "view_level"),
            products=values("products", "product"),
            instance_ids=values("instance_ids", "instance_id"),
            project_ids=values("project_ids", "project_id"),
            session_ids=values("session_ids", "session_id"),
            actions=values("actions", "action"),
            categories=values("categories", "category"),
            from_time=(
                str(value["from_time"]) if value.get("from_time") is not None else None
            ),
            to_time=str(value["to_time"]) if value.get("to_time") is not None else None,
            text=str(value.get("text", "")),
            cursor=str(value["cursor"]) if value.get("cursor") is not None else None,
            limit=int(value.get("limit", 100)),
            sort=str(value.get("sort", "asc")),
        )

    def to_dict(self, *, include_cursor: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": self.schema_version,
            "source_modes": list(self.source_modes),
            "view_levels": list(self.view_levels),
            "products": list(self.products),
            "instance_ids": list(self.instance_ids),
            "project_ids": list(self.project_ids),
            "session_ids": list(self.session_ids),
            "actions": list(self.actions),
            "categories": list(self.categories),
            "from_time": self.from_time,
            "to_time": self.to_time,
            "text": self.text,
            "limit": self.limit,
            "sort": self.sort,
        }
        if include_cursor:
            value["cursor"] = self.cursor
        return value


@dataclass(frozen=True, slots=True)
class EventPage:
    events: list[ActivityEvent]
    has_more: bool
    next_cursor: str | None
    total_estimate: int | None = None
    schema_version: int = 1
    returned_count: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "returned_count", len(self.events))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "events": [event.to_dict() for event in self.events],
            "returned_count": self.returned_count,
            "has_more": self.has_more,
            "next_cursor": self.next_cursor,
            "total_estimate": self.total_estimate,
        }


@dataclass(frozen=True)
class SafetyAssessment:
    level: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandEvent:
    sequence: int
    line_number: int
    command: str
    normalized: str
    operation: str
    product: str
    action: str
    category: str
    mode: str
    source: str
    timestamp: str | None
    safety: SafetyAssessment
    activity_event: ActivityEvent | None = None


@dataclass
class Session:
    name: str
    events: list[CommandEvent] = field(default_factory=list)


@dataclass(frozen=True)
class ParseDiagnostic:
    line_number: int
    message: str
    raw: str


@dataclass
class ParseResult:
    sessions: list[Session]
    diagnostics: list[ParseDiagnostic] = field(default_factory=list)
    input_lines: int = 0
    ignored_lines: int = 0

    @property
    def event_count(self) -> int:
        return sum(len(session.events) for session in self.sessions)

    def to_activity_events(self) -> list[dict[str, Any]]:
        """Project parsed commands onto the repository's ActivityEvent v1 contract."""

        projected: list[dict[str, Any]] = []
        for session in self.sessions:
            for sequence, event in enumerate(session.events):
                if event.activity_event is not None:
                    projected.append(event.activity_event.to_dict())
                    continue
                projected.append(
                    ActivityEvent(
                        session_id=session.name,
                        seq=sequence,
                        product=event.product,
                        action=event.action,
                        category=event.category,
                        mode=event.mode,
                        source_mode=event.mode,
                        view_level="L1",
                        params={
                            "command": event.normalized,
                            "operation": event.operation,
                            "risk": event.safety.level,
                            "mode": event.mode,
                            "source": event.source,
                            "hierarchy": [
                                event.product,
                                event.category,
                                event.action,
                            ],
                        },
                        source_line=event.line_number,
                        timestamp=event.timestamp,
                    ).to_dict()
                )
        return projected


@dataclass
class RecipeParameter:
    name: str
    value_type: str
    default: str | int | float
    samples: list[str | int | float]
    quote: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecipeStep:
    step_id: str
    operation: str
    action: str
    template: str
    risk: str
    reasons: list[str]
    source_lines: list[int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowRecipe:
    name: str
    profile: str
    sessions_analyzed: int
    sessions_matched: list[str]
    parameters: list[RecipeParameter]
    steps: list[RecipeStep]
    diagnostics: list[str]
    schema_version: str = "0.1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "profile": self.profile,
            "source": {
                "sessions_analyzed": self.sessions_analyzed,
                "sessions_matched": self.sessions_matched,
            },
            "review": {
                "status": "required",
                "safe_steps": sum(step.risk == "safe" for step in self.steps),
                "review_steps": sum(step.risk == "review" for step in self.steps),
                "blocked_steps": sum(step.risk == "blocked" for step in self.steps),
            },
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "steps": [step.to_dict() for step in self.steps],
            "diagnostics": self.diagnostics,
        }
