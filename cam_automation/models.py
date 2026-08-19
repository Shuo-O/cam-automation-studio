from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


RISK_ORDER = {"safe": 0, "review": 1, "blocked": 2}


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
                projected.append(
                    {
                        "schema_version": 1,
                        "session_id": session.name,
                        "seq": sequence,
                        "product": event.product,
                        "action": event.action,
                        "category": event.category,
                        "params": {
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
                        "source_file": "",
                        "source_line": event.line_number,
                        "duration_ms": None,
                        "timestamp": event.timestamp,
                    }
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
