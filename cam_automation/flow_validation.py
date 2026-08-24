from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .flow_contracts import (
    CONTRACT_NAMES,
    CapabilityManifest,
    FlowGraph,
    FrozenContract,
    normalize_schema_name,
    schema_directory,
)


ERROR_CODES = (
    "FLOW_SCHEMA_INVALID",
    "FLOW_UNKNOWN_SEMANTICS",
    "FLOW_PRODUCT_MIXED",
    "FLOW_NAMESPACE_INVALID",
    "FLOW_ENTRY_INVALID",
    "FLOW_CROSS_FLOW_EDGE",
    "FLOW_DEPENDENCY_CYCLE",
    "FLOW_CONTROL_CYCLE_UNSUPPORTED",
    "FLOW_SUBFLOW_RECURSION",
    "PORT_DIRECTION_INVALID",
    "PORT_TYPE_MISMATCH",
    "PORT_UNIT_MISMATCH",
    "PORT_CARDINALITY_EXCEEDED",
    "BINDING_ONE_OF_INVALID",
    "SOURCE_DIGEST_MISMATCH",
    "SOURCE_SPAN_INVALID",
    "SOURCE_MAPPING_AMBIGUOUS",
    "SOURCE_OPAQUE_EDIT",
    "SOURCE_SILENT_REWRITE",
    "ASSET_RIGHTS_UNKNOWN",
    "ASSET_BINARY_INSPECTION_FORBIDDEN",
    "CAPABILITY_MISSING",
    "CAPABILITY_VERSION_MISMATCH",
    "CAPABILITY_REVOKED",
    "CAPABILITY_RISK_DOWNGRADE",
    "COMPAT_TARGET_VERSION_UNKNOWN",
    "COMPAT_SELECTOR_UNRESOLVED",
    "COMPAT_CONTEXT_INCOMPLETE",
    "ROUNDTRIP_REPARSE_FAILED",
    "ROUNDTRIP_SEMANTIC_MISMATCH",
    "ROUNDTRIP_OPAQUE_CHANGED",
    "PREVIEW_HASH_MISMATCH",
    "PREVIEW_TARGET_AMBIGUOUS",
    "PREVIEW_PERMISSION_REVOKED",
    "SAFETY_LIVE_EXECUTION_FORBIDDEN",
    "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
    "SAFETY_AI_AUTHORITY_EXCEEDED",
    "RESOURCE_LIMIT_EXCEEDED",
)

_ERROR_CODE_SET = frozenset(ERROR_CODES)
_SEVERITY_ORDER = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
_RISK_ORDER = {"safe": 0, "review": 1, "blocked": 2}
_NODE_NAMESPACE = re.compile(r"^(cam|nx|powermill|flow|opaque)\.[A-Za-z0-9_.-]+$")
_BASE_TYPES = {
    "string",
    "integer",
    "number",
    "boolean",
    "enum",
    "path_ref",
    "cam.length",
    "cam.angle",
    "cam.ratio",
    "cam.coordinate_frame",
    "cam.object_selector",
    "cam.control",
    "cam.dependency",
}
_MACHINE_OUTPUT_KEYS = {
    "clsf",
    "clsf_output",
    "g_code",
    "gcode",
    "machine_control",
    "machine_control_payload",
    "machine_output",
    "machine_ready_nc",
    "nc",
    "nc_code",
    "nc_output",
    "nc_program",
    "postprocess",
    "postprocess_payload",
    "postprocessor",
    "postprocessor_output",
}
_MACHINE_ACTION_PARTS = (
    "clsf",
    "gcode",
    "machine_control",
    "machine.output",
    "nc_program",
    "ncprogram",
    "postprocess",
)
_SAFETY_SCAN_EXCLUDED_FIELDS = {
    "blocker_codes",
    "diagnostic_codes",
    "forbidden_modes",
    "message",
    "prohibited_operations",
    "remediation",
    "summary",
}
_AI_AUTHORITY_KEYS = {
    "ai_authoritative",
    "ai_gate_passed",
    "ai_generated_source",
    "ai_wrote_contract",
    "graph_patch_applied_by_ai",
}


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    max_asset_bytes: int = 10 * 1024 * 1024
    max_nodes: int = 500
    max_edges: int = 800
    max_depth: int = 64
    max_string_bytes: int = 1024 * 1024
    max_diagnostics: int = 5_000


DEFAULT_RESOURCE_LIMITS = ResourceLimits()


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    object_ref: str
    source_mapping_ids: tuple[str, ...] = ()
    remediation: str = ""

    def __post_init__(self) -> None:
        if self.code not in _ERROR_CODE_SET:
            raise ValueError(f"Unknown frozen diagnostic code: {self.code}")
        if self.severity not in _SEVERITY_ORDER:
            raise ValueError(f"Invalid diagnostic severity: {self.severity}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "object_ref": self.object_ref,
            "source_mapping_ids": list(self.source_mapping_ids),
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    schema_name: str
    diagnostics: tuple[Diagnostic, ...]

    @property
    def valid(self) -> bool:
        return not any(item.severity in {"error", "blocker"} for item in self.diagnostics)

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item.code for item in self.diagnostics if item.severity == "blocker"
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "schema_name": self.schema_name,
            "valid": self.valid,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


_MESSAGES = {
    "FLOW_SCHEMA_INVALID": "The object does not conform to the frozen v1 schema.",
    "FLOW_UNKNOWN_SEMANTICS": "Unknown optional data cannot be proven non-semantic for editing.",
    "FLOW_PRODUCT_MIXED": "The graph or manifest mixes product-specific semantics.",
    "FLOW_NAMESPACE_INVALID": "A node or action uses an invalid namespace.",
    "FLOW_ENTRY_INVALID": "The entry flow is missing or is not a main flow.",
    "FLOW_CROSS_FLOW_EDGE": "An edge references a node outside its containing flow.",
    "FLOW_DEPENDENCY_CYCLE": "The dependency graph contains a cycle.",
    "FLOW_CONTROL_CYCLE_UNSUPPORTED": "The control graph contains an unsupported cycle.",
    "FLOW_SUBFLOW_RECURSION": "Subflow calls are directly or indirectly recursive.",
    "PORT_DIRECTION_INVALID": "The edge does not connect compatible port directions and kinds.",
    "PORT_TYPE_MISMATCH": "The connected values use incompatible types.",
    "PORT_UNIT_MISMATCH": "The connected values require an explicit unit conversion.",
    "PORT_CARDINALITY_EXCEEDED": "The target port has more sources than its cardinality permits.",
    "BINDING_ONE_OF_INVALID": "A binding must activate exactly one value source matching its kind.",
    "SOURCE_DIGEST_MISMATCH": (
        "A source mapping digest does not match its immutable asset revision."
    ),
    "SOURCE_SPAN_INVALID": "A source mapping contains an invalid byte, line, or column span.",
    "SOURCE_MAPPING_AMBIGUOUS": "A source mapping is ambiguous and requires review.",
    "SOURCE_OPAQUE_EDIT": "The requested edit crosses opaque source content.",
    "SOURCE_SILENT_REWRITE": "Untouched source content changed during round-trip.",
    "ASSET_RIGHTS_UNKNOWN": "Asset rights are insufficient for sharing, egress, or preview.",
    "ASSET_BINARY_INSPECTION_FORBIDDEN": "Protected binary inspection is permanently forbidden.",
    "CAPABILITY_MISSING": "A locked node capability is unavailable.",
    "CAPABILITY_VERSION_MISMATCH": "A locked capability or node version does not match.",
    "CAPABILITY_REVOKED": "A required capability has been revoked.",
    "CAPABILITY_RISK_DOWNGRADE": "Node risk is below the manifest risk floor.",
    "COMPAT_TARGET_VERSION_UNKNOWN": "A target version has not been explicitly bound.",
    "COMPAT_SELECTOR_UNRESOLVED": "An object selector is unresolved or ambiguous.",
    "COMPAT_CONTEXT_INCOMPLETE": "Required CAM context is incomplete.",
    "ROUNDTRIP_REPARSE_FAILED": "The candidate artifact did not reparse.",
    "ROUNDTRIP_SEMANTIC_MISMATCH": "The reparsed candidate is not semantically equivalent.",
    "ROUNDTRIP_OPAQUE_CHANGED": "Opaque source content changed.",
    "PREVIEW_HASH_MISMATCH": "Preview hashes do not match the reviewed immutable inputs.",
    "PREVIEW_TARGET_AMBIGUOUS": "The fixture target is missing or ambiguous.",
    "PREVIEW_PERMISSION_REVOKED": "A required preview permission has been revoked.",
    "SAFETY_LIVE_EXECUTION_FORBIDDEN": "Live CAM, Journal, or macro execution is forbidden.",
    "SAFETY_MACHINE_OUTPUT_FORBIDDEN": "Machine-ready output and machine control are forbidden.",
    "SAFETY_AI_AUTHORITY_EXCEEDED": "AI output cannot directly change contract or gate authority.",
    "RESOURCE_LIMIT_EXCEEDED": "The input exceeds a frozen resource limit.",
}

_REMEDIATIONS = {
    "FLOW_SCHEMA_INVALID": "Correct the object against its v1 JSON Schema.",
    "FLOW_UNKNOWN_SEMANTICS": "Keep the object read-only or install a compatible contract reader.",
    "FLOW_PRODUCT_MIXED": "Use one product per graph and the matching product namespace.",
    "FLOW_NAMESPACE_INVALID": "Use cam.*, nx.*, powermill.*, flow.*, or opaque.* as allowed.",
    "FLOW_ENTRY_INVALID": "Reference exactly one existing main flow as entry_flow_id.",
    "FLOW_CROSS_FLOW_EDGE": "Route cross-flow data through a non-recursive subflow interface.",
    "FLOW_DEPENDENCY_CYCLE": "Remove the dependency cycle.",
    "FLOW_CONTROL_CYCLE_UNSUPPORTED": (
        "Remove the cycle or use a separately reviewed loop capability."
    ),
    "FLOW_SUBFLOW_RECURSION": "Remove direct and indirect subflow recursion.",
    "PORT_DIRECTION_INVALID": "Connect a supported output port to a supported input port.",
    "PORT_TYPE_MISMATCH": "Use matching types or an explicit typed adapter node.",
    "PORT_UNIT_MISMATCH": "Insert an explicit reviewed unit conversion node.",
    "PORT_CARDINALITY_EXCEEDED": "Remove sources or use a manifest port with cardinality many.",
    "BINDING_ONE_OF_INVALID": "Set only the value source selected by binding kind.",
    "SOURCE_DIGEST_MISMATCH": "Re-import the changed asset or perform a three-way diff.",
    "SOURCE_SPAN_INVALID": "Rebuild the mapping from immutable byte offsets.",
    "SOURCE_MAPPING_AMBIGUOUS": "Review all candidate mappings without automatic selection.",
    "SOURCE_OPAQUE_EDIT": "Keep the opaque span unchanged and visible.",
    "SOURCE_SILENT_REWRITE": "Discard the candidate and preserve untouched bytes.",
    "ASSET_RIGHTS_UNKNOWN": "Record sufficient rights evidence before the requested use.",
    "ASSET_BINARY_INSPECTION_FORBIDDEN": (
        "Use an authorized export, public API, or signed manifest."
    ),
    "CAPABILITY_MISSING": "Install and verify the exact locked capability.",
    "CAPABILITY_VERSION_MISMATCH": "Create and review an explicit migration revision.",
    "CAPABILITY_REVOKED": "Keep the graph read-only until separately re-authorized.",
    "CAPABILITY_RISK_DOWNGRADE": "Raise node risk to at least the manifest floor.",
    "COMPAT_TARGET_VERSION_UNKNOWN": "Select and verify an explicit fixture target version.",
    "COMPAT_SELECTOR_UNRESOLVED": "Resolve exactly one typed selector target in fixture context.",
    "COMPAT_CONTEXT_INCOMPLETE": (
        "Provide explicit unit, MCS, project, stock, tool, and fixture context."
    ),
    "ROUNDTRIP_REPARSE_FAILED": "Discard the candidate and repair the product adapter.",
    "ROUNDTRIP_SEMANTIC_MISMATCH": "Discard the candidate and review the semantic diff.",
    "ROUNDTRIP_OPAQUE_CHANGED": "Discard the candidate and preserve opaque bytes exactly.",
    "PREVIEW_HASH_MISMATCH": "Revalidate and review the exact current hashes.",
    "PREVIEW_TARGET_AMBIGUOUS": (
        "Bind one explicit fixture version, instance, and project snapshot."
    ),
    "PREVIEW_PERMISSION_REVOKED": "Request a new explicit grant; do not resume an old task.",
    "SAFETY_LIVE_EXECUTION_FORBIDDEN": (
        "Use offline inspection or fixture_dry_run with transport none."
    ),
    "SAFETY_MACHINE_OUTPUT_FORBIDDEN": "Remove and discard all machine-output payloads.",
    "SAFETY_AI_AUTHORITY_EXCEEDED": "Treat AI content only as an untrusted proposal.",
    "RESOURCE_LIMIT_EXCEEDED": "Reduce the input without truncating or skipping validation.",
}


def _diagnostic(
    code: str,
    object_ref: str,
    source_mapping_ids: Iterable[str] = (),
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity="blocker",
        message=_MESSAGES[code],
        object_ref=object_ref,
        source_mapping_ids=tuple(sorted(set(source_mapping_ids))),
        remediation=_REMEDIATIONS[code],
    )


def _sort_diagnostics(
    diagnostics: Iterable[Diagnostic],
    limit: int,
) -> tuple[Diagnostic, ...]:
    unique = {
        (
            item.code,
            item.severity,
            item.message,
            item.object_ref,
            item.source_mapping_ids,
            item.remediation,
        ): item
        for item in diagnostics
    }
    ordered = sorted(
        unique.values(),
        key=lambda item: (
            _SEVERITY_ORDER[item.severity],
            item.code,
            item.object_ref,
            item.source_mapping_ids,
            item.message,
        ),
    )
    if len(ordered) <= limit:
        return tuple(ordered)
    clipped = ordered[: max(0, limit - 1)]
    clipped.append(_diagnostic("RESOURCE_LIMIT_EXCEEDED", "diagnostics"))
    return tuple(clipped)


@lru_cache(maxsize=1)
def _schema_bundle() -> dict[str, Any]:
    path = schema_directory() / "cam-flow.schema.json"
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _schema_definition(schema_name: str) -> Mapping[str, Any]:
    definition_name = {
        "automation_asset": "AutomationAsset",
        "capability_manifest": "CapabilityManifest",
        "flow_graph": "FlowGraph",
        "flow_node": "FlowNode",
        "flow_edge": "FlowEdge",
        "port_contract": "PortContract",
        "parameter_binding": "ParameterBinding",
        "source_mapping": "SourceMapping",
        "round_trip_report": "RoundTripReport",
        "compatibility_report": "CompatibilityReport",
        "preview_plan": "PreviewPlan",
        "flow_version": "FlowVersion",
    }[schema_name]
    return _schema_bundle()["$defs"][definition_name]


def _resolve_ref(reference: str) -> Mapping[str, Any]:
    if not reference.startswith("#/$defs/"):
        raise ValueError(f"Unsupported non-local schema reference: {reference}")
    return _schema_bundle()["$defs"][reference.rsplit("/", 1)[-1]]


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return type(value) is bool
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return type(value) in {int, float}
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict) or isinstance(value, Mapping)
    return False


@lru_cache(maxsize=None)
def _compiled_pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _schema_is_valid(
    value: Any,
    schema: Mapping[str, Any] | bool,
) -> bool:
    if schema is True:
        return True
    if schema is False:
        return False
    reference = schema.get("$ref")
    if reference is not None:
        return _schema_is_valid(value, _resolve_ref(str(reference)))

    alternatives = schema.get("anyOf")
    if alternatives is not None:
        return any(_schema_is_valid(value, item) for item in alternatives)
    alternatives = schema.get("oneOf")
    if alternatives is not None:
        return sum(_schema_is_valid(value, item) for item in alternatives) == 1

    expected_type = schema.get("type")
    if expected_type is not None:
        types = (expected_type,) if isinstance(expected_type, str) else expected_type
        if not any(_matches_type(value, item) for item in types):
            return False
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            return False
        pattern = schema.get("pattern")
        if pattern is not None and _compiled_pattern(str(pattern)).search(value) is None:
            return False
    if type(value) in {int, float} and "minimum" in schema:
        if value < schema["minimum"]:
            return False
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            return False
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            return False
        if schema.get("uniqueItems"):
            rendered = [
                json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value
            ]
            if len(rendered) != len(set(rendered)):
                return False
        item_schema = schema.get("items")
        if item_schema is not None:
            for item in value:
                if not _schema_is_valid(item, item_schema):
                    return False
    if isinstance(value, dict) or isinstance(value, Mapping):
        for required in schema.get("required", ()):
            if required not in value:
                return False
        properties = schema.get("properties", {})
        additional_allowed = schema.get("additionalProperties") is not False
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                if not _schema_is_valid(item, child_schema):
                    return False
            elif not additional_allowed:
                return False
    return True


def _compile_schema_validator(
    schema: Mapping[str, Any] | bool,
    memo: dict[int, Callable[[Any], bool]],
) -> Callable[[Any], bool]:
    if schema is True:
        return lambda _value: True
    if schema is False:
        return lambda _value: False

    schema_id = id(schema)
    cached = memo.get(schema_id)
    if cached is not None:
        return cached

    target: list[Callable[[Any], bool]] = []

    def indirect(value: Any) -> bool:
        return target[0](value)

    memo[schema_id] = indirect
    reference = schema.get("$ref")
    if reference is not None:
        validator = _compile_schema_validator(
            _resolve_ref(str(reference)),
            memo,
        )
    elif "anyOf" in schema:
        alternatives = tuple(
            _compile_schema_validator(item, memo) for item in schema["anyOf"]
        )

        def validator(value: Any) -> bool:
            return any(check(value) for check in alternatives)

    elif "oneOf" in schema:
        alternatives = tuple(
            _compile_schema_validator(item, memo) for item in schema["oneOf"]
        )

        def validator(value: Any) -> bool:
            return sum(check(value) for check in alternatives) == 1

    else:
        expected_type = schema.get("type")
        type_names = (
            frozenset((expected_type,))
            if isinstance(expected_type, str)
            else frozenset(expected_type or ())
        )
        has_const = "const" in schema
        const_value = schema.get("const")
        enum_values = schema.get("enum")
        min_length = int(schema.get("minLength", 0))
        pattern_text = schema.get("pattern")
        pattern = (
            _compiled_pattern(str(pattern_text)) if pattern_text is not None else None
        )
        minimum = schema.get("minimum")
        has_minimum = "minimum" in schema
        min_items = int(schema.get("minItems", 0))
        max_items = int(schema["maxItems"]) if "maxItems" in schema else None
        unique_items = bool(schema.get("uniqueItems"))
        item_schema = schema.get("items")
        item_validator = (
            _compile_schema_validator(item_schema, memo)
            if item_schema is not None
            else None
        )
        required = tuple(schema.get("required", ()))
        properties = {
            key: _compile_schema_validator(item, memo)
            for key, item in schema.get("properties", {}).items()
        }
        additional_allowed = schema.get("additionalProperties") is not False

        def validator(value: Any) -> bool:
            if type_names:
                value_type = type(value)
                if value is None:
                    matches = "null" in type_names
                elif value_type is bool:
                    matches = "boolean" in type_names
                elif value_type is int:
                    matches = "integer" in type_names or "number" in type_names
                elif value_type is float:
                    matches = "number" in type_names
                elif isinstance(value, str):
                    matches = "string" in type_names
                elif isinstance(value, list):
                    matches = "array" in type_names
                elif isinstance(value, dict) or isinstance(value, Mapping):
                    matches = "object" in type_names
                else:
                    matches = False
                if not matches:
                    return False
            if has_const and value != const_value:
                return False
            if enum_values is not None and value not in enum_values:
                return False
            if isinstance(value, str):
                if len(value) < min_length:
                    return False
                if pattern is not None and pattern.search(value) is None:
                    return False
            if type(value) in {int, float} and has_minimum and value < minimum:
                return False
            if isinstance(value, list):
                if len(value) < min_items:
                    return False
                if max_items is not None and len(value) > max_items:
                    return False
                if unique_items:
                    rendered = [
                        json.dumps(item, sort_keys=True, ensure_ascii=False)
                        for item in value
                    ]
                    if len(rendered) != len(set(rendered)):
                        return False
                if item_validator is not None:
                    for item in value:
                        if not item_validator(item):
                            return False
            if isinstance(value, dict) or isinstance(value, Mapping):
                for key in required:
                    if key not in value:
                        return False
                for key, item in value.items():
                    child_validator = properties.get(key)
                    if child_validator is not None:
                        if not child_validator(item):
                            return False
                    elif not additional_allowed:
                        return False
            return True

    target.append(validator)
    memo[schema_id] = validator
    return validator


@lru_cache(maxsize=None)
def _schema_validator(schema_name: str) -> Callable[[Any], bool]:
    return _compile_schema_validator(_schema_definition(schema_name), {})


def _schema_errors(
    value: Any,
    schema: Mapping[str, Any] | bool,
    path: str = "$",
) -> list[str]:
    if schema is True:
        return []
    if schema is False:
        return [path]
    if "$ref" in schema:
        return _schema_errors(value, _resolve_ref(str(schema["$ref"])), path)

    errors: list[str] = []
    if "anyOf" in schema:
        if not any(_schema_is_valid(value, item) for item in schema["anyOf"]):
            errors.append(path)
        return errors
    if "oneOf" in schema:
        matches = sum(_schema_is_valid(value, item) for item in schema["oneOf"])
        if matches != 1:
            errors.append(path)
        return errors

    expected_type = schema.get("type")
    if expected_type is not None:
        types = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        if not any(_matches_type(value, item) for item in types):
            return [path]
    if "const" in schema and value != schema["const"]:
        errors.append(path)
    if "enum" in schema and value not in schema["enum"]:
        errors.append(path)

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(path)
        pattern = schema.get("pattern")
        if pattern is not None and _compiled_pattern(str(pattern)).search(value) is None:
            errors.append(path)
    if type(value) in {int, float} and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(path)
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(path)
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(path)
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            if len(rendered) != len(set(rendered)):
                errors.append(path)
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, item_schema, f"{path}[{index}]"))
    if isinstance(value, Mapping):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}.{required}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors.extend(_schema_errors(item, properties[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}")
    return errors


def _detect_schema_name(value: Mapping[str, Any], schema_name: str | None) -> str:
    if schema_name is not None:
        return normalize_schema_name(schema_name)
    contract = value.get("contract")
    for name, expected in CONTRACT_NAMES.items():
        if expected == contract:
            return name
    raise ValueError("schema_name is required for embedded CAM flow objects.")


def validate_schema(
    value: Mapping[str, Any] | FrozenContract,
    schema_name: str | None = None,
    *,
    limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
) -> ValidationResult:
    raw = value._validation_view() if isinstance(value, FrozenContract) else value
    if not isinstance(raw, Mapping):
        normalized = normalize_schema_name(schema_name or "flow_graph")
        return ValidationResult(
            normalized,
            (_diagnostic("FLOW_SCHEMA_INVALID", "$"),),
        )
    normalized = _detect_schema_name(raw, schema_name)
    definition = _schema_definition(normalized)
    errors = (
        []
        if _schema_validator(normalized)(raw)
        else _schema_errors(raw, definition)
    )
    diagnostics = [
        _diagnostic("FLOW_SCHEMA_INVALID", path)
        for path in errors
    ]
    return ValidationResult(
        normalized,
        _sort_diagnostics(diagnostics, limits.max_diagnostics),
    )


def _resource_diagnostics(
    value: Any,
    schema_name: str,
    limits: ResourceLimits,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    def visit(item: Any, depth: int, path: str) -> None:
        if depth > limits.max_depth:
            diagnostics.append(_diagnostic("RESOURCE_LIMIT_EXCEEDED", path))
            return
        if isinstance(item, str):
            if len(item.encode("utf-8")) > limits.max_string_bytes:
                diagnostics.append(_diagnostic("RESOURCE_LIMIT_EXCEEDED", path))
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                visit(child, depth + 1, f"{path}.{key}")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, depth + 1, f"{path}[{index}]")

    visit(value, 1, "$")
    if schema_name == "automation_asset" and isinstance(value, Mapping):
        byte_length = value.get("byte_length")
        if type(byte_length) is int and byte_length > limits.max_asset_bytes:
            diagnostics.append(
                _diagnostic("RESOURCE_LIMIT_EXCEEDED", "asset:byte_length")
            )
    if schema_name == "flow_graph" and isinstance(value, Mapping):
        node_count = 0
        edge_count = 0
        for flow in value.get("flows", []):
            if isinstance(flow, Mapping):
                nodes = flow.get("nodes", [])
                edges = flow.get("edges", [])
                node_count += len(nodes) if isinstance(nodes, list) else 0
                edge_count += len(edges) if isinstance(edges, list) else 0
        if node_count > limits.max_nodes:
            diagnostics.append(_diagnostic("RESOURCE_LIMIT_EXCEEDED", "graph:nodes"))
        if edge_count > limits.max_edges:
            diagnostics.append(_diagnostic("RESOURCE_LIMIT_EXCEEDED", "graph:edges"))
    return diagnostics


def _safety_diagnostics(value: Mapping[str, Any], schema_name: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if schema_name == "preview_plan":
        target = value.get("target", {})
        if (
            value.get("execution_mode") != "fixture_dry_run"
            or value.get("transport") != "none"
            or not isinstance(target, Mapping)
            or target.get("target_kind") != "fixture"
            or value.get("journal_executed") is not False
            or value.get("macro_executed") is not False
            or value.get("commands_sent") != 0
        ):
            diagnostics.append(
                _diagnostic("SAFETY_LIVE_EXECUTION_FORBIDDEN", "preview:safety")
            )
        if value.get("machine_output_count") != 0:
            diagnostics.append(
                _diagnostic("SAFETY_MACHINE_OUTPUT_FORBIDDEN", "preview:output")
            )

    def scan(item: Any, path: str, parent_key: str = "") -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized_key = str(key).lower().replace("-", "_").replace(".", "_")
                if normalized_key in _MACHINE_OUTPUT_KEYS:
                    diagnostics.append(
                        _diagnostic("SAFETY_MACHINE_OUTPUT_FORBIDDEN", "payload:rejected")
                    )
                    continue
                if (
                    normalized_key in _AI_AUTHORITY_KEYS
                    and child is not False
                    and child is not None
                ):
                    diagnostics.append(
                        _diagnostic(
                            "SAFETY_AI_AUTHORITY_EXCEEDED",
                            "payload:rejected",
                        )
                    )
                    continue
                if key in _SAFETY_SCAN_EXCLUDED_FIELDS:
                    continue
                if key in {"action", "node_type", "operation"} and isinstance(child, str):
                    normalized = child.lower().replace("-", "_")
                    if any(part in normalized for part in _MACHINE_ACTION_PARTS):
                        diagnostics.append(
                            _diagnostic(
                                "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
                                "payload:rejected",
                            )
                        )
                if key in {"execution_mode", "mode", "target_kind"} and child == "live":
                    diagnostics.append(
                        _diagnostic(
                            "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                            "payload:rejected",
                        )
                    )
                if key == "transport" and child is not None and child != "none":
                    diagnostics.append(
                        _diagnostic(
                            "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                            "payload:rejected",
                        )
                    )
                if key in {"journal_executed", "macro_executed"} and child is True:
                    diagnostics.append(
                        _diagnostic(
                            "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                            "payload:rejected",
                        )
                    )
                if key == "commands_sent" and type(child) is int and child > 0:
                    diagnostics.append(
                        _diagnostic(
                            "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                            "payload:rejected",
                        )
                    )
                scan(child, f"{path}.{key}", str(key))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                scan(child, f"{path}[{index}]", parent_key)

    scan(value, "$")
    return diagnostics


@lru_cache(maxsize=256)
def _normalized_safety_key(key: str) -> str:
    return key.lower().replace("-", "_").replace(".", "_")


def _preflight_diagnostics(
    value: Mapping[str, Any],
    schema_name: str,
    limits: ResourceLimits,
) -> list[Diagnostic]:
    safety: list[Diagnostic] = []
    resource_tree_invalid = False
    if schema_name == "preview_plan":
        target = value.get("target", {})
        if (
            value.get("execution_mode") != "fixture_dry_run"
            or value.get("transport") != "none"
            or not isinstance(target, Mapping)
            or target.get("target_kind") != "fixture"
            or value.get("journal_executed") is not False
            or value.get("macro_executed") is not False
            or value.get("commands_sent") != 0
        ):
            safety.append(
                _diagnostic("SAFETY_LIVE_EXECUTION_FORBIDDEN", "preview:safety")
            )
        if value.get("machine_output_count") != 0:
            safety.append(
                _diagnostic("SAFETY_MACHINE_OUTPUT_FORBIDDEN", "preview:output")
            )

    def scan(item: Any, depth: int, scan_safety: bool, scan_resource: bool) -> None:
        nonlocal resource_tree_invalid
        if scan_resource:
            if depth > limits.max_depth:
                resource_tree_invalid = True
                scan_resource = False
            elif isinstance(item, str):
                if len(item) > limits.max_string_bytes or (
                    not item.isascii()
                    and len(item.encode("utf-8")) > limits.max_string_bytes
                ):
                    resource_tree_invalid = True

        if isinstance(item, Mapping):
            for key, child in item.items():
                child_safety = scan_safety
                if scan_safety:
                    text_key = str(key)
                    normalized_key = _normalized_safety_key(text_key)
                    if normalized_key in _MACHINE_OUTPUT_KEYS:
                        safety.append(
                            _diagnostic(
                                "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
                                "payload:rejected",
                            )
                        )
                        child_safety = False
                    elif (
                        normalized_key in _AI_AUTHORITY_KEYS
                        and child is not False
                        and child is not None
                    ):
                        safety.append(
                            _diagnostic(
                                "SAFETY_AI_AUTHORITY_EXCEEDED",
                                "payload:rejected",
                            )
                        )
                        child_safety = False
                    elif key in _SAFETY_SCAN_EXCLUDED_FIELDS:
                        child_safety = False
                    else:
                        if key in {"action", "node_type", "operation"} and isinstance(
                            child, str
                        ):
                            normalized = child.lower().replace("-", "_")
                            if any(part in normalized for part in _MACHINE_ACTION_PARTS):
                                safety.append(
                                    _diagnostic(
                                        "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
                                        "payload:rejected",
                                    )
                                )
                        if (
                            key in {"execution_mode", "mode", "target_kind"}
                            and child == "live"
                        ):
                            safety.append(
                                _diagnostic(
                                    "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                                    "payload:rejected",
                                )
                            )
                        if key == "transport" and child is not None and child != "none":
                            safety.append(
                                _diagnostic(
                                    "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                                    "payload:rejected",
                                )
                            )
                        if key in {"journal_executed", "macro_executed"} and child is True:
                            safety.append(
                                _diagnostic(
                                    "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                                    "payload:rejected",
                                )
                            )
                        if key == "commands_sent" and type(child) is int and child > 0:
                            safety.append(
                                _diagnostic(
                                    "SAFETY_LIVE_EXECUTION_FORBIDDEN",
                                    "payload:rejected",
                                )
                            )
                scan(child, depth + 1, child_safety, scan_resource)
        elif isinstance(item, list):
            for child in item:
                scan(child, depth + 1, scan_safety, scan_resource)

    scan(value, 1, True, True)
    if resource_tree_invalid:
        resource = _resource_diagnostics(value, schema_name, limits)
    else:
        resource = []
        if schema_name == "automation_asset":
            byte_length = value.get("byte_length")
            if type(byte_length) is int and byte_length > limits.max_asset_bytes:
                resource.append(
                    _diagnostic("RESOURCE_LIMIT_EXCEEDED", "asset:byte_length")
                )
        elif schema_name == "flow_graph":
            node_count = 0
            edge_count = 0
            for flow in value.get("flows", []):
                if isinstance(flow, Mapping):
                    nodes = flow.get("nodes", [])
                    edges = flow.get("edges", [])
                    node_count += len(nodes) if isinstance(nodes, list) else 0
                    edge_count += len(edges) if isinstance(edges, list) else 0
            if node_count > limits.max_nodes:
                resource.append(
                    _diagnostic("RESOURCE_LIMIT_EXCEEDED", "graph:nodes")
                )
            if edge_count > limits.max_edges:
                resource.append(
                    _diagnostic("RESOURCE_LIMIT_EXCEEDED", "graph:edges")
                )
    return resource + safety


def _asset_diagnostics(value: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    product = value.get("product")
    asset_type = value.get("asset_type")
    if (
        isinstance(asset_type, str)
        and (
            (asset_type.startswith("nx_") and product != "nx")
            or (asset_type.startswith("powermill_") and product != "powermill")
        )
    ):
        diagnostics.append(
            _diagnostic(
                "FLOW_PRODUCT_MIXED",
                f"asset:{value.get('asset_id', 'unknown')}",
            )
        )
    source_locator = value.get("source_locator")
    if isinstance(source_locator, str) and (
        re.match(r"^[A-Za-z]:[\\/]", source_locator) or source_locator.startswith("/")
    ):
        diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", "asset:source_locator"))
    rights = value.get("rights")
    if isinstance(rights, Mapping):
        if rights.get("binary_inspection") is not False:
            diagnostics.append(
                _diagnostic(
                    "ASSET_BINARY_INSPECTION_FORBIDDEN",
                    f"asset:{value.get('asset_id', 'unknown')}",
                )
            )
        requested_preview = "fixture_dry_run" in value.get("runtime_modes", [])
        if rights.get("status") in {"unreviewed", "restricted"} and requested_preview:
            diagnostics.append(
                _diagnostic(
                    "ASSET_RIGHTS_UNKNOWN",
                    f"asset:{value.get('asset_id', 'unknown')}",
                )
            )
    return diagnostics


def _manifest_diagnostics(value: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    product = value.get("product")
    object_ref = f"manifest:{value.get('manifest_id', 'unknown')}"
    if value.get("revocation") is not None:
        diagnostics.append(_diagnostic("CAPABILITY_REVOKED", object_ref))
    prohibited = {
        str(item).lower().replace("-", "_")
        for item in value.get("prohibited_operations", [])
    }
    required_groups = (
        {"live_journal", "journal_live"},
        {"live_macro", "macro_live"},
        {"nc", "nc_output"},
        {"gcode", "g_code"},
        {"postprocess", "postprocessor"},
        {"machine_control", "machinecontrol"},
    )
    if any(not group.intersection(prohibited) for group in required_groups):
        diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", object_ref))
    for node_type in value.get("node_types", []):
        if not isinstance(node_type, Mapping):
            continue
        name = node_type.get("node_type")
        diagnostics.extend(_namespace_diagnostics(name, product, f"node_type:{name}"))
        for port in node_type.get("ports", []):
            if isinstance(port, Mapping) and not _valid_type_ref(port.get("type_ref")):
                diagnostics.append(
                    _diagnostic(
                        "PORT_TYPE_MISMATCH",
                        f"port:{name}:{port.get('port_id', 'unknown')}",
                    )
                )
    return diagnostics


def _namespace_diagnostics(
    node_type: Any,
    product: Any,
    object_ref: str,
) -> list[Diagnostic]:
    if not isinstance(node_type, str) or _NODE_NAMESPACE.fullmatch(node_type) is None:
        return [_diagnostic("FLOW_NAMESPACE_INVALID", object_ref)]
    prefix = node_type.split(".", 1)[0]
    if (
        prefix in {"nx", "powermill"}
        and product in {"nx", "powermill"}
        and prefix != product
    ):
        return [_diagnostic("FLOW_PRODUCT_MIXED", object_ref)]
    return []


def _manifest_indexes(
    manifests: Sequence[Mapping[str, Any] | CapabilityManifest],
) -> tuple[
    dict[tuple[str, str], Mapping[str, Any]],
    dict[tuple[str, str, str, str], Mapping[str, Any]],
    dict[tuple[str, str, str, str, str], Mapping[str, Any]],
]:
    manifest_index: dict[tuple[str, str], Mapping[str, Any]] = {}
    node_index: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    port_index: dict[tuple[str, str, str, str, str], Mapping[str, Any]] = {}
    for item in manifests:
        manifest = item.to_dict() if isinstance(item, FrozenContract) else item
        if not isinstance(manifest, Mapping):
            continue
        manifest_id = str(manifest.get("manifest_id", ""))
        manifest_hash = str(manifest.get("manifest_hash", ""))
        manifest_index[(manifest_id, manifest_hash)] = manifest
        for node_type in manifest.get("node_types", []):
            if not isinstance(node_type, Mapping):
                continue
            name = str(node_type.get("node_type", ""))
            version = str(node_type.get("node_type_version", ""))
            node_index[(manifest_id, manifest_hash, name, version)] = node_type
            for port in node_type.get("ports", []):
                if isinstance(port, Mapping):
                    port_index[
                        (
                            manifest_id,
                            manifest_hash,
                            name,
                            version,
                            str(port.get("port_id", "")),
                        )
                    ] = port
    return manifest_index, node_index, port_index


def _node_capability_key(node: Mapping[str, Any]) -> tuple[str, str, str, str]:
    capability = node.get("capability_ref", {})
    if not isinstance(capability, Mapping):
        capability = {}
    return (
        str(capability.get("manifest_id", "")),
        str(capability.get("manifest_hash", "")),
        str(node.get("node_type", capability.get("node_type", ""))),
        str(node.get("node_type_version", capability.get("node_type_version", ""))),
    )


def _port_for_node(
    node: Mapping[str, Any],
    port_id: str,
    port_index: Mapping[tuple[str, str, str, str, str], Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    return port_index.get((*_node_capability_key(node), port_id))


def _units(port: Mapping[str, Any]) -> tuple[Any, Any]:
    constraints = port.get("constraints", {})
    if not isinstance(constraints, Mapping):
        return None, None
    return constraints.get("unit_dimension"), constraints.get("unit")


def _valid_type_ref(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if value in _BASE_TYPES:
        return True
    if value.startswith("list<") and value.endswith(">"):
        return _valid_type_ref(value[5:-1])
    return False


def _type_assignable(source_type: Any, target_type: Any) -> bool:
    if source_type == target_type:
        return True
    if not isinstance(source_type, str) or not isinstance(target_type, str):
        return False
    if source_type.startswith("list<") or target_type.startswith("list<"):
        return source_type == target_type
    return source_type in _BASE_TYPES and source_type == target_type


def _units_assignable(
    source_port: Mapping[str, Any],
    target_port: Mapping[str, Any],
) -> bool:
    source_dimension, source_unit = _units(source_port)
    target_dimension, target_unit = _units(target_port)
    if source_dimension and target_dimension and source_dimension != target_dimension:
        return False
    if source_unit and target_unit and source_unit != target_unit:
        return False
    return True


def _has_cycle(adjacency: Mapping[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for target in sorted(adjacency.get(node, ())):
            if visit(target):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in sorted(adjacency))


def _id_duplicates(values: Iterable[Any]) -> bool:
    strings = [str(item) for item in values]
    return len(strings) != len(set(strings))


def _binding_diagnostics(
    node: Mapping[str, Any],
    binding: Mapping[str, Any],
    graph_parameters: Mapping[str, Mapping[str, Any]],
    nodes: Mapping[str, Mapping[str, Any]],
    port_index: Mapping[tuple[str, str, str, str, str], Mapping[str, Any]],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    binding_id = str(binding.get("binding_id", "unknown"))
    object_ref = f"binding:{binding_id}"
    kind = binding.get("kind")
    source_fields = {
        "literal": binding.get("literal"),
        "graph_parameter": binding.get("graph_parameter_id"),
        "node_output": binding.get("source_output"),
        "secret_ref": binding.get("secret_ref"),
    }
    active = [name for name, item in source_fields.items() if item is not None]
    if len(active) != 1 or active[0] != kind:
        diagnostics.append(
            _diagnostic(
                "BINDING_ONE_OF_INVALID",
                object_ref,
                binding.get("source_mapping_ids", []),
            )
        )
        return diagnostics

    target = binding.get("target", {})
    if not isinstance(target, Mapping) or target.get("node_id") != node.get("node_id"):
        diagnostics.append(_diagnostic("BINDING_ONE_OF_INVALID", object_ref))
        return diagnostics
    target_port_id = str(target.get("port_id", ""))
    target_port = _port_for_node(node, target_port_id, port_index)
    if target_port is None:
        return diagnostics

    source_type: Any = None
    source_unit: Any = None
    source_port: Mapping[str, Any] | None = None
    if kind == "literal":
        literal = binding.get("literal")
        if isinstance(literal, Mapping):
            source_type = literal.get("type_ref")
            source_unit = literal.get("unit")
    elif kind == "graph_parameter":
        parameter = graph_parameters.get(str(binding.get("graph_parameter_id", "")))
        if parameter is not None:
            source_type = parameter.get("type_ref")
            source_unit = parameter.get("unit")
    elif kind == "node_output":
        source = binding.get("source_output")
        if isinstance(source, Mapping):
            source_node = nodes.get(str(source.get("node_id", "")))
            if source_node is not None:
                source_port = _port_for_node(
                    source_node,
                    str(source.get("port_id", "")),
                    port_index,
                )
                if source_port is not None:
                    source_type = source_port.get("type_ref")

    if source_type is not None and not _type_assignable(
        source_type,
        target_port.get("type_ref"),
    ):
        diagnostics.append(_diagnostic("PORT_TYPE_MISMATCH", object_ref))
    if source_port is not None:
        if not _units_assignable(source_port, target_port):
            diagnostics.append(_diagnostic("PORT_UNIT_MISMATCH", object_ref))
    elif source_unit is not None:
        target_dimension, target_unit = _units(target_port)
        if target_unit and source_unit != target_unit:
            diagnostics.append(_diagnostic("PORT_UNIT_MISMATCH", object_ref))
        if target_dimension and source_unit is None:
            diagnostics.append(_diagnostic("PORT_UNIT_MISMATCH", object_ref))
    return diagnostics


def _edge_diagnostics(
    edge: Mapping[str, Any],
    flow_nodes: Mapping[str, Mapping[str, Any]],
    all_node_flows: Mapping[str, str],
    port_index: Mapping[tuple[str, str, str, str, str], Mapping[str, Any]],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    edge_id = str(edge.get("edge_id", "unknown"))
    object_ref = f"edge:{edge_id}"
    source = edge.get("source", {})
    target = edge.get("target", {})
    if not isinstance(source, Mapping) or not isinstance(target, Mapping):
        return diagnostics
    source_id = str(source.get("node_id", ""))
    target_id = str(target.get("node_id", ""))
    if source_id not in flow_nodes or target_id not in flow_nodes:
        if source_id in all_node_flows or target_id in all_node_flows:
            diagnostics.append(
                _diagnostic(
                    "FLOW_CROSS_FLOW_EDGE",
                    object_ref,
                    edge.get("source_mapping_ids", []),
                )
            )
        return diagnostics

    source_node = flow_nodes[source_id]
    target_node = flow_nodes[target_id]
    source_port = _port_for_node(source_node, str(source.get("port_id", "")), port_index)
    target_port = _port_for_node(target_node, str(target.get("port_id", "")), port_index)
    if source_port is None or target_port is None:
        return diagnostics
    kind = edge.get("kind")
    source_kinds = source_port.get("edge_kinds", [])
    target_kinds = target_port.get("edge_kinds", [])
    if (
        source_port.get("direction") != "output"
        or target_port.get("direction") != "input"
        or kind not in source_kinds
        or kind not in target_kinds
    ):
        diagnostics.append(_diagnostic("PORT_DIRECTION_INVALID", object_ref))
    if kind == "data":
        if not _type_assignable(
            source_port.get("type_ref"),
            target_port.get("type_ref"),
        ):
            diagnostics.append(_diagnostic("PORT_TYPE_MISMATCH", object_ref))
        if not _units_assignable(source_port, target_port):
            diagnostics.append(_diagnostic("PORT_UNIT_MISMATCH", object_ref))
        if str(source_node.get("node_type", "")).startswith("opaque."):
            diagnostics.append(_diagnostic("PORT_TYPE_MISMATCH", object_ref))
    return diagnostics


def _source_mapping_diagnostics(
    graph: Mapping[str, Any],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    asset_hashes = {
        (str(item.get("asset_id", "")), str(item.get("asset_revision_id", ""))): item.get(
            "content_hash"
        )
        for item in graph.get("asset_refs", [])
        if isinstance(item, Mapping)
    }
    for mapping in graph.get("source_mappings", []):
        if not isinstance(mapping, Mapping):
            continue
        mapping_id = str(mapping.get("mapping_id", "unknown"))
        object_ref = f"mapping:{mapping_id}"
        expected = asset_hashes.get(
            (
                str(mapping.get("asset_id", "")),
                str(mapping.get("asset_revision_id", "")),
            )
        )
        if expected is None or mapping.get("source_digest") != expected:
            diagnostics.append(_diagnostic("SOURCE_DIGEST_MISMATCH", object_ref))
        span = mapping.get("source_span", {})
        if isinstance(span, Mapping):
            start_byte = span.get("start_byte")
            end_byte = span.get("end_byte")
            start_line = span.get("start_line")
            end_line = span.get("end_line")
            start_column = span.get("start_column")
            end_column = span.get("end_column")
            invalid = (
                type(start_byte) is not int
                or type(end_byte) is not int
                or end_byte < start_byte
                or type(start_line) is not int
                or type(end_line) is not int
                or end_line < start_line
                or mapping.get("source_line") != start_line
                or (
                    start_line == end_line
                    and type(start_column) is int
                    and type(end_column) is int
                    and end_column < start_column
                )
            )
            if invalid:
                diagnostics.append(_diagnostic("SOURCE_SPAN_INVALID", object_ref))
        if mapping.get("mapping_quality") == "ambiguous":
            diagnostics.append(_diagnostic("SOURCE_MAPPING_AMBIGUOUS", object_ref))
    return diagnostics


def _flow_graph_diagnostics(
    graph: Mapping[str, Any],
    capability_manifests: Sequence[Mapping[str, Any] | CapabilityManifest] | None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    product = graph.get("product")
    flows = [item for item in graph.get("flows", []) if isinstance(item, Mapping)]
    flow_by_id = {str(item.get("flow_id", "")): item for item in flows}
    entry = flow_by_id.get(str(graph.get("entry_flow_id", "")))
    if entry is None or entry.get("kind") != "main":
        diagnostics.append(_diagnostic("FLOW_ENTRY_INVALID", "graph:entry"))
    if _id_duplicates(item.get("flow_id") for item in flows):
        diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", "graph:flow_ids"))
    raw_parameters = [
        item
        for item in graph.get("graph_parameters", [])
        if isinstance(item, Mapping)
    ]
    parameters = {
        str(item.get("parameter_id", "")): item
        for item in raw_parameters
    }
    if _id_duplicates(item.get("parameter_id") for item in raw_parameters):
        diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", "graph:parameter_ids"))
    for parameter_id, parameter in parameters.items():
        if not _valid_type_ref(parameter.get("type_ref")):
            diagnostics.append(
                _diagnostic(
                    "PORT_TYPE_MISMATCH",
                    f"parameter:{parameter_id}",
                )
            )

    manifests = capability_manifests or ()
    manifest_index, node_index, port_index = _manifest_indexes(manifests)
    all_nodes: dict[str, Mapping[str, Any]] = {}
    all_node_flows: dict[str, str] = {}
    for flow in flows:
        flow_id = str(flow.get("flow_id", ""))
        for node in flow.get("nodes", []):
            if not isinstance(node, Mapping):
                continue
            node_id = str(node.get("node_id", ""))
            if node_id in all_nodes:
                diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", f"node:{node_id}"))
            all_nodes[node_id] = node
            all_node_flows[node_id] = flow_id
    binding_ids: list[str] = []
    edge_ids: list[str] = []

    for flow in flows:
        flow_id = str(flow.get("flow_id", ""))
        nodes = {
            str(item.get("node_id", "")): item
            for item in flow.get("nodes", [])
            if isinstance(item, Mapping)
        }
        dependency_graph: dict[str, set[str]] = defaultdict(set)
        control_graph: dict[str, set[str]] = defaultdict(set)
        incoming: Counter[tuple[str, str]] = Counter()

        for node in nodes.values():
            node_id = str(node.get("node_id", "unknown"))
            object_ref = f"node:{node_id}"
            diagnostics.extend(
                _namespace_diagnostics(node.get("node_type"), product, object_ref)
            )
            compatibility = node.get("compatibility_status")
            opaque = node.get("opaque")
            if (
                str(node.get("node_type", "")).startswith("opaque.")
                or compatibility in {"unsupported", "capability_unavailable"}
            ) and not isinstance(opaque, Mapping):
                diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", object_ref))
            if isinstance(opaque, Mapping) and opaque.get("semantic_editable") is not False:
                diagnostics.append(_diagnostic("SOURCE_OPAQUE_EDIT", object_ref))

            capability_key = _node_capability_key(node)
            if capability_manifests is not None:
                manifest = manifest_index.get(capability_key[:2])
                definition = node_index.get(capability_key)
                if manifest is None or definition is None:
                    diagnostics.append(_diagnostic("CAPABILITY_MISSING", object_ref))
                else:
                    if manifest.get("product") != product:
                        diagnostics.append(_diagnostic("FLOW_PRODUCT_MIXED", object_ref))
                    if manifest.get("revocation") is not None:
                        diagnostics.append(_diagnostic("CAPABILITY_REVOKED", object_ref))
                    if (
                        definition.get("node_type_version")
                        != node.get("node_type_version")
                    ):
                        diagnostics.append(
                            _diagnostic("CAPABILITY_VERSION_MISMATCH", object_ref)
                        )
                    floor = definition.get("static_risk_floor")
                    risk = node.get("risk")
                    if floor in _RISK_ORDER and risk in _RISK_ORDER:
                        if _RISK_ORDER[risk] < _RISK_ORDER[floor]:
                            diagnostics.append(
                                _diagnostic("CAPABILITY_RISK_DOWNGRADE", object_ref)
                            )

            for binding in node.get("bindings", []):
                if not isinstance(binding, Mapping):
                    continue
                binding_ids.append(str(binding.get("binding_id", "")))
                diagnostics.extend(
                    _binding_diagnostics(
                        node,
                        binding,
                        parameters,
                        all_nodes,
                        port_index,
                    )
                )
                target = binding.get("target", {})
                if isinstance(target, Mapping):
                    incoming[
                        (str(target.get("node_id", "")), str(target.get("port_id", "")))
                    ] += 1

        for edge in flow.get("edges", []):
            if not isinstance(edge, Mapping):
                continue
            edge_id = str(edge.get("edge_id", ""))
            edge_ids.append(edge_id)
            diagnostics.extend(
                _edge_diagnostics(edge, nodes, all_node_flows, port_index)
            )
            source = edge.get("source", {})
            target = edge.get("target", {})
            if not isinstance(source, Mapping) or not isinstance(target, Mapping):
                continue
            source_id = str(source.get("node_id", ""))
            target_id = str(target.get("node_id", ""))
            if edge.get("kind") == "dependency":
                dependency_graph[source_id].add(target_id)
            elif edge.get("kind") == "control":
                control_graph[source_id].add(target_id)
            elif edge.get("kind") == "data":
                incoming[(target_id, str(target.get("port_id", "")))] += 1
        if _has_cycle(dependency_graph):
            diagnostics.append(
                _diagnostic("FLOW_DEPENDENCY_CYCLE", f"flow:{flow_id}")
            )
        if _has_cycle(control_graph):
            diagnostics.append(
                _diagnostic("FLOW_CONTROL_CYCLE_UNSUPPORTED", f"flow:{flow_id}")
            )
        for (node_id, port_id), count in sorted(incoming.items()):
            node = nodes.get(node_id)
            if node is None:
                continue
            port = _port_for_node(node, port_id, port_index)
            if port is not None and port.get("cardinality") in {"one", "optional"}:
                if count > 1:
                    diagnostics.append(
                        _diagnostic(
                            "PORT_CARDINALITY_EXCEEDED",
                            f"port:{node_id}:{port_id}",
                        )
                    )

    if _id_duplicates(binding_ids):
        diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", "graph:binding_ids"))
    if _id_duplicates(edge_ids):
        diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", "graph:edge_ids"))

    call_graph: dict[str, set[str]] = defaultdict(set)
    for flow in flows:
        flow_id = str(flow.get("flow_id", ""))
        for node in flow.get("nodes", []):
            if not isinstance(node, Mapping) or node.get("node_type") != "flow.subflow_call":
                continue
            configuration = node.get("configuration", {})
            if not isinstance(configuration, Mapping):
                continue
            target = configuration.get(
                "subflow_id",
                configuration.get("target_flow_id", configuration.get("flow_id")),
            )
            if not isinstance(target, str) or target not in flow_by_id:
                diagnostics.append(
                    _diagnostic(
                        "FLOW_ENTRY_INVALID",
                        f"node:{node.get('node_id', 'unknown')}",
                    )
                )
                continue
            call_graph[flow_id].add(target)
    if _has_cycle(call_graph):
        diagnostics.append(_diagnostic("FLOW_SUBFLOW_RECURSION", "graph:subflows"))

    diagnostics.extend(_source_mapping_diagnostics(graph))
    return diagnostics


def _round_trip_diagnostics(value: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    status = value.get("status")
    if status == "passed" and value.get("candidate_reparsed") is not True:
        diagnostics.append(
            _diagnostic("ROUNDTRIP_REPARSE_FAILED", f"report:{value.get('report_id')}")
        )
    if (
        status == "passed"
        and value.get("candidate_semantic_hash") != value.get("original_semantic_hash")
    ):
        diagnostics.append(
            _diagnostic(
                "ROUNDTRIP_SEMANTIC_MISMATCH",
                f"report:{value.get('report_id')}",
            )
        )
    if status == "passed" and value.get("opaque_spans_preserved") is not True:
        diagnostics.append(
            _diagnostic("ROUNDTRIP_OPAQUE_CHANGED", f"report:{value.get('report_id')}")
        )
    if status == "passed" and value.get("untouched_spans_exact") is not True:
        diagnostics.append(
            _diagnostic("SOURCE_SILENT_REWRITE", f"report:{value.get('report_id')}")
        )
    return diagnostics


def _compatibility_diagnostics(value: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    target = value.get("target_profile")
    if not isinstance(target, Mapping) or not target.get("target_version"):
        diagnostics.append(
            _diagnostic(
                "COMPAT_TARGET_VERSION_UNKNOWN",
                f"report:{value.get('report_id')}",
            )
        )
    if value.get("status") != "compatible" and value.get("preview_eligible") is True:
        diagnostics.append(
            _diagnostic(
                "COMPAT_CONTEXT_INCOMPLETE",
                f"report:{value.get('report_id')}",
            )
        )
    unresolved_statuses = {"ambiguous", "missing", "unresolved", "unsupported"}
    if any(
        isinstance(item, Mapping) and item.get("status") in unresolved_statuses
        for item in value.get("selector_results", [])
    ):
        diagnostics.append(
            _diagnostic(
                "COMPAT_SELECTOR_UNRESOLVED",
                f"report:{value.get('report_id')}",
            )
        )
    return diagnostics


def _preview_diagnostics(value: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    target = value.get("target")
    if not isinstance(target, Mapping) or not all(
        target.get(field)
        for field in (
            "product",
            "target_version",
            "target_instance_id",
            "project_id",
            "project_snapshot_hash",
        )
    ):
        diagnostics.append(
            _diagnostic("PREVIEW_TARGET_AMBIGUOUS", f"plan:{value.get('plan_id')}")
        )
    protected_gates = {
        "cam_simulation",
        "collision_check",
        "gouge_check",
        "machine_simulation",
        "shop_approval",
    }
    if any(
        isinstance(item, Mapping)
        and item.get("gate") in protected_gates
        and item.get("status") == "passed"
        for item in value.get("gate_results", [])
    ):
        diagnostics.append(
            _diagnostic(
                "SAFETY_AI_AUTHORITY_EXCEEDED",
                f"plan:{value.get('plan_id')}",
            )
        )
    return diagnostics


def _unknown_semantics(
    value: Any,
    schema: Mapping[str, Any] | bool,
    path: str = "$",
) -> list[str]:
    if schema is True or schema is False:
        return []
    if "$ref" in schema:
        return _unknown_semantics(value, _resolve_ref(str(schema["$ref"])), path)
    if isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema is None:
            return []
        unknown: list[str] = []
        for index, item in enumerate(value):
            unknown.extend(
                _unknown_semantics(item, item_schema, f"{path}[{index}]")
            )
        return unknown
    if not isinstance(value, Mapping):
        return []
    properties = schema.get("properties", {})
    unknown: list[str] = []
    for key, item in value.items():
        if key == "extensions" and isinstance(item, Mapping):
            for extension_name, extension in item.items():
                if not (
                    isinstance(extension, Mapping)
                    and extension.get("semantic") is False
                ):
                    unknown.append(f"{path}.extensions.{extension_name}")
            continue
        child_schema = properties.get(key)
        if child_schema is None:
            unknown.append(f"{path}.{key}")
        else:
            unknown.extend(_unknown_semantics(item, child_schema, f"{path}.{key}"))
    return unknown


def validate_contract(
    value: Mapping[str, Any] | FrozenContract,
    schema_name: str | None = None,
    *,
    capability_manifests: Sequence[
        Mapping[str, Any] | CapabilityManifest
    ] | None = None,
    require_known_semantics: bool = False,
    limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
) -> ValidationResult:
    raw = value._validation_view() if isinstance(value, FrozenContract) else value
    if not isinstance(raw, Mapping):
        return validate_schema(raw, schema_name, limits=limits)
    normalized = _detect_schema_name(raw, schema_name)
    diagnostics = list(
        validate_schema(raw, normalized, limits=limits).diagnostics
    )
    diagnostics.extend(_preflight_diagnostics(raw, normalized, limits))

    try:
        if normalized == "automation_asset":
            diagnostics.extend(_asset_diagnostics(raw))
        elif normalized == "capability_manifest":
            diagnostics.extend(_manifest_diagnostics(raw))
        elif normalized == "flow_graph":
            diagnostics.extend(_flow_graph_diagnostics(raw, capability_manifests))
        elif normalized == "flow_node":
            diagnostics.extend(
                _namespace_diagnostics(
                    raw.get("node_type"),
                    None,
                    f"node:{raw.get('node_id', 'unknown')}",
                )
            )
            opaque = raw.get("opaque")
            if isinstance(opaque, Mapping) and opaque.get("semantic_editable") is not False:
                diagnostics.append(
                    _diagnostic(
                        "SOURCE_OPAQUE_EDIT",
                        f"node:{raw.get('node_id', 'unknown')}",
                    )
                )
        elif normalized == "port_contract":
            if not _valid_type_ref(raw.get("type_ref")):
                diagnostics.append(
                    _diagnostic(
                        "PORT_TYPE_MISMATCH",
                        f"port:{raw.get('port_id', 'unknown')}",
                    )
                )
        elif normalized == "round_trip_report":
            diagnostics.extend(_round_trip_diagnostics(raw))
        elif normalized == "compatibility_report":
            diagnostics.extend(_compatibility_diagnostics(raw))
        elif normalized == "preview_plan":
            diagnostics.extend(_preview_diagnostics(raw))
        elif normalized == "parameter_binding":
            target = raw.get("target", {})
            placeholder = {
                "node_id": target.get("node_id")
                if isinstance(target, Mapping)
                else "",
                "bindings": [raw],
            }
            diagnostics.extend(
                _binding_diagnostics(placeholder, raw, {}, {}, {})
            )
        elif normalized == "source_mapping":
            graph = {
                "asset_refs": [
                    {
                        "asset_id": raw.get("asset_id"),
                        "asset_revision_id": raw.get("asset_revision_id"),
                        "content_hash": raw.get("source_digest"),
                    }
                ],
                "source_mappings": [raw],
            }
            diagnostics.extend(_source_mapping_diagnostics(graph))
    except (AttributeError, KeyError, TypeError, ValueError):
        diagnostics.append(_diagnostic("FLOW_SCHEMA_INVALID", "$"))

    if require_known_semantics:
        diagnostics.extend(
            _diagnostic("FLOW_UNKNOWN_SEMANTICS", path)
            for path in _unknown_semantics(raw, _schema_definition(normalized))
        )
    return ValidationResult(
        normalized,
        _sort_diagnostics(diagnostics, limits.max_diagnostics),
    )


def validate_flow_graph(
    graph: Mapping[str, Any] | FlowGraph,
    *,
    capability_manifests: Sequence[
        Mapping[str, Any] | CapabilityManifest
    ] | None = None,
    require_known_semantics: bool = False,
    limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
) -> ValidationResult:
    return validate_contract(
        graph,
        "flow_graph",
        capability_manifests=capability_manifests,
        require_known_semantics=require_known_semantics,
        limits=limits,
    )


def schema_files() -> tuple[Path, ...]:
    return tuple(sorted(schema_directory().glob("*.schema.json")))


__all__ = [
    "DEFAULT_RESOURCE_LIMITS",
    "Diagnostic",
    "ERROR_CODES",
    "ResourceLimits",
    "ValidationResult",
    "schema_files",
    "validate_contract",
    "validate_flow_graph",
    "validate_schema",
]
