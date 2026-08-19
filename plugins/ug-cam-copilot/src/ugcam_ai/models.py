from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

EVENT_MODES = frozenset({"manual", "automation", "system"})
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

    @property
    def token(self) -> str:
        return self.action

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "seq": self.seq,
            "product": self.product,
            "action": self.action,
            "category": self.category,
            "mode": normalize_event_mode(self.mode),
            "params": dict(self.params),
            "source_file": self.source_file,
            "source_line": self.source_line,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        fallback_source: str = "",
        fallback_line: int = 0,
    ) -> "ActivityEvent":
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            session_id=str(value["session_id"]),
            seq=int(value["seq"]),
            product=str(value["product"]).lower(),
            action=str(value["action"]),
            category=str(value.get("category", "other")),
            mode=normalize_event_mode(
                value.get("mode")
                or value.get("interaction_mode")
                or (
                    value.get("params", {}).get("mode")
                    if isinstance(value.get("params"), Mapping)
                    else None
                )
            ),
            params=dict(value.get("params", {})),
            source_file=str(value.get("source_file") or fallback_source),
            source_line=int(value.get("source_line") or fallback_line),
            duration_ms=(
                int(value["duration_ms"]) if value.get("duration_ms") is not None else None
            ),
            timestamp=(
                str(value["timestamp"]) if value.get("timestamp") is not None else None
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
