from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .flow_contracts import (
    CompatibilityReport,
    FlowGraph,
    FlowVersion,
    FrozenContract,
    RoundTripReport,
    canonical_hash,
    canonical_json,
    canonicalize,
    compute_artifact_hash,
    compute_capability_lock_hash,
    compute_semantic_hash,
    compute_source_snapshot_hash,
    semantic_projection,
    source_snapshot_projection,
)


DEFAULT_TIMESTAMP = "1970-01-01T00:00:00Z"
_MISSING = object()
_VERSION_STATUSES = {
    "draft",
    "review_required",
    "reviewed_for_fixture",
    "retired",
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


def _pointer_tokens(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Graph change paths must be non-root JSON Pointers.")
    if path == "/":
        return ("",)
    return tuple(
        part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")
    )


def _pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _parent_at(value: Any, path: str) -> tuple[Any, str]:
    tokens = _pointer_tokens(path)
    current = value
    for token in tokens[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (IndexError, TypeError, ValueError) as error:
                raise ValueError(f"Graph change path does not exist: {path}") from error
        elif isinstance(current, Mapping):
            if token not in current:
                raise ValueError(f"Graph change path does not exist: {path}")
            current = current[token]
        else:
            raise ValueError(f"Graph change path does not exist: {path}")
    return current, tokens[-1]


def _same(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


@dataclass(frozen=True, slots=True, init=False)
class GraphChange:
    """One deeply immutable, reversible JSON Pointer mutation."""

    path: str
    operation: Literal["add", "remove", "replace"]
    _before_json: str | None
    _after_json: str | None

    def __init__(
        self,
        path: str,
        operation: Literal["add", "remove", "replace"],
        *,
        before: Any = _MISSING,
        after: Any = _MISSING,
    ) -> None:
        _pointer_tokens(path)
        if operation not in {"add", "remove", "replace"}:
            raise ValueError(f"Unsupported graph change operation: {operation!r}")
        if operation == "add" and (before is not _MISSING or after is _MISSING):
            raise ValueError("An add change requires only an after value.")
        if operation == "remove" and (before is _MISSING or after is not _MISSING):
            raise ValueError("A remove change requires only a before value.")
        if operation == "replace" and (before is _MISSING or after is _MISSING):
            raise ValueError("A replace change requires before and after values.")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(
            self,
            "_before_json",
            None if before is _MISSING else canonical_json(_clone(before)),
        )
        object.__setattr__(
            self,
            "_after_json",
            None if after is _MISSING else canonical_json(_clone(after)),
        )

    @classmethod
    def add(cls, path: str, value: Any) -> GraphChange:
        return cls(path, "add", after=value)

    @classmethod
    def remove(cls, path: str, value: Any) -> GraphChange:
        return cls(path, "remove", before=value)

    @classmethod
    def replace(cls, path: str, before: Any, after: Any) -> GraphChange:
        return cls(path, "replace", before=before, after=after)

    @property
    def before(self) -> Any:
        return _MISSING if self._before_json is None else json.loads(self._before_json)

    @property
    def after(self) -> Any:
        return _MISSING if self._after_json is None else json.loads(self._after_json)

    def inverse(self) -> GraphChange:
        if self.operation == "add":
            return GraphChange.remove(self.path, self.after)
        if self.operation == "remove":
            return GraphChange.add(self.path, self.before)
        return GraphChange.replace(self.path, self.after, self.before)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path": self.path,
            "operation": self.operation,
        }
        if self._before_json is not None:
            result["before"] = self.before
        if self._after_json is not None:
            result["after"] = self.after
        return result


def _apply_change(document: dict[str, Any], change: GraphChange) -> None:
    parent, token = _parent_at(document, change.path)
    if isinstance(parent, list):
        if change.operation == "add":
            if token == "-":
                parent.append(_clone(change.after))
                return
            try:
                index = int(token)
            except ValueError as error:
                raise ValueError(f"Invalid array path: {change.path}") from error
            if index < 0 or index > len(parent):
                raise ValueError(f"Graph change path does not exist: {change.path}")
            parent.insert(index, _clone(change.after))
            return
        try:
            index = int(token)
            current = parent[index]
        except (IndexError, TypeError, ValueError) as error:
            raise ValueError(f"Graph change path does not exist: {change.path}") from error
        if not _same(current, change.before):
            raise ValueError(f"Graph change precondition failed: {change.path}")
        if change.operation == "remove":
            del parent[index]
        else:
            parent[index] = _clone(change.after)
        return

    if not isinstance(parent, dict):
        raise ValueError(f"Graph change path does not reference a container: {change.path}")
    if change.operation == "add":
        if token in parent:
            raise ValueError(f"Graph add path already exists: {change.path}")
        parent[token] = _clone(change.after)
        return
    if token not in parent:
        raise ValueError(f"Graph change path does not exist: {change.path}")
    if not _same(parent[token], change.before):
        raise ValueError(f"Graph change precondition failed: {change.path}")
    if change.operation == "remove":
        del parent[token]
    else:
        parent[token] = _clone(change.after)


@dataclass(frozen=True, slots=True)
class GraphCommand:
    """A reversible transaction that is explicitly semantic or layout-only."""

    command_id: str
    kind: Literal["semantic", "layout"]
    changes: tuple[GraphChange, ...]
    message: str = ""

    def __post_init__(self) -> None:
        if not self.command_id:
            raise ValueError("GraphCommand.command_id is required.")
        if self.kind not in {"semantic", "layout"}:
            raise ValueError("GraphCommand.kind must be semantic or layout.")
        object.__setattr__(self, "changes", tuple(self.changes))
        if not self.changes:
            raise ValueError("GraphCommand requires at least one change.")
        for change in self.changes:
            if not isinstance(change, GraphChange):
                raise TypeError("GraphCommand changes must be GraphChange values.")
            is_layout = change.path == "/layout" or change.path.startswith("/layout/")
            if self.kind == "layout" and not is_layout:
                raise ValueError("Layout commands may only change /layout.")
            if self.kind == "semantic" and is_layout:
                raise ValueError("Semantic commands may not change /layout.")

    @classmethod
    def replace(
        cls,
        command_id: str,
        kind: Literal["semantic", "layout"],
        path: str,
        before: Any,
        after: Any,
        *,
        message: str = "",
    ) -> GraphCommand:
        return cls(
            command_id,
            kind,
            (GraphChange.replace(path, before, after),),
            message,
        )

    def inverse(self) -> GraphCommand:
        return GraphCommand(
            command_id=f"{self.command_id}:undo",
            kind=self.kind,
            changes=tuple(change.inverse() for change in reversed(self.changes)),
            message=f"Undo {self.message or self.command_id}",
        )

    def apply(self, graph: Mapping[str, Any] | FlowGraph) -> FlowGraph:
        document = _raw(graph)
        for change in self.changes:
            _apply_change(document, change)
        document["semantic_hash"] = compute_semantic_hash(document)
        return FlowGraph(document)

    def undo(self, graph: Mapping[str, Any] | FlowGraph) -> FlowGraph:
        return self.inverse().apply(graph)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "kind": self.kind,
            "changes": [change.to_dict() for change in self.changes],
            "message": self.message,
        }


class CommandHistory:
    """In-memory undo/redo history. It never invokes a parser or source generator."""

    def __init__(self, graph: Mapping[str, Any] | FlowGraph) -> None:
        self._graph = FlowGraph(_raw(graph))
        self._undo: list[GraphCommand] = []
        self._redo: list[GraphCommand] = []

    @property
    def graph(self) -> FlowGraph:
        return FlowGraph(self._graph.to_dict())

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def execute(self, command: GraphCommand) -> FlowGraph:
        self._graph = command.apply(self._graph)
        self._undo.append(command)
        self._redo.clear()
        return self.graph

    def undo(self) -> FlowGraph:
        if not self._undo:
            raise IndexError("No GraphCommand is available to undo.")
        command = self._undo.pop()
        self._graph = command.undo(self._graph)
        self._redo.append(command)
        return self.graph

    def redo(self) -> FlowGraph:
        if not self._redo:
            raise IndexError("No GraphCommand is available to redo.")
        command = self._redo.pop()
        self._graph = command.apply(self._graph)
        self._undo.append(command)
        return self.graph


def prepare_graph_snapshot(
    graph: Mapping[str, Any] | FlowGraph,
) -> FlowGraph:
    document = _raw(graph)
    document["semantic_hash"] = compute_semantic_hash(document)
    document["source_snapshot_hash"] = compute_source_snapshot_hash(document)
    return FlowGraph(document)


def _report_value(
    value: Mapping[str, Any] | FrozenContract | None,
) -> dict[str, Any] | None:
    return None if value is None else _raw(value)


def _review_evidence_passes(
    round_trip_report: Mapping[str, Any] | RoundTripReport | None,
    compatibility_report: Mapping[str, Any] | CompatibilityReport | None,
    projection_report: Mapping[str, Any] | None,
) -> bool:
    round_trip = _report_value(round_trip_report)
    compatibility = _report_value(compatibility_report)
    if round_trip is None or compatibility is None:
        return False
    if round_trip.get("status") != "passed" or round_trip.get("fidelity") == "FB":
        return False
    if (
        compatibility.get("status") != "compatible"
        or compatibility.get("preview_eligible") is not True
    ):
        return False
    if projection_report is not None:
        projection = _raw(projection_report)
        if (
            projection.get("status") != "projected"
            or projection.get("preview_eligible") is not True
        ):
            return False
    return True


def create_flow_version(
    graph: Mapping[str, Any] | FlowGraph,
    *,
    parent_versions: Sequence[Mapping[str, Any] | FlowVersion] = (),
    status: str = "draft",
    author_ref: str,
    message: str = "",
    round_trip_report: Mapping[str, Any] | RoundTripReport | None = None,
    compatibility_report: Mapping[str, Any] | CompatibilityReport | None = None,
    projection_report: Mapping[str, Any] | None = None,
    created_at: str = DEFAULT_TIMESTAMP,
    version_id: str | None = None,
) -> FlowVersion:
    """Create a content-addressed immutable FlowVersion without saving source files."""

    if status not in _VERSION_STATUSES:
        raise ValueError(f"Unsupported FlowVersion status: {status!r}")
    if not author_ref:
        raise ValueError("FlowVersion author_ref is required.")
    snapshot = prepare_graph_snapshot(graph)
    document = snapshot.to_dict()
    semantic_hash = compute_semantic_hash(document)
    source_hash = compute_source_snapshot_hash(document)
    capability_hash = compute_capability_lock_hash(document)
    artifact_hash = compute_artifact_hash(document)
    parents = tuple(_raw(parent) for parent in parent_versions)
    parent_ids = tuple(sorted(str(parent.get("version_id", "")) for parent in parents))
    if any(not item for item in parent_ids):
        raise ValueError("Every parent FlowVersion requires a version_id.")

    review_invalidated = any(
        (
            parent.get("semantic_hash"),
            parent.get("source_snapshot_hash"),
            parent.get("capability_lock_hash"),
        )
        != (semantic_hash, source_hash, capability_hash)
        for parent in parents
        if parent.get("status") == "reviewed_for_fixture"
    )
    if status == "reviewed_for_fixture" and (
        review_invalidated
        or not _review_evidence_passes(
            round_trip_report,
            compatibility_report,
            projection_report,
        )
    ):
        status = "review_required"

    round_trip = _report_value(round_trip_report)
    compatibility = _report_value(compatibility_report)
    round_trip_id = (
        str(round_trip.get("report_id"))
        if round_trip is not None
        else "roundtrip:not-checked"
    )
    compatibility_id = (
        str(compatibility.get("report_id"))
        if compatibility is not None
        else "compatibility:not-checked"
    )
    identity = {
        "graph_id": document.get("graph_id"),
        "revision_id": document.get("revision_id"),
        "parent_version_ids": parent_ids,
        "semantic_hash": semantic_hash,
        "artifact_hash": artifact_hash,
        "source_snapshot_hash": source_hash,
        "capability_lock_hash": capability_hash,
        "status": status,
        "author_ref": author_ref,
        "message": message,
        "round_trip_report_id": round_trip_id,
        "compatibility_report_id": compatibility_id,
        "created_at": created_at,
    }
    if version_id is None:
        suffix = hashlib.sha256(canonicalize(identity)).hexdigest()[:24]
        version_id = f"flow-version:{suffix}"
    return FlowVersion(
        {
            "schema_version": 1,
            "contract": "cam.flow_version.v1",
            "version_id": version_id,
            "graph_id": document["graph_id"],
            "revision_id": document["revision_id"],
            "parent_version_ids": list(parent_ids),
            "semantic_hash": semantic_hash,
            "artifact_hash": artifact_hash,
            "source_snapshot_hash": source_hash,
            "capability_lock_hash": capability_hash,
            "status": status,
            "author_ref": author_ref,
            "message": message,
            "round_trip_report_id": round_trip_id,
            "compatibility_report_id": compatibility_id,
            "created_at": created_at,
            "immutable": True,
            "extensions": {
                "cam.flow_versions": {
                    "semantic": False,
                    "review_invalidated": review_invalidated,
                }
            },
        }
    )


@dataclass(frozen=True, slots=True)
class StoredFlowVersion:
    version: FlowVersion
    graph: FlowGraph


class FlowVersionStore:
    """Small in-memory immutable revision store used by the route-neutral service."""

    def __init__(self) -> None:
        self._records: dict[str, StoredFlowVersion] = {}
        self._graph_versions: dict[str, list[str]] = {}

    def save(
        self,
        graph: Mapping[str, Any] | FlowGraph,
        **version_options: Any,
    ) -> StoredFlowVersion:
        snapshot = prepare_graph_snapshot(graph)
        version = create_flow_version(snapshot, **version_options)
        existing = self._records.get(version.version_id)
        if existing is not None:
            return existing
        record = StoredFlowVersion(version, snapshot)
        self._records[version.version_id] = record
        self._graph_versions.setdefault(version.graph_id, []).append(version.version_id)
        return record

    def get(self, version_id: str) -> StoredFlowVersion:
        try:
            return self._records[version_id]
        except KeyError as error:
            raise KeyError(f"Unknown FlowVersion: {version_id}") from error

    def versions(self, graph_id: str) -> tuple[StoredFlowVersion, ...]:
        return tuple(
            self._records[version_id]
            for version_id in self._graph_versions.get(graph_id, ())
        )


@dataclass(frozen=True, slots=True)
class MigrationResult:
    graph: FlowGraph
    report: Mapping[str, Any]


def migrate_flow_graph(
    graph: Mapping[str, Any] | FlowGraph,
    command: GraphCommand,
    *,
    new_revision_id: str,
    target_versions: Sequence[str] | None = None,
    capability_lock: Sequence[Mapping[str, Any]] | None = None,
    checked_at: str = DEFAULT_TIMESTAMP,
) -> MigrationResult:
    if command.kind != "semantic":
        raise ValueError("A capability or target migration requires a semantic command.")
    source = _raw(graph)
    old_revision_id = str(source.get("revision_id", ""))
    if not new_revision_id or new_revision_id == old_revision_id:
        raise ValueError("Migration must create a distinct new revision_id.")
    migrated = command.apply(source).to_dict()
    migrated["parent_revision_id"] = old_revision_id
    migrated["revision_id"] = new_revision_id
    if target_versions is not None:
        migrated["target_versions"] = sorted(set(target_versions))
    if capability_lock is not None:
        migrated["capability_lock"] = _clone(list(capability_lock))
    node_results: list[dict[str, Any]] = []
    changed_paths = tuple(change.path for change in command.changes)
    for flow in migrated.get("flows", []):
        if not isinstance(flow, Mapping):
            continue
        for node in flow.get("nodes", []):
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("node_id", ""))
            node_path_fragment = f"/nodes/{node_id}/"
            changed = any(
                node_path_fragment in path
                or path.endswith(f"/nodes/{node_id}")
                for path in changed_paths
            )
            if node.get("enabled") is True:
                node["review_status"] = "needs_review"
                node["compatibility_status"] = "unknown"
            node_results.append(
                {
                    "node_id": node_id,
                    "status": "migrated" if changed else "unchanged",
                }
            )
    migrated["semantic_hash"] = compute_semantic_hash(migrated)
    migrated["source_snapshot_hash"] = compute_source_snapshot_hash(migrated)
    migrated_graph = FlowGraph(migrated)
    report_body = {
        "schema_version": 1,
        "contract": "cam.migration_report.v1",
        "graph_id": migrated_graph.graph_id,
        "source_revision_id": old_revision_id,
        "target_revision_id": new_revision_id,
        "status": "review_required",
        "node_results": sorted(node_results, key=lambda item: item["node_id"]),
        "changed_paths": sorted(changed_paths),
        "checked_at": checked_at,
        "extensions": {},
    }
    report_body["report_id"] = (
        "migration:" + canonical_hash(report_body).removeprefix("sha256:")[:24]
    )
    return MigrationResult(migrated_graph, report_body)


@dataclass(frozen=True, slots=True)
class JsonChange:
    operation: Literal["add", "remove", "replace"]
    path: str
    before: Any = _MISSING
    after: Any = _MISSING

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "operation": self.operation,
            "path": self.path,
        }
        if self.before is not _MISSING:
            result["before"] = _clone(self.before)
        if self.after is not _MISSING:
            result["after"] = _clone(self.after)
        return result


def _json_changes(
    before: Any,
    after: Any,
    path: str = "",
) -> tuple[JsonChange, ...]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        changes: list[JsonChange] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{_pointer_part(str(key))}"
            if key not in before:
                changes.append(JsonChange("add", child, after=after[key]))
            elif key not in after:
                changes.append(JsonChange("remove", child, before=before[key]))
            else:
                changes.extend(_json_changes(before[key], after[key], child))
        return tuple(changes)
    if isinstance(before, list) and isinstance(after, list):
        if _same(before, after):
            return ()
        changes = []
        common = min(len(before), len(after))
        for index in range(common):
            changes.extend(_json_changes(before[index], after[index], f"{path}/{index}"))
        for index in range(len(before) - 1, common - 1, -1):
            changes.append(JsonChange("remove", f"{path}/{index}", before=before[index]))
        for index in range(common, len(after)):
            changes.append(JsonChange("add", f"{path}/{index}", after=after[index]))
        return tuple(changes)
    if _same(before, after):
        return ()
    return (JsonChange("replace", path or "/", before=before, after=after),)


def _source_projection(graph: Mapping[str, Any] | FlowGraph) -> dict[str, Any]:
    value = _raw(graph)
    mappings = []
    for mapping in value.get("source_mappings", []):
        if not isinstance(mapping, Mapping):
            continue
        mappings.append(
            {
                key: _clone(mapping.get(key))
                for key in (
                    "mapping_id",
                    "asset_id",
                    "asset_revision_id",
                    "source_digest",
                    "source_span",
                    "target",
                    "role",
                    "mapping_quality",
                    "excerpt_hash",
                )
            }
        )
    mappings.sort(key=lambda item: str(item.get("mapping_id", "")))
    return {
        "source_snapshot": source_snapshot_projection(value),
        "source_mappings": mappings,
    }


def _opaque_evidence(graph: Mapping[str, Any] | FlowGraph) -> list[dict[str, Any]]:
    value = _raw(graph)
    result: list[dict[str, Any]] = []
    for flow in value.get("flows", []):
        if not isinstance(flow, Mapping):
            continue
        for node in flow.get("nodes", []):
            if not isinstance(node, Mapping) or not isinstance(node.get("opaque"), Mapping):
                continue
            opaque = node["opaque"]
            result.append(
                {
                    "node_id": node.get("node_id"),
                    "content_hash": opaque.get("content_hash"),
                    "source_span_ids": _clone(opaque.get("source_span_ids", [])),
                    "status": "preserved",
                }
            )
    return sorted(result, key=lambda item: str(item.get("node_id", "")))


def _optional_projection(value: Mapping[str, Any] | FrozenContract | None) -> Any:
    return {} if value is None else _raw(value)


@dataclass(frozen=True, slots=True)
class FourLayerDiff:
    report_id: str
    graph_id: str
    before_revision_id: str
    after_revision_id: str
    source_changes: tuple[JsonChange, ...]
    graph_changes: tuple[JsonChange, ...]
    compatibility_changes: tuple[JsonChange, ...]
    preview_changes: tuple[JsonChange, ...]
    retained_opaque: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "contract": "cam.flow_four_layer_diff.v1",
            "report_id": self.report_id,
            "graph_id": self.graph_id,
            "before_revision_id": self.before_revision_id,
            "after_revision_id": self.after_revision_id,
            "layers": {
                "source": [item.to_dict() for item in self.source_changes],
                "graph": [item.to_dict() for item in self.graph_changes],
                "compatibility": [
                    item.to_dict() for item in self.compatibility_changes
                ],
                "preview": [item.to_dict() for item in self.preview_changes],
            },
            "retained_opaque": [_clone(item) for item in self.retained_opaque],
            "layout_excluded": True,
            "extensions": {},
        }

    def canonical_bytes(self) -> bytes:
        return canonicalize(self.to_dict())


def four_layer_diff(
    before_graph: Mapping[str, Any] | FlowGraph,
    after_graph: Mapping[str, Any] | FlowGraph,
    *,
    before_compatibility: Mapping[str, Any] | CompatibilityReport | None = None,
    after_compatibility: Mapping[str, Any] | CompatibilityReport | None = None,
    before_preview: Mapping[str, Any] | FrozenContract | None = None,
    after_preview: Mapping[str, Any] | FrozenContract | None = None,
) -> FourLayerDiff:
    before = _raw(before_graph)
    after = _raw(after_graph)
    if before.get("graph_id") != after.get("graph_id"):
        raise ValueError("Four-layer diff requires revisions of the same graph_id.")
    source_changes = _json_changes(
        _source_projection(before),
        _source_projection(after),
    )
    graph_changes = _json_changes(
        semantic_projection(before),
        semantic_projection(after),
    )
    compatibility_changes = _json_changes(
        _optional_projection(before_compatibility),
        _optional_projection(after_compatibility),
    )
    preview_changes = _json_changes(
        _optional_projection(before_preview),
        _optional_projection(after_preview),
    )
    retained = tuple(
        item
        for item in _opaque_evidence(after)
        if item in _opaque_evidence(before)
    )
    identity = {
        "graph_id": before["graph_id"],
        "before_revision_id": before.get("revision_id"),
        "after_revision_id": after.get("revision_id"),
        "source": [item.to_dict() for item in source_changes],
        "graph": [item.to_dict() for item in graph_changes],
        "compatibility": [item.to_dict() for item in compatibility_changes],
        "preview": [item.to_dict() for item in preview_changes],
        "retained_opaque": retained,
    }
    report_id = "flow-diff:" + hashlib.sha256(canonicalize(identity)).hexdigest()[:24]
    return FourLayerDiff(
        report_id=report_id,
        graph_id=str(before["graph_id"]),
        before_revision_id=str(before.get("revision_id", "")),
        after_revision_id=str(after.get("revision_id", "")),
        source_changes=source_changes,
        graph_changes=graph_changes,
        compatibility_changes=compatibility_changes,
        preview_changes=preview_changes,
        retained_opaque=retained,
    )


__all__ = [
    "CommandHistory",
    "DEFAULT_TIMESTAMP",
    "FlowVersionStore",
    "FourLayerDiff",
    "GraphChange",
    "GraphCommand",
    "JsonChange",
    "MigrationResult",
    "StoredFlowVersion",
    "create_flow_version",
    "four_layer_diff",
    "migrate_flow_graph",
    "prepare_graph_snapshot",
]
