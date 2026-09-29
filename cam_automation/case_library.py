"""Offline case reuse, deterministic proposals, and evidence closure.

The service intentionally uses only the Python standard library and SQLite.  It
does not execute actions or emit machine-ready NC output; every proposal is a
reviewable dry-run artifact.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
PRODUCTS = frozenset({"nx", "powermill"})
UNITS = frozenset({"mm", "inch"})
GATES = ("review", "simulation", "collision", "shop_approval")
EVIDENCE_STATUSES = frozenset({"passed", "failed"})
KNOWN_ACTIONS = frozenset(
    {
        "cam.operation.plan",
        "cam.operation.create",
        "cam.toolpath.preview",
        "cam.toolpath.simulate",
        "nx.operation.plan",
        "nx.operation.create",
        "powermill.operation.plan",
        "powermill.operation.create",
    }
)
FORBIDDEN_ACTION_TOKENS = frozenset({"nc", "postprocess", "execute", "machinecode"})
FORBIDDEN_PARAMETER_KEYS = frozenset(
    {"nc", "nc_code", "nc_output", "machine_code", "postprocess", "execute", "execution", "command"}
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _external_context_digest(value: Mapping[str, Any]) -> str:
    """Match manufacturing_context.content_hash (UTF-8 JSON with ensure_ascii)."""
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")
    return value.strip()


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp string.")
    candidate = value.strip().replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp string.") from error
    return value.strip()


def _reject_machine_fields(value: Any, field: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in FORBIDDEN_PARAMETER_KEYS:
                raise ValueError(f"{field} contains machine-ready NC or execution field: {key}.")
            _reject_machine_fields(item, field)
    elif isinstance(value, list):
        for item in value:
            _reject_machine_fields(item, field)


def _snapshot_semantics(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return only values that can influence matching, binding, or advice."""
    excluded = {
        "snapshot_id",
        "content_hash",
        "captured_at",
        "warnings",
        "_local_snapshot_digest",
    }
    return {key: _copy(value) for key, value in snapshot.items() if key not in excluded}


def _snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    return _digest(_snapshot_semantics(snapshot))


def _validate_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot must be an object.")
    value = _copy(dict(snapshot))
    product = _string(value.get("product"), "snapshot.product").lower()
    if product not in PRODUCTS:
        raise ValueError("snapshot.product must be nx or powermill.")
    _string(value.get("instance_id"), "snapshot.instance_id")
    _string(value.get("project_id"), "snapshot.project_id")
    _string(value.get("target_version"), "snapshot.target_version")
    units = _string(value.get("units"), "snapshot.units").lower()
    if units not in UNITS:
        raise ValueError("snapshot.units must be mm or inch.")
    _string(value.get("material"), "snapshot.material")
    machine = value.get("machine")
    if not isinstance(machine, Mapping):
        raise ValueError("snapshot.machine must be an object.")
    axes = machine.get("axes")
    if isinstance(axes, bool) or not isinstance(axes, int) or axes < 1:
        raise ValueError("snapshot.machine.axes must be a positive integer.")
    objects = value.get("objects")
    if not isinstance(objects, list):
        raise ValueError("snapshot.objects must be an array.")
    seen: set[str] = set()
    for item in objects:
        if not isinstance(item, Mapping):
            raise ValueError("snapshot.objects items must be objects.")
        object_id = _string(item.get("object_id"), "snapshot.objects.object_id")
        if object_id in seen:
            raise ValueError(f"duplicate snapshot object_id: {object_id}")
        seen.add(object_id)
        _string(item.get("kind"), "snapshot.objects.kind")
        _string(item.get("name"), "snapshot.objects.name")
        attributes = item.get("attributes", {})
        if not isinstance(attributes, Mapping):
            raise ValueError("snapshot.objects.attributes must be an object.")
    # A supplied hash is retained as an external context identity.  The
    # independent local digest below catches content changes even if a caller
    # supplies a stale or forged content_hash.
    supplied_hash = value.get("content_hash")
    if supplied_hash is not None and (
        not isinstance(supplied_hash, str) or not supplied_hash.strip()
    ):
        raise ValueError("snapshot.content_hash must be a non-empty string.")
    generated_local_hash = supplied_hash == value.get("_local_snapshot_digest")
    if isinstance(supplied_hash, str) and supplied_hash.startswith("sha256:") and not generated_local_hash:
        hash_payload = {
            key: _copy(item)
            for key, item in value.items()
            if key not in {"snapshot_id", "content_hash", "captured_at", "warnings", "_local_snapshot_digest"}
        }
        if supplied_hash != _external_context_digest(hash_payload):
            raise ValueError("snapshot.content_hash does not match snapshot content.")
    value["product"] = product
    value["units"] = units
    value["_local_snapshot_digest"] = _snapshot_digest(value)
    if not supplied_hash:
        value["content_hash"] = value["_local_snapshot_digest"]
    return value


def _case_semantics(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "name",
        "product",
        "material",
        "units",
        "machine",
        "tags",
        "parameters",
        "steps",
        "provenance",
    )
    return {field: _copy(payload.get(field)) for field in fields}


def _validate_case(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("case payload must be an object.")
    value = _copy(dict(payload))
    value["name"] = _string(value.get("name"), "name")
    value["product"] = _string(value.get("product"), "product").lower()
    if value["product"] not in PRODUCTS:
        raise ValueError("product must be nx or powermill.")
    value["material"] = _string(value.get("material"), "material")
    value["units"] = _string(value.get("units"), "units").lower()
    if value["units"] not in UNITS:
        raise ValueError("units must be mm or inch.")
    machine = value.get("machine")
    if not isinstance(machine, Mapping):
        raise ValueError("machine must be an object.")
    axes = machine.get("axes")
    if isinstance(axes, bool) or not isinstance(axes, int) or axes < 1:
        raise ValueError("machine.axes must be a positive integer.")
    tags = value.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError("tags must be an array of non-empty strings.")
    value["tags"] = [tag.strip() for tag in tags]
    parameters = value.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise ValueError("parameters must be an object.")
    value["parameters"] = dict(parameters)
    _reject_machine_fields(value["parameters"], "parameters")
    steps = value.get("steps", [])
    if not isinstance(steps, list) or any(not isinstance(step, Mapping) for step in steps):
        raise ValueError("steps must be an array of objects.")
    normalized_steps: list[dict[str, Any]] = []
    step_ids: set[str] = set()
    for index, raw in enumerate(steps, 1):
        step = _copy(dict(raw))
        step.setdefault("step_id", f"step-{index:03d}")
        if not isinstance(step["step_id"], str) or not step["step_id"].strip():
            raise ValueError("step.step_id must be a non-empty string.")
        step["step_id"] = step["step_id"].strip()
        if step["step_id"] in step_ids:
            raise ValueError(f"duplicate step_id: {step['step_id']}")
        step_ids.add(step["step_id"])
        action = step.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("step.action must be a non-empty string.")
        action = action.strip()
        lowered_action = action.casefold()
        if lowered_action.startswith("nx.") and value["product"] != "nx":
            raise ValueError("nx.* actions are only valid for nx cases.")
        if lowered_action.startswith("powermill.") and value["product"] != "powermill":
            raise ValueError("powermill.* actions are only valid for powermill cases.")
        forbidden_fields = {
            "nc_code",
            "nc_output",
            "machine_code",
            "postprocess",
            "execution_mode",
            "execute",
        }
        if (
            any(field in step for field in forbidden_fields)
            or any(
                token in FORBIDDEN_ACTION_TOKENS
                for token in re.split(r"[^a-z0-9]+", lowered_action)
                if token
            )
        ):
            raise ValueError("machine-ready NC or execution output is not accepted in a case step.")
        step["action"] = action
        # The case remains importable, but only canonical cam.* and matching
        # product-prefixed actions are considered known.  Opaque actions block
        # readiness rather than silently becoming executable behavior.
        step["action_status"] = (
            "known"
            if action.casefold() in KNOWN_ACTIONS
            else "opaque"
        )
        if "selector" in step and not isinstance(step["selector"], Mapping):
            raise ValueError("step.selector must be an object.")
        if "parameters" in step:
            _reject_machine_fields(step["parameters"], f"step {step['step_id']} parameters")
        normalized_steps.append(step)
    value["steps"] = normalized_steps
    provenance = value.get("provenance", {})
    if not isinstance(provenance, Mapping):
        raise ValueError("provenance must be an object.")
    value["provenance"] = dict(provenance)
    expected_hash = _digest(_case_semantics(value))
    if value.get("content_hash") is not None and value.get("content_hash") != expected_hash:
        raise ValueError("case content_hash does not match case content.")
    value["content_hash"] = expected_hash
    value["case_id"] = value.get("case_id") or f"case:{expected_hash.removeprefix('sha256:')}"
    if not isinstance(value["case_id"], str) or not value["case_id"].strip():
        raise ValueError("case_id must be a non-empty string.")
    value["schema_version"] = SCHEMA_VERSION
    return value


def _selector_matches(snapshot: Mapping[str, Any], selector: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(selector, Mapping):
        raise ValueError("selector must be an object.")
    allowed = {"object_id", "name", "kind", "attributes"}
    unknown = sorted(set(selector) - allowed)
    if unknown:
        raise ValueError("selector contains unknown fields: " + ", ".join(unknown))
    if not selector:
        raise ValueError("selector must not be empty.")
    # B's context service may provide the canonical resolver.  Import lazily
    # so this module remains usable while the two services are developed in
    # parallel; the local matcher is an equivalent offline fallback.
    try:
        from .manufacturing_context import resolve_snapshot_selector  # type: ignore
    except (ImportError, AttributeError):
        resolve_snapshot_selector = None
    if callable(resolve_snapshot_selector):
        resolved = resolve_snapshot_selector(dict(snapshot), dict(selector))
        if isinstance(resolved, Mapping):
            candidate_matches = resolved.get("matches")
            if isinstance(candidate_matches, list):
                by_id = {item.get("object_id"): dict(item) for item in snapshot["objects"]}
                normalized: list[dict[str, Any]] = []
                for item in candidate_matches:
                    if isinstance(item, Mapping):
                        normalized.append(dict(item))
                    elif item in by_id:
                        normalized.append(by_id[item])
                return normalized
    attributes = selector.get("attributes", {})
    if not isinstance(attributes, Mapping):
        raise ValueError("selector.attributes must be an object.")
    result: list[dict[str, Any]] = []
    for raw in snapshot["objects"]:
        item = dict(raw)
        if "object_id" in selector and item.get("object_id") != selector["object_id"]:
            continue
        if "name" in selector and item.get("name") != selector["name"]:
            continue
        if "kind" in selector and item.get("kind") != selector["kind"]:
            continue
        item_attributes = item.get("attributes", {})
        if any(item_attributes.get(key) != val for key, val in attributes.items()):
            continue
        result.append(item)
    return result


class CaseLibraryService:
    """Persistent case library with deterministic, review-only proposals."""

    def __init__(self, root: Path, *, clock: Any = _now) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "case_library.sqlite3"
        self._clock = clock
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._database() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    proposal_id TEXT PRIMARY KEY,
                    proposal_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evidence_proposal_idx ON evidence(proposal_id);
                """
            )

    @staticmethod
    def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
        return json.loads(row["payload_json"])

    def add_case(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        case = _validate_case(payload)
        case.setdefault("created_at", self._clock())
        serialized = _canonical(case)
        with self._lock, self._database() as db:
            existing = db.execute(
                "SELECT payload_json FROM cases WHERE content_hash = ?", (case["content_hash"],)
            ).fetchone()
            if existing is not None:
                return _copy(json.loads(existing["payload_json"]))
            try:
                db.execute(
                    "INSERT INTO cases(case_id,content_hash,payload_json,created_at) VALUES(?,?,?,?)",
                    (case["case_id"], case["content_hash"], serialized, case["created_at"]),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("case_id or content_hash is already used by different content.") from error
        return _copy(case)

    def list_cases(self) -> list[dict[str, Any]]:
        with self._lock, self._database() as db:
            rows = db.execute("SELECT payload_json FROM cases ORDER BY created_at, case_id").fetchall()
        return [_copy(json.loads(row["payload_json"])) for row in rows]

    def _get_case(self, case_id: str) -> dict[str, Any]:
        with self._lock, self._database() as db:
            row = db.execute("SELECT payload_json FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        return json.loads(row["payload_json"])

    def search_cases(self, snapshot: Mapping[str, Any], query: str = "") -> list[dict[str, Any]]:
        context = _validate_snapshot(snapshot)
        if not isinstance(query, str):
            raise ValueError("query must be a string.")
        query_text = query.strip().casefold()
        query_terms = [term for term in query_text.split() if term]
        results: list[dict[str, Any]] = []
        for case in self.list_cases():
            if case["product"] != context["product"]:
                continue
            matched: list[str] = ["product"]
            missing: list[str] = []
            score = 1.0
            if case["units"] == context["units"]:
                matched.append("units")
                score += 2
            else:
                missing.append(f"units:{case['units']}")
            if case["material"].casefold() == str(context["material"]).casefold():
                matched.append("material")
                score += 3
            else:
                missing.append(f"material:{case['material']}")
            case_axes = int(case["machine"].get("axes", 0))
            context_axes = int(context["machine"].get("axes", 0))
            if case_axes == context_axes:
                matched.append("machine.axes")
                score += 2
            else:
                missing.append(f"machine.axes:{case_axes}")
            haystack = " ".join(
                [case["name"], case["material"], *case.get("tags", []), _canonical(case.get("steps", []))]
            ).casefold()
            query_hits = [term for term in query_terms if term in haystack]
            if query_terms and query_hits:
                matched.append("query:" + ",".join(query_hits))
                score += len(query_hits)
            elif query_terms:
                missing.append("query")
            item = _copy(case)
            item["match"] = {
                "score": score,
                "matched_conditions": matched,
                "missing_conditions": missing,
                "explanation": (
                    "适用性依据为产品、单位、材料、机床轴数和文本条件的逐项匹配；"
                    "score 仅用于稳定排序，不代表成功率。"
                ),
            }
            results.append(item)
        results.sort(key=lambda item: (-item["match"]["score"], item["case_id"]))
        return results

    def _proposal_payload(
        self, context: dict[str, Any], case: dict[str, Any], parameters: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        if case["product"] != context["product"]:
            raise ValueError("case product does not match snapshot product.")
        if case["units"] != context["units"]:
            raise ValueError("case units do not match snapshot units.")
        if int(case["machine"].get("axes", 0)) != int(context["machine"].get("axes", 0)):
            raise ValueError("case machine.axes does not match snapshot machine.axes.")
        if parameters is not None and not isinstance(parameters, Mapping):
            raise ValueError("parameters must be an object.")
        if parameters is not None:
            _reject_machine_fields(parameters, "parameters")
        merged_parameters = _copy(case["parameters"])
        if parameters:
            merged_parameters.update(_copy(dict(parameters)))
        bindings: list[dict[str, Any]] = []
        blocking: list[str] = []
        steps = _copy(case["steps"])
        if not steps:
            blocking.append("case contains no steps; a proposal cannot be ready.")
        if case["material"].casefold() != str(context["material"]).casefold():
            blocking.append(
                "case material does not match snapshot material; parameter advice is not directly transferable."
            )
        for index, step in enumerate(steps, 1):
            if step.get("action_status") == "opaque":
                blocking.append(
                    f"step {step.get('step_id', f'step-{index:03d}')} action is opaque and cannot establish readiness."
                )
            selector = step.get("selector")
            if selector is None:
                continue
            matches = _selector_matches(context, selector)
            status = "resolved" if len(matches) == 1 else ("ambiguous" if matches else "unresolved")
            binding = {
                "step_id": step.get("step_id", f"step-{index:03d}"),
                "selector": _copy(dict(selector)),
                "status": status,
                "matches": [_copy(item) for item in matches],
                "snapshot_hash": context["content_hash"],
            }
            bindings.append(binding)
            if status != "resolved":
                blocking.append(
                    f"step {binding['step_id']} selector is {status}; a unique object binding is required."
                )
        proposal_base = {
            "schema_version": SCHEMA_VERSION,
            "case_id": case["case_id"],
            "case_hash": case["content_hash"],
            "snapshot_hash": context["content_hash"],
            "snapshot_digest": context["_local_snapshot_digest"],
            "snapshot_id": context.get("snapshot_id"),
            "product": context["product"],
            "source": context.get("source", "imported"),
            "target_version": context.get("target_version"),
            "parameters": merged_parameters,
            "steps": steps,
            "selector_bindings": bindings,
            "dry_run": True,
            "readiness": "blocked" if blocking else "ready_for_review",
            "blocking_reasons": blocking,
            "limitations": self._source_limitations(context),
        }
        proposal_hash = _digest(proposal_base)
        proposal_base["proposal_hash"] = proposal_hash
        proposal_base["proposal_id"] = f"proposal:{proposal_hash.removeprefix('sha256:')}"
        proposal_base["created_at"] = self._clock()
        return proposal_base

    @staticmethod
    def _source_limitations(context: Mapping[str, Any]) -> list[str]:
        source = context.get("source", "imported")
        limitations = [
            "本地证据登记不等于独立认证；仍需客户现场审阅、仿真、碰撞检查和批准。",
            "所有建议均为 dry-run，不提供执行接口或机床 NC。",
        ]
        if source == "fixture":
            limitations.append("该制造上下文标记为 fixture 演示数据，不代表真实宿主连接或客户数据。")
        elif source == "imported":
            limitations.append("该制造上下文来自导入快照，不代表实时宿主连接。")
        return limitations

    def create_proposal(
        self,
        snapshot: Mapping[str, Any],
        case_id: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = _validate_snapshot(snapshot)
        case = self._get_case(_string(case_id, "case_id"))
        proposal = self._proposal_payload(context, case, parameters)
        with self._lock, self._database() as db:
            existing = db.execute(
                "SELECT payload_json FROM proposals WHERE proposal_hash = ?", (proposal["proposal_hash"],)
            ).fetchone()
            if existing is not None:
                return _copy(json.loads(existing["payload_json"]))
            db.execute(
                "INSERT INTO proposals(proposal_id,proposal_hash,payload_json,created_at) VALUES(?,?,?,?)",
                (proposal["proposal_id"], proposal["proposal_hash"], _canonical(proposal), proposal["created_at"]),
            )
        return _copy(proposal)

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal_id = _string(proposal_id, "proposal_id")
        with self._lock, self._database() as db:
            row = db.execute("SELECT payload_json FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return json.loads(row["payload_json"])

    def record_evidence(self, proposal_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        proposal = self.get_proposal(proposal_id)
        if not isinstance(payload, Mapping):
            raise ValueError("evidence payload must be an object.")
        evidence = _copy(dict(payload))
        gate = _string(evidence.get("gate"), "gate")
        if gate not in GATES:
            raise ValueError("gate must be review, simulation, collision, or shop_approval.")
        status = _string(evidence.get("status"), "status")
        if status not in EVIDENCE_STATUSES:
            raise ValueError("status must be passed or failed.")
        actor = _string(evidence.get("actor"), "actor")
        note = _string(evidence.get("note"), "note")
        if evidence.get("proposal_hash") != proposal["proposal_hash"]:
            raise ValueError("evidence proposal_hash does not match the proposal.")
        if evidence.get("snapshot_hash") != proposal["snapshot_hash"]:
            raise ValueError("evidence snapshot_hash does not match the proposal context.")
        if "artifact_digest" in evidence and (
            not isinstance(evidence["artifact_digest"], str) or not evidence["artifact_digest"].strip()
        ):
            raise ValueError("artifact_digest must be a non-empty string.")
        observed_at = evidence.pop("observed_at", None)
        supplied_recorded_at = evidence.pop("recorded_at", None)
        if observed_at is not None:
            evidence["observed_at"] = _timestamp(observed_at, "observed_at")
        if supplied_recorded_at is not None:
            # Accept the historical field name, but keep the server clock
            # authoritative for persisted recorded_at.
            evidence["observed_at"] = _timestamp(supplied_recorded_at, "recorded_at")
        evidence.update(
            {
                "schema_version": SCHEMA_VERSION,
                "proposal_id": proposal_id,
                "gate": gate,
                "status": status,
                "actor": actor,
                "note": note,
                "recorded_at": self._clock(),
            }
        )
        evidence_hash = _digest(evidence)
        evidence["evidence_id"] = f"evidence:{evidence_hash.removeprefix('sha256:')}"
        with self._lock, self._database() as db:
            db.execute(
                "INSERT OR IGNORE INTO evidence(evidence_id,proposal_id,payload_json,recorded_at) VALUES(?,?,?,?)",
                (evidence["evidence_id"], proposal_id, _canonical(evidence), evidence["recorded_at"]),
            )
        return _copy(evidence)

    def _evidence(self, proposal_id: str) -> list[dict[str, Any]]:
        with self._lock, self._database() as db:
            rows = db.execute(
                "SELECT payload_json FROM evidence WHERE proposal_id = ? ORDER BY recorded_at, evidence_id",
                (proposal_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def assess_proposal(self, proposal_id: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        proposal = self.get_proposal(proposal_id)
        context = _validate_snapshot(snapshot)
        evidence = self._evidence(proposal_id)
        blocking = list(proposal.get("blocking_reasons", []))
        if context["content_hash"] != proposal["snapshot_hash"]:
            blocking.append("snapshot_hash differs from the proposal context; evidence is stale.")
        if context["_local_snapshot_digest"] != proposal.get("snapshot_digest"):
            blocking.append("snapshot content differs from the proposal context; evidence is stale.")
        # Never trust persisted selector status: bind again against this exact snapshot.
        for binding in proposal.get("selector_bindings", []):
            matches = _selector_matches(context, binding["selector"])
            status = "resolved" if len(matches) == 1 else ("ambiguous" if matches else "unresolved")
            if status != "resolved":
                blocking.append(f"step {binding['step_id']} selector is {status}; readiness is blocked.")
        gate_results: list[dict[str, Any]] = []
        valid_evidence: list[dict[str, Any]] = []
        for gate in GATES:
            gate_items = [item for item in evidence if item.get("gate") == gate]
            invalid = [
                item
                for item in gate_items
                if item.get("proposal_hash") != proposal["proposal_hash"]
                or item.get("snapshot_hash") != proposal["snapshot_hash"]
            ]
            if invalid:
                blocking.append(f"{gate} evidence has a mismatched proposal or snapshot hash.")
            valid = [item for item in gate_items if item not in invalid]
            valid_evidence.extend(valid)
            status = "passed" if valid and all(item["status"] == "passed" for item in valid) else (
                "failed" if any(item["status"] == "failed" for item in valid) else "pending"
            )
            gate_results.append({"gate": gate, "status": status, "evidence_count": len(valid)})
            if status != "passed":
                blocking.append(f"{gate} gate is {status}.")
        ready = not blocking
        return {
            "schema_version": SCHEMA_VERSION,
            "proposal_id": proposal_id,
            "proposal_hash": proposal["proposal_hash"],
            "snapshot_hash": context["content_hash"],
            "snapshot_id": context.get("snapshot_id"),
            "product": context["product"],
            "source": context.get("source", "imported"),
            "dry_run": True,
            "ready": ready,
            "status": "ready_for_review" if ready else "blocked",
            "blocking_reasons": list(dict.fromkeys(blocking)),
            "gate_results": gate_results,
            "evidence": valid_evidence,
            "limitations": self._source_limitations(context),
            "verified": False,
            "validation_scope": "evidence_recorded_only",
            "execution_available": False,
            "nc_output_available": False,
        }

    def export_bundle(self, proposal_id: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        proposal = self.get_proposal(proposal_id)
        context = _validate_snapshot(snapshot)
        assessment = self.assess_proposal(proposal_id, context)
        case = self._get_case(proposal["case_id"])
        source = context.get("source", "imported")
        limitations = [
            "本地证据登记不等于独立认证；仍需客户现场审阅、仿真、碰撞检查和批准。",
            "所有建议均为 dry-run，不提供执行接口或机床 NC。",
        ]
        if source == "fixture":
            limitations.append("该制造上下文标记为 fixture 演示数据，不代表真实宿主连接或客户数据。")
        elif source == "imported":
            limitations.append("该制造上下文来自导入快照，不代表实时宿主连接。")
        return {
            "schema_version": SCHEMA_VERSION,
            "bundle_id": f"bundle:{uuid.uuid4().hex}",
            "delivery_mode": "dry_run",
            "dry_run": True,
            "case": case,
            "snapshot": {key: value for key, value in context.items() if not key.startswith("_")},
            "proposal": proposal,
            "assessment": assessment,
            "evidence": assessment["evidence"],
            "limitations": list(dict.fromkeys(limitations + assessment["limitations"])),
            "execution_available": False,
            "nc_output_available": False,
        }


__all__ = ["CaseLibraryService", "GATES", "PRODUCTS", "SCHEMA_VERSION"]
