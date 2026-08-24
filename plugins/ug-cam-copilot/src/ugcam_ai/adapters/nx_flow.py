from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..models import JsonValue
from .nx_journal import (
    NxFlowDefinitionEvidence,
    NxFlowNodeEvidence,
    NxJournalAdapter,
    NxJournalStaticAnalysis,
)


_NODE_TYPE_VERSION = "1.0.0"
_MAPPER_MANIFEST_ID = "manifest:nx:offline-static-mapper"
_MAPPER_MANIFEST_VERSION = "1.0.1"
_MAPPER_MANIFEST_HASH = "sha256:" + hashlib.sha256(
    b"cam.flow.nx.offline-static-mapper.v1.0.1"
).hexdigest()
_REQUIRED_GATES = [
    "recipe_review",
    "target_version_validation",
    "stable_selector_review",
    "unsupported_resolution",
    "cam_simulation",
    "collision_check",
    "machine_simulation",
    "shop_approval",
]


class NxFlowMapper:
    """Shape NX static evidence as cam.flowgraph.v1 JSON without shared T01 code."""

    def __init__(self, *, journal_adapter: NxJournalAdapter | None = None) -> None:
        self.journal_adapter = journal_adapter or NxJournalAdapter()

    def map_path(
        self,
        path: str | Path,
        *,
        target_version: str | None = None,
        target_symbols: Iterable[str] | None = None,
        target_contract: Any | None = None,
        selector_match_counts: Mapping[str, int] | None = None,
        asset_id: str | None = None,
        asset_revision_id: str | None = None,
        graph_id: str | None = None,
        revision_id: str | None = None,
    ) -> dict[str, JsonValue]:
        resolved_version, resolved_symbols, contract_evidence = _target_evidence(
            target_version=target_version,
            target_symbols=target_symbols,
            target_contract=target_contract,
        )
        analysis = self.journal_adapter.analyze(
            Path(path),
            target_version=resolved_version,
            target_symbols=resolved_symbols,
            selector_match_counts=selector_match_counts,
        )
        return self._shape(
            analysis,
            target_contract_evidence=contract_evidence,
            asset_id=asset_id,
            asset_revision_id=asset_revision_id,
            graph_id=graph_id,
            revision_id=revision_id,
        )

    def map_source(
        self,
        source: str,
        *,
        source_file: str = "<pasted-nx-journal>",
        session_name: str = "pasted",
        target_version: str | None = None,
        target_symbols: Iterable[str] | None = None,
        target_contract: Any | None = None,
        selector_match_counts: Mapping[str, int] | None = None,
        asset_id: str | None = None,
        asset_revision_id: str | None = None,
        graph_id: str | None = None,
        revision_id: str | None = None,
    ) -> dict[str, JsonValue]:
        resolved_version, resolved_symbols, contract_evidence = _target_evidence(
            target_version=target_version,
            target_symbols=target_symbols,
            target_contract=target_contract,
        )
        analysis = self.journal_adapter.analyze_source(
            source,
            source_file=source_file,
            session_name=session_name,
            target_version=resolved_version,
            target_symbols=resolved_symbols,
            selector_match_counts=selector_match_counts,
        )
        return self._shape(
            analysis,
            target_contract_evidence=contract_evidence,
            asset_id=asset_id,
            asset_revision_id=asset_revision_id,
            graph_id=graph_id,
            revision_id=revision_id,
        )

    def _shape(
        self,
        analysis: NxJournalStaticAnalysis,
        *,
        target_contract_evidence: Mapping[str, JsonValue] | None,
        asset_id: str | None,
        asset_revision_id: str | None,
        graph_id: str | None,
        revision_id: str | None,
    ) -> dict[str, JsonValue]:
        digest_suffix = analysis.source_digest.removeprefix("sha256:")[:16]
        asset_id = asset_id or f"asset:nx:{digest_suffix}"
        asset_revision_id = (
            asset_revision_id or f"asset-rev:nx:{digest_suffix}:1"
        )
        graph_id = graph_id or f"graph:nx:{digest_suffix}"
        revision_id = revision_id or f"revision:nx:{digest_suffix}:1"

        mapping_by_node: dict[str, str] = {}
        source_mappings: list[dict[str, JsonValue]] = []
        for flow in analysis.flows:
            for node in flow.nodes:
                mapping_id = f"map:{len(source_mappings) + 1:06d}"
                mapping_by_node[node.node_id] = mapping_id
                source_mappings.append(
                    _source_mapping(
                        analysis,
                        flow_id=flow.flow_id,
                        node=node,
                        mapping_id=mapping_id,
                        asset_id=asset_id,
                        asset_revision_id=asset_revision_id,
                    )
                )

        flows = [
            _flow_definition(
                flow,
                mapping_by_node=mapping_by_node,
                asset_revision_id=asset_revision_id,
            )
            for flow in analysis.flows
        ]
        diagnostics = [
            _diagnostic_with_mapping(item, mapping_by_node)
            for item in analysis.diagnostics
        ]
        selectors = [
            _evidence_with_mapping(item, mapping_by_node)
            for item in analysis.selectors
        ]
        unsupported = [
            _evidence_with_mapping(item, mapping_by_node)
            for item in analysis.unsupported_regions
        ]
        api_evidence = [
            _evidence_with_mapping(item, mapping_by_node)
            for item in analysis.api_symbol_evidence
        ]
        target_versions = (
            [str(target_contract_evidence["target_version"])]
            if target_contract_evidence
            and target_contract_evidence.get("target_version")
            else []
        )
        source_snapshot_payload = {
            "assets": [
                {
                    "asset_revision_id": asset_revision_id,
                    "content_hash": analysis.source_digest,
                    "encoding": analysis.encoding,
                    "bom": analysis.bom,
                    "newline_profile": analysis.newline_profile,
                }
            ]
        }
        semantic_payload = {
            "product": "nx",
            "target_versions": target_versions,
            "graph_parameters": [],
            "flows": [_semantic_flow(flow) for flow in flows],
            "capability_lock": [
                {
                    "manifest_id": _MAPPER_MANIFEST_ID,
                    "manifest_version": _MAPPER_MANIFEST_VERSION,
                    "manifest_hash": _MAPPER_MANIFEST_HASH,
                }
            ],
            "required_gates": _REQUIRED_GATES,
        }
        return {
            "schema_version": 1,
            "contract": "cam.flowgraph.v1",
            "graph_id": graph_id,
            "revision_id": revision_id,
            "parent_revision_id": None,
            "product": "nx",
            "target_versions": target_versions,
            "entry_flow_id": "flow:main",
            "graph_parameters": [],
            "flows": flows,
            "asset_refs": [
                {
                    "asset_id": asset_id,
                    "asset_revision_id": asset_revision_id,
                    "content_hash": analysis.source_digest,
                }
            ],
            "source_mappings": source_mappings,
            "capability_lock": [
                {
                    "manifest_id": _MAPPER_MANIFEST_ID,
                    "manifest_version": _MAPPER_MANIFEST_VERSION,
                    "manifest_hash": _MAPPER_MANIFEST_HASH,
                }
            ],
            "required_gates": list(_REQUIRED_GATES),
            "semantic_hash": _canonical_hash(semantic_payload),
            "source_snapshot_hash": _canonical_hash(source_snapshot_payload),
            "layout": {"nodes": {}},
            "extensions": {
                "nx": {
                    "source_file_alias": Path(analysis.source_file).name,
                    "parser_contract": dict(analysis.parser_contract),
                    "target_api_contract_evidence": (
                        dict(target_contract_evidence)
                        if target_contract_evidence is not None
                        else {
                            "target_version": None,
                            "status": "not_supplied",
                            "capability_claimed": False,
                        }
                    ),
                    "api_symbol_evidence": api_evidence,
                    "selectors": selectors,
                    "preconditions": [dict(item) for item in analysis.preconditions],
                    "unsupported_regions": unsupported,
                    "diagnostics": diagnostics,
                    "candidate": {
                        "storage": "memory_only",
                        "original_source_mutated": False,
                        "candidate_written": False,
                    },
                    "safety": {
                        "transport": "none",
                        "commands_sent": 0,
                        "journal_imported": False,
                        "journal_executed": False,
                        "journal_replayed": False,
                        "machine_output_count": 0,
                    },
                }
            },
        }


def map_nx_journal_source(source: str, **options: Any) -> dict[str, JsonValue]:
    return NxFlowMapper().map_source(source, **options)


def map_nx_journal(path: str | Path, **options: Any) -> dict[str, JsonValue]:
    return NxFlowMapper().map_path(path, **options)


def _flow_definition(
    flow: NxFlowDefinitionEvidence,
    *,
    mapping_by_node: Mapping[str, str],
    asset_revision_id: str,
) -> dict[str, JsonValue]:
    used_ports: dict[str, set[str]] = {node.node_id: set() for node in flow.nodes}
    for edge in flow.edges:
        used_ports[edge.source_node_id].add(edge.source_port)
        used_ports[edge.target_node_id].add(edge.target_port)
    nodes = [
        _flow_node(
            node,
            mapping_id=mapping_by_node[node.node_id],
            used_ports=used_ports[node.node_id],
            asset_revision_id=asset_revision_id,
        )
        for node in flow.nodes
    ]
    edges = [
        {
            "edge_id": edge.edge_id,
            "kind": edge.kind,
            "source": {
                "node_id": edge.source_node_id,
                "port_id": edge.source_port,
            },
            "target": {
                "node_id": edge.target_node_id,
                "port_id": edge.target_port,
            },
            "condition": edge.condition,
            "priority": edge.priority,
            "source_mapping_ids": [mapping_by_node[edge.source_node_id]],
            "extensions": {"nx": {"edge_role": edge.role}},
        }
        for edge in flow.edges
    ]
    return {
        "flow_id": flow.flow_id,
        "kind": flow.kind,
        "name": flow.name,
        "interface_ports": [],
        "parameter_ids": [],
        "nodes": nodes,
        "edges": edges,
        "source_mapping_ids": [
            mapping_by_node[node.node_id] for node in flow.nodes
        ],
    }


def _flow_node(
    node: NxFlowNodeEvidence,
    *,
    mapping_id: str,
    used_ports: set[str],
    asset_revision_id: str,
) -> dict[str, JsonValue]:
    opaque: dict[str, JsonValue] | None = None
    if node.opaque is not None:
        diagnostic_code = str(
            node.opaque.get("diagnostic_code") or "FLOW_UNKNOWN_SEMANTICS"
        )
        opaque = {
            "reason": str(node.opaque["reason"]),
            "asset_revision_id": asset_revision_id,
            "source_span_ids": [mapping_id],
            "content_hash": str(node.opaque["source_excerpt_hash"]),
            "round_trip_policy": "preserve_exact",
            "semantic_editable": False,
            "diagnostic_codes": [diagnostic_code],
            "extensions": {"nx": dict(node.opaque)},
        }
    return {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "node_type_version": _NODE_TYPE_VERSION,
        "enabled": True,
        "risk": node.risk,
        "review_status": node.review_status,
        "port_contract_refs": [
            f"{node.node_type}@{_NODE_TYPE_VERSION}#{port}"
            for port in sorted(used_ports)
        ],
        "bindings": [],
        "configuration": dict(node.configuration),
        "source_mapping_ids": [mapping_id],
        "fidelity": node.fidelity,
        "capability_ref": {
            "manifest_id": _MAPPER_MANIFEST_ID,
            "manifest_hash": _MAPPER_MANIFEST_HASH,
            "node_type": node.node_type,
            "node_type_version": _NODE_TYPE_VERSION,
        },
        "compatibility_status": node.compatibility_status,
        "opaque": opaque,
        "extensions": {
            "nx": {
                "static_evidence": True,
                "source_role": node.source_role,
                "executed_during_import": False,
            }
        },
    }


def _source_mapping(
    analysis: NxJournalStaticAnalysis,
    *,
    flow_id: str,
    node: NxFlowNodeEvidence,
    mapping_id: str,
    asset_id: str,
    asset_revision_id: str,
) -> dict[str, JsonValue]:
    span = node.source_span
    excerpt = analysis.source_bytes[span.start_byte : span.end_byte]
    return {
        "mapping_id": mapping_id,
        "asset_id": asset_id,
        "asset_revision_id": asset_revision_id,
        "source_digest": analysis.source_digest,
        "source_line": span.start_line,
        "source_span": span.to_dict(),
        "target": {"flow_id": flow_id, "node_id": node.node_id},
        "role": node.source_role,
        "mapping_quality": "synthetic" if span.start_byte == span.end_byte else "exact",
        "excerpt_hash": "sha256:" + hashlib.sha256(excerpt).hexdigest(),
        "extensions": {
            "nx": {
                "analysis_mode": "ast_token_static",
                "source_file_alias": Path(analysis.source_file).name,
            }
        },
    }


def _diagnostic_with_mapping(
    value: Mapping[str, JsonValue],
    mapping_by_node: Mapping[str, str],
) -> dict[str, JsonValue]:
    result = dict(value)
    object_ref = str(result.get("object_ref") or "")
    mapping_id = mapping_by_node.get(object_ref)
    result["source_mapping_ids"] = [mapping_id] if mapping_id else []
    return result


def _evidence_with_mapping(
    value: Mapping[str, JsonValue],
    mapping_by_node: Mapping[str, str],
) -> dict[str, JsonValue]:
    result = dict(value)
    node_id = result.get("node_id")
    if isinstance(node_id, str) and node_id in mapping_by_node:
        result["source_mapping_ids"] = [mapping_by_node[node_id]]
    return result


def _semantic_flow(flow: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    nodes = flow["nodes"]
    edges = flow["edges"]
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    return {
        "flow_id": flow["flow_id"],
        "kind": flow["kind"],
        "parameter_ids": flow["parameter_ids"],
        "nodes": [
            {
                "node_id": node["node_id"],
                "node_type": node["node_type"],
                "node_type_version": node["node_type_version"],
                "enabled": node["enabled"],
                "risk": node["risk"],
                "bindings": node["bindings"],
                "configuration": node["configuration"],
                "capability_ref": node["capability_ref"],
                "opaque": node["opaque"],
            }
            for node in nodes
            if isinstance(node, dict)
        ],
        "edges": [
            {
                "edge_id": edge["edge_id"],
                "kind": edge["kind"],
                "source": edge["source"],
                "target": edge["target"],
                "condition": edge["condition"],
                "priority": edge["priority"],
                "extensions": edge["extensions"],
            }
            for edge in edges
            if isinstance(edge, dict)
        ],
    }


def _target_evidence(
    *,
    target_version: str | None,
    target_symbols: Iterable[str] | None,
    target_contract: Any | None,
) -> tuple[str | None, tuple[str, ...] | None, dict[str, JsonValue] | None]:
    symbols: tuple[str, ...] | None = (
        tuple(sorted(str(item) for item in target_symbols))
        if target_symbols is not None
        else None
    )
    evidence: dict[str, JsonValue] | None = None
    if target_contract is not None:
        contract_symbols = getattr(target_contract, "symbols", None)
        contract_version = getattr(target_contract, "target_version", None)
        if contract_symbols is None or contract_version is None:
            raise TypeError(
                "target_contract must expose target_version and symbols evidence"
            )
        symbols = tuple(sorted(str(item) for item in contract_symbols))
        target_version = str(contract_version)
        evidence = {
            "target_version": target_version,
            "python_minor": getattr(target_contract, "python_minor", None),
            "metadata_source_alias": Path(
                str(getattr(target_contract, "metadata_source", "unknown"))
            ).name,
            "symbol_count": len(symbols),
            "symbol_set_digest": _canonical_hash(list(symbols)),
            "status": "static_stub_evidence_only",
            "capability_claimed": False,
            "uf_coverage_complete": False,
        }
    elif target_version is not None or symbols is not None:
        evidence = {
            "target_version": target_version,
            "symbol_count": len(symbols or ()),
            "symbol_set_digest": (
                _canonical_hash(list(symbols)) if symbols is not None else None
            ),
            "status": "static_symbol_evidence_only",
            "capability_claimed": False,
            "uf_coverage_complete": False,
        }
    return target_version, symbols, evidence


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
