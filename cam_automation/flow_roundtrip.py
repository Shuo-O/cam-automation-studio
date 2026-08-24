from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .flow_contracts import (
    FlowGraph,
    FrozenContract,
    RoundTripReport,
    canonical_hash,
    canonical_json,
    compute_semantic_hash,
    semantic_projection,
)
from .flow_versions import DEFAULT_TIMESTAMP


_FIDELITIES = {"F0", "F1", "F2", "F3", "FB"}


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


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


@runtime_checkable
class RoundTripAdapter(Protocol):
    """Product-owned offline hook. Shared code never parses product syntax."""

    def reparse_candidate(
        self,
        candidate: bytes,
        *,
        target_graph: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True, slots=True)
class SourcePatch:
    mapping_id: str
    replacement: bytes
    start_byte: int | None = None
    end_byte: int | None = None
    expected_excerpt_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.mapping_id:
            raise ValueError("SourcePatch.mapping_id is required.")
        if not isinstance(self.replacement, bytes):
            raise TypeError("SourcePatch.replacement must be encoded bytes.")
        if (self.start_byte is None) != (self.end_byte is None):
            raise ValueError("SourcePatch byte bounds must be supplied together.")
        if self.start_byte is not None and (
            self.start_byte < 0
            or self.end_byte is None
            or self.end_byte < self.start_byte
        ):
            raise ValueError("SourcePatch byte bounds are invalid.")


@dataclass(frozen=True, slots=True)
class CandidateArtifact:
    """An in-memory candidate. It has no path and cannot overwrite an asset."""

    content: bytes
    content_hash: str
    changed_mapping_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        content: bytes,
        changed_mapping_ids: Sequence[str],
    ) -> CandidateArtifact:
        return cls(
            bytes(content),
            _hash_bytes(content),
            tuple(sorted(set(changed_mapping_ids))),
        )

    @property
    def artifact_ref(self) -> str:
        return "memory:candidate:" + self.content_hash.removeprefix("sha256:")[:24]


@dataclass(frozen=True, slots=True)
class RoundTripResult:
    report: RoundTripReport
    candidate: CandidateArtifact | None
    reparsed_graph: FlowGraph | None


class RoundTripBlocked(ValueError):
    def __init__(self, code: str, object_ref: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.object_ref = object_ref
        self.message = message

    def issue(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": "blocker",
            "message": self.message,
            "object_ref": self.object_ref,
            "source_mapping_ids": (
                [self.object_ref.removeprefix("mapping:")]
                if self.object_ref.startswith("mapping:")
                else []
            ),
            "remediation": "Discard the candidate and review the immutable source evidence.",
        }


def _mapping_index(graph: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for mapping in graph.get("source_mappings", []):
        if not isinstance(mapping, Mapping):
            continue
        mapping_id = str(mapping.get("mapping_id", ""))
        if not mapping_id or mapping_id in result:
            raise RoundTripBlocked(
                "FLOW_SCHEMA_INVALID",
                "graph:source_mappings",
                "Source mappings require unique non-empty mapping_id values.",
            )
        result[mapping_id] = mapping
    return result


def _asset_identity(
    graph: Mapping[str, Any],
    asset_revision_id: str | None,
) -> tuple[str, str]:
    refs = [
        item
        for item in graph.get("asset_refs", [])
        if isinstance(item, Mapping)
        and (
            asset_revision_id is None
            or item.get("asset_revision_id") == asset_revision_id
        )
    ]
    if len(refs) != 1:
        raise RoundTripBlocked(
            "SOURCE_DIGEST_MISMATCH",
            "graph:asset_refs",
            "Round-trip requires exactly one explicitly selected asset revision.",
        )
    revision_id = str(refs[0].get("asset_revision_id", ""))
    content_hash = str(refs[0].get("content_hash", ""))
    if not revision_id or not content_hash:
        raise RoundTripBlocked(
            "SOURCE_DIGEST_MISMATCH",
            "graph:asset_refs",
            "The selected asset revision has no immutable content hash.",
        )
    return revision_id, content_hash


def _span(mapping: Mapping[str, Any]) -> tuple[int, int]:
    value = mapping.get("source_span")
    if not isinstance(value, Mapping):
        raise RoundTripBlocked(
            "SOURCE_SPAN_INVALID",
            f"mapping:{mapping.get('mapping_id', 'unknown')}",
            "The source mapping has no byte span.",
        )
    try:
        start = int(value["start_byte"])
        end = int(value["end_byte"])
    except (KeyError, TypeError, ValueError) as error:
        raise RoundTripBlocked(
            "SOURCE_SPAN_INVALID",
            f"mapping:{mapping.get('mapping_id', 'unknown')}",
            "The source mapping byte span is invalid.",
        ) from error
    if start < 0 or end < start:
        raise RoundTripBlocked(
            "SOURCE_SPAN_INVALID",
            f"mapping:{mapping.get('mapping_id', 'unknown')}",
            "The source mapping byte span is invalid.",
        )
    return start, end


def _opaque_mapping_ids(
    graph: Mapping[str, Any],
    mappings: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    result = {
        mapping_id
        for mapping_id, mapping in mappings.items()
        if mapping.get("role") == "opaque"
    }
    for flow in graph.get("flows", []):
        if not isinstance(flow, Mapping):
            continue
        for node in flow.get("nodes", []):
            if not isinstance(node, Mapping):
                continue
            opaque = node.get("opaque")
            if not isinstance(opaque, Mapping):
                continue
            for mapping_id in opaque.get("source_span_ids", []):
                if isinstance(mapping_id, str):
                    result.add(mapping_id)
    return result


def _assert_excerpt(
    source: bytes,
    mapping: Mapping[str, Any],
    *,
    expected_hash: str | None = None,
) -> tuple[int, int]:
    start, end = _span(mapping)
    if end > len(source):
        raise RoundTripBlocked(
            "SOURCE_SPAN_INVALID",
            f"mapping:{mapping.get('mapping_id', 'unknown')}",
            "The source mapping byte span exceeds the source length.",
        )
    actual_hash = _hash_bytes(source[start:end])
    mapping_hash = str(mapping.get("excerpt_hash", ""))
    if (
        not mapping_hash
        or actual_hash != mapping_hash
        or (expected_hash is not None and expected_hash != mapping_hash)
    ):
        raise RoundTripBlocked(
            "SOURCE_DIGEST_MISMATCH",
            f"mapping:{mapping.get('mapping_id', 'unknown')}",
            "The source span no longer matches its reviewed excerpt hash.",
        )
    return start, end


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def build_minimal_candidate(
    original_bytes: bytes,
    graph: Mapping[str, Any] | FlowGraph,
    patches: Sequence[SourcePatch],
    *,
    asset_revision_id: str | None = None,
) -> CandidateArtifact:
    """Apply exact, non-overlapping source-map patches to an in-memory copy."""

    if not isinstance(original_bytes, bytes):
        raise TypeError("Round-trip source must be immutable bytes.")
    document = _raw(graph)
    selected_revision, source_digest = _asset_identity(document, asset_revision_id)
    if _hash_bytes(original_bytes) != source_digest:
        raise RoundTripBlocked(
            "SOURCE_DIGEST_MISMATCH",
            f"asset-revision:{selected_revision}",
            "The current source bytes differ from the immutable asset revision.",
        )
    mappings = _mapping_index(document)
    for mapping_id, mapping in mappings.items():
        if (
            mapping.get("asset_revision_id") != selected_revision
            or mapping.get("source_digest") != source_digest
        ):
            raise RoundTripBlocked(
                "SOURCE_DIGEST_MISMATCH",
                f"mapping:{mapping_id}",
                "A source mapping does not match the selected immutable asset revision.",
            )
    opaque_ids = _opaque_mapping_ids(document, mappings)
    opaque_hashes: dict[str, str] = {}
    for flow in document.get("flows", []):
        if not isinstance(flow, Mapping):
            continue
        for node in flow.get("nodes", []):
            if not isinstance(node, Mapping):
                continue
            opaque = node.get("opaque")
            if not isinstance(opaque, Mapping):
                continue
            for mapping_id in opaque.get("source_span_ids", []):
                if isinstance(mapping_id, str):
                    opaque_hashes[mapping_id] = str(opaque.get("content_hash", ""))
    opaque_spans: list[tuple[int, int, str]] = []
    for mapping_id in sorted(opaque_ids):
        mapping = mappings.get(mapping_id)
        if mapping is None:
            raise RoundTripBlocked(
                "FLOW_SCHEMA_INVALID",
                f"mapping:{mapping_id}",
                "An opaque node references a missing source mapping.",
            )
        start, end = _assert_excerpt(original_bytes, mapping)
        node_hash = opaque_hashes.get(mapping_id)
        if node_hash and node_hash != _hash_bytes(original_bytes[start:end]):
            raise RoundTripBlocked(
                "ROUNDTRIP_OPAQUE_CHANGED",
                f"mapping:{mapping_id}",
                "The opaque node hash does not match its immutable source bytes.",
            )
        opaque_spans.append((start, end, mapping_id))

    resolved: list[tuple[int, int, SourcePatch]] = []
    seen: set[str] = set()
    for patch in patches:
        if not isinstance(patch, SourcePatch):
            raise TypeError("Minimal patches must be SourcePatch values.")
        if patch.mapping_id in seen:
            raise RoundTripBlocked(
                "SOURCE_MAPPING_AMBIGUOUS",
                f"mapping:{patch.mapping_id}",
                "A source mapping may be patched at most once per transaction.",
            )
        seen.add(patch.mapping_id)
        mapping = mappings.get(patch.mapping_id)
        if mapping is None:
            raise RoundTripBlocked(
                "SOURCE_MAPPING_AMBIGUOUS",
                f"mapping:{patch.mapping_id}",
                "The requested source mapping does not exist.",
            )
        if mapping.get("mapping_quality") != "exact":
            raise RoundTripBlocked(
                "SOURCE_MAPPING_AMBIGUOUS",
                f"mapping:{patch.mapping_id}",
                "Only an exact source mapping may be patched.",
            )
        if patch.mapping_id in opaque_ids or mapping.get("role") == "opaque":
            raise RoundTripBlocked(
                "SOURCE_OPAQUE_EDIT",
                f"mapping:{patch.mapping_id}",
                "Opaque source bytes are not semantically editable.",
            )
        start, end = _assert_excerpt(
            original_bytes,
            mapping,
            expected_hash=patch.expected_excerpt_hash,
        )
        if patch.start_byte is not None and (patch.start_byte, patch.end_byte) != (
            start,
            end,
        ):
            raise RoundTripBlocked(
                "SOURCE_SPAN_INVALID",
                f"mapping:{patch.mapping_id}",
                "A minimal patch must exactly match its reviewed source span.",
            )
        for opaque_start, opaque_end, opaque_id in opaque_spans:
            if _overlaps((start, end), (opaque_start, opaque_end)):
                raise RoundTripBlocked(
                    "SOURCE_OPAQUE_EDIT",
                    f"mapping:{opaque_id}",
                    "A patch may not cross or split an opaque source span.",
                )
        resolved.append((start, end, patch))

    resolved.sort(key=lambda item: (item[0], item[1], item[2].mapping_id))
    for previous, current in zip(resolved, resolved[1:]):
        if previous[0] == current[0] or previous[1] > current[0]:
            raise RoundTripBlocked(
                "SOURCE_MAPPING_AMBIGUOUS",
                f"mapping:{current[2].mapping_id}",
                "Minimal source patches may not overlap.",
            )

    candidate = bytearray()
    cursor = 0
    for start, end, patch in resolved:
        candidate.extend(original_bytes[cursor:start])
        candidate.extend(patch.replacement)
        cursor = end
    candidate.extend(original_bytes[cursor:])
    return CandidateArtifact.create(
        bytes(candidate),
        [patch.mapping_id for _, _, patch in resolved],
    )


def _selector_semantics(value: Any, path: str = "$") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        selector_keys = {
            "selector_id",
            "resolution_status",
            "match_count",
            "unique_match_claimed",
            "recorded_identifier",
            "strategy",
            "selector_type",
            "resolved",
        }
        if selector_keys.intersection(value):
            result.append(
                {
                    "path": path,
                    "value": {
                        str(key): _clone(value[key])
                        for key in sorted(selector_keys.intersection(value))
                    },
                }
            )
        for key in sorted(value):
            result.extend(_selector_semantics(value[key], f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_selector_semantics(item, f"{path}[{index}]"))
    return result


def round_trip_semantic_projection(
    graph: Mapping[str, Any] | FlowGraph,
) -> dict[str, Any]:
    document = _raw(graph)
    return {
        "graph": semantic_projection(document),
        "selectors": _selector_semantics(document),
    }


def _adapter_projection(
    adapter: RoundTripAdapter,
    graph: Mapping[str, Any],
) -> Any:
    project = getattr(adapter, "semantic_projection", None)
    if callable(project):
        return _clone(project(graph))
    return round_trip_semantic_projection(graph)


def _adapter_issues(
    adapter: RoundTripAdapter,
    candidate: bytes,
    target_graph: Mapping[str, Any],
) -> list[dict[str, Any]]:
    validate = getattr(adapter, "validate_candidate", None)
    if not callable(validate):
        return []
    raw_issues = validate(candidate, target_graph=target_graph)
    if raw_issues is None:
        return []
    if not isinstance(raw_issues, Sequence) or isinstance(
        raw_issues, (str, bytes, bytearray)
    ):
        raise TypeError("RoundTripAdapter.validate_candidate must return a sequence.")
    issues: list[dict[str, Any]] = []
    for issue in raw_issues:
        if not isinstance(issue, Mapping):
            raise TypeError("Candidate safety issues must be JSON objects.")
        normalized = _clone(dict(issue))
        normalized.setdefault("severity", "blocker")
        normalized.setdefault("object_ref", "candidate:memory")
        normalized.setdefault("source_mapping_ids", [])
        normalized.setdefault(
            "remediation",
            "Discard the candidate and repair the product adapter.",
        )
        issues.append(normalized)
    return sorted(
        issues,
        key=lambda item: (
            str(item.get("code", "")),
            str(item.get("object_ref", "")),
            canonical_json(item),
        ),
    )


def _opaque_preserved(
    original: bytes,
    candidate: bytes,
    graph: Mapping[str, Any],
    patches: Sequence[SourcePatch],
) -> bool:
    mappings = _mapping_index(graph)
    opaque_ids = _opaque_mapping_ids(graph, mappings)
    opaque_hashes: dict[str, str] = {}
    for flow in graph.get("flows", []):
        if not isinstance(flow, Mapping):
            continue
        for node in flow.get("nodes", []):
            if not isinstance(node, Mapping):
                continue
            opaque = node.get("opaque")
            if not isinstance(opaque, Mapping):
                continue
            for mapping_id in opaque.get("source_span_ids", []):
                if isinstance(mapping_id, str):
                    opaque_hashes[mapping_id] = str(opaque.get("content_hash", ""))
    patch_ranges: list[tuple[int, int, int]] = []
    for patch in patches:
        mapping = mappings[patch.mapping_id]
        start, end = _span(mapping)
        patch_ranges.append((start, end, len(patch.replacement) - (end - start)))
    patch_ranges.sort()
    for mapping_id in opaque_ids:
        mapping = mappings.get(mapping_id)
        if mapping is None:
            return False
        start, end = _span(mapping)
        shift = sum(delta for left, right, delta in patch_ranges if right <= start)
        if any(_overlaps((start, end), (left, right)) for left, right, _ in patch_ranges):
            return False
        candidate_start = start + shift
        candidate_end = end + shift
        if candidate[candidate_start:candidate_end] != original[start:end]:
            return False
        if _hash_bytes(original[start:end]) != mapping.get("excerpt_hash"):
            return False
        node_hash = opaque_hashes.get(mapping_id)
        if node_hash and node_hash != _hash_bytes(original[start:end]):
            return False
    return True


def _report(
    graph: Mapping[str, Any],
    *,
    asset_revision_id: str,
    original_content_hash: str,
    status: str,
    fidelity: str,
    checked_at: str,
    candidate: CandidateArtifact | None,
    candidate_semantic_hash: str | None,
    candidate_reparsed: bool,
    untouched_spans_exact: bool,
    opaque_spans_preserved: bool,
    checks: Sequence[str],
    issues: Sequence[Mapping[str, Any]],
    report_id: str | None,
) -> RoundTripReport:
    body = {
        "schema_version": 1,
        "contract": "cam.round_trip_report.v1",
        "graph_id": graph["graph_id"],
        "revision_id": graph["revision_id"],
        "asset_revision_id": asset_revision_id,
        "original_content_hash": original_content_hash,
        "candidate_content_hash": (
            candidate.content_hash if candidate is not None else None
        ),
        "original_semantic_hash": compute_semantic_hash(graph),
        "candidate_semantic_hash": candidate_semantic_hash,
        "status": status,
        "fidelity": fidelity,
        "untouched_spans_exact": untouched_spans_exact,
        "opaque_spans_preserved": opaque_spans_preserved,
        "candidate_reparsed": candidate_reparsed,
        "changed_mapping_ids": (
            list(candidate.changed_mapping_ids) if candidate is not None else []
        ),
        "checks": list(checks),
        "issues": [_clone(item) for item in issues],
        "candidate_artifact_ref": (
            candidate.artifact_ref if candidate is not None else None
        ),
        "checked_at": checked_at,
        "extensions": {
            "cam.flow_roundtrip": {
                "semantic": False,
                "storage": "memory_only",
                "source_overwritten": False,
                "transport": "none",
            }
        },
    }
    if report_id is None:
        report_id = "roundtrip:" + canonical_hash(body).removeprefix("sha256:")[:24]
    body["report_id"] = report_id
    return RoundTripReport(body)


def verify_round_trip(
    original_graph: Mapping[str, Any] | FlowGraph,
    target_graph: Mapping[str, Any] | FlowGraph,
    original_bytes: bytes,
    patches: Sequence[SourcePatch],
    adapter: RoundTripAdapter,
    *,
    required_fidelity: str | None = None,
    asset_revision_id: str | None = None,
    checked_at: str = DEFAULT_TIMESTAMP,
    report_id: str | None = None,
) -> RoundTripResult:
    """Verify a candidate end-to-end and discard it on every failed proof."""

    original = _raw(original_graph)
    target = _raw(target_graph)
    if original.get("graph_id") != target.get("graph_id"):
        raise ValueError("Round-trip graphs must share graph_id.")
    if original.get("product") != target.get("product"):
        raise ValueError("Round-trip graphs must share product.")
    if not isinstance(adapter, RoundTripAdapter):
        raise TypeError("adapter must implement reparse_candidate(candidate, target_graph=...).")
    selected_revision = ""
    original_hash = _hash_bytes(original_bytes)
    try:
        selected_revision, expected_hash = _asset_identity(
            original,
            asset_revision_id,
        )
        if original_hash != expected_hash:
            raise RoundTripBlocked(
                "SOURCE_DIGEST_MISMATCH",
                f"asset-revision:{selected_revision}",
                "The source changed after the reviewed asset revision was imported.",
            )
        candidate = build_minimal_candidate(
            original_bytes,
            original,
            patches,
            asset_revision_id=selected_revision,
        )
    except RoundTripBlocked as error:
        report = _report(
            target,
            asset_revision_id=selected_revision or asset_revision_id or "unknown",
            original_content_hash=original_hash,
            status="blocked",
            fidelity="FB",
            checked_at=checked_at,
            candidate=None,
            candidate_semantic_hash=None,
            candidate_reparsed=False,
            untouched_spans_exact=False,
            opaque_spans_preserved=False,
            checks=("source_digest_guard",),
            issues=(error.issue(),),
            report_id=report_id,
        )
        return RoundTripResult(report, None, None)

    fidelity = required_fidelity
    if fidelity is None:
        mappings = _mapping_index(original)
        fidelity = (
            "F0"
            if not patches
            else "F3"
            if _opaque_mapping_ids(original, mappings)
            else "F1"
        )
    if fidelity not in _FIDELITIES or fidelity == "FB":
        raise ValueError("required_fidelity must be one of F0, F1, F2, or F3.")
    if fidelity == "F0" and candidate.content != original_bytes:
        issue = RoundTripBlocked(
            "SOURCE_SILENT_REWRITE",
            "candidate:memory",
            "F0 requires byte-exact output when there is no semantic edit.",
        )
        report = _report(
            target,
            asset_revision_id=selected_revision,
            original_content_hash=original_hash,
            status="blocked",
            fidelity="FB",
            checked_at=checked_at,
            candidate=None,
            candidate_semantic_hash=None,
            candidate_reparsed=False,
            untouched_spans_exact=False,
            opaque_spans_preserved=False,
            checks=("byte_exact",),
            issues=(issue.issue(),),
            report_id=report_id,
        )
        return RoundTripResult(report, None, None)

    opaque_preserved = _opaque_preserved(
        original_bytes,
        candidate.content,
        original,
        patches,
    )
    if not opaque_preserved:
        issue = RoundTripBlocked(
            "ROUNDTRIP_OPAQUE_CHANGED",
            "candidate:memory",
            "An untouched opaque span changed in the candidate.",
        )
        report = _report(
            target,
            asset_revision_id=selected_revision,
            original_content_hash=original_hash,
            status="blocked",
            fidelity="FB",
            checked_at=checked_at,
            candidate=None,
            candidate_semantic_hash=None,
            candidate_reparsed=False,
            untouched_spans_exact=True,
            opaque_spans_preserved=False,
            checks=("source_digest_guard", "minimal_patch", "opaque_preserved"),
            issues=(issue.issue(),),
            report_id=report_id,
        )
        return RoundTripResult(report, None, None)

    try:
        safety_issues = _adapter_issues(adapter, candidate.content, target)
    except Exception as error:
        safety_issues = [
            {
                "code": "FLOW_SCHEMA_INVALID",
                "severity": "blocker",
                "message": "The adapter candidate validation hook failed.",
                "object_ref": "candidate:memory",
                "source_mapping_ids": [],
                "remediation": "Discard the candidate and repair the product adapter.",
                "extensions": {"error_type": type(error).__name__},
            }
        ]
    if safety_issues:
        report = _report(
            target,
            asset_revision_id=selected_revision,
            original_content_hash=original_hash,
            status="blocked",
            fidelity="FB",
            checked_at=checked_at,
            candidate=None,
            candidate_semantic_hash=None,
            candidate_reparsed=False,
            untouched_spans_exact=True,
            opaque_spans_preserved=True,
            checks=(
                "source_digest_guard",
                "minimal_patch",
                "opaque_preserved",
                "adapter_candidate_validation",
            ),
            issues=safety_issues,
            report_id=report_id,
        )
        return RoundTripResult(report, None, None)

    try:
        reparsed_value = adapter.reparse_candidate(
            candidate.content,
            target_graph=target,
        )
        if not isinstance(reparsed_value, Mapping):
            raise TypeError("The adapter returned a non-object graph.")
        reparsed = FlowGraph(_clone(dict(reparsed_value)))
    except Exception as error:
        issue = {
            "code": "ROUNDTRIP_REPARSE_FAILED",
            "severity": "blocker",
            "message": "The in-memory candidate did not reparse.",
            "object_ref": "candidate:memory",
            "source_mapping_ids": [],
            "remediation": "Discard the candidate and repair the product adapter.",
            "extensions": {"error_type": type(error).__name__},
        }
        report = _report(
            target,
            asset_revision_id=selected_revision,
            original_content_hash=original_hash,
            status="failed",
            fidelity="FB",
            checked_at=checked_at,
            candidate=None,
            candidate_semantic_hash=None,
            candidate_reparsed=False,
            untouched_spans_exact=True,
            opaque_spans_preserved=True,
            checks=(
                "source_digest_guard",
                "minimal_patch",
                "opaque_preserved",
                "candidate_reparse",
            ),
            issues=(issue,),
            report_id=report_id,
        )
        return RoundTripResult(report, None, None)

    target_projection = _adapter_projection(adapter, target)
    reparsed_projection = _adapter_projection(adapter, reparsed.to_dict())
    candidate_semantic_hash = compute_semantic_hash(reparsed)
    if (
        canonical_json(target_projection) != canonical_json(reparsed_projection)
        or compute_semantic_hash(target) != candidate_semantic_hash
    ):
        issue = {
            "code": "ROUNDTRIP_SEMANTIC_MISMATCH",
            "severity": "blocker",
            "message": (
                "The reparsed node, edge, binding, unit, selector, or gate "
                "semantics differ from the target graph."
            ),
            "object_ref": "candidate:memory",
            "source_mapping_ids": [],
            "remediation": "Discard the candidate and review the semantic graph diff.",
            "extensions": {
                "target_projection_hash": canonical_hash(target_projection),
                "candidate_projection_hash": canonical_hash(reparsed_projection),
            },
        }
        report = _report(
            target,
            asset_revision_id=selected_revision,
            original_content_hash=original_hash,
            status="failed",
            fidelity="FB",
            checked_at=checked_at,
            candidate=None,
            candidate_semantic_hash=None,
            candidate_reparsed=True,
            untouched_spans_exact=True,
            opaque_spans_preserved=True,
            checks=(
                "source_digest_guard",
                "minimal_patch",
                "opaque_preserved",
                "candidate_reparse",
                "semantic_equal",
            ),
            issues=(issue,),
            report_id=report_id,
        )
        return RoundTripResult(report, None, None)

    checks = [
        "source_digest_guard",
        "byte_exact" if fidelity == "F0" else "minimal_patch",
        "untouched_spans_exact",
        "opaque_preserved",
        "candidate_reparse",
        "semantic_equal",
    ]
    report = _report(
        target,
        asset_revision_id=selected_revision,
        original_content_hash=original_hash,
        status="passed",
        fidelity=fidelity,
        checked_at=checked_at,
        candidate=candidate,
        candidate_semantic_hash=candidate_semantic_hash,
        candidate_reparsed=True,
        untouched_spans_exact=True,
        opaque_spans_preserved=True,
        checks=checks,
        issues=(),
        report_id=report_id,
    )
    return RoundTripResult(report, candidate, reparsed)


__all__ = [
    "CandidateArtifact",
    "RoundTripAdapter",
    "RoundTripBlocked",
    "RoundTripResult",
    "SourcePatch",
    "build_minimal_candidate",
    "round_trip_semantic_projection",
    "verify_round_trip",
]
