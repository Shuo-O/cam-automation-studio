from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import (
    RISK_ORDER,
    CommandEvent,
    ParseResult,
    RecipeParameter,
    RecipeStep,
    Session,
    WorkflowRecipe,
)
from .parser import parse_log
from .profiles import PowerMillProfile


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


def _session_signature(session: Session) -> tuple[str, ...]:
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
    parameters: list[RecipeParameter]
    by_samples: dict[tuple[str, tuple[object, ...]], RecipeParameter]
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
    ) -> RecipeParameter:
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

        parameter = RecipeParameter(
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


def _select_session_group(sessions: list[Session]) -> tuple[list[Session], list[str]]:
    groups: dict[tuple[str, ...], list[Session]] = defaultdict(list)
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
            "Only one structurally matching session was available; literal variation is not inferred."
        )
    return selected, diagnostics


def _build_step(
    index: int,
    events: list[CommandEvent],
    registry: _ParameterRegistry,
    diagnostics: list[str],
) -> RecipeStep:
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
    return RecipeStep(
        step_id=f"step-{index + 1:03d}",
        operation=representative.operation,
        action=representative.action,
        template="".join(template_tokens),
        risk=highest_risk,
        reasons=reasons,
        source_lines=[event.line_number for event in events],
    )


def learn_recipe(parsed: ParseResult, name: str = "powermill-workflow") -> WorkflowRecipe:
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
    return WorkflowRecipe(
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
) -> tuple[ParseResult, WorkflowRecipe]:
    parsed = parse_log(text, profile=profile)
    return parsed, learn_recipe(parsed, name=name)


def learn_file(path: str | Path, *, name: str | None = None) -> tuple[ParseResult, WorkflowRecipe]:
    source = Path(path)
    text = source.read_text(encoding="utf-8-sig")
    return learn_workflow(text, name=name or source.stem)
