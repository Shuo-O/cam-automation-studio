from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from .models import (
    RISK_ORDER,
    CommandEvent,
    ParseResult,
    RecipeParameter as LegacyRecipeParameter,
    RecipeStep as LegacyRecipeStep,
    Session as LegacySession,
    WorkflowRecipe as LegacyWorkflowRecipe,
)
from .recipes import (
    REQUIRED_PRODUCTION_GATES,
    Recipe,
    RecipeParameter,
    RecipeStep,
    compute_recipe_hash,
)
from .sessions import (
    EventRef,
    Session,
    SessionService,
    _EventRecord,
    _align_signatures,
    _flatten_json,
    _is_noise,
    _stable_hash,
    _stable_json,
    _step_key,
    _structural_signature,
    _workflow_records,
)

if TYPE_CHECKING:
    from .profiles import PowerMillProfile

__all__ = [
    "CandidateParameter",
    "RecipeCandidate",
    "SessionService",
    "WorkflowLearner",
    "command_shape",
    "learn_file",
    "learn_recipe",
    "learn_workflow",
]


_TOKEN_RE = re.compile(
    r"\s+|\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'|"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|"
    r"[A-Za-z_][A-Za-z0-9_]*|."
)
_NUMBER_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$")
_SAFE_NAME = re.compile(r"[^a-z0-9]+")
_PATH_HINT = re.compile(r"[\\/]|(?:\.[A-Za-z0-9]{2,8})$")


def _tokens(command: str) -> list[str]:
    return _TOKEN_RE.findall(command)


def _literal_kind(token: str) -> str | None:
    if len(token) >= 2 and token[0] in {'"', "'"} and token[-1] == token[0]:
        return "string"
    if _NUMBER_RE.match(token):
        return "number"
    return None


def _literal_value(token: str, kind: str) -> str | int | float:
    if kind == "string":
        quote = token[0]
        return token[1:-1].replace(quote * 2, quote)
    if any(marker in token.lower() for marker in (".", "e")):
        return float(token)
    return int(token)


def command_shape(command: str) -> str:
    shaped: list[str] = []
    for token in _tokens(command):
        if token.isspace():
            if shaped and shaped[-1] != " ":
                shaped.append(" ")
            continue
        kind = _literal_kind(token)
        if kind == "string":
            shaped.append("<STRING>")
        elif kind == "number":
            shaped.append("<NUMBER>")
        elif token[0].isalpha() or token[0] == "_":
            shaped.append(token.upper())
        else:
            shaped.append(token)
    return "".join(shaped).strip()


def _session_signature(session: LegacySession) -> tuple[str, ...]:
    return tuple(command_shape(event.normalized) for event in session.events)


def _slug(value: str) -> str:
    slug = _SAFE_NAME.sub("_", value.lower()).strip("_")
    return slug or "value"


def _previous_context(tokens: list[str], index: int) -> str:
    for token in reversed(tokens[:index]):
        if token.isspace() or _literal_kind(token):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token):
            return token.lower()
    return "value"


@dataclass
class _ParameterRegistry:
    parameters: list[LegacyRecipeParameter]
    by_samples: dict[tuple[str, tuple[object, ...]], LegacyRecipeParameter]
    used_names: set[str]

    @classmethod
    def create(cls) -> "_ParameterRegistry":
        return cls([], {}, set())

    def get_or_create(
        self,
        *,
        samples: list[str | int | float],
        literal_kind: str,
        quote: str,
        operation: str,
        context: str,
    ) -> LegacyRecipeParameter:
        value_type = literal_kind
        if literal_kind == "string" and any(_PATH_HINT.search(str(value)) for value in samples):
            value_type = "path"
        key = (value_type, tuple(samples))
        if key in self.by_samples:
            return self.by_samples[key]

        base_context = context
        if context in {
            "as",
            "calculate",
            "create",
            "edit",
            "file",
            "filesave",
            "from",
            "import",
            "print",
            "to",
            "value",
        }:
            base_context = _slug(operation)
        suffix = "path" if value_type == "path" else "name" if value_type == "string" else "value"
        base = _slug(base_context)
        if not base.endswith(suffix):
            base = f"{base}_{suffix}"
        name = base
        counter = 2
        while name in self.used_names:
            name = f"{base}_{counter}"
            counter += 1

        parameter = LegacyRecipeParameter(
            name=name,
            value_type=value_type,
            default=samples[0],
            samples=list(dict.fromkeys(samples)),
            quote=quote,
            description=f"Learned from varying {operation.lower()} values.",
        )
        self.parameters.append(parameter)
        self.by_samples[key] = parameter
        self.used_names.add(name)
        return parameter


def _select_session_group(
    sessions: list[LegacySession],
) -> tuple[list[LegacySession], list[str]]:
    groups: dict[tuple[str, ...], list[LegacySession]] = defaultdict(list)
    signature_order: list[tuple[str, ...]] = []
    for session in sessions:
        signature = _session_signature(session)
        if signature not in groups:
            signature_order.append(signature)
        groups[signature].append(session)

    best_signature = max(
        signature_order,
        key=lambda signature: (
            len(groups[signature]),
            len(signature),
            -signature_order.index(signature),
        ),
    )
    selected = groups[best_signature]
    diagnostics: list[str] = []
    if len(selected) != len(sessions):
        diagnostics.append(
            f"Used {len(selected)} of {len(sessions)} sessions with the dominant command shape."
        )
    if len(selected) == 1:
        diagnostics.append(
            "Only one structurally matching session was available; "
            "literal variation is not inferred."
        )
    return selected, diagnostics


def _build_step(
    index: int,
    events: list[CommandEvent],
    registry: _ParameterRegistry,
    diagnostics: list[str],
) -> LegacyRecipeStep:
    representative = events[0]
    token_sets = [_tokens(event.normalized) for event in events]
    template_tokens = list(token_sets[0])

    if not all(len(tokens) == len(template_tokens) for tokens in token_sets):
        diagnostics.append(
            f"Step {index + 1} token alignment differed; kept the representative command."
        )
    else:
        for token_index, representative_token in enumerate(template_tokens):
            values = [tokens[token_index] for tokens in token_sets]
            if len(set(values)) == 1:
                continue
            kinds = [_literal_kind(value) for value in values]
            if not kinds[0] or len(set(kinds)) != 1:
                diagnostics.append(
                    f"Step {index + 1} has a non-literal variation; kept the first observed value."
                )
                continue
            literal_kind = kinds[0]
            samples = [_literal_value(value, literal_kind) for value in values]
            parameter = registry.get_or_create(
                samples=samples,
                literal_kind=literal_kind,
                quote=representative_token[0] if literal_kind == "string" else "",
                operation=representative.operation,
                context=_previous_context(template_tokens, token_index),
            )
            template_tokens[token_index] = "{{" + parameter.name + "}}"

    highest_risk = max(
        (event.safety.level for event in events),
        key=lambda level: RISK_ORDER[level],
    )
    reasons = sorted(
        {
            reason
            for event in events
            if event.safety.level == highest_risk
            for reason in event.safety.reasons
        }
    )
    return LegacyRecipeStep(
        step_id=f"step-{index + 1:03d}",
        operation=representative.operation,
        action=representative.action,
        template="".join(template_tokens),
        risk=highest_risk,
        reasons=reasons,
        source_lines=[event.line_number for event in events],
    )


def learn_recipe(
    parsed: ParseResult, name: str = "powermill-workflow"
) -> LegacyWorkflowRecipe:
    if not parsed.sessions or parsed.event_count == 0:
        raise ValueError("No PowerMill commands were found in the supplied log.")

    selected, diagnostics = _select_session_group(parsed.sessions)
    diagnostics.extend(
        f"Line {item.line_number}: {item.message}" for item in parsed.diagnostics
    )
    registry = _ParameterRegistry.create()
    steps = [
        _build_step(index, [session.events[index] for session in selected], registry, diagnostics)
        for index in range(len(selected[0].events))
    ]
    return LegacyWorkflowRecipe(
        name=name,
        profile="powermill",
        sessions_analyzed=len(parsed.sessions),
        sessions_matched=[session.name for session in selected],
        parameters=registry.parameters,
        steps=steps,
        diagnostics=diagnostics,
    )


def learn_workflow(
    text: str,
    *,
    name: str = "powermill-workflow",
    profile: PowerMillProfile | None = None,
) -> tuple[ParseResult, LegacyWorkflowRecipe]:
    # Compatibility entry point for the original offline PowerMill demo. The
    # product-neutral WorkflowLearner below never imports a product parser.
    from .parser import parse_log

    parsed = parse_log(text, profile=profile)
    return parsed, learn_recipe(parsed, name=name)


def learn_file(
    path: str | Path, *, name: str | None = None
) -> tuple[ParseResult, LegacyWorkflowRecipe]:
    source = Path(path)
    text = source.read_text(encoding="utf-8-sig")
    return learn_workflow(text, name=name or source.stem)


MAX_STRUCTURAL_STEPS = 512
MAX_SHAPE_GROUPS = 1_024
MAX_SHAPE_FAMILIES = 128
MAX_RECIPE_CANDIDATES = 32
_PARAMETER_EVIDENCE_LIMIT = 128
_IGNORED_PARAMETER_PATHS = {
    "command",
    "hierarchy",
    "mode",
    "raw",
    "risk",
    "source",
}
_RISK_RANK = {"safe": 0, "review": 1, "blocked": 2}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _set_argument(target: dict[str, Any], path: str, value: Any) -> None:
    keys = [part for part in path.split(".") if part]
    if not keys:
        return
    current = target
    for key in keys[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[keys[-1]] = _json_copy(value)


def _parameter_name(path: str, used: set[str], step_order: int) -> str:
    leaf = path.rsplit(".", 1)[-1]
    base = _SAFE_NAME.sub("_", leaf.lower()).strip("_")
    if not base or not base[0].isalpha():
        base = f"value_{base}".rstrip("_")
    name = base
    if name in used:
        name = f"step_{step_order}_{base}"
    counter = 2
    candidate = name
    while candidate in used:
        candidate = f"{name}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _unique_samples(values: Iterable[Any]) -> tuple[Any, ...]:
    by_key: dict[str, Any] = {}
    for value in values:
        by_key.setdefault(_stable_json(value), _json_copy(value))
    return tuple(by_key[key] for key in sorted(by_key))


def _infer_value_type(path: str, values: Sequence[Any]) -> str | None:
    if values and all(type(value) is bool for value in values):
        return "boolean"
    if values and all(type(value) is int for value in values):
        return "integer"
    if values and all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        for value in values
    ):
        return "number"
    if values and all(isinstance(value, Mapping) for value in values):
        lowered = path.lower()
        selector_keys = {
            str(key).lower()
            for value in values
            for key in value
        }
        if "selector" in lowered or {"strategy", "value"}.issubset(selector_keys):
            return "object_selector"
        return None
    if values and all(isinstance(value, str) for value in values):
        if any(_PATH_HINT.search(value) for value in values):
            return "path"
        lowered = path.lower()
        if lowered.endswith(("method", "mode", "strategy", "type", "units")):
            return "enum"
        return "string"
    return None


def _record_risk(record: _EventRecord) -> str:
    value = str(
        getattr(record.source, "risk", "")
        if not isinstance(record.source, Mapping)
        else record.source.get("risk", "")
    ).lower()
    if not value:
        value = str(record.params.get("risk", "")).lower()
    return value if value in _RISK_RANK else "review"


@dataclass(frozen=True)
class CandidateParameter:
    name: str
    value_type: str
    required: bool
    default: Any
    samples: tuple[Any, ...]
    description: str
    step_key: str
    parameter: str
    source_event_refs: tuple[EventRef, ...]
    constraints: Mapping[str, Any] = field(default_factory=dict)
    enum_values: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = {
            "name": self.name,
            "value_type": self.value_type,
            "required": self.required,
            "default": _json_copy(self.default),
            "samples": _json_copy(list(self.samples)),
            "description": self.description,
            "step_key": self.step_key,
            "parameter": self.parameter,
            "source_event_refs": [item.to_dict() for item in self.source_event_refs],
        }
        if self.constraints:
            value["constraints"] = _json_copy(dict(self.constraints))
        if self.enum_values:
            value["enum_values"] = _json_copy(list(self.enum_values))
        return value

    def to_recipe_parameter(self) -> RecipeParameter:
        return RecipeParameter(
            name=self.name,
            value_type=self.value_type,
            required=self.required,
            default=_json_copy(self.default),
            samples=tuple(_json_copy(list(self.samples))),
            description=self.description,
            constraints=_json_copy(dict(self.constraints)),
            enum_values=tuple(_json_copy(list(self.enum_values))),
            source_event_refs=self.source_event_refs,
        )


@dataclass(frozen=True)
class RecipeCandidate:
    candidate_id: str
    product: str
    source_session_ids: tuple[str, ...]
    support: Mapping[str, Any]
    common_steps: tuple[Mapping[str, Any], ...]
    branches: tuple[Mapping[str, Any], ...]
    parameters: tuple[CandidateParameter, ...]
    differences: tuple[Mapping[str, Any], ...]
    evidence: tuple[Mapping[str, Any], ...]
    target_versions: tuple[str, ...]
    created_at: str
    schema_version: int = 1
    view_level: str = "L3"
    candidate_type: str = "stable_workflow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "candidate_type": self.candidate_type,
            "view_level": self.view_level,
            "product": self.product,
            "source_session_ids": list(self.source_session_ids),
            "support": _json_copy(dict(self.support)),
            "common_steps": _json_copy(list(self.common_steps)),
            "branches": _json_copy(list(self.branches)),
            "parameters": [item.to_dict() for item in self.parameters],
            "differences": _json_copy(list(self.differences)),
            "evidence": _json_copy(list(self.evidence)),
            "target_versions": list(self.target_versions),
            "created_at": self.created_at,
        }

    def to_recipe(self, name: str | None = None) -> Recipe:
        parameter_bindings: dict[str, list[CandidateParameter]] = defaultdict(list)
        for parameter in self.parameters:
            parameter_bindings[parameter.step_key].append(parameter)

        steps: list[RecipeStep] = []
        for common_step in self.common_steps:
            arguments = _json_copy(common_step.get("arguments", {}))
            for parameter in parameter_bindings.get(str(common_step["step_key"]), []):
                _set_argument(arguments, parameter.parameter, {"parameter": parameter.name})
            steps.append(
                RecipeStep(
                    step_id=f"step-{int(common_step['order']):03d}",
                    order=int(common_step["order"]),
                    action=str(common_step["action"]),
                    enabled=True,
                    risk=str(common_step.get("risk", "review")),
                    review_status="needs_review",
                    arguments=arguments,
                    condition=_json_copy(common_step.get("condition")),
                    source_event_refs=tuple(
                        EventRef.from_value(item)
                        for item in common_step.get("source_event_refs", [])
                    ),
                    notes="Learned from repeated sessions; human review is required.",
                )
            )

        recipe = Recipe(
            recipe_id=self.candidate_id.replace("candidate:", "recipe:", 1),
            recipe_hash="",
            name=name or f"{self.product.upper()} repeated workflow",
            product=self.product,
            status="review_required",
            target_versions=self.target_versions,
            source_session_ids=self.source_session_ids,
            support=_json_copy(dict(self.support)),
            parameters=tuple(item.to_recipe_parameter() for item in self.parameters),
            steps=tuple(steps),
            required_gates=REQUIRED_PRODUCTION_GATES,
            created_at=self.created_at,
            updated_at=self.created_at,
            description=(
                "Review-first learned workflow. Simulation, collision checks, and "
                "shop approval have not been completed."
            ),
            project_conditions={"test_copy_required": True},
            branches=tuple(_json_copy(list(self.branches))),
        )
        return replace(recipe, recipe_hash=compute_recipe_hash(recipe))


@dataclass
class _ShapeGroup:
    sequence: tuple[str, ...]
    sessions: list[Session]


@dataclass
class _WorkflowFamily:
    groups: list[_ShapeGroup]

    @property
    def representative(self) -> tuple[str, ...]:
        return self.groups[0].sequence

    @property
    def session_count(self) -> int:
        return sum(len(group.sessions) for group in self.groups)


def _family_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    jaccard = len(left_set & right_set) / len(left_set | right_set)
    mapping, _ = _align_signatures(left, right)
    order_ratio = len(mapping) / min(len(left), len(right))
    return min(jaccard, order_ratio)


def _common_backbone(groups: Sequence[_ShapeGroup]) -> tuple[str, ...]:
    backbone = list(groups[0].sequence)
    for group in groups[1:]:
        mapping, _ = _align_signatures(backbone, group.sequence)
        backbone = [backbone[index] for index in sorted(mapping)]
        if not backbone:
            break
    return tuple(backbone)


class WorkflowLearner:
    """Mine product-neutral repeated workflows from deterministic sessions."""

    def __init__(
        self,
        sessions: SessionService | Iterable[Session],
        *,
        min_support: int = 2,
        family_similarity: float = 0.6,
        max_structural_steps: int = MAX_STRUCTURAL_STEPS,
        max_shape_groups: int = MAX_SHAPE_GROUPS,
        max_families: int = MAX_SHAPE_FAMILIES,
        max_candidates: int = MAX_RECIPE_CANDIDATES,
    ) -> None:
        if min_support < 2:
            raise ValueError("Repeated workflow support must be at least 2.")
        if not 0 < family_similarity <= 1:
            raise ValueError("family_similarity must fall in (0, 1].")
        self.session_service = (
            sessions if isinstance(sessions, SessionService) else SessionService(sessions)
        )
        self.min_support = min_support
        self.family_similarity = family_similarity
        self.max_structural_steps = max(2, min(max_structural_steps, MAX_STRUCTURAL_STEPS))
        self.max_shape_groups = max(1, min(max_shape_groups, MAX_SHAPE_GROUPS))
        self.max_families = max(1, min(max_families, MAX_SHAPE_FAMILIES))
        self.max_candidates = max(1, min(max_candidates, MAX_RECIPE_CANDIDATES))

    def mine(
        self, session_ids: Iterable[str | Session] | None = None
    ) -> list[RecipeCandidate]:
        selected = self._resolve_sessions(session_ids)
        if len(selected) < self.min_support:
            return []
        by_product: dict[str, list[Session]] = defaultdict(list)
        for session in selected:
            by_product[session.product].append(session)

        candidates: list[RecipeCandidate] = []
        for product in sorted(by_product):
            product_sessions = sorted(
                by_product[product], key=lambda session: session.session_id
            )
            if len(product_sessions) < self.min_support:
                continue
            shape_groups: dict[tuple[str, ...], list[Session]] = defaultdict(list)
            for session in product_sessions:
                records = _workflow_records(session)[: self.max_structural_steps]
                sequence = tuple(_structural_signature(record) for record in records)
                if len(sequence) >= 2:
                    shape_groups[sequence].append(session)
            ordered_groups = [
                _ShapeGroup(
                    sequence=sequence,
                    sessions=sorted(items, key=lambda item: item.session_id),
                )
                for sequence, items in shape_groups.items()
            ]
            ordered_groups.sort(key=lambda item: (-len(item.sessions), item.sequence))
            ordered_groups = ordered_groups[: self.max_shape_groups]

            families: list[_WorkflowFamily] = []
            for group in ordered_groups:
                best_family: _WorkflowFamily | None = None
                best_score = 0.0
                for family in families:
                    score = _family_similarity(family.representative, group.sequence)
                    if score >= self.family_similarity and score > best_score:
                        best_family = family
                        best_score = score
                if best_family is not None:
                    best_family.groups.append(group)
                elif len(families) < self.max_families:
                    families.append(_WorkflowFamily([group]))

            for family in families:
                if family.session_count < self.min_support:
                    continue
                candidate = self._build_candidate(
                    product,
                    product_sessions,
                    family,
                )
                if candidate is not None:
                    candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                -int(item.support["matched_sessions"]),
                -len(item.common_steps),
                item.candidate_id,
            )
        )
        return candidates[: self.max_candidates]

    def _resolve_sessions(
        self, requested: Iterable[str | Session] | None
    ) -> list[Session]:
        if requested is None:
            return list(self.session_service.sessions)
        resolved: dict[str, Session] = {}
        for item in requested:
            session = item if isinstance(item, Session) else self.session_service.get(str(item))
            resolved[session.session_id] = session
        return [resolved[key] for key in sorted(resolved)]

    def _build_candidate(
        self,
        product: str,
        product_sessions: Sequence[Session],
        family: _WorkflowFamily,
    ) -> RecipeCandidate | None:
        groups = sorted(
            family.groups,
            key=lambda item: (-len(item.sessions), item.sequence),
        )
        backbone = _common_backbone(groups)
        if len(backbone) < 2:
            return None
        sessions = sorted(
            {
                session.session_id: session
                for group in groups
                for session in group.sessions
            }.values(),
            key=lambda item: item.session_id,
        )
        aligned: dict[str, tuple[_EventRecord, ...]] = {}
        full_records: dict[str, tuple[_EventRecord, ...]] = {}
        full_signatures: dict[str, tuple[str, ...]] = {}
        alignment_indices: dict[str, dict[int, int]] = {}
        for session in sessions:
            records = _workflow_records(session)[: self.max_structural_steps]
            signatures = tuple(_structural_signature(record) for record in records)
            mapping, _ = _align_signatures(backbone, signatures)
            if len(mapping) != len(backbone):
                return None
            aligned[session.session_id] = tuple(
                records[mapping[index]] for index in range(len(backbone))
            )
            full_records[session.session_id] = records
            full_signatures[session.session_id] = signatures
            alignment_indices[session.session_id] = mapping

        occurrences: dict[str, int] = defaultdict(int)
        step_keys: list[str] = []
        for signature in backbone:
            occurrences[signature] += 1
            step_keys.append(_step_key(signature, occurrences[signature]))

        used_parameter_names: set[str] = set()
        parameters: list[CandidateParameter] = []
        differences: list[dict[str, Any]] = []
        common_steps: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        for step_index, signature in enumerate(backbone):
            records = [aligned[session.session_id][step_index] for session in sessions]
            refs = tuple(sorted({record.ref for record in records}))
            flattened = {
                session.session_id: _flatten_json(
                    aligned[session.session_id][step_index].params,
                    limit=_PARAMETER_EVIDENCE_LIMIT,
                )
                for session in sessions
            }
            paths = sorted(
                {
                    path
                    for values in flattened.values()
                    for path in values
                    if path not in _IGNORED_PARAMETER_PATHS
                    and path.split(".", 1)[0] not in _IGNORED_PARAMETER_PATHS
                }
            )
            arguments: dict[str, Any] = {}
            for path in paths:
                values_by_session = {
                    session.session_id: flattened[session.session_id][path]
                    for session in sessions
                    if path in flattened[session.session_id]
                }
                rendered_values = {
                    session_id: _stable_json(value)
                    for session_id, value in values_by_session.items()
                }
                if (
                    len(values_by_session) == len(sessions)
                    and len(set(rendered_values.values())) == 1
                ):
                    _set_argument(arguments, path, next(iter(values_by_session.values())))
                    continue
                difference: dict[str, Any] = {
                    "step_key": step_keys[step_index],
                    "parameter": path,
                    "values_by_session": _json_copy(values_by_session),
                }
                if len(values_by_session) != len(sessions):
                    difference["missing_by_session"] = [
                        session.session_id
                        for session in sessions
                        if session.session_id not in values_by_session
                    ]
                raw_values = list(values_by_session.values())
                value_type = (
                    _infer_value_type(path, raw_values)
                    if len(values_by_session) >= self.min_support
                    else None
                )
                if value_type is None:
                    difference["classification"] = "type_conflict_or_sparse"
                    differences.append(difference)
                    continue
                samples = _unique_samples(raw_values)
                counts = Counter(_stable_json(value) for value in raw_values)
                default_key = sorted(counts, key=lambda key: (-counts[key], key))[0]
                default = next(
                    value for value in samples if _stable_json(value) == default_key
                )
                name = _parameter_name(path, used_parameter_names, step_index + 1)
                source_refs = tuple(
                    sorted(
                        aligned[session.session_id][step_index].ref
                        for session in sessions
                        if session.session_id in values_by_session
                    )
                )
                enum_values = samples if value_type == "enum" else ()
                parameter = CandidateParameter(
                    name=name,
                    value_type=value_type,
                    required=len(values_by_session) == len(sessions),
                    default=_json_copy(default),
                    samples=samples,
                    description=(
                        f"Observed variation in {path}; confirm units, bounds, and "
                        "selection semantics before simulation."
                    ),
                    step_key=step_keys[step_index],
                    parameter=path,
                    source_event_refs=source_refs,
                    enum_values=enum_values,
                )
                parameters.append(parameter)
                difference["classification"] = "parameter"
                difference["recipe_parameter"] = name
                differences.append(difference)

            highest_risk = max(
                (_record_risk(record) for record in records),
                key=lambda item: _RISK_RANK[item],
            )
            if highest_risk == "safe":
                highest_risk = "review"
            common_steps.append(
                {
                    "step_key": step_keys[step_index],
                    "order": step_index + 1,
                    "action": records[0].action,
                    "structural_signature": signature,
                    "support": {
                        "matched_sessions": len(sessions),
                        "total_sessions": len(sessions),
                        "ratio": 1.0,
                    },
                    "arguments": arguments,
                    "risk": highest_risk,
                    "source_event_refs": [item.to_dict() for item in refs],
                }
            )
            evidence.extend(
                {
                    "step_key": step_keys[step_index],
                    "session_id": session.session_id,
                    "event_ref": aligned[session.session_id][step_index].ref.to_dict(),
                    "structural_signature": signature,
                }
                for session in sessions
            )

        branches = self._branches(
            sessions,
            backbone,
            full_records,
            full_signatures,
            alignment_indices,
        )
        differences.extend(
            {
                "classification": item["classification"],
                "branch_id": item["branch_id"],
                "source_session_ids": item["source_session_ids"],
                "steps": item["steps"],
            }
            for item in branches
        )
        source_session_ids = tuple(session.session_id for session in sessions)
        support = {
            "matched_sessions": len(sessions),
            "total_sessions": len(product_sessions),
            "ratio": len(sessions) / len(product_sessions),
        }
        target_versions = tuple(
            sorted(
                {
                    record.target_version
                    for session in sessions
                    for record in session._records
                    if record.target_version
                }
            )
        )
        candidate_identity = {
            "product": product,
            "session_ids": source_session_ids,
            "backbone": backbone,
        }
        return RecipeCandidate(
            candidate_id=f"candidate:{product}:" + _stable_hash(candidate_identity)[:24],
            product=product,
            source_session_ids=source_session_ids,
            support=support,
            common_steps=tuple(common_steps),
            branches=tuple(branches),
            parameters=tuple(sorted(parameters, key=lambda item: item.name)),
            differences=tuple(
                sorted(
                    differences,
                    key=lambda item: (
                        str(item.get("step_key", "")),
                        str(item.get("parameter", "")),
                        str(item.get("branch_id", "")),
                    ),
                )
            ),
            evidence=tuple(
                sorted(
                    evidence,
                    key=lambda item: (
                        item["step_key"],
                        item["session_id"],
                        item["event_ref"]["event_session_id"],
                        item["event_ref"]["seq"],
                    ),
                )
            ),
            target_versions=target_versions,
            created_at=max(session.ended_at for session in sessions),
        )

    @staticmethod
    def _branches(
        sessions: Sequence[Session],
        backbone: Sequence[str],
        full_records: Mapping[str, tuple[_EventRecord, ...]],
        full_signatures: Mapping[str, tuple[str, ...]],
        alignments: Mapping[str, Mapping[int, int]],
    ) -> list[dict[str, Any]]:
        occurrences: dict[tuple[int, str, str, bool], dict[str, Any]] = {}
        backbone_set = set(backbone)
        for session in sessions:
            session_id = session.session_id
            aligned_indices = set(alignments[session_id].values())
            for index, (record, signature) in enumerate(
                zip(full_records[session_id], full_signatures[session_id])
            ):
                if index in aligned_indices:
                    continue
                anchor = sum(
                    aligned_index < index
                    for aligned_index in alignments[session_id].values()
                )
                key = (anchor, signature, record.action, False)
                item = occurrences.setdefault(
                    key,
                    {
                        "anchor_after_order": anchor,
                        "signature": signature,
                        "action": record.action,
                        "noise": False,
                        "sessions": set(),
                        "refs": set(),
                    },
                )
                item["sessions"].add(session_id)
                item["refs"].add(record.ref)

            significant_refs = {record.ref for record in full_records[session_id]}
            for record in session._records:
                if record.ref in significant_refs or not _is_noise(record):
                    continue
                key = (0, _structural_signature(record), record.action, True)
                item = occurrences.setdefault(
                    key,
                    {
                        "anchor_after_order": 0,
                        "signature": key[1],
                        "action": record.action,
                        "noise": True,
                        "sessions": set(),
                        "refs": set(),
                    },
                )
                item["sessions"].add(session_id)
                item["refs"].add(record.ref)

        branches: list[dict[str, Any]] = []
        for key in sorted(occurrences):
            item = occurrences[key]
            source_session_ids = sorted(item["sessions"])
            if item["signature"] in backbone_set:
                classification = "rework"
            elif item["noise"] or len(source_session_ids) == 1:
                classification = "exploration"
            else:
                classification = "branch"
            branch_identity = {
                "classification": classification,
                "anchor": item["anchor_after_order"],
                "signature": item["signature"],
                "sessions": source_session_ids,
            }
            branches.append(
                {
                    "branch_id": "branch:" + _stable_hash(branch_identity)[:20],
                    "classification": classification,
                    "condition": None,
                    "anchor_after_order": item["anchor_after_order"],
                    "support": {
                        "matched_sessions": len(source_session_ids),
                        "total_sessions": len(sessions),
                        "ratio": len(source_session_ids) / len(sessions),
                    },
                    "source_session_ids": source_session_ids,
                    "source_event_refs": [
                        ref.to_dict() for ref in sorted(item["refs"])
                    ],
                    "steps": [
                        {
                            "action": item["action"],
                            "structural_signature": item["signature"],
                        }
                    ],
                }
            )
        classification_order = {"branch": 0, "rework": 1, "exploration": 2}
        branches.sort(
            key=lambda item: (
                classification_order[item["classification"]],
                item["anchor_after_order"],
                item["branch_id"],
            )
        )
        return branches
