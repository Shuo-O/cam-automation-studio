from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

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
OPTIONAL_ACTIVITY_FIELDS = (
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


def normalize_event_mode(value: Any = None) -> str:
    """Return a stable mode for filtering while accepting recorder aliases."""

    normalized = str(value or "manual").strip().lower()
    normalized = _MODE_ALIASES.get(normalized, normalized)
    return normalized if normalized in EVENT_MODES else "manual"


def normalize_source_mode(value: Any = None, *, fallback: str | None = None) -> str | None:
    """Normalize source mode without forcing new fields into legacy events."""

    if value is None:
        return fallback
    normalized = str(value).strip().lower()
    normalized = _MODE_ALIASES.get(normalized, normalized)
    return normalized if normalized in SOURCE_MODES else fallback


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """Versioned, product-neutral record of one observable CAM action."""

    session_id: str
    seq: int
    product: str
    action: str
    category: str
    params: Mapping[str, JsonValue] = field(default_factory=dict)
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
    command_response: Mapping[str, JsonValue] | None = None
    review_status: str | None = None
    recipe_hash: str | None = None
    _present_optional_fields: frozenset[str] = field(
        default_factory=frozenset, repr=False, compare=False
    )

    @property
    def token(self) -> str:
        return self.action

    @property
    def effective_source_mode(self) -> str:
        return self.source_mode or normalize_event_mode(self.mode)

    def to_dict(self) -> dict[str, Any]:
        source_mode = normalize_source_mode(self.source_mode)
        mode = (
            "automation"
            if source_mode == "execution_audit"
            else normalize_event_mode(self.mode)
        )
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "seq": self.seq,
            "product": self.product,
            "action": self.action,
            "category": self.category,
            "mode": mode,
            "params": dict(self.params),
            "source_file": self.source_file,
            "source_line": self.source_line,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }
        for name in OPTIONAL_ACTIVITY_FIELDS:
            value = source_mode if name == "source_mode" else getattr(self, name)
            if value is not None or name in self._present_optional_fields:
                result[name] = dict(value) if isinstance(value, Mapping) else value
        return result

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        fallback_source: str = "",
        fallback_line: int = 0,
    ) -> "ActivityEvent":
        raw_mode = (
            value.get("mode")
            or value.get("interaction_mode")
            or (
                value.get("params", {}).get("mode")
                if isinstance(value.get("params"), Mapping)
                else None
            )
        )
        source_mode = (
            normalize_source_mode(value.get("source_mode"))
            if "source_mode" in value
            else None
        )
        mode = (
            "automation"
            if source_mode == "execution_audit"
            else normalize_event_mode(raw_mode)
        )
        command_response = value.get("command_response")
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            session_id=str(value["session_id"]),
            seq=int(value["seq"]),
            product=str(value["product"]).lower(),
            action=str(value["action"]),
            category=str(value.get("category", "other")),
            mode=mode,
            params=dict(value.get("params", {})),
            source_file=str(value.get("source_file") or fallback_source),
            source_line=int(value.get("source_line") or fallback_line),
            duration_ms=(
                int(value["duration_ms"]) if value.get("duration_ms") is not None else None
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
            _present_optional_fields=frozenset(
                name for name in OPTIONAL_ACTIVITY_FIELDS if name in value
            ),
        )


@dataclass(frozen=True, slots=True)
class Pattern:
    pattern_id: str
    product: str
    steps: tuple[str, ...]
    support: int
    session_count: int
    occurrences: int
    score: float

    @property
    def confidence(self) -> float:
        return self.support / self.session_count if self.session_count else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "product": self.product,
            "steps": list(self.steps),
            "support": self.support,
            "session_count": self.session_count,
            "occurrences": self.occurrences,
            "confidence": round(self.confidence, 4),
            "score": round(self.score, 4),
        }
