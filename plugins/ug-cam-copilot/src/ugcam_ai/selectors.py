from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from .models import JsonValue


_RECORDED_ID_PATTERNS = (
    re.compile(r"\b(?:ENTITY|HANDLE|FACE|EDGE|BODY|FEATURE)\b.*\d", re.IGNORECASE),
    re.compile(r"^#[0-9]+$"),
    re.compile(r"^[A-Z_]+\([0-9, ]+\)$"),
)
_GENERIC_NAMES = frozenset(
    {
        "GEOMETRY",
        "METHOD",
        "MCS",
        "NC_PROGRAM",
        "PROGRAM",
        "TOOL",
        "WORKPIECE",
    }
)


@dataclass(frozen=True, slots=True)
class NxSelectorSuggestion:
    strategy: str
    criteria: Mapping[str, JsonValue]
    rationale: str
    requires_unique_match: bool = True
    requires_user_input: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "strategy": self.strategy,
            "criteria": dict(self.criteria),
            "rationale": self.rationale,
            "requires_unique_match": self.requires_unique_match,
            "requires_user_input": self.requires_user_input,
        }


@dataclass(frozen=True, slots=True)
class NxSelectorAssessment:
    recorded_identifier: str | None
    fragile: bool
    recorded_id_pattern: bool
    ambiguity_risk: str
    reasons: tuple[str, ...]
    suggestions: tuple[NxSelectorSuggestion, ...]
    unique_match_claimed: bool = False
    resolved: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "source": "FindObject",
            "recorded_identifier": self.recorded_identifier,
            "fragile": self.fragile,
            "recorded_id_pattern": self.recorded_id_pattern,
            "ambiguity_risk": self.ambiguity_risk,
            "reasons": list(self.reasons),
            "suggestions": [item.to_dict() for item in self.suggestions],
            "unique_match_claimed": self.unique_match_claimed,
            "resolved": self.resolved,
        }


def assess_find_object(identifier: str | None) -> NxSelectorAssessment:
    """Describe safer selector candidates without claiming that any is unique."""

    normalized = identifier.strip() if identifier else None
    recorded_id = bool(
        normalized and any(pattern.search(normalized) for pattern in _RECORDED_ID_PATTERNS)
    )
    generic_name = bool(normalized and normalized.upper() in _GENERIC_NAMES)
    reasons = ["find_object_identifier_is_not_a_verified_stable_selector"]
    if recorded_id:
        reasons.append("identifier_looks_session_or_recording_scoped")
    if generic_name:
        reasons.append("generic_name_can_match_multiple_cam_objects")
    if normalized is None:
        reasons.append("selector_expression_is_dynamic")

    name_criteria: dict[str, JsonValue] = {"name": normalized}
    return NxSelectorAssessment(
        recorded_identifier=normalized,
        fragile=True,
        recorded_id_pattern=recorded_id,
        ambiguity_risk="high" if recorded_id or generic_name or normalized is None else "medium",
        reasons=tuple(reasons),
        suggestions=(
            NxSelectorSuggestion(
                strategy="name",
                criteria=name_criteria,
                rationale="Use a shop-controlled object name and verify exactly one match.",
                requires_user_input=normalized is None,
            ),
            NxSelectorSuggestion(
                strategy="attribute",
                criteria={"attribute_name": None, "attribute_value": None},
                rationale="Prefer a governed NX user attribute when the shop defines one.",
                requires_user_input=True,
            ),
            NxSelectorSuggestion(
                strategy="pmi",
                criteria={"pmi_type": None, "label": None},
                rationale="Use reviewed PMI semantics when PMI is authoritative for selection.",
                requires_user_input=True,
            ),
            NxSelectorSuggestion(
                strategy="geometry",
                criteria={
                    "entity_type": None,
                    "geometric_signature": None,
                    "tolerance": None,
                },
                rationale=(
                    "Use a verified geometric query with explicit units and tolerance, "
                    "then reject zero or multiple matches."
                ),
                requires_user_input=True,
            ),
        ),
    )
