from __future__ import annotations

import argparse
import copy
import ctypes
import gc
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
UG_SRC = ROOT / "plugins" / "ug-cam-copilot" / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(UG_SRC) not in sys.path:
    sys.path.insert(0, str(UG_SRC))

from cam_automation.asset_registry import AssetRegistry
from cam_automation.capability_registry import CapabilityRegistry
from cam_automation.flow_compatibility import build_compatibility_report
from cam_automation.flow_contracts import (
    canonicalize,
    compute_capability_lock_hash,
    compute_semantic_hash,
)
from cam_automation.flow_roundtrip import verify_round_trip
from cam_automation.flow_service import FlowService, ZERO_EXECUTION_FIELDS
from cam_automation.flow_validation import validate_contract, validate_flow_graph
from cam_automation.flow_versions import (
    CommandHistory,
    GraphCommand,
    four_layer_diff,
    prepare_graph_snapshot,
)
from cam_automation.adapters.powermill_macro import PowerMillMacroParser
from ugcam_ai.adapters.nx_journal import NxJournalAdapter


FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "cam-flow" / "performance"
FIXED_TIME = "2026-08-24T08:00:00Z"
FIXED_DATETIME = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
DEFAULT_SAMPLES = 20
DETERMINISM_RUNS = 20
NODE_COUNT = 500
EDGE_COUNT = 800
PARSE_LINE_COUNT = 10_000
MIB = 1024 * 1024
FORBIDDEN_SOURCE = re.compile(
    r"(?i)(?:\bG0?[0-3]\b|\bM\d{2,3}\b|\bCLSF\b|\bGCODE\b|"
    r"\bNC_CODE\b|post[\s_.-]*process|machine[\s_.-]*control)"
)
THRESHOLDS: dict[str, tuple[str, float, str]] = {
    "cold_open": ("p95_ms", 2_000.0, "ms"),
    "warm_open": ("p95_ms", 750.0, "ms"),
    "incremental_validation": ("p95_ms", 100.0, "ms"),
    "undo": ("p95_ms", 50.0, "ms"),
    "redo": ("p95_ms", 50.0, "ms"),
    "semantic_diff": ("p95_ms", 300.0, "ms"),
    "nx_static_parse": ("p95_ms", 1_500.0, "ms"),
    "powermill_static_parse": ("p95_ms", 1_500.0, "ms"),
    "fixture_preview": ("p95_ms", 3_000.0, "ms"),
    "cancel": ("p95_ms", 250.0, "ms"),
    "process_rss": ("max_mib", 350.0, "MiB"),
}


class FixedClock:
    def now(self) -> datetime:
        return FIXED_DATETIME

    def __call__(self) -> datetime:
        return FIXED_DATETIME


class PerformanceRoundTripAdapter:
    """Reparse the fixture line format without importing or executing product code."""

    def validate_candidate(
        self,
        candidate: bytes,
        *,
        target_graph: dict[str, Any],
    ) -> list[dict[str, Any]]:
        del target_graph
        if FORBIDDEN_SOURCE.search(candidate.decode("utf-8", errors="replace")):
            return [
                {
                    "code": "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
                    "severity": "blocker",
                    "message": "The fixture candidate contains forbidden machine output.",
                    "object_ref": "candidate:memory",
                    "source_mapping_ids": [],
                    "remediation": "Discard the candidate.",
                }
            ]
        self._records(candidate)
        return []

    def reparse_candidate(
        self,
        candidate: bytes,
        *,
        target_graph: dict[str, Any],
    ) -> dict[str, Any]:
        records = self._records(candidate)
        reparsed = copy.deepcopy(target_graph)
        nodes = reparsed["flows"][0]["nodes"]
        if len(records) != len(nodes):
            raise ValueError("Fixture reparse node count does not match the target graph.")
        for node, (node_id, fixture_index) in zip(nodes, records):
            if node["node_id"] != node_id:
                raise ValueError("Fixture reparse node order does not match the graph.")
            node["configuration"] = {"fixture_index": fixture_index}
        reparsed["semantic_hash"] = compute_semantic_hash(reparsed)
        return reparsed

    @staticmethod
    def _records(candidate: bytes) -> list[tuple[str, int]]:
        try:
            text = candidate.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Performance fixture source must remain UTF-8.") from error
        records: list[tuple[str, int]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            match = re.fullmatch(r"STEP (node:\d{4}) (\d+)", line)
            if match is None:
                raise ValueError(f"Invalid fixture source record at line {line_number}.")
            records.append((match.group(1), int(match.group(2))))
        if len(records) != NODE_COUNT:
            raise ValueError(f"Fixture reparse requires exactly {NODE_COUNT} records.")
        return records


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _asset_revision_id(asset_id: str, content_hash: str) -> str:
    payload = f"{asset_id}\0{content_hash}".encode("utf-8")
    return "asset-rev:" + hashlib.sha256(payload).hexdigest()


def _port(
    port_id: str,
    direction: str,
    *,
    edge_kind: str,
    type_ref: str,
) -> dict[str, Any]:
    return {
        "port_id": port_id,
        "direction": direction,
        "edge_kinds": [edge_kind],
        "type_ref": type_ref,
        "cardinality": "many",
        "required": False,
        "constraints": {},
        "sensitivity": "public",
        "extensions": {},
    }


def _performance_source() -> bytes:
    return "".join(
        f"STEP node:{index:04d} {index}\n" for index in range(NODE_COUNT)
    ).encode("ascii")


def _nx_source() -> bytes:
    lines = ["import NXOpen\n", "nx_ref_00001 = NXOpen\n"]
    for record_number in range(2, 5_001):
        if record_number % 1_000 == 0:
            lines.append(f"nx_call_{record_number:05d} = \\\n")
            lines.append("    NXOpen.Session.GetSession()\n")
        else:
            lines.append(f"nx_ref_{record_number:05d} = \\\n")
            lines.append("    NXOpen\n")
    if len(lines) != PARSE_LINE_COUNT:
        raise AssertionError("NX fixture generator must emit exactly 10,000 lines.")
    return "".join(lines).encode("ascii")


def _powermill_source() -> bytes:
    return "".join(
        f'PRINT "PERFORMANCE_{line_number:05d}"\n'
        for line_number in range(1, PARSE_LINE_COUNT + 1)
    ).encode("ascii")


def _manifest() -> dict[str, Any]:
    manifest_hash = _sha256_bytes(b"cam-flow-performance-manifest-v1")
    node_type = "nx.performance.step"
    return {
        "schema_version": 1,
        "contract": "cam.capability_manifest.v1",
        "manifest_id": "manifest:nx:performance:1",
        "manifest_version": "1.0.0",
        "manifest_hash": manifest_hash,
        "provider": {
            "provider_id": "cam-flow-performance-fixture",
            "display_name": "CAM Flow deterministic performance fixture",
            "claim_level": "fixture_verified",
        },
        "product": "nx",
        "adapter_kind": "manifest_only",
        "authorization": {
            "status": "fixture_verified",
            "evidence_ref": "fixture:cam-flow:performance:v1",
            "verified_by": "tests/flow_performance",
            "valid_until": None,
        },
        "target_version_ranges": ["NX 2406"],
        "execution_modes": ["offline", "read_only", "fixture_dry_run"],
        "permissions": ["read:selected-files"],
        "node_types": [
            {
                "node_type": node_type,
                "node_type_version": "1.0.0",
                "category": "fixture",
                "display_name": "Deterministic fixture step",
                "ports": [
                    _port(
                        "in",
                        "input",
                        edge_kind="control",
                        type_ref="cam.control",
                    ),
                    _port(
                        "out",
                        "output",
                        edge_kind="control",
                        type_ref="cam.control",
                    ),
                    _port(
                        "value",
                        "input",
                        edge_kind="data",
                        type_ref="number",
                    ),
                ],
                "configuration_schema": {
                    "type": "object",
                    "required": ["fixture_index"],
                    "properties": {"fixture_index": {"type": "integer"}},
                    "additionalProperties": False,
                },
                "static_risk_floor": "safe",
                "required_gates": [
                    "recipe_review",
                    "target_version_validation",
                    "cam_simulation",
                    "collision_check",
                    "shop_approval",
                ],
                "forbidden_modes": ["live"],
                "parse_support": True,
                "preview_support": True,
                "recipe_projection_support": True,
                "fidelity": "semantic_round_trip",
            }
        ],
        "prohibited_operations": [
            "live_journal",
            "live_macro",
            "nc",
            "gcode",
            "clsf",
            "postprocess",
            "machine_control",
        ],
        "evidence": [
            {
                "kind": "fixture_test",
                "ref": "tests/flow_performance",
                "version": "1",
                "checked_at": FIXED_TIME,
            }
        ],
        "revocation": None,
        "extensions": {
            "cam.flow.performance": {
                "semantic": False,
                "transport": "none",
            }
        },
    }


def _flow_bundle(source: bytes) -> dict[str, Any]:
    asset_id = "asset:nx:performance:500"
    content_hash = _sha256_bytes(source)
    revision_id = _asset_revision_id(asset_id, content_hash)
    manifest = _manifest()
    manifest_hash = manifest["manifest_hash"]
    source_lines = source.splitlines(keepends=True)
    nodes: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    offsets: list[int] = []
    cursor = 0
    for line in source_lines:
        offsets.append(cursor)
        cursor += len(line)
    for index, line in enumerate(source_lines):
        node_id = f"node:{index:04d}"
        mapping_id = f"mapping:{index:04d}"
        excerpt = line.rstrip(b"\r\n")
        start_byte = offsets[index]
        end_byte = start_byte + len(excerpt)
        nodes.append(
            {
                "node_id": node_id,
                "node_type": "nx.performance.step",
                "node_type_version": "1.0.0",
                "enabled": True,
                "risk": "safe",
                "review_status": "accepted",
                "port_contract_refs": [
                    "nx.performance.step@1.0.0#in",
                    "nx.performance.step@1.0.0#out",
                    "nx.performance.step@1.0.0#value",
                ],
                "bindings": [
                    {
                        "binding_id": f"binding:{index:04d}",
                        "target": {"node_id": node_id, "port_id": "value"},
                        "kind": "graph_parameter",
                        "literal": None,
                        "graph_parameter_id": "parameter:feed",
                        "source_output": None,
                        "secret_ref": None,
                        "source_mapping_ids": [mapping_id],
                        "extensions": {},
                    }
                ],
                "configuration": {"fixture_index": index},
                "source_mapping_ids": [mapping_id],
                "fidelity": "F2",
                "capability_ref": {
                    "manifest_id": manifest["manifest_id"],
                    "manifest_hash": manifest_hash,
                    "node_type": "nx.performance.step",
                    "node_type_version": "1.0.0",
                },
                "compatibility_status": "supported",
                "opaque": None,
                "extensions": {},
            }
        )
        mappings.append(
            {
                "mapping_id": mapping_id,
                "asset_id": asset_id,
                "asset_revision_id": revision_id,
                "source_digest": content_hash,
                "source_line": index + 1,
                "source_span": {
                    "start_byte": start_byte,
                    "end_byte": end_byte,
                    "start_line": index + 1,
                    "end_line": index + 1,
                    "start_column": 0,
                    "end_column": len(excerpt),
                    "column_encoding": "unicode_scalar",
                },
                "target": {
                    "flow_id": "flow:main",
                    "node_id": node_id,
                    "property_path": "/configuration/fixture_index",
                },
                "role": "primary",
                "mapping_quality": "exact",
                "excerpt_hash": _sha256_bytes(excerpt),
                "extensions": {},
            }
        )

    edges: list[dict[str, Any]] = []
    for index in range(NODE_COUNT - 1):
        edges.append(
            {
                "edge_id": f"edge:chain:{index:04d}",
                "kind": "control",
                "source": {"node_id": f"node:{index:04d}", "port_id": "out"},
                "target": {"node_id": f"node:{index + 1:04d}", "port_id": "in"},
                "condition": None,
                "priority": None,
                "source_mapping_ids": [],
                "extensions": {},
            }
        )
    for index in range(EDGE_COUNT - len(edges)):
        edges.append(
            {
                "edge_id": f"edge:skip:{index:04d}",
                "kind": "control",
                "source": {"node_id": f"node:{index:04d}", "port_id": "out"},
                "target": {"node_id": f"node:{index + 2:04d}", "port_id": "in"},
                "condition": None,
                "priority": None,
                "source_mapping_ids": [],
                "extensions": {},
            }
        )

    graph = {
        "schema_version": 1,
        "contract": "cam.flowgraph.v1",
        "graph_id": "graph:nx:performance:500",
        "revision_id": "revision:nx:performance:500:1",
        "parent_revision_id": None,
        "product": "nx",
        "target_versions": ["NX 2406"],
        "entry_flow_id": "flow:main",
        "graph_parameters": [
            {
                "parameter_id": "parameter:feed",
                "name": "Fixture feed",
                "type_ref": "number",
                "unit": None,
                "required": True,
                "default": 1.0,
                "constraints": {"minimum": 0.0, "maximum": 10.0},
                "evidence_mapping_ids": ["mapping:0000"],
                "review_status": "accepted",
            }
        ],
        "flows": [
            {
                "flow_id": "flow:main",
                "kind": "main",
                "name": "500 node deterministic performance flow",
                "interface_ports": [],
                "parameter_ids": ["parameter:feed"],
                "nodes": nodes,
                "edges": edges,
                "source_mapping_ids": [
                    f"mapping:{index:04d}" for index in range(NODE_COUNT)
                ],
            }
        ],
        "asset_refs": [
            {
                "asset_id": asset_id,
                "asset_revision_id": revision_id,
                "content_hash": content_hash,
            }
        ],
        "source_mappings": mappings,
        "capability_lock": [
            {
                "manifest_id": manifest["manifest_id"],
                "manifest_version": manifest["manifest_version"],
                "manifest_hash": manifest_hash,
            }
        ],
        "required_gates": [
            "recipe_review",
            "target_version_validation",
            "cam_simulation",
            "collision_check",
            "shop_approval",
        ],
        "semantic_hash": "sha256:" + ("0" * 64),
        "source_snapshot_hash": "sha256:" + ("0" * 64),
        "layout": {
            "nodes": {
                f"node:{index:04d}": {
                    "x": (index % 25) * 180,
                    "y": (index // 25) * 96,
                }
                for index in range(NODE_COUNT)
            }
        },
        "extensions": {
            "cam.flow.performance": {
                "semantic": False,
                "fixture_only": True,
                "transport": "none",
            }
        },
    }
    graph = prepare_graph_snapshot(graph).to_dict()
    target = {
        "product": "nx",
        "target_version": "NX 2406",
        "target_instance_id": "fixture:nx:performance:1",
        "project_id": "fixture-project:nx:performance",
        "project_snapshot_hash": _sha256_bytes(
            b"fixture-project:nx:performance:v1"
        ),
        "target_kind": "fixture",
        "permissions": ["read:selected-files"],
        "extensions": {
            "cam.flow.performance": {
                "semantic": False,
                "transport": "none",
            }
        },
    }
    return {
        "schema_version": 1,
        "contract": "cam.flow.performance_bundle.v1",
        "source_file": "flow-500-source.txt",
        "asset_request": {
            "asset_id": asset_id,
            "product": "nx",
            "asset_type": "nx_action_log",
            "display_name": "flow-500-source.txt",
            "source_locator": "selected-file",
            "source_origin": "user_authored",
            "rights": {
                "status": "legal_reviewed",
                "evidence_ref": "rights:cam-flow:performance:v1",
                "sharing_scope": "private",
                "redistribution_allowed": False,
                "network_egress_allowed": False,
                "binary_inspection": False,
            },
            "target_versions": ["NX 2406"],
            "runtime_modes": ["offline", "fixture_dry_run"],
            "dependencies": [],
            "extensions": {
                "cam.flow.performance": {
                    "semantic": False,
                    "transport": "none",
                }
            },
        },
        "manifest": manifest,
        "graph": graph,
        "target": target,
        "expected": {
            "nodes": NODE_COUNT,
            "edges": EDGE_COUNT,
            "source_mappings": NODE_COUNT,
            "source_lines": NODE_COUNT,
            "source_sha256": content_hash,
        },
    }


def generate_fixtures(destination: Path = FIXTURE_ROOT) -> dict[str, str]:
    destination.mkdir(parents=True, exist_ok=True)
    flow_source = _performance_source()
    nx_source = _nx_source()
    powermill_source = _powermill_source()
    files: dict[str, bytes] = {
        "flow-500-source.txt": flow_source,
        "flow-500.json": (
            json.dumps(
                _flow_bundle(flow_source),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
        "nx-10000.py": nx_source,
        "powermill-10000.mac": powermill_source,
    }
    for name, payload in files.items():
        (destination / name).write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "contract": "cam.flow.performance_fixture_set.v1",
        "fixtures": {
            name: {
                "sha256": _sha256_bytes(payload),
                "byte_length": len(payload),
                "line_count": (
                    len(payload.decode("utf-8").splitlines())
                    if name != "flow-500.json"
                    else None
                ),
            }
            for name, payload in sorted(files.items())
        },
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    (destination / "fixture-manifest.json").write_bytes(manifest_payload)
    return {
        **{name: _sha256_bytes(payload) for name, payload in files.items()},
        "fixture-manifest.json": _sha256_bytes(manifest_payload),
    }


def load_fixture_set(fixture_root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    manifest_path = fixture_root / "fixture-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixtures = manifest.get("fixtures", {})
    required = {
        "flow-500-source.txt",
        "flow-500.json",
        "nx-10000.py",
        "powermill-10000.mac",
    }
    if set(fixtures) != required:
        raise AssertionError("Performance fixture manifest has an unexpected file set.")
    for name, expected in fixtures.items():
        path = fixture_root / name
        payload = path.read_bytes()
        if _sha256_bytes(payload) != expected["sha256"]:
            raise AssertionError(f"Performance fixture hash mismatch: {name}")
        if len(payload) != expected["byte_length"]:
            raise AssertionError(f"Performance fixture byte length mismatch: {name}")
        if expected["line_count"] is not None:
            if len(payload.decode("utf-8").splitlines()) != expected["line_count"]:
                raise AssertionError(f"Performance fixture line count mismatch: {name}")

    bundle = json.loads((fixture_root / "flow-500.json").read_text(encoding="utf-8"))
    source = (fixture_root / bundle["source_file"]).read_bytes()
    graph = bundle["graph"]
    nodes = graph["flows"][0]["nodes"]
    edges = graph["flows"][0]["edges"]
    mappings = graph["source_mappings"]
    expected = bundle["expected"]
    actual_counts = {
        "nodes": len(nodes),
        "edges": len(edges),
        "source_mappings": len(mappings),
        "source_lines": len(source.decode("utf-8").splitlines()),
    }
    for key, value in actual_counts.items():
        if value != expected[key]:
            raise AssertionError(f"Performance fixture {key} is {value}, expected {expected[key]}.")
    if actual_counts != {
        "nodes": NODE_COUNT,
        "edges": EDGE_COUNT,
        "source_mappings": NODE_COUNT,
        "source_lines": NODE_COUNT,
    }:
        raise AssertionError("Performance fixture dimensions were reduced.")
    if _sha256_bytes(source) != expected["source_sha256"]:
        raise AssertionError("Performance graph source hash mismatch.")
    if graph["asset_refs"][0]["content_hash"] != expected["source_sha256"]:
        raise AssertionError("Performance graph asset reference is stale.")
    if compute_semantic_hash(graph) != graph["semantic_hash"]:
        raise AssertionError("Performance graph semantic hash is stale.")
    validation = validate_flow_graph(
        graph,
        capability_manifests=[bundle["manifest"]],
    )
    if not validation.valid:
        raise AssertionError(
            "Performance graph is invalid: "
            + json.dumps(validation.to_dict(), sort_keys=True)
        )
    for name in ("flow-500-source.txt", "nx-10000.py", "powermill-10000.mac"):
        if FORBIDDEN_SOURCE.search((fixture_root / name).read_text(encoding="utf-8")):
            raise AssertionError(f"Forbidden machine-output content in fixture: {name}")
    if len((fixture_root / "nx-10000.py").read_text(encoding="utf-8").splitlines()) != PARSE_LINE_COUNT:
        raise AssertionError("NX parse fixture must remain exactly 10,000 lines.")
    if len((fixture_root / "powermill-10000.mac").read_text(encoding="utf-8").splitlines()) != PARSE_LINE_COUNT:
        raise AssertionError("PowerMill parse fixture must remain exactly 10,000 lines.")
    return {
        "root": fixture_root,
        "manifest": manifest,
        "bundle": bundle,
        "source": source,
        "nx_source": (fixture_root / "nx-10000.py").read_text(encoding="utf-8"),
        "powermill_source": (fixture_root / "powermill-10000.mac").read_bytes(),
    }


def _build_service(
    fixtures: Mapping[str, Any],
    data_dir: Path,
) -> tuple[FlowService, dict[str, Any]]:
    bundle = fixtures["bundle"]
    clock = FixedClock()
    service = FlowService(
        asset_registry=AssetRegistry(data_dir / "assets", clock=clock),
        capability_registry=CapabilityRegistry(data_dir / "capabilities", clock=clock),
        fixture_targets=[bundle["target"]],
        clock=clock,
        cursor_secret=b"cam-flow-performance-v1",
    )
    asset = service.register_asset(
        fixtures["source"],
        **copy.deepcopy(bundle["asset_request"]),
    )
    expected_ref = bundle["graph"]["asset_refs"][0]
    for field in ("asset_id", "asset_revision_id", "content_hash"):
        if asset[field] != expected_ref[field]:
            raise AssertionError(f"Registered asset does not match graph field {field}.")
    service.register_capability(copy.deepcopy(bundle["manifest"]))
    saved = service.save_graph(copy.deepcopy(bundle["graph"]))
    validation = service.validate(
        graph_id=saved["graph_id"],
        revision_id=saved["revision_id"],
    )
    if not validation["valid"]:
        raise AssertionError("Service rejected the frozen performance graph.")
    return service, saved


def _adjacent_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    adjacent = copy.deepcopy(graph)
    adjacent["parent_revision_id"] = graph["revision_id"]
    adjacent["revision_id"] = "revision:nx:performance:500:2"
    adjacent["graph_parameters"][0]["default"] = 2.0
    adjacent["source_mappings"][0]["target"]["property_path"] = (
        "/configuration/fixture_index/adjacent"
    )
    return prepare_graph_snapshot(adjacent).to_dict()


def _diagnostic_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    invalid = copy.deepcopy(graph)
    invalid["flows"][0]["nodes"][1]["node_type"] = "powermill.performance.step"
    invalid["flows"][0]["edges"][0]["source"]["port_id"] = "in"
    invalid["source_mappings"][0]["source_digest"] = "sha256:" + ("0" * 64)
    invalid["semantic_hash"] = compute_semantic_hash(invalid)
    return invalid


def _digest_outputs(outputs: Sequence[bytes]) -> dict[str, Any]:
    digests = [_sha256_bytes(item) for item in outputs]
    unique = sorted(set(digests))
    return {
        "runs": len(outputs),
        "unique_outputs": len(unique),
        "sha256": unique[0] if len(unique) == 1 else None,
        "passed": len(unique) == 1,
    }


def run_determinism(
    fixtures: Mapping[str, Any],
    *,
    runs: int = DETERMINISM_RUNS,
) -> dict[str, Any]:
    if runs < DETERMINISM_RUNS:
        raise ValueError("Determinism gates require at least 20 runs.")
    bundle = fixtures["bundle"]
    graph = bundle["graph"]
    manifest = bundle["manifest"]
    adjacent = _adjacent_graph(graph)
    adjacent_validation = validate_flow_graph(
        adjacent,
        capability_manifests=[manifest],
    )
    if not adjacent_validation.valid:
        raise AssertionError("Adjacent performance revision is invalid.")
    invalid = _diagnostic_graph(graph)
    target_profile = copy.deepcopy(bundle["target"])
    adapter = PerformanceRoundTripAdapter()

    semantic_hashes: list[bytes] = []
    diagnostic_orders: list[bytes] = []
    source_diffs: list[bytes] = []
    round_trip_reports: list[bytes] = []
    compatibility_reports: list[bytes] = []
    for _ in range(runs):
        semantic_hashes.append(compute_semantic_hash(graph).encode("ascii"))
        diagnostics = validate_flow_graph(
            invalid,
            capability_manifests=[manifest],
        )
        if len(diagnostics.diagnostics) < 3:
            raise AssertionError("Diagnostic determinism fixture is not substantive.")
        diagnostic_orders.append(
            canonicalize([item.to_dict() for item in diagnostics.diagnostics])
        )
        diff = four_layer_diff(graph, adjacent)
        if not diff.source_changes or not diff.graph_changes:
            raise AssertionError("Adjacent fixture must exercise source and semantic diff.")
        source_diffs.append(diff.canonical_bytes())

        round_trip = verify_round_trip(
            graph,
            graph,
            fixtures["source"],
            [],
            adapter,
            required_fidelity="F0",
            checked_at=FIXED_TIME,
        ).report
        round_trip_validation = validate_contract(round_trip, "round_trip_report")
        if not round_trip_validation.valid:
            raise AssertionError("Generated RoundTripReport is invalid.")
        if (
            round_trip["status"] != "passed"
            or round_trip["candidate_reparsed"] is not True
        ):
            raise AssertionError("Round-trip determinism skipped candidate reparse.")
        round_trip_reports.append(round_trip.canonical_bytes())

        compatibility = build_compatibility_report(
            graph,
            [manifest],
            target_profile=target_profile,
            checked_at=FIXED_TIME,
        )
        compatibility_validation = validate_contract(
            compatibility,
            "compatibility_report",
        )
        if not compatibility_validation.valid:
            raise AssertionError("Generated CompatibilityReport is invalid.")
        compatibility_reports.append(compatibility.canonical_bytes())

    results = {
        "semantic_hash": _digest_outputs(semantic_hashes),
        "diagnostic_order": _digest_outputs(diagnostic_orders),
        "source_diff": _digest_outputs(source_diffs),
        "RoundTripReport": _digest_outputs(round_trip_reports),
        "CompatibilityReport": _digest_outputs(compatibility_reports),
    }
    results["passed"] = all(item["passed"] for item in results.values())
    return results


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("At least one sample is required.")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _stats(values: Sequence[float], *, suffix: str) -> dict[str, Any]:
    rounded = [round(value, 3) for value in values]
    return {
        "sample_count": len(values),
        f"p50_{suffix}": round(_nearest_rank(values, 0.50), 3),
        f"p95_{suffix}": round(_nearest_rank(values, 0.95), 3),
        f"max_{suffix}": round(max(values), 3),
        f"samples_{suffix}": rounded,
    }


def _measure(
    operation: Callable[[int], Any],
    samples: int,
    *,
    after_each: Callable[[], None] | None = None,
) -> list[float]:
    values: list[float] = []
    for index in range(samples):
        start = time.perf_counter_ns()
        operation(index)
        elapsed = time.perf_counter_ns() - start
        values.append(elapsed / 1_000_000)
        if after_each is not None:
            after_each()
    return values


def _apply_gate(name: str, stats: dict[str, Any]) -> dict[str, Any]:
    stat_name, limit, unit = THRESHOLDS[name]
    value = stats.get(stat_name)
    passed = value is not None and float(value) <= limit
    return {
        **stats,
        "gate": {
            "stat": stat_name,
            "maximum": limit,
            "unit": unit,
        },
        "passed": passed,
    }


def _assert_zero_execution(value: Mapping[str, Any]) -> None:
    if value.get("transport") != "none":
        raise AssertionError("Preview transport must remain none.")
    for field, expected in ZERO_EXECUTION_FIELDS.items():
        if value.get(field) != expected:
            raise AssertionError(f"Preview field {field} violated zero execution.")


def _rss_mib() -> float | None:
    if os.name == "nt":
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        query_memory = getattr(
            kernel32,
            "K32GetProcessMemoryInfo",
            psapi.GetProcessMemoryInfo,
        )
        query_memory.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        query_memory.restype = wintypes.BOOL
        ok = query_memory(
            kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        )
        return counters.WorkingSetSize / MIB if ok else None
    proc_status = Path("/proc/self/status")
    if proc_status.is_file():
        match = re.search(
            r"^VmRSS:\s+(\d+)\s+kB$",
            proc_status.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match:
            return int(match.group(1)) / 1024
    return None


def _total_ram_mib() -> float | None:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys / MIB
        return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / MIB
    except (AttributeError, OSError, ValueError):
        return None


def _cpu_name() -> str:
    candidates = [
        os.environ.get("PROCESSOR_IDENTIFIER", ""),
        platform.processor(),
        platform.machine(),
    ]
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                candidates.insert(0, str(winreg.QueryValueEx(key, "ProcessorNameString")[0]))
        except OSError:
            pass
    return next((item.strip() for item in candidates if item and item.strip()), "unknown")


def _node_version() -> str:
    bundled = Path(sys.executable).resolve().parents[1] / "node" / "bin" / (
        "node.exe" if os.name == "nt" else "node"
    )
    candidates: list[str | Path] = [bundled, "node"]
    for executable in candidates:
        try:
            result = subprocess.run(
                [str(executable), "--version"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() or "unknown"
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
    return "unavailable"


def environment_report() -> dict[str, Any]:
    ram = _total_ram_mib()
    return {
        "cpu": _cpu_name(),
        "logical_cpu_count": os.cpu_count(),
        "ram_total_mib": None if ram is None else round(ram, 1),
        "os": platform.platform(),
        "windows_release": platform.release() if os.name == "nt" else None,
        "windows_version": platform.version() if os.name == "nt" else None,
        "python": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "node": _node_version(),
    }


def _cold_worker(fixture_root: Path) -> None:
    fixtures = load_fixture_set(fixture_root)
    with tempfile.TemporaryDirectory(prefix="cam-flow-cold-") as directory:
        service, graph = _build_service(fixtures, Path(directory))
        opened = service.get_graph(graph["graph_id"], graph["revision_id"])
        validation = service.validate(
            graph_id=opened["graph_id"],
            revision_id=opened["revision_id"],
        )
        if not validation["valid"]:
            raise AssertionError("Cold worker validation failed.")


def run_benchmarks(
    *,
    samples: int = DEFAULT_SAMPLES,
    determinism_runs: int = DETERMINISM_RUNS,
    fixture_root: Path = FIXTURE_ROOT,
) -> dict[str, Any]:
    if samples < DEFAULT_SAMPLES:
        raise ValueError("Release performance gates require at least 20 samples.")
    if determinism_runs < DETERMINISM_RUNS:
        raise ValueError("Determinism gates require at least 20 runs.")
    fixtures = load_fixture_set(fixture_root)
    determinism = run_determinism(fixtures, runs=determinism_runs)
    bundle = fixtures["bundle"]
    graph = bundle["graph"]
    manifest = bundle["manifest"]
    adjacent = _adjacent_graph(graph)
    command = GraphCommand.replace(
        "command:performance:parameter",
        "semantic",
        "/graph_parameters/0/default",
        1.0,
        2.0,
    )
    benchmark_results: dict[str, Any] = {}

    worker_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--cold-worker",
        str(fixture_root),
    ]

    def cold_open(_index: int) -> None:
        subprocess.run(
            worker_command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        )

    cold_values = _measure(cold_open, samples)
    benchmark_results["cold_open"] = _apply_gate(
        "cold_open",
        _stats(cold_values, suffix="ms"),
    )

    with tempfile.TemporaryDirectory(prefix="cam-flow-performance-") as directory:
        service, saved = _build_service(fixtures, Path(directory))

        def warm_open(_index: int) -> None:
            opened = service.get_graph(saved["graph_id"], saved["revision_id"])
            validation = service.validate(
                graph_id=opened["graph_id"],
                revision_id=opened["revision_id"],
            )
            if not validation["valid"] or len(opened["flows"][0]["nodes"]) != NODE_COUNT:
                raise AssertionError("Warm open did not fully validate the fixture.")

        warm_values = _measure(warm_open, samples)
        benchmark_results["warm_open"] = _apply_gate(
            "warm_open",
            _stats(warm_values, suffix="ms"),
        )

        def incremental_validation(_index: int) -> None:
            changed = command.apply(graph)
            validation = validate_flow_graph(
                changed,
                capability_manifests=[manifest],
            )
            if not validation.valid:
                raise AssertionError("Incremental parameter validation failed.")

        incremental_values = _measure(incremental_validation, samples)
        benchmark_results["incremental_validation"] = _apply_gate(
            "incremental_validation",
            _stats(incremental_values, suffix="ms"),
        )

        undo_values: list[float] = []
        redo_values: list[float] = []
        for _ in range(samples):
            history = CommandHistory(graph)
            history.execute(command)
            start = time.perf_counter_ns()
            undone = history.undo()
            undo_values.append((time.perf_counter_ns() - start) / 1_000_000)
            if compute_semantic_hash(undone) != graph["semantic_hash"]:
                raise AssertionError("Undo did not restore the semantic hash.")
            start = time.perf_counter_ns()
            redone = history.redo()
            redo_values.append((time.perf_counter_ns() - start) / 1_000_000)
            if compute_semantic_hash(redone) != adjacent["semantic_hash"]:
                raise AssertionError("Redo did not restore the changed semantic hash.")
        benchmark_results["undo"] = _apply_gate(
            "undo",
            _stats(undo_values, suffix="ms"),
        )
        benchmark_results["redo"] = _apply_gate(
            "redo",
            _stats(redo_values, suffix="ms"),
        )

        def semantic_diff(_index: int) -> None:
            diff = four_layer_diff(graph, adjacent)
            if not diff.source_changes or not diff.graph_changes:
                raise AssertionError("Semantic diff omitted a frozen layer.")
            diff.canonical_bytes()

        diff_values = _measure(semantic_diff, samples)
        benchmark_results["semantic_diff"] = _apply_gate(
            "semantic_diff",
            _stats(diff_values, suffix="ms"),
        )

        nx_source = fixtures["nx_source"]

        def nx_static_parse(_index: int) -> None:
            events = NxJournalAdapter().parse_source(
                nx_source,
                source_file="nx-10000.py",
                session_name="performance",
            )
            if len(events) < 4_500:
                raise AssertionError("NX parser did not inspect the 10,000-line fixture.")

        nx_values = _measure(nx_static_parse, samples)
        benchmark_results["nx_static_parse"] = _apply_gate(
            "nx_static_parse",
            _stats(nx_values, suffix="ms"),
        )

        powermill_source = fixtures["powermill_source"]

        def powermill_static_parse(_index: int) -> None:
            document = PowerMillMacroParser(
                source_name="powermill-10000.mac"
            ).parse(powermill_source)
            if len(document.statements) != PARSE_LINE_COUNT:
                raise AssertionError("PowerMill parser did not inspect all 10,000 lines.")
            if document.round_trip_bytes() != powermill_source:
                raise AssertionError("PowerMill parser lost source fidelity.")

        powermill_values = _measure(powermill_static_parse, samples)
        benchmark_results["powermill_static_parse"] = _apply_gate(
            "powermill_static_parse",
            _stats(powermill_values, suffix="ms"),
        )

        compatibility = service.check_compatibility(
            saved["graph_id"],
            bundle["target"],
            revision_id=saved["revision_id"],
        )
        if not compatibility["preview_eligible"]:
            raise AssertionError("Performance graph is not fixture-preview eligible.")
        round_trip = verify_round_trip(
            graph,
            graph,
            fixtures["source"],
            [],
            PerformanceRoundTripAdapter(),
            required_fidelity="F0",
            checked_at=FIXED_TIME,
        ).report
        version = service.create_version(
            graph_id=saved["graph_id"],
            revision_id=saved["revision_id"],
            status="reviewed_for_fixture",
            author_ref="operator:performance",
            message="Reviewed deterministic performance fixture.",
            round_trip_report_id=round_trip["report_id"],
            compatibility_report_id=compatibility["report_id"],
        )
        preview_request = {
            "graph_id": saved["graph_id"],
            "revision_id": saved["revision_id"],
            "flow_version_id": version["version_id"],
            "execution_mode": "fixture_dry_run",
            "transport": "none",
            "target": {
                key: bundle["target"][key]
                for key in (
                    "product",
                    "target_version",
                    "target_instance_id",
                    "project_id",
                    "project_snapshot_hash",
                    "target_kind",
                )
            },
            "hashes": {
                "source_snapshot_hash": saved["source_snapshot_hash"],
                "semantic_hash": saved["semantic_hash"],
                "capability_lock_hash": compute_capability_lock_hash(saved),
                "reviewed_recipe_hash": None,
            },
        }
        rss_values: list[float] = []

        def fixture_preview(index: int) -> None:
            request = copy.deepcopy(preview_request)
            request["plan_id"] = f"preview:performance:run:{index:04d}"
            plan = service.create_preview_plan(request)
            result = service.execute_preview_plan(plan["plan_id"])
            if result["status"] != "succeeded" or len(result["steps"]) != NODE_COUNT:
                raise AssertionError("500-node fixture preview did not complete.")
            _assert_zero_execution(result)

        def record_rss() -> None:
            gc.collect()
            value = _rss_mib()
            if value is not None:
                rss_values.append(value)

        preview_values = _measure(
            fixture_preview,
            samples,
            after_each=record_rss,
        )
        benchmark_results["fixture_preview"] = _apply_gate(
            "fixture_preview",
            _stats(preview_values, suffix="ms"),
        )

        cancel_values: list[float] = []
        for index in range(samples):
            request = copy.deepcopy(preview_request)
            request["plan_id"] = f"preview:performance:cancel:{index:04d}"
            plan = service.create_preview_plan(request)
            start = time.perf_counter_ns()
            cancelled = service.cancel_preview_plan(plan["plan_id"])
            cancel_values.append((time.perf_counter_ns() - start) / 1_000_000)
            if cancelled["status"] != "cancelled":
                raise AssertionError("Fixture preview cancellation did not complete.")
            _assert_zero_execution(cancelled)
        benchmark_results["cancel"] = _apply_gate(
            "cancel",
            _stats(cancel_values, suffix="ms"),
        )

        if rss_values:
            benchmark_results["process_rss"] = _apply_gate(
                "process_rss",
                _stats(rss_values, suffix="mib"),
            )
            benchmark_results["process_rss"]["method"] = (
                "Python benchmark process working set after each 500-node preview"
            )
        else:
            benchmark_results["process_rss"] = {
                "available": False,
                "passed": None,
                "gate": {
                    "stat": "max_mib",
                    "maximum": THRESHOLDS["process_rss"][1],
                    "unit": "MiB",
                },
            }

    failures = [
        name
        for name, result in benchmark_results.items()
        if result.get("passed") is False
    ]
    if not determinism["passed"]:
        failures.append("determinism")
    status = "PASS" if not failures else "PERF-BLOCKED"
    fixture_report = {
        name: {
            **metadata,
            "path": str((fixture_root / name).resolve()),
        }
        for name, metadata in fixtures["manifest"]["fixtures"].items()
    }
    return {
        "schema_version": 1,
        "status": status,
        "environment": environment_report(),
        "methodology": {
            "clock": "time.perf_counter_ns",
            "percentile": "nearest-rank",
            "samples_per_timing_scenario": samples,
            "determinism_runs": determinism_runs,
            "cold": (
                "new Python process, module/schema caches cold, fixture hash/load, "
                "new registries, service save, get, and full validation"
            ),
            "warm": (
                "same loaded FlowService; get_graph followed by full schema/type/"
                "source-map/safety validation on every sample"
            ),
            "incremental_validation": (
                "one GraphCommand parameter edit followed by uncached full graph validation"
            ),
            "preview": (
                "create and execute a unique 500-node fixture PreviewPlan; "
                "transport=none and all zero-execution fields asserted"
            ),
            "memory": (
                "benchmark-process working set after each completed 500-node preview"
            ),
        },
        "fixtures": fixture_report,
        "benchmarks": benchmark_results,
        "determinism": determinism,
        "safety": {
            "transport": "none",
            **ZERO_EXECUTION_FIELDS,
            "cam_started_or_connected": False,
            "journal_or_macro_executed": False,
            "machine_output_generated": False,
        },
        "failures": failures,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic CAM Flow release performance barriers."
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help="Timing samples per scenario; values below 20 are rejected.",
    )
    parser.add_argument(
        "--determinism-runs",
        type=int,
        default=DETERMINISM_RUNS,
        help="Repeated deterministic outputs; values below 20 are rejected.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    parser.add_argument(
        "--generate-fixtures",
        action="store_true",
        help="Mechanically regenerate the frozen performance fixtures.",
    )
    parser.add_argument("--cold-worker", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cold_worker is not None:
        _cold_worker(args.cold_worker.resolve())
        return 0
    if args.generate_fixtures:
        hashes = generate_fixtures()
        print(json.dumps(hashes, indent=2, sort_keys=True))
        return 0
    report = run_benchmarks(
        samples=args.samples,
        determinism_runs=args.determinism_runs,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
