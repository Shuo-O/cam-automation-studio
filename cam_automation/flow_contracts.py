from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar


SCHEMA_VERSION = 1
CONTRACT_FAMILY = "cam.flow.contracts.v1"
HASH_PREFIX = "sha256:"
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_STRING_ESCAPE = re.compile(r'["\\\x00-\x1f]')

CONTRACT_NAMES = {
    "automation_asset": "cam.automation_asset.v1",
    "capability_manifest": "cam.capability_manifest.v1",
    "flow_graph": "cam.flowgraph.v1",
    "flow_node": None,
    "flow_edge": None,
    "port_contract": None,
    "parameter_binding": None,
    "source_mapping": None,
    "round_trip_report": "cam.round_trip_report.v1",
    "compatibility_report": "cam.compatibility_report.v1",
    "preview_plan": "cam.preview_plan.v1",
    "flow_version": "cam.flow_version.v1",
}

SCHEMA_FILENAMES = {
    name: name.replace("_", "-") + ".schema.json" for name in CONTRACT_NAMES
}

_CONTRACT_TO_SCHEMA = {
    contract: name for name, contract in CONTRACT_NAMES.items() if contract is not None
}


def _json_clone(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )


def _projection_clone(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON projections do not allow NaN or infinity.")
        return value
    if isinstance(value, Mapping):
        return {key: _projection_clone(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_projection_clone(item) for item in value]
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


@lru_cache(maxsize=512)
def _short_utf16_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def _utf16_sort_key(value: str) -> bytes:
    if len(value) <= 128:
        return _short_utf16_sort_key(value)
    return value.encode("utf-16-be", errors="surrogatepass")


def _serialize_string(value: str) -> str:
    if value.isascii() and _STRING_ESCAPE.search(value) is None:
        return '"' + value + '"'
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("RFC 8785 input cannot contain lone Unicode surrogates.")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@lru_cache(maxsize=512)
def _serialize_short_key(value: str) -> str:
    return _serialize_string(value)


def _serialize_key(value: str) -> str:
    if len(value) <= 128:
        return _serialize_short_key(value)
    return _serialize_string(value)


def _serialize_number(value: int | float) -> str:
    if isinstance(value, bool):
        raise TypeError("Boolean values are not JSON numbers.")
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
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
    """Serialize JSON data using the RFC 8785 JSON Canonicalization Scheme."""

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
            _serialize_key(key) + ":" + canonical_json(value[key])
            for key in sorted(value, key=_utf16_sort_key)
        ]
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def canonicalize(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return HASH_PREFIX + hashlib.sha256(canonicalize(value)).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class FrozenContract(Mapping[str, Any]):
    """Immutable, lossless JSON object wrapper used by every frozen v1 model."""

    schema_name: ClassVar[str] = ""
    contract_name: ClassVar[str | None] = None
    _serialized: str
    _validation_payload: dict[str, Any]

    def __init__(self, value: Mapping[str, Any]) -> None:
        if not isinstance(value, Mapping):
            raise TypeError(f"{type(self).__name__} requires a JSON object.")
        serialized = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        payload = json.loads(serialized)
        if self.contract_name is not None:
            actual = payload.get("contract")
            if actual != self.contract_name:
                raise ValueError(
                    f"{type(self).__name__} contract must be {self.contract_name!r}."
                )
            if payload.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(
                    f"{type(self).__name__} schema_version must be {SCHEMA_VERSION}."
                )
        object.__setattr__(self, "_serialized", serialized)
        object.__setattr__(self, "_validation_payload", payload)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FrozenContract:
        return cls(value)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._serialized)

    def _validation_view(self) -> Mapping[str, Any]:
        return self._validation_payload

    def with_updates(self, **updates: Any) -> FrozenContract:
        value = self.to_dict()
        value.update(_json_clone(updates))
        return type(self)(value)

    def canonical_bytes(self) -> bytes:
        return canonicalize(self.to_dict())

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __getattr__(self, name: str) -> Any:
        try:
            return self.to_dict()[name]
        except KeyError as error:
            raise AttributeError(name) from error


class AutomationAsset(FrozenContract):
    schema_name = "automation_asset"
    contract_name = CONTRACT_NAMES[schema_name]


class CapabilityManifest(FrozenContract):
    schema_name = "capability_manifest"
    contract_name = CONTRACT_NAMES[schema_name]


class FlowGraph(FrozenContract):
    schema_name = "flow_graph"
    contract_name = CONTRACT_NAMES[schema_name]


class FlowNode(FrozenContract):
    schema_name = "flow_node"


class FlowEdge(FrozenContract):
    schema_name = "flow_edge"


class PortContract(FrozenContract):
    schema_name = "port_contract"


class ParameterBinding(FrozenContract):
    schema_name = "parameter_binding"


class SourceMapping(FrozenContract):
    schema_name = "source_mapping"


class RoundTripReport(FrozenContract):
    schema_name = "round_trip_report"
    contract_name = CONTRACT_NAMES[schema_name]


class CompatibilityReport(FrozenContract):
    schema_name = "compatibility_report"
    contract_name = CONTRACT_NAMES[schema_name]


class PreviewPlan(FrozenContract):
    schema_name = "preview_plan"
    contract_name = CONTRACT_NAMES[schema_name]


class FlowVersion(FrozenContract):
    schema_name = "flow_version"
    contract_name = CONTRACT_NAMES[schema_name]


CONTRACT_MODELS: dict[str, type[FrozenContract]] = {
    model.schema_name: model
    for model in (
        AutomationAsset,
        CapabilityManifest,
        FlowGraph,
        FlowNode,
        FlowEdge,
        PortContract,
        ParameterBinding,
        SourceMapping,
        RoundTripReport,
        CompatibilityReport,
        PreviewPlan,
        FlowVersion,
    )
}


def schema_directory() -> Path:
    return Path(__file__).with_name("schemas") / "cam-flow"


def schema_path(schema_name: str) -> Path:
    normalized = normalize_schema_name(schema_name)
    return schema_directory() / SCHEMA_FILENAMES[normalized]


def load_schema(schema_name: str) -> dict[str, Any]:
    with schema_path(schema_name).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def normalize_schema_name(schema_name: str) -> str:
    normalized = _CONTRACT_TO_SCHEMA.get(schema_name, schema_name)
    normalized = normalized.replace("-", "_").strip().lower()
    if normalized not in CONTRACT_MODELS:
        raise KeyError(f"Unknown CAM flow schema: {schema_name!r}")
    return normalized


def contract_model(
    value: Mapping[str, Any],
    schema_name: str | None = None,
) -> FrozenContract:
    if schema_name is None:
        contract = value.get("contract")
        if not isinstance(contract, str) or contract not in _CONTRACT_TO_SCHEMA:
            raise ValueError("schema_name is required for embedded contract objects.")
        schema_name = _CONTRACT_TO_SCHEMA[contract]
    return CONTRACT_MODELS[normalize_schema_name(schema_name)](value)


def parse_contract_json(
    payload: str | bytes,
    schema_name: str | None = None,
) -> FrozenContract:
    """Parse strict JSON while rejecting duplicate keys and non-finite constants."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON object key: {key!r}")
            result[key] = item
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON numeric constant: {value}")

    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    value = json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=invalid_constant,
    )
    if not isinstance(value, Mapping):
        raise ValueError("A CAM flow contract must be a JSON object.")
    return contract_model(value, schema_name)


def round_trip_contract(
    value: Mapping[str, Any],
    schema_name: str | None = None,
) -> dict[str, Any]:
    """Round-trip a contract without discarding unknown optional fields."""

    return contract_model(value, schema_name).to_dict()


def _semantic_extensions(
    value: Any,
    *,
    detached: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, Mapping) and item.get("semantic") is True:
            result[str(key)] = _projection_clone(item) if detached else item
    return result


def _binding_semantics(
    binding: Mapping[str, Any],
    *,
    detached: bool,
) -> dict[str, Any]:
    return {
        key: (
            _projection_clone(binding.get(key))
            if detached
            else binding.get(key)
        )
        for key in (
            "binding_id",
            "target",
            "kind",
            "literal",
            "graph_parameter_id",
            "source_output",
            "secret_ref",
        )
    } | {
        "extensions": _semantic_extensions(
            binding.get("extensions"),
            detached=detached,
        )
    }


def _node_semantics(
    node: Mapping[str, Any],
    *,
    detached: bool,
) -> dict[str, Any]:
    bindings = [
        _binding_semantics(item, detached=detached)
        for item in node.get("bindings", [])
        if isinstance(item, Mapping)
    ]
    bindings.sort(key=lambda item: str(item.get("binding_id", "")))
    result = {
        "node_id": node.get("node_id"),
        "node_type": node.get("node_type"),
        "node_type_version": node.get("node_type_version"),
        "enabled": node.get("enabled"),
        "risk": node.get("risk"),
        "bindings": bindings,
        "configuration": (
            _projection_clone(node.get("configuration", {}))
            if detached
            else node.get("configuration", {})
        ),
        "extensions": _semantic_extensions(
            node.get("extensions"),
            detached=detached,
        ),
    }
    opaque = node.get("opaque")
    if isinstance(opaque, Mapping):
        result["opaque"] = {
            key: (
                _projection_clone(opaque.get(key))
                if detached
                else opaque.get(key)
            )
            for key in (
                "reason",
                "asset_revision_id",
                "content_hash",
                "round_trip_policy",
                "semantic_editable",
            )
        }
    return result


def _edge_semantics(
    edge: Mapping[str, Any],
    *,
    detached: bool,
) -> dict[str, Any]:
    return {
        "edge_id": edge.get("edge_id"),
        "kind": edge.get("kind"),
        "source": (
            _projection_clone(edge.get("source"))
            if detached
            else edge.get("source")
        ),
        "target": (
            _projection_clone(edge.get("target"))
            if detached
            else edge.get("target")
        ),
        "condition": (
            _projection_clone(edge.get("condition"))
            if detached
            else edge.get("condition")
        ),
        "priority": edge.get("priority"),
        "extensions": _semantic_extensions(
            edge.get("extensions"),
            detached=detached,
        ),
    }


def _parameter_semantics(
    parameter: Mapping[str, Any],
    *,
    detached: bool,
) -> dict[str, Any]:
    ignored = {"evidence_mapping_ids", "review_status", "source_mapping_ids"}
    return {
        str(key): _projection_clone(item) if detached else item
        for key, item in parameter.items()
        if key not in ignored and key != "extensions"
    } | {
        "extensions": _semantic_extensions(
            parameter.get("extensions"),
            detached=detached,
        )
    }


def _semantic_projection(
    graph: Mapping[str, Any] | FlowGraph,
    *,
    detached: bool,
) -> dict[str, Any]:
    value = graph._validation_view() if isinstance(graph, FrozenContract) else graph
    parameters = [
        _parameter_semantics(item, detached=detached)
        for item in value.get("graph_parameters", [])
        if isinstance(item, Mapping)
    ]
    parameters.sort(key=lambda item: str(item.get("parameter_id", "")))

    flows: list[dict[str, Any]] = []
    for flow in value.get("flows", []):
        if not isinstance(flow, Mapping):
            continue
        nodes = [
            _node_semantics(item, detached=detached)
            for item in flow.get("nodes", [])
            if isinstance(item, Mapping)
        ]
        nodes.sort(key=lambda item: str(item.get("node_id", "")))
        edges = [
            _edge_semantics(item, detached=detached)
            for item in flow.get("edges", [])
            if isinstance(item, Mapping)
        ]
        edges.sort(key=lambda item: str(item.get("edge_id", "")))
        interface_ports = [
            _projection_clone(item) if detached else item
            for item in flow.get("interface_ports", [])
            if isinstance(item, Mapping)
        ]
        interface_ports.sort(key=lambda item: str(item.get("port_id", "")))
        flows.append(
            {
                "flow_id": flow.get("flow_id"),
                "kind": flow.get("kind"),
                "interface_ports": interface_ports,
                "parameter_ids": sorted(
                    set(flow.get("parameter_ids", [])),
                    key=lambda item: str(item),
                ),
                "nodes": nodes,
                "edges": edges,
                "extensions": _semantic_extensions(
                    flow.get("extensions"),
                    detached=detached,
                ),
            }
        )
    flows.sort(key=lambda item: str(item.get("flow_id", "")))

    locks = [
        _projection_clone(item) if detached else item
        for item in value.get("capability_lock", [])
        if isinstance(item, Mapping)
    ]
    locks.sort(
        key=lambda item: (
            str(item.get("manifest_id", "")),
            str(item.get("manifest_version", "")),
            str(item.get("manifest_hash", "")),
        )
    )
    return {
        "schema_version": value.get("schema_version"),
        "contract": value.get("contract"),
        "product": value.get("product"),
        "target_versions": sorted(
            set(value.get("target_versions", [])),
            key=lambda item: str(item),
        ),
        "entry_flow_id": value.get("entry_flow_id"),
        "graph_parameters": parameters,
        "flows": flows,
        "capability_lock": locks,
        "required_gates": sorted(
            set(value.get("required_gates", [])),
            key=lambda item: str(item),
        ),
        "extensions": _semantic_extensions(
            value.get("extensions"),
            detached=detached,
        ),
    }


def semantic_projection(graph: Mapping[str, Any] | FlowGraph) -> dict[str, Any]:
    return _semantic_projection(graph, detached=True)


def compute_semantic_hash(graph: Mapping[str, Any] | FlowGraph) -> str:
    return canonical_hash(_semantic_projection(graph, detached=False))


def source_snapshot_projection(
    value: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    assets: Sequence[Mapping[str, Any] | AutomationAsset] = (),
) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        raw_refs = value.get("asset_refs", [])
    else:
        raw_refs = value
    asset_index: dict[str, Mapping[str, Any]] = {}
    for item in assets:
        asset = item.to_dict() if isinstance(item, FrozenContract) else item
        revision_id = asset.get("asset_revision_id")
        if isinstance(revision_id, str):
            asset_index[revision_id] = asset

    projection: list[dict[str, Any]] = []
    for item in raw_refs:
        if not isinstance(item, Mapping):
            continue
        revision_id = item.get("asset_revision_id")
        asset = asset_index.get(str(revision_id), {})
        record = {
            "asset_id": item.get("asset_id", asset.get("asset_id")),
            "asset_revision_id": revision_id,
            "content_hash": item.get("content_hash", asset.get("content_hash")),
        }
        for field_name in ("encoding", "bom", "newline_profile"):
            if field_name in item:
                record[field_name] = item[field_name]
            elif field_name in asset:
                record[field_name] = asset[field_name]
        projection.append(_json_clone(record))
    projection.sort(
        key=lambda item: (
            str(item.get("asset_revision_id", "")),
            str(item.get("asset_id", "")),
        )
    )
    return projection


def compute_source_snapshot_hash(
    value: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    assets: Sequence[Mapping[str, Any] | AutomationAsset] = (),
) -> str:
    return canonical_hash(source_snapshot_projection(value, assets))


def compute_capability_lock_hash(graph: Mapping[str, Any] | FlowGraph) -> str:
    value = graph.to_dict() if isinstance(graph, FrozenContract) else graph
    locks = [
        _json_clone(item)
        for item in value.get("capability_lock", [])
        if isinstance(item, Mapping)
    ]
    locks.sort(
        key=lambda item: (
            str(item.get("manifest_id", "")),
            str(item.get("manifest_version", "")),
            str(item.get("manifest_hash", "")),
        )
    )
    return canonical_hash(locks)


def compute_artifact_hash(value: Mapping[str, Any] | FrozenContract | bytes | str) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    elif isinstance(value, FrozenContract):
        payload = canonicalize(value.to_dict())
    else:
        payload = canonicalize(value)
    return HASH_PREFIX + hashlib.sha256(payload).hexdigest()


__all__ = [
    "AutomationAsset",
    "CapabilityManifest",
    "CompatibilityReport",
    "CONTRACT_FAMILY",
    "CONTRACT_MODELS",
    "CONTRACT_NAMES",
    "FlowEdge",
    "FlowGraph",
    "FlowNode",
    "FlowVersion",
    "FrozenContract",
    "ParameterBinding",
    "PortContract",
    "PreviewPlan",
    "RoundTripReport",
    "SCHEMA_FILENAMES",
    "SCHEMA_VERSION",
    "SourceMapping",
    "canonical_hash",
    "canonical_json",
    "canonicalize",
    "compute_artifact_hash",
    "compute_capability_lock_hash",
    "compute_semantic_hash",
    "compute_source_snapshot_hash",
    "contract_model",
    "load_schema",
    "normalize_schema_name",
    "parse_contract_json",
    "round_trip_contract",
    "schema_directory",
    "schema_path",
    "semantic_projection",
    "source_snapshot_projection",
]
