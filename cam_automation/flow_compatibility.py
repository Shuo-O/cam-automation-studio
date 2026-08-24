from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol, runtime_checkable

from .flow_contracts import (
    CapabilityManifest,
    CompatibilityReport,
    FlowGraph,
    FrozenContract,
    canonical_hash,
    canonical_json,
    compute_capability_lock_hash,
)
from .flow_versions import DEFAULT_TIMESTAMP
from .recipes import (
    REQUIRED_PRODUCTION_GATES,
    Recipe,
    RecipeParameter,
    RecipeStep,
    compute_recipe_hash,
)
from .version_compatibility import (
    classify_target_version,
    normalize_target_version,
    target_version_matches,
)


_PARAMETER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_BOUNDARY_NODE_TYPES = {
    "cam.flow.start",
    "cam.flow.end",
}
_BLOCKING_SELECTOR_STATUSES = {
    "ambiguous",
    "missing",
    "unresolved",
    "unsupported",
    "unknown",
}
_BLOCKING_SYMBOL_STATUSES = {
    "missing",
    "signature_changed",
    "internal",
    "unsupported",
    "unknown",
}
_HARD_COMPATIBILITY_CODES = {
    "CAPABILITY_MISSING",
    "CAPABILITY_VERSION_MISMATCH",
    "CAPABILITY_REVOKED",
    "COMPAT_TARGET_VERSION_UNKNOWN",
    "COMPAT_TARGET_VERSION_OPAQUE_ONLY",
    "COMPAT_TARGET_VERSION_UNSUPPORTED",
    "COMPAT_SELECTOR_UNRESOLVED",
    "COMPAT_CONTEXT_INCOMPLETE",
}


def _clone(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )


def _raw(value: Mapping[str, Any] | FrozenContract) -> dict[str, Any]:
    if isinstance(value, FrozenContract):
        return value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("A CAM flow value must be a JSON object.")
    return _clone(dict(value))


def _nodes(graph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result = []
    for flow in graph.get("flows", []):
        if not isinstance(flow, Mapping):
            continue
        result.extend(
            node for node in flow.get("nodes", []) if isinstance(node, Mapping)
        )
    return result


def _major(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d+)\.\d+\.\d+(?:[-+].*)?", value.strip())
    return int(match.group(1)) if match else None


def _issue(
    code: str,
    object_ref: str,
    message: str,
    *,
    severity: str = "blocker",
    evidence: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "object_ref": object_ref,
        "source_mapping_ids": [],
        "remediation": "Bind reviewed target evidence or resolve the capability gap.",
        "evidence": sorted(set(evidence)),
    }


@runtime_checkable
class CompatibilityAdapter(Protocol):
    """Product-owned static evidence hook. It has no CAM transport."""

    def compatibility_evidence(
        self,
        graph: Mapping[str, Any],
        *,
        target_profile: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


def _manifest_index(
    manifests: Sequence[Mapping[str, Any] | CapabilityManifest],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[tuple[str, str], tuple[dict[str, Any], Mapping[str, Any]]],
]:
    by_id: dict[str, dict[str, Any]] = {}
    node_types: dict[
        tuple[str, str],
        tuple[dict[str, Any], Mapping[str, Any]],
    ] = {}
    for value in manifests:
        manifest = _raw(value)
        manifest_id = str(manifest.get("manifest_id", ""))
        if not manifest_id or manifest_id in by_id:
            raise ValueError("Capability manifests require unique manifest_id values.")
        by_id[manifest_id] = manifest
        for definition in manifest.get("node_types", []):
            if not isinstance(definition, Mapping):
                continue
            key = (manifest_id, str(definition.get("node_type", "")))
            if key in node_types:
                raise ValueError("Capability node definitions must be unique per manifest.")
            node_types[key] = (manifest, definition)
    return by_id, node_types


def _normalize_selector_result(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _clone(dict(value))
    match_count = result.get("match_count")
    status = str(
        result.get("status", result.get("resolution_status", "unknown"))
    ).lower()
    if type(match_count) is int:
        if match_count == 1:
            status = "supported"
        elif match_count == 0:
            status = "missing"
        elif match_count > 1:
            status = "ambiguous"
    if result.get("unique_match_claimed") is False and status == "supported":
        status = "unresolved"
    if result.get("resolved") is False and status == "supported":
        status = "unresolved"
    result["status"] = status
    result.setdefault(
        "selector_id",
        str(result.get("node_id", result.get("object_ref", "selector:unknown"))),
    )
    return result


def _binding_selectors(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in _nodes(graph):
        for binding in node.get("bindings", []):
            if not isinstance(binding, Mapping):
                continue
            literal = binding.get("literal")
            if not isinstance(literal, Mapping):
                continue
            if literal.get("type_ref") != "cam.object_selector":
                continue
            value = literal.get("value")
            selector = dict(value) if isinstance(value, Mapping) else {}
            selector.setdefault("selector_id", binding.get("binding_id"))
            selector.setdefault("node_id", node.get("node_id"))
            result.append(_normalize_selector_result(selector))
    return result


def _adapter_evidence(
    adapter: CompatibilityAdapter | None,
    graph: Mapping[str, Any],
    target_profile: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if adapter is not None:
        if not isinstance(adapter, CompatibilityAdapter):
            raise TypeError("adapter must implement compatibility_evidence().")
        value = adapter.compatibility_evidence(
            graph,
            target_profile=target_profile,
        )
        if not isinstance(value, Mapping):
            raise TypeError("Compatibility adapter evidence must be a JSON object.")
        return _clone(dict(value))
    return {} if evidence is None else _raw(evidence)


def build_compatibility_report(
    graph: Mapping[str, Any] | FlowGraph,
    manifests: Sequence[Mapping[str, Any] | CapabilityManifest],
    *,
    source_profile: Mapping[str, Any] | None = None,
    target_profile: Mapping[str, Any] | None = None,
    selector_results: Sequence[Mapping[str, Any]] = (),
    required_context: Sequence[str] = (),
    context: Mapping[str, Any] | None = None,
    adapter: CompatibilityAdapter | None = None,
    adapter_evidence: Mapping[str, Any] | None = None,
    checked_at: str = DEFAULT_TIMESTAMP,
    report_id: str | None = None,
) -> CompatibilityReport:
    """Build a deterministic static report. Compatible never means executable."""

    document = _raw(graph)
    target = _clone(dict(target_profile or {}))
    source = _clone(dict(source_profile or {}))
    if "target_version" not in source:
        versions = document.get("target_versions", [])
        source["target_version"] = versions[0] if len(versions) == 1 else ""
    target_version = str(target.get("target_version", "")).strip()
    target["target_version"] = target_version
    normalized_target_version = normalize_target_version(
        target_version,
        str(document.get("product", "")),
    )
    evidence = _adapter_evidence(
        adapter,
        document,
        target,
        adapter_evidence,
    )
    by_id, node_definitions = _manifest_index(manifests)
    issues: list[dict[str, Any]] = []
    coverage_gaps: list[dict[str, Any]] = [
        _clone(item)
        for item in evidence.get("coverage_gaps", [])
        if isinstance(item, Mapping)
    ]
    if not target_version:
        issues.append(
            _issue(
                "COMPAT_TARGET_VERSION_UNKNOWN",
                "target_profile",
                "Compatibility requires an explicit target version.",
            )
        )
    elif normalized_target_version not in {
        normalize_target_version(
            str(item),
            str(document.get("product", "")),
        )
        for item in document.get("target_versions", [])
    }:
        issues.append(
            _issue(
                "COMPAT_CONTEXT_INCOMPLETE",
                "target_profile",
                "The target version is not part of this graph revision's reviewed set.",
                evidence=(target_version,),
            )
        )

    checked_manifest_hashes = sorted(
        {
            str(manifest.get("manifest_hash"))
            for manifest in by_id.values()
            if manifest.get("manifest_hash")
        }
    )
    locks = [
        item
        for item in document.get("capability_lock", [])
        if isinstance(item, Mapping)
    ]
    version_results: list[dict[str, Any]] = []
    target_version_tier = "verified"
    tier_order = {
        "verified": 0,
        "review_required": 1,
        "opaque_only": 2,
        "unsupported": 3,
        "unknown": 4,
    }
    for lock in locks:
        manifest_id = str(lock.get("manifest_id", ""))
        manifest = by_id.get(manifest_id)
        if manifest is None:
            issues.append(
                _issue(
                    "CAPABILITY_MISSING",
                    f"manifest:{manifest_id or 'unknown'}",
                    "The graph capability lock is not installed.",
                )
            )
            continue
        if (
            lock.get("manifest_hash") != manifest.get("manifest_hash")
            or lock.get("manifest_version") != manifest.get("manifest_version")
        ):
            issues.append(
                _issue(
                    "CAPABILITY_VERSION_MISMATCH",
                    f"manifest:{manifest_id}",
                    "The installed manifest does not match the exact graph lock.",
                    evidence=(
                        str(lock.get("manifest_hash", "")),
                        str(manifest.get("manifest_hash", "")),
                    ),
                )
            )
        if manifest.get("revocation") is not None:
            issues.append(
                _issue(
                    "CAPABILITY_REVOKED",
                    f"manifest:{manifest_id}",
                    "The locked capability manifest is revoked.",
                )
            )
        if manifest.get("product") != document.get("product"):
            issues.append(
                _issue(
                    "CAPABILITY_MISSING",
                    f"manifest:{manifest_id}",
                    "The capability manifest belongs to another product.",
                )
            )
        version_result = classify_target_version(target_version, manifest)
        version_results.append(
            {
                "manifest_id": manifest_id,
                **version_result.to_dict(),
            }
        )
        if tier_order[version_result.tier] > tier_order[target_version_tier]:
            target_version_tier = version_result.tier
        ranges = [
            str(item) for item in manifest.get("target_version_ranges", [])
        ]
        if version_result.tier == "review_required":
            issues.append(
                _issue(
                    "COMPAT_TARGET_VERSION_REVIEW_REQUIRED",
                    f"manifest:{manifest_id}",
                    (
                        "The target version is statically recognizable but has not "
                        "passed this manifest's fixture verification."
                    ),
                    severity="warning",
                    evidence=(
                        target_version,
                        version_result.normalized_version,
                        str(version_result.matched_range or ""),
                    ),
                )
            )
        elif version_result.tier == "opaque_only":
            coverage_gaps.append(
                {
                    "object_ref": f"manifest:{manifest_id}",
                    "reason": "target_version_opaque_only",
                    "target_version": target_version,
                }
            )
            issues.append(
                _issue(
                    "COMPAT_TARGET_VERSION_OPAQUE_ONLY",
                    f"manifest:{manifest_id}",
                    (
                        "The target version is limited to source preservation and "
                        "cannot enter semantic editing, projection, or preview."
                    ),
                    evidence=(
                        target_version,
                        version_result.normalized_version,
                        str(version_result.matched_range or ""),
                    ),
                )
            )
            issues.append(
                _issue(
                    "CAPABILITY_VERSION_MISMATCH",
                    f"manifest:{manifest_id}",
                    "The target version is outside the manifest's verified ranges.",
                    evidence=(target_version, *ranges),
                )
            )
        elif version_result.tier in {"unsupported", "unknown"} and target_version:
            coverage_gaps.append(
                {
                    "object_ref": f"manifest:{manifest_id}",
                    "reason": "target_version_unclassified",
                    "target_version": target_version,
                }
            )
            issues.append(
                _issue(
                    "COMPAT_TARGET_VERSION_UNSUPPORTED",
                    f"manifest:{manifest_id}",
                    "The target version is not covered by a declared compatibility tier.",
                    evidence=(
                        target_version,
                        version_result.normalized_version,
                    ),
                )
            )
            issues.append(
                _issue(
                    "CAPABILITY_VERSION_MISMATCH",
                    f"manifest:{manifest_id}",
                    "The target version is outside the manifest's verified ranges.",
                    evidence=(target_version, *ranges),
                )
            )

    node_results: list[dict[str, Any]] = []
    for node in sorted(_nodes(document), key=lambda item: str(item.get("node_id", ""))):
        node_id = str(node.get("node_id", ""))
        if node.get("enabled") is not True:
            node_results.append({"node_id": node_id, "status": "disabled"})
            continue
        capability = node.get("capability_ref")
        if not isinstance(capability, Mapping):
            issues.append(
                _issue(
                    "CAPABILITY_MISSING",
                    f"node:{node_id}",
                    "The node has no exact capability reference.",
                )
            )
            node_results.append({"node_id": node_id, "status": "unsupported"})
            continue
        manifest_id = str(capability.get("manifest_id", ""))
        if (
            capability.get("node_type") != node.get("node_type")
            or capability.get("node_type_version") != node.get("node_type_version")
        ):
            issues.append(
                _issue(
                    "CAPABILITY_VERSION_MISMATCH",
                    f"node:{node_id}",
                    "The node does not match its exact capability reference.",
                )
            )
        definition_pair = node_definitions.get(
            (manifest_id, str(node.get("node_type", "")))
        )
        if definition_pair is None:
            issues.append(
                _issue(
                    "CAPABILITY_MISSING",
                    f"node:{node_id}",
                    "The node type is absent from its locked capability manifest.",
                )
            )
            node_results.append({"node_id": node_id, "status": "unsupported"})
            continue
        manifest, definition = definition_pair
        if capability.get("manifest_hash") != manifest.get("manifest_hash"):
            issues.append(
                _issue(
                    "CAPABILITY_VERSION_MISMATCH",
                    f"node:{node_id}",
                    "The node capability hash differs from the installed manifest.",
                )
            )
        graph_version = node.get("node_type_version")
        definition_version = definition.get("node_type_version")
        graph_major = _major(graph_version)
        definition_major = _major(definition_version)
        if (
            graph_major is None
            or definition_major is None
            or graph_major != definition_major
        ):
            issues.append(
                _issue(
                    "CAPABILITY_VERSION_MISMATCH",
                    f"node:{node_id}",
                    "The node capability major version is incompatible.",
                    evidence=(str(graph_version), str(definition_version)),
                )
            )
            node_results.append(
                {
                    "node_id": node_id,
                    "status": "unsupported",
                    "node_type_version": graph_version,
                    "available_version": definition_version,
                }
            )
            continue
        status = "supported"
        if graph_version != definition_version:
            status = "needs_review"
        if node.get("compatibility_status") in {
            "unsupported",
            "capability_unavailable",
        }:
            status = "unsupported"
            issues.append(
                _issue(
                    "CAPABILITY_MISSING",
                    f"node:{node_id}",
                    "The graph already marks this node as unavailable.",
                )
            )
        if node.get("opaque") is not None:
            status = "unsupported"
            coverage_gaps.append(
                {
                    "object_ref": f"node:{node_id}",
                    "reason": "opaque_disposition_unresolved",
                }
            )
        if definition.get("preview_support") is not True:
            status = "needs_review"
            coverage_gaps.append(
                {
                    "object_ref": f"node:{node_id}",
                    "reason": "preview_support_not_declared",
                }
            )
        if target_version_tier == "review_required" and status == "supported":
            status = "needs_review"
        elif target_version_tier in {"opaque_only", "unsupported", "unknown"}:
            status = "unsupported"
        node_results.append(
            {
                "node_id": node_id,
                "status": status,
                "node_type_version": graph_version,
                "available_version": definition_version,
            }
        )

    normalized_selectors = [
        _normalize_selector_result(item)
        for item in (
            list(_binding_selectors(document))
            + [item for item in selector_results if isinstance(item, Mapping)]
            + [
                item
                for item in evidence.get("selector_results", [])
                if isinstance(item, Mapping)
            ]
        )
    ]
    normalized_selectors.sort(
        key=lambda item: (
            str(item.get("selector_id", "")),
            str(item.get("node_id", "")),
            canonical_json(item),
        )
    )
    if any(
        item.get("status") in _BLOCKING_SELECTOR_STATUSES
        for item in normalized_selectors
    ):
        issues.append(
            _issue(
                "COMPAT_SELECTOR_UNRESOLVED",
                "graph:selectors",
                "Every object selector must resolve to exactly one reviewed fixture object.",
            )
        )

    symbol_results = [
        _clone(item)
        for item in evidence.get("symbol_results", [])
        if isinstance(item, Mapping)
    ]
    symbol_results.sort(
        key=lambda item: (
            str(item.get("symbol", item.get("api_symbol", ""))),
            canonical_json(item),
        )
    )
    if any(
        str(item.get("status", "unknown")).lower() in _BLOCKING_SYMBOL_STATUSES
        for item in symbol_results
    ):
        issues.append(
            _issue(
                "CAPABILITY_VERSION_MISMATCH",
                "graph:symbols",
                "One or more required target symbols are unavailable or changed.",
            )
        )

    required = sorted(
        {
            str(item)
            for item in (
                list(required_context)
                + list(evidence.get("required_context", []))
                + list(target.get("required_context", []))
            )
            if str(item)
        }
    )
    resolved_context = _clone(dict(context or target.get("context", {}) or {}))
    missing_context = [
        item for item in required if resolved_context.get(item) in (None, "", [])
    ]
    for parameter in document.get("graph_parameters", []):
        if not isinstance(parameter, Mapping):
            continue
        type_ref = str(parameter.get("type_ref", ""))
        if type_ref in {"cam.length", "cam.angle", "cam.ratio"} and not parameter.get(
            "unit"
        ):
            missing_context.append(f"unit:{parameter.get('parameter_id', 'unknown')}")
    if missing_context or coverage_gaps:
        issues.append(
            _issue(
                "COMPAT_CONTEXT_INCOMPLETE",
                "target_profile:context",
                "Required unit, coordinate, project, or adapter context is incomplete.",
                evidence=tuple(sorted(set(missing_context))),
            )
        )

    deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in issues:
        key = (
            str(item.get("code", "")),
            str(item.get("object_ref", "")),
            canonical_json(item),
        )
        deduplicated[key] = item
    issues = [deduplicated[key] for key in sorted(deduplicated)]
    blocker_codes = sorted(
        {
            str(item["code"])
            for item in issues
            if item.get("severity") == "blocker"
        }
    )
    if not target_version and blocker_codes == ["COMPAT_TARGET_VERSION_UNKNOWN"]:
        status = "unknown"
    elif any(code in _HARD_COMPATIBILITY_CODES for code in blocker_codes):
        status = "incompatible"
    elif issues or any(item.get("status") == "needs_review" for item in node_results):
        status = "needs_review"
    else:
        status = "compatible"
    preview_eligible = (
        status == "compatible"
        and not blocker_codes
        and all(
            item.get("status") in {"supported", "disabled"} for item in node_results
        )
    )
    body = {
        "schema_version": 1,
        "contract": "cam.compatibility_report.v1",
        "graph_id": document["graph_id"],
        "revision_id": document["revision_id"],
        "product": document["product"],
        "source_profile": source,
        "target_profile": target,
        "capability_lock_hash": compute_capability_lock_hash(document),
        "checked_manifest_hashes": checked_manifest_hashes,
        "status": status,
        "node_results": node_results,
        "symbol_results": symbol_results,
        "selector_results": normalized_selectors,
        "coverage_gaps": sorted(coverage_gaps, key=canonical_json),
        "issues": issues,
        "blocker_codes": blocker_codes,
        "preview_eligible": preview_eligible,
        "checked_at": checked_at,
        "extensions": {
            "cam.flow_compatibility": {
                "semantic": False,
                "static_only": True,
                "transport": "none",
                "simulation_claimed": False,
                "version_results": sorted(
                    version_results,
                    key=lambda item: (
                        str(item.get("manifest_id", "")),
                        str(item.get("normalized_version", "")),
                    ),
                ),
            }
        },
    }
    if report_id is None:
        report_id = (
            "compatibility:" + canonical_hash(body).removeprefix("sha256:")[:24]
        )
    body["report_id"] = report_id
    return CompatibilityReport(body)


class ProjectionReport(FrozenContract):
    """Frozen T05 report; it is intentionally not a public T01 schema entry."""


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    recipe: Recipe | None
    report: ProjectionReport


def _blocker(code: str, object_ref: str, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "blocker",
        "object_ref": object_ref,
        "message": message,
    }


def _parameter_type(type_ref: str) -> str | None:
    return {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "enum": "enum",
        "path_ref": "path",
        "cam.length": "number",
        "cam.angle": "number",
        "cam.ratio": "number",
        "cam.object_selector": "object_selector",
    }.get(type_ref)


def _recipe_parameters(
    graph: Mapping[str, Any],
    blockers: list[dict[str, Any]],
) -> tuple[tuple[RecipeParameter, ...], dict[str, str]]:
    parameters: list[RecipeParameter] = []
    names: dict[str, str] = {}
    for value in sorted(
        (
            item
            for item in graph.get("graph_parameters", [])
            if isinstance(item, Mapping)
        ),
        key=lambda item: str(item.get("parameter_id", "")),
    ):
        parameter_id = str(value.get("parameter_id", ""))
        name = str(value.get("name", ""))
        value_type = _parameter_type(str(value.get("type_ref", "")))
        if not parameter_id or not _PARAMETER_NAME.fullmatch(name) or value_type is None:
            blockers.append(
                _blocker(
                    "PROJECTION_PARAMETER_UNSUPPORTED",
                    f"parameter:{parameter_id or 'unknown'}",
                    "The graph parameter cannot be represented by Recipe v1.",
                )
            )
            continue
        if name in names.values():
            blockers.append(
                _blocker(
                    "PROJECTION_PARAMETER_UNSUPPORTED",
                    f"parameter:{parameter_id}",
                    "Recipe v1 parameter names must be unique.",
                )
            )
            continue
        constraints = _clone(value.get("constraints", {}))
        if value.get("unit") is not None:
            constraints["unit"] = value["unit"]
        enum_values = tuple(constraints.get("enum", ())) if value_type == "enum" else ()
        parameters.append(
            RecipeParameter(
                name=name,
                value_type=value_type,
                required=value.get("required") is True,
                default=_clone(value.get("default")),
                samples=(),
                description=str(value.get("description", "")),
                constraints=constraints,
                enum_values=enum_values,
                source_event_refs=(),
            )
        )
        names[parameter_id] = name
    return tuple(parameters), names


def _step_arguments(
    node: Mapping[str, Any],
    parameter_names: Mapping[str, str],
    blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    arguments = _clone(node.get("configuration", {}))
    if not isinstance(arguments, dict):
        blockers.append(
            _blocker(
                "PROJECTION_NODE_UNSUPPORTED",
                f"node:{node.get('node_id', 'unknown')}",
                "Recipe v1 requires object-valued node configuration.",
            )
        )
        return {}
    for binding in sorted(
        (
            item
            for item in node.get("bindings", [])
            if isinstance(item, Mapping)
        ),
        key=lambda item: str(item.get("binding_id", "")),
    ):
        target = binding.get("target")
        if not isinstance(target, Mapping):
            blockers.append(
                _blocker(
                    "PROJECTION_BINDING_UNSUPPORTED",
                    f"node:{node.get('node_id', 'unknown')}",
                    "The binding target is not representable.",
                )
            )
            continue
        port_id = str(target.get("port_id", ""))
        if not port_id or port_id in arguments:
            blockers.append(
                _blocker(
                    "PROJECTION_BINDING_UNSUPPORTED",
                    f"binding:{binding.get('binding_id', 'unknown')}",
                    "The binding collides with configuration or has no port_id.",
                )
            )
            continue
        kind = binding.get("kind")
        if kind == "literal":
            literal = binding.get("literal")
            if not isinstance(literal, Mapping):
                blockers.append(
                    _blocker(
                        "PROJECTION_BINDING_UNSUPPORTED",
                        f"binding:{binding.get('binding_id', 'unknown')}",
                        "A literal binding has no typed literal value.",
                    )
                )
                continue
            arguments[port_id] = _clone(dict(literal))
        elif kind == "graph_parameter":
            parameter_id = str(binding.get("graph_parameter_id", ""))
            name = parameter_names.get(parameter_id)
            if name is None:
                blockers.append(
                    _blocker(
                        "PROJECTION_BINDING_UNSUPPORTED",
                        f"binding:{binding.get('binding_id', 'unknown')}",
                        "The binding references an unprojected graph parameter.",
                    )
                )
                continue
            arguments[port_id] = {"parameter": name}
        elif kind == "node_output":
            source_output = binding.get("source_output")
            if not isinstance(source_output, Mapping):
                blockers.append(
                    _blocker(
                        "PROJECTION_BINDING_UNSUPPORTED",
                        f"binding:{binding.get('binding_id', 'unknown')}",
                        "The node output binding is incomplete.",
                    )
                )
                continue
            arguments[port_id] = {"source_output": _clone(dict(source_output))}
        elif kind == "secret_ref":
            secret_ref = binding.get("secret_ref")
            if not isinstance(secret_ref, str) or not secret_ref:
                blockers.append(
                    _blocker(
                        "PROJECTION_BINDING_UNSUPPORTED",
                        f"binding:{binding.get('binding_id', 'unknown')}",
                        "Recipe projection may retain only an opaque secret reference.",
                    )
                )
                continue
            arguments[port_id] = {"secret_ref": secret_ref}
        else:
            blockers.append(
                _blocker(
                    "PROJECTION_BINDING_UNSUPPORTED",
                    f"binding:{binding.get('binding_id', 'unknown')}",
                    "The binding kind cannot be represented by Recipe v1.",
                )
            )
    return arguments


def _projection_manifest_definitions(
    manifests: Sequence[Mapping[str, Any] | CapabilityManifest] | None,
) -> dict[tuple[str, str], Mapping[str, Any]] | None:
    if manifests is None:
        return None
    _, definitions = _manifest_index(manifests)
    return {key: value[1] for key, value in definitions.items()}


def project_flow_to_recipe(
    graph: Mapping[str, Any] | FlowGraph,
    *,
    compatibility_report: Mapping[str, Any] | CompatibilityReport | None,
    manifests: Sequence[Mapping[str, Any] | CapabilityManifest] | None = None,
    checked_at: str = DEFAULT_TIMESTAMP,
    report_id: str | None = None,
) -> ProjectionResult:
    """Project only a proven linear graph. General graphs fail closed."""

    document = _raw(graph)
    compatibility = (
        None if compatibility_report is None else _raw(compatibility_report)
    )
    blockers: list[dict[str, Any]] = []
    if compatibility is None:
        blockers.append(
            _blocker(
                "COMPAT_CONTEXT_INCOMPLETE",
                "graph:compatibility",
                "Recipe projection requires a current CompatibilityReport.",
            )
        )
    elif (
        compatibility.get("graph_id") != document.get("graph_id")
        or compatibility.get("revision_id") != document.get("revision_id")
        or compatibility.get("status") != "compatible"
        or compatibility.get("preview_eligible") is not True
    ):
        blockers.append(
            _blocker(
                "COMPAT_CONTEXT_INCOMPLETE",
                "graph:compatibility",
                "The CompatibilityReport is stale or not preview-eligible.",
            )
        )

    flows = [
        flow for flow in document.get("flows", []) if isinstance(flow, Mapping)
    ]
    entry = next(
        (
            flow
            for flow in flows
            if flow.get("flow_id") == document.get("entry_flow_id")
            and flow.get("kind") == "main"
        ),
        None,
    )
    if entry is None:
        blockers.append(
            _blocker(
                "FLOW_ENTRY_INVALID",
                "graph:entry",
                "Recipe projection requires one explicit main entry flow.",
            )
        )
        entry = {"nodes": [], "edges": []}
    for flow in flows:
        if flow is entry:
            continue
        if any(
            isinstance(node, Mapping) and node.get("enabled") is True
            for node in flow.get("nodes", [])
        ):
            blockers.append(
                _blocker(
                    "PROJECTION_SUBFLOW_UNSUPPORTED",
                    f"flow:{flow.get('flow_id', 'unknown')}",
                    "Recipe v1 cannot preserve enabled subflow semantics.",
                )
            )

    all_nodes = {
        str(node.get("node_id", "")): node
        for node in entry.get("nodes", [])
        if isinstance(node, Mapping)
    }
    enabled = {
        node_id: node
        for node_id, node in all_nodes.items()
        if node.get("enabled") is True
    }
    definitions = _projection_manifest_definitions(manifests)
    node_results: list[dict[str, Any]] = []
    for node_id, node in sorted(enabled.items()):
        if node.get("node_type") in _BOUNDARY_NODE_TYPES:
            if (
                node.get("risk") == "blocked"
                or node.get("opaque") is not None
                or node.get("compatibility_status") != "supported"
            ):
                blockers.append(
                    _blocker(
                        "PROJECTION_NODE_UNSUPPORTED",
                        f"node:{node_id}",
                        "A structural boundary is blocked, opaque, or unsupported.",
                    )
                )
                node_results.append({"node_id": node_id, "status": "blocked"})
                continue
            node_results.append(
                {
                    "node_id": node_id,
                    "status": "projected",
                    "projection": "structural_boundary",
                }
            )
            continue
        if not str(node.get("node_type", "")).startswith(
            ("cam.", "nx.", "powermill.")
        ):
            blockers.append(
                _blocker(
                    "PROJECTION_NODE_UNSUPPORTED",
                    f"node:{node_id}",
                    "Recipe v1 cannot represent this node action namespace.",
                )
            )
            node_results.append({"node_id": node_id, "status": "blocked"})
            continue
        if (
            node.get("risk") == "blocked"
            or node.get("review_status") == "rejected"
            or node.get("opaque") is not None
            or node.get("compatibility_status") != "supported"
        ):
            blockers.append(
                _blocker(
                    "PROJECTION_NODE_UNSUPPORTED",
                    f"node:{node_id}",
                    "The enabled node is blocked, opaque, rejected, or unsupported.",
                )
            )
            node_results.append({"node_id": node_id, "status": "blocked"})
            continue
        capability = node.get("capability_ref")
        if not isinstance(capability, Mapping):
            blockers.append(
                _blocker(
                    "CAPABILITY_MISSING",
                    f"node:{node_id}",
                    "The enabled node has no exact capability reference.",
                )
            )
            node_results.append({"node_id": node_id, "status": "blocked"})
            continue
        if definitions is not None:
            definition = definitions.get(
                (
                    str(capability.get("manifest_id", "")),
                    str(node.get("node_type", "")),
                )
            )
            if definition is None or definition.get("recipe_projection_support") is not True:
                blockers.append(
                    _blocker(
                        "PROJECTION_NODE_UNSUPPORTED",
                        f"node:{node_id}",
                        "The locked capability does not declare Recipe projection support.",
                    )
                )
                node_results.append({"node_id": node_id, "status": "blocked"})
                continue
        node_results.append(
            {
                "node_id": node_id,
                "status": "projected",
                "projection": "recipe_step",
            }
        )

    control_edges = []
    data_edges = []
    dependency_edges = []
    edge_results: list[dict[str, Any]] = []
    for edge in sorted(
        (
            item
            for item in entry.get("edges", [])
            if isinstance(item, Mapping)
        ),
        key=lambda item: str(item.get("edge_id", "")),
    ):
        source = edge.get("source")
        target = edge.get("target")
        source_id = str(source.get("node_id", "")) if isinstance(source, Mapping) else ""
        target_id = str(target.get("node_id", "")) if isinstance(target, Mapping) else ""
        if source_id not in enabled or target_id not in enabled:
            blockers.append(
                _blocker(
                    "PROJECTION_EDGE_UNSUPPORTED",
                    f"edge:{edge.get('edge_id', 'unknown')}",
                    "An edge connected to a disabled or missing node cannot be projected.",
                )
            )
            edge_results.append(
                {"edge_id": edge.get("edge_id"), "status": "blocked"}
            )
            continue
        kind = edge.get("kind")
        if kind == "control":
            control_edges.append(edge)
        elif kind == "data":
            data_edges.append(edge)
        elif kind == "dependency":
            dependency_edges.append(edge)
        else:
            blockers.append(
                _blocker(
                    "PROJECTION_EDGE_UNSUPPORTED",
                    f"edge:{edge.get('edge_id', 'unknown')}",
                    "Recipe v1 cannot represent this edge kind.",
                )
            )

    incoming: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    outgoing: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for edge in control_edges:
        source_id = str(edge["source"]["node_id"])
        target_id = str(edge["target"]["node_id"])
        outgoing[source_id].append(edge)
        incoming[target_id].append(edge)
    if any(len(items) > 1 for items in incoming.values()) or any(
        len(items) > 1 for items in outgoing.values()
    ):
        blockers.append(
            _blocker(
                "PROJECTION_CONTROL_FLOW_UNSUPPORTED",
                "flow:control",
                "Recipe v1 cannot silently linearize branches or joins.",
            )
        )

    starts = [
        node_id
        for node_id in enabled
        if not incoming[node_id]
    ]
    explicit_starts = [
        node_id
        for node_id in starts
        if enabled[node_id].get("node_type") == "cam.flow.start"
    ]
    if len(explicit_starts) == 1:
        start = explicit_starts[0]
    elif len(starts) == 1:
        start = starts[0]
    else:
        start = ""
        if enabled:
            blockers.append(
                _blocker(
                    "PROJECTION_CONTROL_FLOW_UNSUPPORTED",
                    "flow:control",
                    "Recipe projection requires exactly one graph-derived control path.",
                )
            )

    order: list[str] = []
    visited: set[str] = set()
    current = start
    while current and current not in visited:
        order.append(current)
        visited.add(current)
        next_edges = outgoing.get(current, [])
        current = (
            str(next_edges[0]["target"]["node_id"])
            if len(next_edges) == 1
            else ""
        )
    if current in visited:
        blockers.append(
            _blocker(
                "PROJECTION_CONTROL_FLOW_UNSUPPORTED",
                "flow:control",
                "Recipe v1 cannot represent a control cycle.",
            )
        )
    if visited != set(enabled):
        blockers.append(
            _blocker(
                "PROJECTION_CONTROL_FLOW_UNSUPPORTED",
                "flow:control",
                "All enabled nodes must belong to one explicit control path.",
            )
        )
    position = {node_id: index for index, node_id in enumerate(order)}

    for edge in control_edges:
        edge_id = str(edge.get("edge_id", ""))
        source_id = str(edge["source"]["node_id"])
        target_id = str(edge["target"]["node_id"])
        if edge.get("priority") is not None:
            blockers.append(
                _blocker(
                    "PROJECTION_EDGE_UNSUPPORTED",
                    f"edge:{edge_id}",
                    "Recipe v1 cannot preserve control-edge priority.",
                )
            )
            edge_results.append({"edge_id": edge_id, "status": "blocked"})
        elif (
            edge.get("condition") is not None
            and enabled[target_id].get("node_type") in _BOUNDARY_NODE_TYPES
        ):
            blockers.append(
                _blocker(
                    "PROJECTION_EDGE_UNSUPPORTED",
                    f"edge:{edge_id}",
                    "A condition targeting a structural boundary cannot be projected.",
                )
            )
            edge_results.append({"edge_id": edge_id, "status": "blocked"})
        elif position.get(target_id) == position.get(source_id, -2) + 1:
            edge_results.append(
                {
                    "edge_id": edge_id,
                    "status": "projected",
                    "projection": (
                        "recipe_step_condition"
                        if edge.get("condition") is not None
                        else "recipe_order"
                    ),
                }
            )
        else:
            blockers.append(
                _blocker(
                    "PROJECTION_EDGE_UNSUPPORTED",
                    f"edge:{edge_id}",
                    "The control edge is not an adjacent step transition.",
                )
            )
            edge_results.append({"edge_id": edge_id, "status": "blocked"})

    for edge in dependency_edges:
        edge_id = str(edge.get("edge_id", ""))
        source_id = str(edge["source"]["node_id"])
        target_id = str(edge["target"]["node_id"])
        if position.get(source_id, 10**9) < position.get(target_id, -1):
            edge_results.append(
                {
                    "edge_id": edge_id,
                    "status": "projected",
                    "projection": "recipe_order_dependency",
                }
            )
        else:
            blockers.append(
                _blocker(
                    "PROJECTION_EDGE_UNSUPPORTED",
                    f"edge:{edge_id}",
                    "The dependency edge conflicts with the explicit control order.",
                )
            )
            edge_results.append({"edge_id": edge_id, "status": "blocked"})

    for edge in data_edges:
        edge_id = str(edge.get("edge_id", ""))
        source = edge["source"]
        target = edge["target"]
        target_node = enabled[str(target["node_id"])]
        represented = any(
            isinstance(binding, Mapping)
            and binding.get("kind") == "node_output"
            and isinstance(binding.get("target"), Mapping)
            and binding["target"].get("port_id") == target.get("port_id")
            and isinstance(binding.get("source_output"), Mapping)
            and binding["source_output"].get("node_id") == source.get("node_id")
            and binding["source_output"].get("port_id") == source.get("port_id")
            for binding in target_node.get("bindings", [])
        )
        if represented:
            edge_results.append(
                {
                    "edge_id": edge_id,
                    "status": "projected",
                    "projection": "recipe_node_output_binding",
                }
            )
        else:
            blockers.append(
                _blocker(
                    "PROJECTION_EDGE_UNSUPPORTED",
                    f"edge:{edge_id}",
                    "The data edge has no equivalent Recipe node-output binding.",
                )
            )
            edge_results.append({"edge_id": edge_id, "status": "blocked"})

    parameters, parameter_names = _recipe_parameters(document, blockers)
    steps: list[RecipeStep] = []
    for node_id in order:
        node = enabled[node_id]
        if node.get("node_type") in _BOUNDARY_NODE_TYPES:
            continue
        arguments = _step_arguments(node, parameter_names, blockers)
        incoming_edge = incoming.get(node_id, [])
        condition = (
            _clone(incoming_edge[0].get("condition"))
            if len(incoming_edge) == 1
            else None
        )
        steps.append(
            RecipeStep(
                step_id=node_id,
                order=len(steps) + 1,
                action=str(node.get("node_type", "")),
                enabled=True,
                risk=str(node.get("risk", "blocked")),
                review_status=str(node.get("review_status", "needs_review")),
                arguments=arguments,
                condition=condition,
                source_event_refs=(),
            )
        )

    blockers = sorted(
        {
            (item["code"], item["object_ref"], item["message"]): item
            for item in blockers
        }.values(),
        key=lambda item: (
            item["code"],
            item["object_ref"],
            item["message"],
        ),
    )
    recipe: Recipe | None = None
    recipe_hash: str | None = None
    if not blockers:
        required_gates = tuple(
            sorted(
                set(document.get("required_gates", ()))
                | set(REQUIRED_PRODUCTION_GATES)
            )
        )
        recipe_id = (
            "recipe:flow:"
            + hashlib.sha256(str(document["graph_id"]).encode("utf-8")).hexdigest()[:24]
        )
        recipe = Recipe(
            recipe_id=recipe_id,
            recipe_hash="",
            name=f"Flow {document['graph_id']}",
            product=str(document["product"]),
            status="review_required",
            target_versions=tuple(sorted(set(document.get("target_versions", ())))),
            source_session_ids=tuple(
                sorted(
                    str(item.get("asset_revision_id"))
                    for item in document.get("asset_refs", [])
                    if isinstance(item, Mapping) and item.get("asset_revision_id")
                )
            ),
            support={
                "projection": "deterministic",
                "flow_graph_id": document["graph_id"],
                "flow_revision_id": document["revision_id"],
            },
            parameters=parameters,
            steps=tuple(steps),
            required_gates=required_gates,
            created_at=checked_at,
            updated_at=checked_at,
            description="Review-first projection from a CAM FlowGraph.",
            project_conditions={"fixture_only": True, "transport": "none"},
        )
        recipe_hash = compute_recipe_hash(recipe)
        recipe = replace(recipe, recipe_hash=recipe_hash)

    status = "projected" if recipe is not None else "blocked"
    body = {
        "schema_version": 1,
        "contract": "cam.projection_report.v1",
        "graph_id": document["graph_id"],
        "revision_id": document["revision_id"],
        "status": status,
        "node_results": sorted(
            node_results,
            key=lambda item: str(item.get("node_id", "")),
        ),
        "edge_results": sorted(
            edge_results,
            key=lambda item: str(item.get("edge_id", "")),
        ),
        "blockers": blockers,
        "recipe_hash": recipe_hash,
        "preview_eligible": recipe is not None,
        "checked_at": checked_at,
        "extensions": {
            "cam.flow_projection": {
                "semantic": False,
                "transport": "none",
                "commands_sent": 0,
                "journal_executed": False,
                "macro_executed": False,
                "machine_output_count": 0,
            }
        },
    }
    if report_id is None:
        report_id = "projection:" + canonical_hash(body).removeprefix("sha256:")[:24]
    body["report_id"] = report_id
    return ProjectionResult(recipe, ProjectionReport(body))


__all__ = [
    "CompatibilityAdapter",
    "ProjectionReport",
    "ProjectionResult",
    "build_compatibility_report",
    "project_flow_to_recipe",
    "target_version_matches",
]
