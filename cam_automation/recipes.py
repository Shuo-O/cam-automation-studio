from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from .product_catalog import PRODUCT_ACTION_NAMESPACES, PRODUCT_KEYS
from .sessions import EventRef

__all__ = [
    "DiffReport",
    "PreviewAdapter",
    "PreviewRequest",
    "PreviewTarget",
    "Recipe",
    "RecipeParameter",
    "RecipeService",
    "RecipeStep",
    "RecipeVersion",
    "REQUIRED_PRODUCTION_GATES",
    "canonical_json",
    "canonicalize",
    "compute_recipe_hash",
    "recipe_semantics",
]


SCHEMA_VERSION = 1
REQUIRED_PRODUCTION_GATES = (
    "recipe_review",
    "target_version_validation",
    "cam_simulation",
    "collision_check",
    "shop_approval",
)
_PARAMETER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_STATUSES = {
    "draft",
    "review_required",
    "approved_for_simulation",
    "rejected",
    "retired",
}
_VALUE_TYPES = {
    "string",
    "integer",
    "number",
    "boolean",
    "enum",
    "path",
    "object_selector",
}
_STEP_RISKS = {"safe", "review", "blocked"}
_STEP_REVIEW_STATUSES = {"unreviewed", "needs_review", "accepted", "rejected"}
_DIFF_STATUSES = {"no_change", "changes_detected", "unavailable", "failed"}
_GATE_STATUSES = {"passed", "failed", "required", "not_run", "unavailable"}
_MACHINE_OUTPUT_ACTION_PARTS = (
    "gcode",
    "machine_code",
    "machine.output",
    "nc_program",
    "ncprogram",
    "postprocess",
)
_MACHINE_OUTPUT_KEYS = {
    "gcode",
    "machinecode",
    "machinereadync",
    "machinereadyncincluded",
    "nccode",
    "ncoutput",
    "ncprogram",
    "postprocess",
    "postprocessor",
}
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def _serialize_string(value: str) -> str:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("RFC 8785 input cannot contain lone Unicode surrogates.")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _serialize_number(value: int | float) -> str:
    if isinstance(value, bool):
        raise TypeError("Boolean values are not JSON numbers.")
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError("RFC 8785 numbers must be exactly representable as IEEE-754.")
        return str(value)
    if not math.isfinite(value):
        raise ValueError("RFC 8785 does not allow NaN or infinity.")
    if value == 0:
        return "0"

    sign = "-" if value < 0 else ""
    absolute = abs(value)
    rendered = repr(absolute).lower()
    if "e" in rendered:
        mantissa, raw_exponent = rendered.split("e", 1)
        exponent = int(raw_exponent)
    else:
        mantissa = rendered
        exponent = 0
    if "." in mantissa:
        whole, fraction = mantissa.split(".", 1)
    else:
        whole, fraction = mantissa, ""
    digits = (whole + fraction).lstrip("0")
    decimal_exponent = exponent - len(fraction)
    if not digits:
        return "0"
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]
        decimal_exponent += 1
    decimal_position = len(digits) + decimal_exponent

    if 1e-6 <= absolute < 1e21:
        if decimal_position <= 0:
            body = "0." + ("0" * -decimal_position) + digits
        elif decimal_position >= len(digits):
            body = digits + ("0" * (decimal_position - len(digits)))
        else:
            body = digits[:decimal_position] + "." + digits[decimal_position:]
        return sign + body

    scientific_exponent = decimal_position - 1
    coefficient = digits[0]
    if len(digits) > 1:
        coefficient += "." + digits[1:]
    exponent_sign = "+" if scientific_exponent >= 0 else ""
    return f"{sign}{coefficient}e{exponent_sign}{scientific_exponent}"


def canonical_json(value: Any) -> str:
    """Serialize JSON data using RFC 8785 JSON Canonicalization Scheme."""

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _serialize_number(value)
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("RFC 8785 object keys must be strings.")
        parts = [
            _serialize_string(key) + ":" + canonical_json(value[key])
            for key in sorted(value, key=_utf16_sort_key)
        ]
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def canonicalize(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _event_refs(values: Iterable[EventRef | Mapping[str, Any]]) -> tuple[EventRef, ...]:
    return tuple(sorted({EventRef.from_value(item) for item in values}))


@dataclass(frozen=True)
class RecipeParameter:
    name: str
    value_type: str
    required: bool
    default: Any
    samples: tuple[Any, ...]
    description: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    enum_values: tuple[Any, ...] = ()
    source_event_refs: tuple[EventRef, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name,
            "value_type": self.value_type,
            "required": self.required,
            "default": _json_copy(self.default),
            "samples": _json_copy(list(self.samples)),
            "description": self.description,
        }
        if self.constraints:
            value["constraints"] = _json_copy(dict(self.constraints))
        if self.enum_values:
            value["enum_values"] = _json_copy(list(self.enum_values))
        if self.source_event_refs:
            value["source_event_refs"] = [
                item.to_dict() for item in self.source_event_refs
            ]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecipeParameter:
        return cls(
            name=str(value["name"]),
            value_type=str(value["value_type"]),
            required=value["required"],
            default=_json_copy(value.get("default")),
            samples=tuple(_json_copy(value.get("samples", []))),
            description=str(value.get("description", "")),
            constraints=_json_copy(value.get("constraints", {})),
            enum_values=tuple(_json_copy(value.get("enum_values", []))),
            source_event_refs=_event_refs(value.get("source_event_refs", [])),
        )


@dataclass(frozen=True)
class RecipeStep:
    step_id: str
    order: int
    action: str
    enabled: bool
    risk: str
    review_status: str
    arguments: Mapping[str, Any]
    condition: Mapping[str, Any] | None
    source_event_refs: tuple[EventRef, ...]
    notes: str = ""
    adapter_payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "step_id": self.step_id,
            "order": self.order,
            "action": self.action,
            "enabled": self.enabled,
            "risk": self.risk,
            "review_status": self.review_status,
            "arguments": _json_copy(dict(self.arguments)),
            "condition": _json_copy(self.condition),
            "source_event_refs": [item.to_dict() for item in self.source_event_refs],
        }
        if self.notes:
            value["notes"] = self.notes
        if self.adapter_payload:
            value["adapter_payload"] = _json_copy(dict(self.adapter_payload))
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecipeStep:
        raw_condition = value.get("condition")
        if raw_condition is not None and not isinstance(raw_condition, Mapping):
            raise ValueError("Recipe step condition must be an object or null.")
        return cls(
            step_id=str(value["step_id"]),
            order=int(value["order"]),
            action=str(value["action"]),
            enabled=value["enabled"],
            risk=str(value["risk"]),
            review_status=str(value["review_status"]),
            arguments=_json_copy(value.get("arguments", {})),
            condition=_json_copy(raw_condition),
            source_event_refs=_event_refs(value.get("source_event_refs", [])),
            notes=str(value.get("notes", "")),
            adapter_payload=_json_copy(value.get("adapter_payload", {})),
        )


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    recipe_hash: str
    name: str
    product: str
    status: str
    target_versions: tuple[str, ...]
    source_session_ids: tuple[str, ...]
    support: Mapping[str, Any]
    parameters: tuple[RecipeParameter, ...]
    steps: tuple[RecipeStep, ...]
    required_gates: tuple[str, ...]
    created_at: str
    updated_at: str
    description: str = ""
    project_conditions: Mapping[str, Any] = field(default_factory=dict)
    branches: tuple[Mapping[str, Any], ...] = ()
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": self.schema_version,
            "recipe_id": self.recipe_id,
            "recipe_hash": self.recipe_hash,
            "name": self.name,
            "product": self.product,
            "status": self.status,
            "target_versions": list(self.target_versions),
            "source_session_ids": list(self.source_session_ids),
            "support": _json_copy(dict(self.support)),
            "parameters": [item.to_dict() for item in self.parameters],
            "steps": [item.to_dict() for item in self.steps],
            "required_gates": list(self.required_gates),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.description:
            value["description"] = self.description
        if self.project_conditions:
            value["project_conditions"] = _json_copy(dict(self.project_conditions))
        if self.branches:
            value["branches"] = _json_copy(list(self.branches))
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Recipe:
        return cls(
            schema_version=int(value.get("schema_version", SCHEMA_VERSION)),
            recipe_id=str(value.get("recipe_id", "")),
            recipe_hash=str(value.get("recipe_hash", "")),
            name=str(value.get("name", "")),
            product=str(value.get("product", "")).lower(),
            status=str(value.get("status", "draft")),
            target_versions=tuple(str(item) for item in value.get("target_versions", [])),
            source_session_ids=tuple(
                str(item) for item in value.get("source_session_ids", [])
            ),
            support=_json_copy(value.get("support", {})),
            parameters=tuple(
                RecipeParameter.from_dict(item) for item in value.get("parameters", [])
            ),
            steps=tuple(RecipeStep.from_dict(item) for item in value.get("steps", [])),
            required_gates=tuple(str(item) for item in value.get("required_gates", [])),
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            description=str(value.get("description", "")),
            project_conditions=_json_copy(value.get("project_conditions", {})),
            branches=tuple(_json_copy(value.get("branches", []))),
        )


def recipe_semantics(recipe: Recipe | Mapping[str, Any]) -> dict[str, Any]:
    current = recipe if isinstance(recipe, Recipe) else Recipe.from_dict(recipe)
    parameter_semantics = []
    for parameter in sorted(current.parameters, key=lambda item: _utf16_sort_key(item.name)):
        item: dict[str, Any] = {
            "name": parameter.name,
            "value_type": parameter.value_type,
            "required": parameter.required,
            "default": _json_copy(parameter.default),
            "constraints": _json_copy(dict(parameter.constraints)),
        }
        if parameter.value_type == "enum":
            item["enum_values"] = sorted(
                (_json_copy(value) for value in parameter.enum_values),
                key=canonical_json,
            )
        parameter_semantics.append(item)

    step_semantics = []
    for step in sorted(current.steps, key=lambda item: (item.order, item.step_id)):
        item = {
            "step_id": step.step_id,
            "order": step.order,
            "action": step.action,
            "enabled": step.enabled,
            "risk": step.risk,
            "arguments": _json_copy(dict(step.arguments)),
            "condition": _json_copy(step.condition),
        }
        if step.adapter_payload:
            item["adapter_payload"] = _json_copy(dict(step.adapter_payload))
        step_semantics.append(item)

    return {
        "schema_version": current.schema_version,
        "product": current.product,
        "target_versions": sorted(set(current.target_versions), key=_utf16_sort_key),
        "project_conditions": _json_copy(dict(current.project_conditions)),
        "parameters": parameter_semantics,
        "steps": step_semantics,
        "required_gates": sorted(set(current.required_gates), key=_utf16_sort_key),
    }


def compute_recipe_hash(recipe: Recipe | Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonicalize(recipe_semantics(recipe))).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class RecipeVersion:
    version_id: str
    recipe_id: str
    version: int
    recipe_hash: str
    content_address: str
    saved_at: str
    recipe: Recipe
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "version_id": self.version_id,
            "recipe_id": self.recipe_id,
            "version": self.version,
            "recipe_hash": self.recipe_hash,
            "content_address": self.content_address,
            "saved_at": self.saved_at,
            "recipe": self.recipe.to_dict(),
        }


@dataclass(frozen=True)
class PreviewTarget:
    target_version: str
    target_instance_id: str
    project_id: str
    reviewer: str


@dataclass(frozen=True)
class PreviewRequest:
    task_id: str
    recipe: Recipe
    parameters: Mapping[str, Any]
    product: str
    target_version: str
    target_instance_id: str
    project_id: str
    reviewer: str
    execution_mode: str = "dry_run"
    schema_version: int = SCHEMA_VERSION

    @property
    def recipe_hash(self) -> str:
        return self.recipe.recipe_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_type": "recipe_preview",
            "execution_mode": self.execution_mode,
            "product": self.product,
            "target_version": self.target_version,
            "target_instance_id": self.target_instance_id,
            "project_id": self.project_id,
            "recipe_hash": self.recipe.recipe_hash,
            "parameters": _json_copy(dict(self.parameters)),
            "review": {
                "status": "accepted",
                "reviewer": self.reviewer,
                "scope": "dry_run_only",
            },
            "recipe": self.recipe.to_dict(),
        }


@dataclass(frozen=True)
class DiffReport:
    diff_id: str
    task_id: str
    product: str
    target_instance_id: str
    project_id: str
    recipe_hash: str
    status: str
    before_snapshot: Mapping[str, Any] | None
    after_snapshot: Mapping[str, Any] | None
    changes: tuple[Mapping[str, Any], ...]
    gate_results: tuple[Mapping[str, Any], ...]
    summary: str
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "diff_id": self.diff_id,
            "task_id": self.task_id,
            "product": self.product,
            "target_instance_id": self.target_instance_id,
            "project_id": self.project_id,
            "recipe_hash": self.recipe_hash,
            "status": self.status,
            "before_snapshot": _json_copy(self.before_snapshot),
            "after_snapshot": _json_copy(self.after_snapshot),
            "changes": _json_copy(list(self.changes)),
            "gate_results": _json_copy(list(self.gate_results)),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DiffReport:
        return cls(
            schema_version=int(value.get("schema_version", SCHEMA_VERSION)),
            diff_id=str(value.get("diff_id", "")),
            task_id=str(value.get("task_id", "")),
            product=str(value.get("product", "")),
            target_instance_id=str(value.get("target_instance_id", "")),
            project_id=str(value.get("project_id", "")),
            recipe_hash=str(value.get("recipe_hash", "")),
            status=str(value.get("status", "unavailable")),
            before_snapshot=_json_copy(value.get("before_snapshot")),
            after_snapshot=_json_copy(value.get("after_snapshot")),
            changes=tuple(_json_copy(value.get("changes", []))),
            gate_results=tuple(_json_copy(value.get("gate_results", []))),
            summary=str(value.get("summary", "")),
        )


@runtime_checkable
class PreviewAdapter(Protocol):
    def preview(self, request: PreviewRequest) -> DiffReport | Mapping[str, Any]:
        ...


def _validate_parameter_value(parameter: RecipeParameter, value: Any) -> None:
    if value is None:
        return
    value_type = parameter.value_type
    valid = True
    if value_type in {"string", "path"}:
        valid = isinstance(value, str)
    elif value_type == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif value_type == "number":
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    elif value_type == "boolean":
        valid = isinstance(value, bool)
    elif value_type == "enum":
        valid = any(canonical_json(value) == canonical_json(item) for item in parameter.enum_values)
    elif value_type == "object_selector":
        valid = isinstance(value, Mapping)
    if not valid:
        raise ValueError(
            f"Parameter {parameter.name} must match value_type {parameter.value_type}."
        )

    constraints = parameter.constraints
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in constraints and value < constraints["minimum"]:
            raise ValueError(f"Parameter {parameter.name} is below its minimum.")
        if "maximum" in constraints and value > constraints["maximum"]:
            raise ValueError(f"Parameter {parameter.name} is above its maximum.")
    if isinstance(value, str) and constraints.get("pattern"):
        if re.fullmatch(str(constraints["pattern"]), value) is None:
            raise ValueError(f"Parameter {parameter.name} does not match its pattern.")


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _contains_machine_output(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in _MACHINE_OUTPUT_KEYS and item not in (None, False, "", [], {}):
                return True
            if _contains_machine_output(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_machine_output(item) for item in value)
    return False


def _dangerous_action(action: str) -> bool:
    normalized = action.lower().replace("-", "_")
    return any(part in normalized for part in _MACHINE_OUTPUT_ACTION_PARTS)


class RecipeService:
    def __init__(
        self,
        adapters: Mapping[str, PreviewAdapter] | PreviewAdapter | None = None,
        *,
        adapter: PreviewAdapter | None = None,
        product: str | None = None,
        target_version: str = "",
        target_instance_id: str = "",
        project_id: str = "",
        reviewer: str = "",
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._adapters: dict[str, PreviewAdapter] = {}
        self._default_adapter: PreviewAdapter | None = None
        if adapters is not None and not isinstance(adapters, Mapping):
            if adapter is not None:
                raise ValueError("Specify only one positional or keyword adapter.")
            adapter = adapters
            adapters = None
        for adapter_product, current in (adapters or {}).items():
            self.register_adapter(str(adapter_product), current)
        if adapter is not None:
            adapter_product = product or str(getattr(adapter, "product", "") or "")
            if adapter_product:
                self.register_adapter(adapter_product, adapter)
            else:
                self._default_adapter = adapter
        self._target_version = target_version
        self._target_instance_id = target_instance_id
        self._project_id = project_id
        self._reviewer = reviewer
        self._clock = clock
        self._versions_by_recipe: dict[str, list[RecipeVersion]] = defaultdict(list)
        self._versions_by_hash: dict[str, list[RecipeVersion]] = defaultdict(list)
        self._fingerprints: dict[tuple[str, str], RecipeVersion] = {}
        self._content: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def register_adapter(self, product: str, adapter: PreviewAdapter) -> None:
        normalized = product.strip().lower()
        if normalized not in PRODUCT_KEYS:
            raise ValueError(f"Unsupported adapter product: {product!r}")
        if not callable(getattr(adapter, "preview", None)):
            raise TypeError("Preview adapters must implement preview(request).")
        self._adapters[normalized] = adapter

    def save(self, recipe: Recipe | Mapping[str, Any] | Any) -> RecipeVersion:
        current = self._coerce_recipe(recipe)
        now = self._clock()
        current = self._normalize_recipe(current, now)
        self._validate_recipe(current)
        recipe_hash = compute_recipe_hash(current)
        recipe_id = current.recipe_id or f"recipe:{current.product}:{recipe_hash[7:31]}"
        current = replace(current, recipe_id=recipe_id, recipe_hash=recipe_hash)
        fingerprint = hashlib.sha256(canonicalize(current.to_dict())).hexdigest()

        with self._lock:
            duplicate = self._fingerprints.get((recipe_id, fingerprint))
            if duplicate is not None:
                return duplicate
            version_number = len(self._versions_by_recipe[recipe_id]) + 1
            version = RecipeVersion(
                version_id=f"{recipe_id}:v{version_number}:{recipe_hash[7:19]}",
                recipe_id=recipe_id,
                version=version_number,
                recipe_hash=recipe_hash,
                content_address=recipe_hash,
                saved_at=now,
                recipe=current,
            )
            self._versions_by_recipe[recipe_id].append(version)
            self._versions_by_hash[recipe_hash].append(version)
            self._fingerprints[(recipe_id, fingerprint)] = version
            self._content.setdefault(recipe_hash, canonicalize(recipe_semantics(current)))
            return version

    def get(self, recipe_hash: str) -> RecipeVersion:
        with self._lock:
            versions = self._versions_by_hash.get(recipe_hash)
            if not versions:
                raise KeyError(f"Unknown recipe hash: {recipe_hash}")
            return versions[-1]

    def versions(self, recipe_id: str) -> tuple[RecipeVersion, ...]:
        with self._lock:
            return tuple(self._versions_by_recipe.get(recipe_id, ()))

    def preview(
        self,
        recipe_hash: str,
        parameters: Mapping[str, Any],
        *,
        target_version: str | None = None,
        target_instance_id: str | None = None,
        project_id: str | None = None,
        reviewer: str | None = None,
    ) -> DiffReport:
        if not _HASH.fullmatch(recipe_hash):
            raise ValueError("recipe_hash must be a sha256 content address.")
        version = self.get(recipe_hash)
        recipe = version.recipe
        adapter = self._adapters.get(recipe.product) or self._default_adapter
        if adapter is None:
            raise ValueError(f"No preview adapter is registered for {recipe.product}.")

        resolved_target_version = (
            target_version
            or self._target_version
            or str(getattr(adapter, "target_version", "") or "")
            or (recipe.target_versions[0] if len(recipe.target_versions) == 1 else "")
        ).strip()
        resolved_instance = (
            target_instance_id
            or self._target_instance_id
            or str(getattr(adapter, "target_instance_id", "") or "")
        ).strip()
        resolved_project = (
            project_id
            or self._project_id
            or str(getattr(adapter, "project_id", "") or "")
        ).strip()
        resolved_reviewer = (
            reviewer
            or self._reviewer
            or str(getattr(adapter, "reviewer", "") or "")
        ).strip()
        missing = [
            name
            for name, value in (
                ("target_version", resolved_target_version),
                ("target_instance_id", resolved_instance),
                ("project_id", resolved_project),
                ("reviewer", resolved_reviewer),
            )
            if not value
        ]
        if missing:
            raise ValueError("Preview requires " + ", ".join(missing) + ".")
        if recipe.target_versions and resolved_target_version not in recipe.target_versions:
            raise ValueError("Target CAM version is outside the recipe's reviewed versions.")
        if recipe.status in {"draft", "rejected", "retired"}:
            raise ValueError(f"Recipe status {recipe.status!r} is not previewable.")

        resolved_parameters = self._resolve_parameters(recipe, parameters)
        enabled_steps: list[RecipeStep] = []
        for step in recipe.steps:
            if not step.enabled:
                continue
            if step.risk == "blocked":
                raise ValueError(f"Blocked step {step.step_id} cannot enter preview.")
            if step.review_status == "rejected":
                raise ValueError(f"Rejected step {step.step_id} cannot enter preview.")
            if _dangerous_action(step.action):
                raise ValueError("Machine-ready NC and postprocessing actions are blocked.")
            if _contains_machine_output(step.arguments) or _contains_machine_output(
                step.adapter_payload
            ):
                raise ValueError("Machine-ready NC payloads are blocked.")
            enabled_steps.append(step)
        if _contains_machine_output(resolved_parameters):
            raise ValueError("Machine-ready NC parameter payloads are blocked.")

        preview_recipe = replace(recipe, steps=tuple(enabled_steps))
        task_semantics = {
            "recipe_hash": recipe_hash,
            "parameters": resolved_parameters,
            "target_version": resolved_target_version,
            "target_instance_id": resolved_instance,
            "project_id": resolved_project,
            "execution_mode": "dry_run",
        }
        task_id = "task:preview:" + hashlib.sha256(canonicalize(task_semantics)).hexdigest()[:24]
        request = PreviewRequest(
            task_id=task_id,
            recipe=preview_recipe,
            parameters=resolved_parameters,
            product=recipe.product,
            target_version=resolved_target_version,
            target_instance_id=resolved_instance,
            project_id=resolved_project,
            reviewer=resolved_reviewer,
        )
        response = adapter.preview(request)
        return self._normalize_diff_report(response, request, version.recipe)

    @staticmethod
    def _coerce_recipe(recipe: Recipe | Mapping[str, Any] | Any) -> Recipe:
        if isinstance(recipe, Recipe):
            return recipe
        if isinstance(recipe, Mapping):
            return Recipe.from_dict(recipe)
        to_recipe = getattr(recipe, "to_recipe", None)
        if callable(to_recipe):
            converted = to_recipe()
            if isinstance(converted, Recipe):
                return converted
        raise TypeError("save() requires a Recipe, recipe mapping, or RecipeCandidate.")

    @staticmethod
    def _normalize_recipe(recipe: Recipe, now: str) -> Recipe:
        return replace(
            recipe,
            product=recipe.product.strip().lower(),
            target_versions=tuple(
                sorted({item.strip() for item in recipe.target_versions if item.strip()},
                       key=_utf16_sort_key)
            ),
            source_session_ids=tuple(sorted(set(recipe.source_session_ids))),
            parameters=tuple(sorted(recipe.parameters, key=lambda item: item.name)),
            steps=tuple(sorted(recipe.steps, key=lambda item: (item.order, item.step_id))),
            required_gates=tuple(
                sorted(set(recipe.required_gates), key=_utf16_sort_key)
            ),
            created_at=recipe.created_at or now,
            updated_at=recipe.updated_at or now,
        )

    @staticmethod
    def _validate_recipe(recipe: Recipe) -> None:
        if recipe.schema_version != SCHEMA_VERSION:
            raise ValueError("Only Recipe schema_version 1 is supported.")
        if recipe.product not in PRODUCT_KEYS:
            raise ValueError(f"Unsupported recipe product: {recipe.product!r}")
        if recipe.status not in _STATUSES:
            raise ValueError(f"Invalid recipe status: {recipe.status!r}")
        if not recipe.name.strip():
            raise ValueError("Recipe name is required.")
        if not set(REQUIRED_PRODUCTION_GATES).issubset(recipe.required_gates):
            raise ValueError("Recipe is missing mandatory review and production gates.")
        if len({item.name for item in recipe.parameters}) != len(recipe.parameters):
            raise ValueError("Recipe parameter names must be unique.")
        for parameter in recipe.parameters:
            if not _PARAMETER_NAME.fullmatch(parameter.name):
                raise ValueError(f"Invalid parameter name: {parameter.name!r}")
            if parameter.value_type not in _VALUE_TYPES:
                raise ValueError(f"Invalid value_type for {parameter.name}.")
            if type(parameter.required) is not bool:
                raise ValueError(f"Parameter {parameter.name} required must be boolean.")
            if parameter.value_type == "enum" and not parameter.enum_values:
                raise ValueError(f"Enum parameter {parameter.name} has no enum_values.")
            _validate_parameter_value(parameter, parameter.default)
            for sample in parameter.samples:
                _validate_parameter_value(parameter, sample)

        orders = [step.order for step in recipe.steps]
        if orders != list(range(1, len(recipe.steps) + 1)):
            raise ValueError("Recipe step order must be contiguous and start at 1.")
        if len({step.step_id for step in recipe.steps}) != len(recipe.steps):
            raise ValueError("Recipe step IDs must be unique.")
        for step in recipe.steps:
            if type(step.enabled) is not bool:
                raise ValueError(f"Step {step.step_id} enabled must be boolean.")
            allowed_prefixes = ("cam.",) + tuple(
                f"{namespace}." for namespace in sorted(PRODUCT_ACTION_NAMESPACES)
            )
            if not step.action.startswith(allowed_prefixes):
                raise ValueError(f"Step {step.step_id} uses an invalid action namespace.")
            if step.risk not in _STEP_RISKS:
                raise ValueError(f"Step {step.step_id} has an invalid risk.")
            if step.review_status not in _STEP_REVIEW_STATUSES:
                raise ValueError(f"Step {step.step_id} has an invalid review_status.")
            if not isinstance(step.arguments, Mapping):
                raise ValueError(f"Step {step.step_id} arguments must be an object.")
            if step.condition is not None and not isinstance(step.condition, Mapping):
                raise ValueError(f"Step {step.step_id} condition must be an object or null.")

    @staticmethod
    def _resolve_parameters(
        recipe: Recipe, overrides: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(overrides, Mapping):
            raise TypeError("Preview parameters must be an object.")
        definitions = {item.name: item for item in recipe.parameters}
        unknown = sorted(set(overrides) - set(definitions))
        if unknown:
            raise ValueError("Unknown recipe parameters: " + ", ".join(unknown))
        resolved: dict[str, Any] = {}
        for name in sorted(definitions):
            parameter = definitions[name]
            value = overrides[name] if name in overrides else parameter.default
            if value is None and parameter.required:
                raise ValueError(f"Required parameter {name} has no value.")
            _validate_parameter_value(parameter, value)
            resolved[name] = _json_copy(value)
        return resolved

    @staticmethod
    def _normalize_diff_report(
        response: DiffReport | Mapping[str, Any],
        request: PreviewRequest,
        stored_recipe: Recipe,
    ) -> DiffReport:
        if isinstance(response, DiffReport):
            raw = response.to_dict()
        elif isinstance(response, Mapping):
            raw = _json_copy(dict(response))
        else:
            raise TypeError("Preview adapters must return DiffReport or a mapping.")
        if _contains_machine_output(raw):
            raise ValueError("Adapter returned machine-ready NC content.")
        execution_mode = str(raw.get("execution_mode", "dry_run")).lower().replace("-", "_")
        if execution_mode != "dry_run":
            raise ValueError("Adapter preview responses must remain dry-run.")

        for field_name, expected in (
            ("task_id", request.task_id),
            ("product", request.product),
            ("target_instance_id", request.target_instance_id),
            ("project_id", request.project_id),
            ("recipe_hash", request.recipe_hash),
        ):
            actual = raw.get(field_name)
            if actual not in (None, "", expected):
                raise ValueError(f"Adapter DiffReport {field_name} does not match preview.")

        raw_changes = raw.get("changes", [])
        if not isinstance(raw_changes, list) or any(
            not isinstance(item, Mapping) for item in raw_changes
        ):
            raise ValueError("DiffReport changes must be an array of objects.")
        changes = tuple(_json_copy(raw_changes))
        status = str(
            raw.get("status") or ("changes_detected" if changes else "no_change")
        )
        if status not in _DIFF_STATUSES:
            raise ValueError(f"Invalid DiffReport status: {status!r}")

        gates: dict[str, dict[str, Any]] = {}
        for item in raw.get("gate_results", []):
            if not isinstance(item, Mapping):
                raise ValueError("DiffReport gate_results must contain objects.")
            gate = str(item.get("gate", "")).strip()
            gate_status = str(item.get("status", "")).strip()
            if not gate or gate_status not in _GATE_STATUSES:
                raise ValueError("DiffReport contains an invalid gate result.")
            evidence = list(item.get("evidence_refs", []))
            if (
                gate_status == "passed"
                and gate in {"cam_simulation", "collision_check", "shop_approval"}
                and not evidence
            ):
                gate_status = "required"
            gates[gate] = {
                "gate": gate,
                "status": gate_status,
                "evidence_refs": _json_copy(evidence),
            }

        gates["recipe_review"] = {
            "gate": "recipe_review",
            "status": "passed",
            "evidence_refs": [request.reviewer],
        }
        if stored_recipe.target_versions:
            gates["target_version_validation"] = {
                "gate": "target_version_validation",
                "status": "passed",
                "evidence_refs": [request.target_version, request.recipe_hash],
            }
        else:
            gates.setdefault(
                "target_version_validation",
                {
                    "gate": "target_version_validation",
                    "status": "required",
                    "evidence_refs": [],
                },
            )
        for gate in stored_recipe.required_gates:
            default_status = "not_run" if gate == "cam_simulation" else "required"
            gates.setdefault(
                gate,
                {"gate": gate, "status": default_status, "evidence_refs": []},
            )
        gate_results = tuple(gates[key] for key in sorted(gates, key=_utf16_sort_key))
        incomplete = [
            item["gate"]
            for item in gate_results
            if item["status"] in {"required", "not_run", "unavailable", "failed"}
        ]
        summary = str(raw.get("summary", "")).strip()
        if incomplete:
            safety_summary = (
                "Simulation, collision checks, and shop approval remain incomplete; "
                "this result is dry-run only."
            )
            summary = f"{summary} {safety_summary}".strip()
        elif not summary:
            summary = "Dry-run preview completed; no production execution was performed."

        diff_identity = {
            "task_id": request.task_id,
            "status": status,
            "changes": changes,
            "gate_results": gate_results,
        }
        diff_id = str(raw.get("diff_id", "")).strip() or (
            "diff:preview:" + hashlib.sha256(canonicalize(diff_identity)).hexdigest()[:24]
        )
        return DiffReport(
            diff_id=diff_id,
            task_id=request.task_id,
            product=request.product,
            target_instance_id=request.target_instance_id,
            project_id=request.project_id,
            recipe_hash=request.recipe_hash,
            status=status,
            before_snapshot=_json_copy(raw.get("before_snapshot")),
            after_snapshot=_json_copy(raw.get("after_snapshot")),
            changes=changes,
            gate_results=gate_results,
            summary=summary,
        )
