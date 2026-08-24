from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO, Mapping, Sequence

from cam_automation.adapters.powermill_macro import (
    LosslessPowerMillDocument,
    PowerMillMacroParser,
    PowerMillOfflineImportError,
    PowerMillSourceToken,
    PowerMillStatement,
    contains_machine_ready_output,
)
from cam_automation.profiles.powermill import PowerMillProfile
from cam_automation.recipes import canonicalize


FlowGraph = dict[str, Any]
Diagnostic = dict[str, Any]

_REQUIRED_GATES = [
    "recipe_review",
    "target_version_validation",
    "cam_simulation",
    "collision_check",
    "shop_approval",
]
_NODE_VERSION = "1.0.0"
_MANIFEST_ID = "manifest:powermill:offline-import:v1"
_MANIFEST_VERSION = "1.0.1"
_MANIFEST_HASH = (
    "sha256:"
    + hashlib.sha256(
        b"cam-automation-studio:powermill:offline-import:v1.0.1"
    ).hexdigest()
)
_EMPTY_HASH = "sha256:" + hashlib.sha256(b"").hexdigest()
_VARIABLE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
_NAMESPACE = re.compile(r"^(?:cam|powermill)\.")
_SEVERITY_ORDER = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
_MAX_NODES = 500
_MAX_EDGES = 800
_MAX_NESTING_DEPTH = 64


def _hash_json(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonicalize(value)).hexdigest()}"


def _hash_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _decoded_value(token: PowerMillSourceToken) -> Any:
    raw = token.raw
    if token.kind == "string" and len(raw) >= 2:
        quote = raw[0]
        return raw[1:-1].replace(quote * 2, quote)
    if token.kind == "number":
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return raw
    return raw


class PowerMillFlowImporter:
    """Lower offline PowerMill evidence to the frozen FlowGraph JSON shape."""

    def __init__(
        self,
        *,
        source_name: str = "powermill-inline.mac",
        target_versions: Sequence[str] = (),
        include_sources: Mapping[str, object] | None = None,
        profile: PowerMillProfile | None = None,
    ) -> None:
        self.source_name = source_name
        self.target_versions = tuple(
            sorted({str(version).strip() for version in target_versions if str(version).strip()})
        )
        self.include_sources = dict(include_sources or {})
        self.profile = profile or PowerMillProfile()
        self.document: LosslessPowerMillDocument | None = None
        self.diagnostics: list[Diagnostic] = []
        self.automation_asset: dict[str, Any] | None = None

    def import_source(
        self,
        source: bytes | bytearray | str | Path | IO[str] | IO[bytes],
    ) -> FlowGraph:
        document = PowerMillMacroParser(
            source_name=self.source_name,
            profile=self.profile,
        ).parse(source)
        self.document = document
        if len(document.statements) + 2 > _MAX_NODES:
            raise PowerMillOfflineImportError(
                "RESOURCE_LIMIT_EXCEEDED",
                "PowerMill FlowGraph exceeds the 500 node limit.",
            )
        digest_suffix = document.content_hash.removeprefix("sha256:")[:16]
        asset_id = f"asset:powermill:{digest_suffix}"
        asset_revision_id = f"asset-rev:powermill:{digest_suffix}"
        graph_id = f"graph:powermill:{digest_suffix}"
        revision_id = f"revision:powermill:{digest_suffix}:1"

        self.automation_asset = self._automation_asset(
            document,
            asset_id=asset_id,
            asset_revision_id=asset_revision_id,
            target_versions=self.target_versions,
        )
        (
            open_to_close,
            close_to_open,
            structure_errors,
        ) = self._block_maps(document.statements)
        token_lookup = document.token_by_id()
        for statement in document.statements:
            if any(
                token_lookup[token_id].kind.startswith("error.")
                for token_id in statement.token_ids
                if token_id in token_lookup
            ):
                structure_errors.setdefault(
                    statement.index,
                    "unterminated or invalid PowerMill token",
                )
            if statement.metadata.get("parse_error"):
                structure_errors.setdefault(
                    statement.index,
                    str(statement.metadata["parse_error"]),
                )
        node_ids = {
            statement.index: f"node:powermill:{statement.index + 1:06d}"
            for statement in document.statements
        }
        (
            source_mappings,
            statement_mapping_ids,
            token_mapping_ids,
        ) = self._source_mappings(
            document,
            asset_id=asset_id,
            asset_revision_id=asset_revision_id,
            node_ids=node_ids,
        )

        nodes: list[dict[str, Any]] = [
            self._boundary_node(
                "node:powermill:start",
                "cam.flow.start",
                ["cam.flow.start@1.0.0#next"],
                ["map:powermill:start"],
            )
        ]
        diagnostics: list[Diagnostic] = []
        if not self.target_versions:
            diagnostics.append(
                self._diagnostic(
                    code="COMPAT_TARGET_VERSION_UNKNOWN",
                    severity="blocker",
                    message="The PowerMill target version was not recorded.",
                    object_ref=graph_id,
                    source_mapping_ids=[],
                    remediation="Bind an explicitly reviewed PowerMill target version.",
                    start_byte=-1,
                )
            )

        for statement in document.statements:
            node, node_diagnostics = self._statement_node(
                statement,
                document=document,
                node_id=node_ids[statement.index],
                mapping_ids=statement_mapping_ids[statement.index],
                token_mapping_ids=token_mapping_ids,
                token_lookup=token_lookup,
                structure_error=structure_errors.get(statement.index),
            )
            nodes.append(node)
            diagnostics.extend(node_diagnostics)

        nodes.append(
            self._boundary_node(
                "node:powermill:end",
                "cam.flow.end",
                ["cam.flow.end@1.0.0#in"],
                ["map:powermill:end"],
            )
        )

        edges, edge_diagnostics = self._edges(
            document.statements,
            node_ids=node_ids,
            open_to_close=open_to_close,
            close_to_open=close_to_open,
            statement_mapping_ids=statement_mapping_ids,
        )
        diagnostics.extend(edge_diagnostics)
        if len(edges) > _MAX_EDGES:
            raise PowerMillOfflineImportError(
                "RESOURCE_LIMIT_EXCEEDED",
                "PowerMill FlowGraph exceeds the 800 edge limit.",
            )
        diagnostics = sorted(
            diagnostics,
            key=lambda item: (
                int(item["extensions"]["start_byte"]),
                _SEVERITY_ORDER[item["severity"]],
                item["code"],
                item["object_ref"],
            ),
        )
        self.diagnostics = diagnostics

        flow = {
            "flow_id": "flow:powermill:main",
            "kind": "main",
            "name": "Main",
            "interface_ports": [],
            "parameter_ids": [],
            "nodes": nodes,
            "edges": edges,
            "source_mapping_ids": [
                mapping["mapping_id"] for mapping in source_mappings
            ],
        }
        capability_lock = [
            {
                "manifest_id": _MANIFEST_ID,
                "manifest_version": _MANIFEST_VERSION,
                "manifest_hash": _MANIFEST_HASH,
            }
        ]
        source_snapshot_hash = _hash_json(
            [
                {
                    "asset_revision_id": asset_revision_id,
                    "content_hash": document.content_hash,
                    "encoding": document.encoding,
                    "bom": document.bom,
                    "newline_profile": document.newline_profile,
                }
            ]
        )
        graph: FlowGraph = {
            "schema_version": 1,
            "contract": "cam.flowgraph.v1",
            "graph_id": graph_id,
            "revision_id": revision_id,
            "parent_revision_id": None,
            "product": "powermill",
            "target_versions": list(self.target_versions),
            "entry_flow_id": "flow:powermill:main",
            "graph_parameters": [],
            "flows": [flow],
            "asset_refs": [
                {
                    "asset_id": asset_id,
                    "asset_revision_id": asset_revision_id,
                    "content_hash": document.content_hash,
                }
            ],
            "source_mappings": source_mappings,
            "capability_lock": capability_lock,
            "required_gates": list(_REQUIRED_GATES),
            "semantic_hash": "",
            "source_snapshot_hash": source_snapshot_hash,
            "layout": {"nodes": {}},
            "extensions": {
                "powermill.offline_import": {
                    "semantic": False,
                    "source_artifact": {
                        "source_name": document.source_name,
                        "content_hash": document.content_hash,
                        "byte_length": len(document.original_bytes),
                        "encoding": document.encoding,
                        "bom": document.bom,
                        "newline_profile": document.newline_profile,
                        "raw_bytes_base64": base64.b64encode(
                            document.original_bytes
                        ).decode("ascii"),
                    },
                    "lossless_cst": {
                        "tokens": [token.as_json() for token in document.tokens],
                        "statements": [
                            statement.as_json() for statement in document.statements
                        ],
                    },
                    "diagnostics": diagnostics,
                    "coverage": {
                        "statement_count": len(document.statements),
                        "opaque_node_count": sum(
                            node["opaque"] is not None for node in nodes
                        ),
                        "commands_sent": 0,
                        "macro_executed": False,
                        "machine_output_count": 0,
                        "transport": "none",
                    },
                }
            },
        }
        graph["semantic_hash"] = _hash_json(self._semantic_payload(graph))
        return graph

    parse = import_source

    def round_trip_bytes(self) -> bytes:
        if self.document is None:
            raise RuntimeError("No PowerMill source has been imported.")
        return self.document.round_trip_bytes()

    @staticmethod
    def _automation_asset(
        document: LosslessPowerMillDocument,
        *,
        asset_id: str,
        asset_revision_id: str,
        target_versions: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "contract": "cam.automation_asset.v1",
            "asset_id": asset_id,
            "asset_revision_id": asset_revision_id,
            "product": "powermill",
            "asset_type": (
                "powermill_command_log"
                if Path(document.source_name).suffix.casefold() in {".log", ".jsonl"}
                else "powermill_macro"
            ),
            "display_name": document.source_name,
            "source_locator": f"local-content:{asset_revision_id}",
            "content_hash": document.content_hash,
            "byte_length": len(document.original_bytes),
            "encoding": document.encoding,
            "bom": document.bom,
            "newline_profile": document.newline_profile,
            "immutable": True,
            "source_origin": "unknown",
            "rights": {
                "status": "unreviewed",
                "evidence_ref": "unknown",
                "sharing_scope": "private",
                "redistribution_allowed": None,
                "network_egress_allowed": False,
                "binary_inspection": False,
            },
            "target_versions": list(target_versions),
            "runtime_modes": ["offline"],
            "dependencies": [],
            "imported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "extensions": {},
        }

    def _statement_node(
        self,
        statement: PowerMillStatement,
        *,
        document: LosslessPowerMillDocument,
        node_id: str,
        mapping_ids: list[str],
        token_mapping_ids: Mapping[str, str],
        token_lookup: Mapping[str, PowerMillSourceToken],
        structure_error: str | None,
    ) -> tuple[dict[str, Any], list[Diagnostic]]:
        kind = statement.kind
        node_type = statement.action
        risk = "safe"
        compatibility = "supported" if self.target_versions else "unknown"
        opaque_reason: str | None = None
        diagnostic_code: str | None = None
        remediation = "Review the source and bind an explicit supported capability."

        safety = self.profile.assess(statement.command)
        if safety.level == "blocked":
            risk = "blocked"
            compatibility = "unsupported"
            opaque_reason = "command is outside the permanent offline safety boundary"
            diagnostic_code = (
                "SAFETY_MACHINE_OUTPUT_FORBIDDEN"
                if any(
                    phrase in " ".join(safety.reasons).casefold()
                    for phrase in ("machine-ready", "postprocess", "machine control")
                )
                else "SAFETY_LIVE_EXECUTION_FORBIDDEN"
            )
            remediation = "Remove executable behavior; this importer never sends commands."
        elif safety.level == "review":
            risk = "review"

        if kind in {
            "command",
            "state",
            "selection",
            "nogui",
            "interaction",
            "macro_call",
            "docommand",
            "call",
        } and risk == "safe":
            if self.profile.assess_query(statement.command).level != "safe":
                risk = "review"

        if kind == "docommand":
            constant = statement.metadata.get("constant_command")
            if not isinstance(constant, str):
                risk = "blocked"
                compatibility = "unsupported"
                opaque_reason = "dynamic or tainted DOCOMMAND cannot be resolved statically"
                diagnostic_code = "FLOW_UNKNOWN_SEMANTICS"
                remediation = "Replace DOCOMMAND with a reviewed constant command."
            else:
                nested_safety = self.profile.assess(constant)
                if contains_machine_ready_output(constant):
                    risk = "blocked"
                    compatibility = "unsupported"
                    opaque_reason = "constant DOCOMMAND contains machine-ready output"
                    diagnostic_code = "SAFETY_MACHINE_OUTPUT_FORBIDDEN"
                elif nested_safety.level == "blocked":
                    risk = "blocked"
                    compatibility = "unsupported"
                    opaque_reason = "constant DOCOMMAND contains a blocked operation"
                    diagnostic_code = "SAFETY_LIVE_EXECUTION_FORBIDDEN"
                else:
                    risk = "review"

        if kind in {"unknown_opaque", "vendor_opaque"} and diagnostic_code is None:
            risk = "blocked"
            compatibility = "unsupported"
            opaque_reason = str(
                statement.metadata.get("unsupported_reason")
                or "unsupported PowerMill syntax"
            )
            diagnostic_code = "CAPABILITY_MISSING"
            remediation = (
                "Register reviewed target-version capability evidence or keep this span read-only."
            )

        if kind in {"include", "macro_call"} and diagnostic_code is None:
            path = str(statement.metadata.get("path") or "")
            if not self._include_available(path):
                risk = "blocked"
                compatibility = "unsupported"
                opaque_reason = "referenced include or macro was not supplied in the import package"
                diagnostic_code = "CAPABILITY_MISSING"
                remediation = "Import the referenced file explicitly as an immutable dependency."

        if structure_error is not None:
            risk = "blocked"
            compatibility = "unsupported"
            opaque_reason = structure_error
            diagnostic_code = "FLOW_SCHEMA_INVALID"
            remediation = "Repair the unmatched PowerMill control-flow delimiter."

        if not _NAMESPACE.match(node_type):
            raise PowerMillOfflineImportError(
                "FLOW_NAMESPACE_INVALID",
                "PowerMill lowering produced a non-canonical node namespace.",
            )

        semantic_tokens = [
            token_lookup[token_id]
            for token_id in statement.token_ids
            if token_id in token_lookup
            and token_lookup[token_id].kind in {"string", "number", "variable"}
            and self._span_contains(statement.command_span, token_lookup[token_id])
        ]
        source_values = [
            {
                "kind": token.kind,
                "value": _decoded_value(token),
            }
            for token in semantic_tokens
        ]
        configuration = {
            "action": statement.action,
            "statement_kind": kind,
            "operation": statement.operation,
            "recorded": self._recorded_metadata(statement),
            "source_values": source_values,
            "preconditions": {
                "project_identity": "unknown",
                "project_units": "unknown",
                "dialog_state": "unknown",
                "selection_state": "unknown",
                "active_entity": "unknown",
            },
        }
        if kind == "docommand" and statement.metadata.get("constant_command") is not None:
            constant = str(statement.metadata["constant_command"])
            configuration["recorded"]["constant_action"] = self.profile.action(constant)

        primary_mapping_ids = [
            mapping_id
            for mapping_id in mapping_ids
            if mapping_id.startswith("map:powermill:statement:")
        ]
        diagnostics: list[Diagnostic] = []
        opaque = None
        if opaque_reason is not None:
            opaque = {
                "reason": opaque_reason,
                "asset_revision_id": (
                    f"asset-rev:powermill:"
                    f"{document.content_hash.removeprefix('sha256:')[:16]}"
                ),
                "source_span_ids": primary_mapping_ids,
                "content_hash": self._span_hash(
                    document,
                    statement.command_span,
                ),
                "round_trip_policy": "preserve_exact",
                "semantic_editable": False,
                "diagnostic_codes": [diagnostic_code],
            }
            diagnostics.append(
                self._diagnostic(
                    code=diagnostic_code or "FLOW_UNKNOWN_SEMANTICS",
                    severity="blocker",
                    message=self._diagnostic_message(
                        diagnostic_code or "FLOW_UNKNOWN_SEMANTICS"
                    ),
                    object_ref=node_id,
                    source_mapping_ids=primary_mapping_ids,
                    remediation=remediation,
                    start_byte=int(statement.command_span["start_byte"]),
                )
            )

        node = {
            "node_id": node_id,
            "node_type": node_type,
            "node_type_version": _NODE_VERSION,
            "enabled": True,
            "risk": risk,
            "review_status": (
                "needs_review"
                if risk != "safe" or compatibility != "supported"
                else "unreviewed"
            ),
            "port_contract_refs": [
                f"{node_type}@{_NODE_VERSION}#in",
                f"{node_type}@{_NODE_VERSION}#next",
            ],
            "bindings": [],
            "configuration": configuration,
            "source_mapping_ids": mapping_ids,
            "fidelity": "F3" if opaque is not None else "F1",
            "capability_ref": {
                "manifest_id": _MANIFEST_ID,
                "manifest_hash": _MANIFEST_HASH,
                "node_type": node_type,
                "node_type_version": _NODE_VERSION,
            },
            "compatibility_status": compatibility,
            "opaque": opaque,
            "extensions": {
                "powermill.statement_id": statement.statement_id,
                "powermill.offline_only": True,
                "powermill.semantic_token_mapping_ids": [
                    token_mapping_ids[token.token_id]
                    for token in semantic_tokens
                    if token.token_id in token_mapping_ids
                ],
            },
        }
        return node, diagnostics

    @staticmethod
    def _recorded_metadata(statement: PowerMillStatement) -> dict[str, Any]:
        allowed = {
            "name",
            "parameters",
            "condition",
            "expression",
            "variable",
            "path",
            "arguments",
            "interaction",
            "recorded_value",
            "value_type",
            "gui_mode",
            "graphics_state",
            "recorded_prior_state",
            "selection_state",
            "active_entity",
            "prior_state",
            "dialog_state",
            "constant_command",
            "tainted",
            "target_version",
            "source_mode",
        }
        return {
            key: value
            for key, value in statement.metadata.items()
            if key in allowed
        }

    @staticmethod
    def _boundary_node(
        node_id: str,
        node_type: str,
        ports: list[str],
        mapping_ids: list[str],
    ) -> dict[str, Any]:
        return {
            "node_id": node_id,
            "node_type": node_type,
            "node_type_version": _NODE_VERSION,
            "enabled": True,
            "risk": "safe",
            "review_status": "unreviewed",
            "port_contract_refs": ports,
            "bindings": [],
            "configuration": {},
            "source_mapping_ids": mapping_ids,
            "fidelity": "F2",
            "capability_ref": {
                "manifest_id": _MANIFEST_ID,
                "manifest_hash": _MANIFEST_HASH,
                "node_type": node_type,
                "node_type_version": _NODE_VERSION,
            },
            "compatibility_status": "supported",
            "opaque": None,
            "extensions": {"powermill.offline_only": True},
        }

    def _source_mappings(
        self,
        document: LosslessPowerMillDocument,
        *,
        asset_id: str,
        asset_revision_id: str,
        node_ids: Mapping[int, str],
    ) -> tuple[list[dict[str, Any]], dict[int, list[str]], dict[str, str]]:
        mappings: list[dict[str, Any]] = []
        statement_mapping_ids: dict[int, list[str]] = {
            statement.index: [] for statement in document.statements
        }
        token_mapping_ids: dict[str, str] = {}
        mappings.append(
            self._mapping(
                "map:powermill:start",
                asset_id=asset_id,
                asset_revision_id=asset_revision_id,
                source_digest=document.content_hash,
                source_span={
                    "start_byte": 0,
                    "end_byte": 0,
                    "start_line": 1,
                    "start_column": 0,
                    "end_line": 1,
                    "end_column": 0,
                    "column_encoding": "unicode_scalar",
                },
                target={
                    "flow_id": "flow:powermill:main",
                    "node_id": "node:powermill:start",
                },
                role="control",
                mapping_quality="synthetic",
                excerpt_hash=_EMPTY_HASH,
            )
        )
        mappings.append(
            self._mapping(
                "map:powermill:end",
                asset_id=asset_id,
                asset_revision_id=asset_revision_id,
                source_digest=document.content_hash,
                source_span={
                    "start_byte": len(document.original_bytes),
                    "end_byte": len(document.original_bytes),
                    "start_line": document.end_line,
                    "start_column": document.end_column,
                    "end_line": document.end_line,
                    "end_column": document.end_column,
                    "column_encoding": "unicode_scalar",
                },
                target={
                    "flow_id": "flow:powermill:main",
                    "node_id": "node:powermill:end",
                },
                role="control",
                mapping_quality="synthetic",
                excerpt_hash=_EMPTY_HASH,
            )
        )

        for statement in document.statements:
            mapping_id = f"map:powermill:statement:{statement.index + 1:06d}"
            mappings.append(
                self._mapping(
                    mapping_id,
                    asset_id=asset_id,
                    asset_revision_id=asset_revision_id,
                    source_digest=document.content_hash,
                    source_span=statement.command_span,
                    target={
                        "flow_id": "flow:powermill:main",
                        "node_id": node_ids[statement.index],
                    },
                    role="primary",
                    mapping_quality="exact",
                    excerpt_hash=self._span_hash(document, statement.command_span),
                )
            )
            statement_mapping_ids[statement.index].append(mapping_id)

        token_to_statement: dict[str, PowerMillStatement] = {}
        for statement in document.statements:
            for token_id in statement.token_ids:
                token_to_statement[token_id] = statement
        semantic_ordinal: dict[int, int] = {}
        for token in document.tokens:
            statement = token_to_statement.get(token.token_id)
            if statement is not None:
                node_id = node_ids[statement.index]
            else:
                node_id = self._nearest_node(token, document.statements, node_ids)
            role = (
                "trivia"
                if token.kind.startswith(("trivia.", "envelope"))
                else "parameter"
            )
            target: dict[str, Any] = {
                "flow_id": "flow:powermill:main",
                "node_id": node_id,
            }
            if (
                role == "parameter"
                and statement is not None
                and token.kind in {"string", "number", "variable"}
                and self._span_contains(statement.command_span, token)
            ):
                ordinal = semantic_ordinal.get(statement.index, 0)
                semantic_ordinal[statement.index] = ordinal + 1
                target["property_path"] = f"/configuration/source_values/{ordinal}"
            mapping_id = f"map:powermill:token:{token.index:06d}"
            token_mapping_ids[token.token_id] = mapping_id
            mappings.append(
                self._mapping(
                    mapping_id,
                    asset_id=asset_id,
                    asset_revision_id=asset_revision_id,
                    source_digest=document.content_hash,
                    source_span=token.source_span,
                    target=target,
                    role=role,
                    mapping_quality="exact",
                    excerpt_hash=_hash_bytes(
                        document.original_bytes[token.start_byte:token.end_byte]
                    ),
                )
            )
            if statement is not None:
                statement_mapping_ids[statement.index].append(mapping_id)
        return mappings, statement_mapping_ids, token_mapping_ids

    @staticmethod
    def _mapping(
        mapping_id: str,
        *,
        asset_id: str,
        asset_revision_id: str,
        source_digest: str,
        source_span: Mapping[str, Any],
        target: Mapping[str, Any],
        role: str,
        mapping_quality: str,
        excerpt_hash: str,
    ) -> dict[str, Any]:
        return {
            "mapping_id": mapping_id,
            "asset_id": asset_id,
            "asset_revision_id": asset_revision_id,
            "source_digest": source_digest,
            "source_line": int(source_span["start_line"]),
            "source_span": dict(source_span),
            "target": dict(target),
            "role": role,
            "mapping_quality": mapping_quality,
            "excerpt_hash": excerpt_hash,
            "extensions": {},
        }

    @staticmethod
    def _nearest_node(
        token: PowerMillSourceToken,
        statements: Sequence[PowerMillStatement],
        node_ids: Mapping[int, str],
    ) -> str:
        for statement in statements:
            if int(statement.source_span["end_byte"]) >= token.start_byte:
                return node_ids[statement.index]
        if statements:
            return node_ids[statements[-1].index]
        return "node:powermill:start"

    @staticmethod
    def _span_contains(
        source_span: Mapping[str, Any],
        token: PowerMillSourceToken,
    ) -> bool:
        return (
            int(source_span["start_byte"]) <= token.start_byte
            and token.end_byte <= int(source_span["end_byte"])
        )

    @staticmethod
    def _span_hash(
        document: LosslessPowerMillDocument,
        source_span: Mapping[str, Any],
    ) -> str:
        return _hash_bytes(
            document.original_bytes[
                int(source_span["start_byte"]):int(source_span["end_byte"])
            ]
        )

    @staticmethod
    def _block_maps(
        statements: Sequence[PowerMillStatement],
    ) -> tuple[dict[int, int], dict[int, int], dict[int, str]]:
        stack: list[int] = []
        open_to_close: dict[int, int] = {}
        close_to_open: dict[int, int] = {}
        errors: dict[int, str] = {}
        for statement in statements:
            closes = int(statement.metadata.get("closes_block") or 0)
            opens = int(statement.metadata.get("opens_block") or 0)
            for _ in range(closes):
                if not stack:
                    errors[statement.index] = "unmatched closing control-flow delimiter"
                    continue
                opener = stack.pop()
                open_to_close[opener] = statement.index
                close_to_open[statement.index] = opener
            for _ in range(opens):
                stack.append(statement.index)
                if len(stack) > _MAX_NESTING_DEPTH:
                    raise PowerMillOfflineImportError(
                        "RESOURCE_LIMIT_EXCEEDED",
                        "PowerMill control-flow nesting exceeds 64 levels.",
                    )
        for opener in stack:
            errors[opener] = "unclosed PowerMill control-flow block"
        return open_to_close, close_to_open, errors

    def _edges(
        self,
        statements: Sequence[PowerMillStatement],
        *,
        node_ids: Mapping[int, str],
        open_to_close: Mapping[int, int],
        close_to_open: Mapping[int, int],
        statement_mapping_ids: Mapping[int, list[str]],
    ) -> tuple[list[dict[str, Any]], list[Diagnostic]]:
        edges: list[dict[str, Any]] = []
        diagnostics: list[Diagnostic] = []
        seen: set[tuple[str, str, str, str]] = set()
        end_node = "node:powermill:end"

        def node(index: int | None) -> str:
            if index is None or index >= len(statements):
                return end_node
            return node_ids[index]

        def add(
            source: str,
            target: str,
            *,
            kind: str = "control",
            condition: Mapping[str, Any] | None = None,
            priority: int | None = None,
            mapping_ids: Sequence[str] = (),
            role: str,
            source_port: str = "next",
            target_port: str = "in",
        ) -> None:
            condition_key = json.dumps(condition, sort_keys=True) if condition else ""
            key = (kind, source, target, f"{source_port}:{target_port}:{condition_key}")
            if key in seen:
                return
            seen.add(key)
            edges.append(
                {
                    "edge_id": f"edge:powermill:{len(edges) + 1:06d}",
                    "kind": kind,
                    "source": {"node_id": source, "port_id": source_port},
                    "target": {"node_id": target, "port_id": target_port},
                    "condition": dict(condition) if condition is not None else None,
                    "priority": priority,
                    "source_mapping_ids": list(mapping_ids),
                    "extensions": {"powermill.control_role": role},
                }
            )

        first = node(0)
        add(
            "node:powermill:start",
            first,
            role="entry",
            source_port="next",
            target_port="in",
        )
        for statement in statements:
            index = statement.index
            source_node = node_ids[index]
            mappings = statement_mapping_ids[index][:1]
            kind = statement.kind
            if kind == "return":
                add(source_node, end_node, role="return", mapping_ids=mappings)
                continue
            if kind in {"break", "continue"}:
                opener = self._nearest_control_opener(
                    index,
                    statements,
                    open_to_close,
                    include_switch=kind == "break",
                )
                if opener is None:
                    add(source_node, end_node, role=kind, mapping_ids=mappings)
                    diagnostics.append(
                        self._diagnostic(
                            code="FLOW_SCHEMA_INVALID",
                            severity="blocker",
                            message=f"{kind.upper()} has no enclosing control block.",
                            object_ref=source_node,
                            source_mapping_ids=mappings,
                            remediation="Place the statement inside a supported loop or switch.",
                            start_byte=int(statement.command_span["start_byte"]),
                        )
                    )
                elif kind == "continue":
                    add(
                        source_node,
                        node(opener),
                        role="continue",
                        mapping_ids=mappings,
                    )
                else:
                    add(
                        source_node,
                        node(open_to_close.get(opener, len(statements)) + 1),
                        role="break",
                        mapping_ids=mappings,
                    )
                continue
            if kind in {"if", "elseif"}:
                expression = str(statement.metadata.get("condition") or "")
                add(
                    source_node,
                    node(index + 1),
                    condition=self._condition(expression, True),
                    priority=0,
                    role="branch_true",
                    mapping_ids=mappings,
                )
                marker = self._branch_marker(
                    index,
                    statements,
                    open_to_close,
                )
                false_target = (
                    marker
                    if marker is not None
                    else self._branch_exit(index, statements, open_to_close)
                )
                add(
                    source_node,
                    node(false_target),
                    condition=self._condition(expression, False),
                    priority=1,
                    role="branch_false",
                    mapping_ids=mappings,
                )
                continue
            if kind in {"while", "foreach"}:
                expression = str(
                    statement.metadata.get("condition")
                    or statement.metadata.get("expression")
                    or ""
                )
                add(
                    source_node,
                    node(index + 1),
                    condition=self._condition(expression, True),
                    priority=0,
                    role="loop_body",
                    mapping_ids=mappings,
                )
                add(
                    source_node,
                    node(open_to_close.get(index, index) + 1),
                    condition=self._condition(expression, False),
                    priority=1,
                    role="loop_exit",
                    mapping_ids=mappings,
                )
                continue
            if kind == "do_while_condition":
                opener = close_to_open.get(index)
                expression = str(statement.metadata.get("condition") or "")
                add(
                    source_node,
                    node((opener + 1) if opener is not None else index + 1),
                    condition=self._condition(expression, True),
                    priority=0,
                    role="loop_back",
                    mapping_ids=mappings,
                )
                add(
                    source_node,
                    node(index + 1),
                    condition=self._condition(expression, False),
                    priority=1,
                    role="loop_exit",
                    mapping_ids=mappings,
                )
                continue
            if kind == "switch":
                close = open_to_close.get(index, len(statements))
                cases = [
                    item
                    for item in statements[index + 1:close + 1]
                    if item.kind in {"case", "default"}
                ]
                if not cases:
                    add(source_node, node(index + 1), role="next", mapping_ids=mappings)
                for priority, case in enumerate(cases):
                    case_expression = (
                        str(case.metadata.get("expression") or "")
                        if case.kind == "case"
                        else "default"
                    )
                    add(
                        source_node,
                        node(case.index),
                        condition={
                            "op": "case",
                            "selector": str(statement.metadata.get("expression") or ""),
                            "value": case_expression,
                        },
                        priority=priority,
                        role=("case" if case.kind == "case" else "default"),
                        mapping_ids=mappings,
                    )
                continue
            if kind == "block_end":
                opener = close_to_open.get(index)
                opener_kind = statements[opener].kind if opener is not None else ""
                if opener_kind in {"while", "foreach"}:
                    add(
                        source_node,
                        node(opener),
                        role="loop_back",
                        mapping_ids=mappings,
                    )
                elif opener_kind in {"if", "elseif", "else"}:
                    add(
                        source_node,
                        node(self._branch_exit(opener, statements, open_to_close)),
                        role="branch_merge",
                        mapping_ids=mappings,
                    )
                else:
                    add(
                        source_node,
                        node(index + 1),
                        role="next",
                        mapping_ids=mappings,
                    )
                continue

            target_index = index + 1
            if target_index < len(statements):
                target_statement = statements[target_index]
                previous_opener = close_to_open.get(target_index)
                if (
                    target_statement.kind in {"else", "elseif"}
                    and previous_opener is not None
                ):
                    target_index = self._branch_exit(
                        previous_opener,
                        statements,
                        open_to_close,
                    )
            add(
                source_node,
                node(target_index),
                role="next",
                mapping_ids=mappings,
            )

        function_nodes = {
            str(statement.metadata.get("name") or "").casefold(): statement.index
            for statement in statements
            if statement.kind == "function"
        }
        definitions: dict[str, int] = {}
        for statement in statements:
            source_node = node_ids[statement.index]
            if statement.kind == "call":
                name = str(statement.metadata.get("name") or "").casefold()
                target_index = function_nodes.get(name)
                enclosing_functions = {
                    opener
                    for opener, close in open_to_close.items()
                    if (
                        opener < statement.index <= close
                        and statements[opener].kind == "function"
                    )
                }
                if target_index is not None and target_index not in enclosing_functions:
                    add(
                        source_node,
                        node_ids[target_index],
                        kind="dependency",
                        role="function_call",
                        mapping_ids=statement_mapping_ids[statement.index][:1],
                        source_port="dependency",
                        target_port="declaration",
                    )
                elif target_index is not None:
                    diagnostics.append(
                        self._diagnostic(
                            code="FLOW_CONTROL_CYCLE_UNSUPPORTED",
                            severity="blocker",
                            message="Recursive PowerMill functions are not supported.",
                            object_ref=source_node,
                            source_mapping_ids=statement_mapping_ids[statement.index][:1],
                            remediation="Remove recursion or keep the function read-only.",
                            start_byte=int(statement.command_span["start_byte"]),
                        )
                    )

            reads = {
                variable.casefold()
                for variable in _VARIABLE.findall(
                    str(statement.metadata.get("expression") or statement.command)
                )
            }
            defined_name = (
                str(statement.metadata.get("name") or "").casefold()
                if statement.kind in {"variable_declare", "variable_assign"}
                else ""
            )
            if statement.kind == "variable_declare":
                reads.discard(defined_name)
            for variable in sorted(reads):
                definition = definitions.get(variable)
                if definition is None:
                    continue
                add(
                    node_ids[definition],
                    source_node,
                    kind="data",
                    role="variable_read",
                    mapping_ids=statement_mapping_ids[statement.index][:1],
                    source_port=f"value:{variable}",
                    target_port=f"variable:{variable}",
                )
            if defined_name:
                definitions[defined_name] = statement.index
        return edges, diagnostics

    @staticmethod
    def _condition(expression: str, expected: bool) -> dict[str, Any]:
        return {
            "op": "is_truthy" if expected else "is_falsy",
            "operand": {
                "kind": "source_expression",
                "text": expression,
            },
        }

    @staticmethod
    def _nearest_control_opener(
        index: int,
        statements: Sequence[PowerMillStatement],
        open_to_close: Mapping[int, int],
        *,
        include_switch: bool,
    ) -> int | None:
        allowed = {"while", "foreach", "do"}
        if include_switch:
            allowed.add("switch")
        candidates = [
            opener
            for opener, close in open_to_close.items()
            if opener < index <= close and statements[opener].kind in allowed
        ]
        return max(candidates) if candidates else None

    @staticmethod
    def _branch_marker(
        header: int,
        statements: Sequence[PowerMillStatement],
        open_to_close: Mapping[int, int],
    ) -> int | None:
        close = open_to_close.get(header)
        if close is None:
            return None
        if statements[close].kind in {"elseif", "else"}:
            return close
        candidate = close + 1
        if candidate < len(statements) and statements[candidate].kind in {"elseif", "else"}:
            return candidate
        return None

    @classmethod
    def _branch_exit(
        cls,
        header: int,
        statements: Sequence[PowerMillStatement],
        open_to_close: Mapping[int, int],
    ) -> int:
        current = header
        visited: set[int] = set()
        while current not in visited:
            visited.add(current)
            close = open_to_close.get(current)
            if close is None:
                return current + 1
            marker = cls._branch_marker(current, statements, open_to_close)
            if marker is None:
                return close + 1
            current = marker
        return len(statements)

    def _include_available(self, path: str) -> bool:
        normalized = path.replace("\\", "/").casefold()
        name = Path(path).name.casefold()
        return any(
            key.replace("\\", "/").casefold() == normalized
            or Path(key).name.casefold() == name
            for key in self.include_sources
        )

    @staticmethod
    def _diagnostic(
        *,
        code: str,
        severity: str,
        message: str,
        object_ref: str,
        source_mapping_ids: Sequence[str],
        remediation: str,
        start_byte: int,
    ) -> Diagnostic:
        return {
            "code": code,
            "severity": severity,
            "message": message,
            "object_ref": object_ref,
            "source_mapping_ids": list(source_mapping_ids),
            "remediation": remediation,
            "extensions": {"start_byte": start_byte},
        }

    @staticmethod
    def _diagnostic_message(code: str) -> str:
        return {
            "CAPABILITY_MISSING": "PowerMill syntax has no reviewed offline capability.",
            "FLOW_UNKNOWN_SEMANTICS": "PowerMill syntax cannot be resolved statically.",
            "FLOW_SCHEMA_INVALID": "PowerMill control-flow syntax is incomplete.",
            "SAFETY_LIVE_EXECUTION_FORBIDDEN": (
                "Executable PowerMill behavior is blocked by the offline importer."
            ),
            "SAFETY_MACHINE_OUTPUT_FORBIDDEN": (
                "Machine output or postprocessing behavior is permanently blocked."
            ),
        }.get(code, "PowerMill import was blocked.")

    @staticmethod
    def _semantic_payload(graph: Mapping[str, Any]) -> dict[str, Any]:
        flows = []
        for flow in graph["flows"]:
            nodes = []
            for node in flow["nodes"]:
                opaque = node["opaque"]
                opaque_semantics = (
                    None
                    if opaque is None
                    else {
                        "reason": opaque["reason"],
                        "content_hash": opaque["content_hash"],
                        "round_trip_policy": opaque["round_trip_policy"],
                        "semantic_editable": opaque["semantic_editable"],
                        "diagnostic_codes": opaque["diagnostic_codes"],
                    }
                )
                nodes.append(
                    {
                        "node_id": node["node_id"],
                        "node_type": node["node_type"],
                        "node_type_version": node["node_type_version"],
                        "enabled": node["enabled"],
                        "risk": node["risk"],
                        "bindings": node["bindings"],
                        "configuration": node["configuration"],
                        "opaque": opaque_semantics,
                    }
                )
            edges = [
                {
                    "edge_id": edge["edge_id"],
                    "kind": edge["kind"],
                    "source": edge["source"],
                    "target": edge["target"],
                    "condition": edge["condition"],
                    "priority": edge["priority"],
                }
                for edge in flow["edges"]
            ]
            flows.append(
                {
                    "flow_id": flow["flow_id"],
                    "kind": flow["kind"],
                    "parameter_ids": flow["parameter_ids"],
                    "interface_ports": flow["interface_ports"],
                    "nodes": nodes,
                    "edges": edges,
                }
            )
        return {
            "product": graph["product"],
            "target_versions": graph["target_versions"],
            "graph_parameters": graph["graph_parameters"],
            "flows": flows,
            "capability_lock": graph["capability_lock"],
            "required_gates": graph["required_gates"],
        }


def import_powermill_flow(
    source: bytes | bytearray | str | Path | IO[str] | IO[bytes],
    *,
    source_name: str = "powermill-inline.mac",
    target_versions: Sequence[str] = (),
    include_sources: Mapping[str, object] | None = None,
) -> FlowGraph:
    return PowerMillFlowImporter(
        source_name=source_name,
        target_versions=target_versions,
        include_sources=include_sources,
    ).import_source(source)


def round_trip_powermill_bytes(graph: Mapping[str, Any]) -> bytes:
    try:
        source = graph["extensions"]["powermill.offline_import"]["source_artifact"]
        encoded = source["raw_bytes_base64"]
        expected_hash = source["content_hash"]
        expected_length = int(source["byte_length"])
    except (KeyError, TypeError, ValueError) as error:
        raise PowerMillOfflineImportError(
            "FLOW_SCHEMA_INVALID",
            "FlowGraph does not contain a lossless PowerMill source artifact.",
        ) from error
    if not isinstance(encoded, str) or not isinstance(expected_hash, str):
        raise PowerMillOfflineImportError(
            "FLOW_SCHEMA_INVALID",
            "PowerMill source artifact metadata is invalid.",
        )
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise PowerMillOfflineImportError(
            "SOURCE_DIGEST_MISMATCH",
            "PowerMill source bytes could not be decoded.",
        ) from error
    if len(value) != expected_length or _hash_bytes(value) != expected_hash:
        raise PowerMillOfflineImportError(
            "SOURCE_DIGEST_MISMATCH",
            "PowerMill source bytes no longer match the immutable asset digest.",
        )
    return value
