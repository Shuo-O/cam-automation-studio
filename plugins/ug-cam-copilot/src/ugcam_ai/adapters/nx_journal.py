from __future__ import annotations

import ast
import hashlib
import io
import math
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..models import ActivityEvent, JsonValue
from ..selectors import assess_find_object


_SAFE_PYTHON_CALLS = frozenset(
    {"bool", "dict", "enumerate", "float", "int", "len", "list", "print", "range", "str"}
)
_GROUP_NAMES = {
    "PROGRAM": "program",
    "METHOD": "method",
    "TOOL": "tool",
    "GEOMETRY": "geometry",
    "WORKPIECE": "geometry",
    "MCS": "geometry",
}
_MACHINE_OUTPUT_PATTERN = re.compile(
    r"(?i)(?:\b(?:G0?[0-3]|M\d{2,3}|CLSF|GCODE|NC_CODE|NCCODE)\b|"
    r"post[\s_.-]*process|machine[\s_.-]*control|send[\s_.-]*to[\s_.-]*machine)"
)
_ABSOLUTE_PATH_PATTERN = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")


@dataclass(frozen=True, slots=True)
class NxJournalLimits:
    """Fail-closed limits for untrusted Journal text and its static AST."""

    max_source_bytes: int = 10 * 1024 * 1024
    max_ast_nodes: int = 50_000
    max_ast_depth: int = 64
    max_tokens: int = 100_000
    max_string_bytes: int = 1024 * 1024
    max_diagnostics: int = 5_000


class NxJournalError(ValueError):
    """Base error for rejected or invalid NX Journal sources."""


class NxUnsupportedSourceError(NxJournalError):
    """Raised when a source is not an NX Open Python Journal."""


class NxJournalResourceLimitError(NxJournalError):
    """Raised instead of truncating or partially interpreting hostile input."""

    code = "RESOURCE_LIMIT_EXCEEDED"


class NxJournalSyntaxError(NxJournalError):
    def __init__(self, source_file: str, error: SyntaxError) -> None:
        self.source_file = source_file
        self.line = error.lineno or 0
        self.offset = error.offset or 0
        super().__init__(
            f"NX Journal AST parse failed at {source_file}:{self.line}:{self.offset}: "
            f"{error.msg}"
        )


@dataclass(slots=True)
class _BuilderState:
    builder_id: str
    builder_type: str
    factory_api: str
    created_line: int
    parameters: dict[str, JsonValue] = field(default_factory=dict)
    committed: bool = False
    abandoned: bool = False
    destroyed: bool = False
    commit_line: int | None = None
    destroy_line: int | None = None
    issues: list[str] = field(default_factory=list)

    def lifecycle(self) -> dict[str, JsonValue]:
        return {
            "builder_id": self.builder_id,
            "builder_type": self.builder_type,
            "factory_api": self.factory_api,
            "created_line": self.created_line,
            "committed": self.committed,
            "abandoned": self.abandoned,
            "destroyed": self.destroyed,
            "commit_line": self.commit_line,
            "destroy_line": self.destroy_line,
            "parameters": dict(self.parameters),
            "issues": list(self.issues),
            "complete": (
                (self.committed or self.abandoned)
                and self.destroyed
                and not self.issues
            ),
        }


class NxJournalAdapter:
    """Parse NX Open Python Journals through AST inspection only."""

    product = "nx"

    def __init__(self, *, limits: NxJournalLimits | None = None) -> None:
        self.limits = limits or NxJournalLimits()

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".py"

    def parse(self, path: Path) -> Iterable[ActivityEvent]:
        if not self.supports(path):
            raise NxUnsupportedSourceError(
                f"NX Journal adapter accepts only .py files, not {path.suffix or '<none>'}"
            )
        if path.stat().st_size > self.limits.max_source_bytes:
            raise NxJournalResourceLimitError(
                f"NX Journal exceeds {self.limits.max_source_bytes} source bytes"
            )
        raw = path.read_bytes()
        return self.parse_source(
            raw.decode("utf-8-sig"),
            source_file=str(path.resolve()),
            source_digest=hashlib.sha256(raw).hexdigest(),
            session_name=path.stem,
        )

    def parse_source(
        self,
        source: str,
        *,
        source_file: str = "<pasted-nx-journal>",
        source_digest: str | None = None,
        session_name: str = "pasted",
    ) -> list[ActivityEvent]:
        digest = source_digest or hashlib.sha256(source.encode("utf-8")).hexdigest()
        session_id = f"nx:{session_name}:{digest[:16]}"
        tree, _ = _parse_static_source(
            source,
            source_file=source_file,
            limits=self.limits,
        )
        _reject_explicit_powermill_source(tree)
        visitor = _NxActionVisitor(session_id=session_id, source_file=source_file)
        visitor.visit(tree)
        if not visitor.nxopen_seen:
            raise NxUnsupportedSourceError(
                "source does not import or reference NXOpen; PowerMill and generic Python "
                "inputs are not accepted by the NX Journal adapter"
            )
        visitor.finalize()
        return visitor.events

    def analyze(
        self,
        path: Path,
        *,
        target_version: str | None = None,
        target_symbols: Iterable[str] | None = None,
        selector_match_counts: Mapping[str, int] | None = None,
    ) -> "NxJournalStaticAnalysis":
        """Return NX-only static evidence without importing or running the file."""

        if not self.supports(path):
            raise NxUnsupportedSourceError(
                f"NX Journal adapter accepts only .py files, not {path.suffix or '<none>'}"
            )
        if path.stat().st_size > self.limits.max_source_bytes:
            raise NxJournalResourceLimitError(
                f"NX Journal exceeds {self.limits.max_source_bytes} source bytes"
            )
        raw = path.read_bytes()
        try:
            source = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise NxJournalError(
                f"NX Journal must be UTF-8 text for static analysis: {path}"
            ) from error
        return self.analyze_source(
            source,
            source_file=str(path.resolve()),
            source_digest=_sha256(raw),
            session_name=path.stem,
            source_bytes=raw,
            bom="utf8" if raw.startswith(b"\xef\xbb\xbf") else "none",
            target_version=target_version,
            target_symbols=target_symbols,
            selector_match_counts=selector_match_counts,
        )

    def analyze_source(
        self,
        source: str,
        *,
        source_file: str = "<pasted-nx-journal>",
        source_digest: str | None = None,
        session_name: str = "pasted",
        source_bytes: bytes | None = None,
        bom: str = "none",
        target_version: str | None = None,
        target_symbols: Iterable[str] | None = None,
        selector_match_counts: Mapping[str, int] | None = None,
    ) -> "NxJournalStaticAnalysis":
        tree, token_count = _parse_static_source(
            source,
            source_file=source_file,
            limits=self.limits,
        )
        _reject_explicit_powermill_source(tree)
        encoded = source.encode("utf-8") if source_bytes is None else source_bytes
        digest = _normalize_sha256(source_digest) if source_digest else _sha256(encoded)
        extractor = _NxFlowEvidenceExtractor(
            source=source,
            source_bytes=encoded,
            source_file=source_file,
            source_digest=digest,
            session_name=session_name,
            bom=bom,
            token_count=token_count,
            limits=self.limits,
            target_version=target_version,
            target_symbols=target_symbols,
            selector_match_counts=selector_match_counts,
        )
        analysis = extractor.extract(tree)
        if not analysis.nxopen_seen:
            raise NxUnsupportedSourceError(
                "source does not import or reference NXOpen; PowerMill and generic Python "
                "inputs are not accepted by the NX Journal adapter"
            )
        return analysis

    def parse_flow(
        self,
        path: Path,
        **options: Any,
    ) -> dict[str, JsonValue]:
        from .nx_flow import NxFlowMapper

        return NxFlowMapper(journal_adapter=self).map_path(path, **options)

    def parse_flow_source(
        self,
        source: str,
        **options: Any,
    ) -> dict[str, JsonValue]:
        from .nx_flow import NxFlowMapper

        return NxFlowMapper(journal_adapter=self).map_source(source, **options)


class _NxActionVisitor(ast.NodeVisitor):
    def __init__(self, *, session_id: str, source_file: str) -> None:
        self.session_id = session_id
        self.source_file = source_file
        self.aliases: dict[str, str] = {}
        self.builders: dict[str, _BuilderState] = {}
        self.builder_aliases: dict[str, str] = {}
        self.events: list[ActivityEvent] = []
        self.error_boundaries: list[str] = []
        self.scopes: list[str] = []
        self.nxopen_seen = False
        self.last_line = 0

    def finalize(self) -> None:
        for state in self.builders.values():
            if (
                (state.committed or state.abandoned)
                and state.destroyed
                and not state.issues
            ):
                continue
            issues = list(state.issues)
            if not state.committed and not state.abandoned:
                issues.append("builder_not_committed")
            if not state.destroyed:
                issues.append("builder_not_destroyed")
            issues = list(dict.fromkeys(issues))
            state.issues[:] = issues
            self._append(
                action="nx.builder.lifecycle_incomplete",
                category="diagnostic",
                params={
                    "risk": "blocked",
                    "reason": "incomplete_builder_lifecycle",
                    "issues": issues,
                    "lifecycle": state.lifecycle(),
                },
                line=state.destroy_line or state.commit_line or state.created_line,
                review_status="needs_changes",
            )

    def visit_Import(self, node: ast.Import) -> Any:
        for imported in node.names:
            if imported.name == "NXOpen" or imported.name.startswith("NXOpen."):
                local_name = imported.asname or imported.name.split(".", 1)[0]
                self.aliases[local_name] = (
                    imported.name if imported.asname else imported.name.split(".", 1)[0]
                )
                self.nxopen_seen = True
        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        module = node.module or ""
        if module == "NXOpen" or module.startswith("NXOpen."):
            self.nxopen_seen = True
            for imported in node.names:
                if imported.name == "*":
                    continue
                self.aliases[imported.asname or imported.name] = (
                    f"{module}.{imported.name}"
                )
        return None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.scopes.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        return None

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> Any:
        if isinstance(node.value, ast.Call):
            target_name = None
            for target in node.targets:
                bound = self._bind_call_result(target, node.value)
                target_name = target_name or bound
            self._record_call(node.value, result_target=target_name)
            return None

        for target in node.targets:
            if isinstance(target, ast.Name):
                path = self._expr_path(node.value)
                if path:
                    self.aliases[target.id] = path
                    builder_id = self._builder_state_id(self._root_name(node.value))
                    if builder_id:
                        self.builder_aliases[target.id] = builder_id
                    if self._is_nx_path(path):
                        self._record_object_access(target.id, path, node.lineno)
            elif isinstance(target, ast.Attribute):
                self._record_property_set(target, node.value, node.lineno)
        self.visit(node.value)
        return None

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        if node.value is None:
            return None
        if isinstance(node.value, ast.Call):
            target_name = self._bind_call_result(node.target, node.value)
            self._record_call(node.value, result_target=target_name)
        elif isinstance(node.target, ast.Attribute):
            self._record_property_set(node.target, node.value, node.lineno)
        elif isinstance(node.target, ast.Name):
            path = self._expr_path(node.value)
            if path:
                self.aliases[node.target.id] = path
                builder_id = self._builder_state_id(self._root_name(node.value))
                if builder_id:
                    self.builder_aliases[node.target.id] = builder_id
                if self._is_nx_path(path):
                    self._record_object_access(node.target.id, path, node.lineno)
            self.visit(node.value)
        return None

    def visit_AugAssign(self, node: ast.AugAssign) -> Any:
        if isinstance(node.target, ast.Attribute):
            self._record_property_set(node.target, node.value, node.lineno)
        else:
            self.generic_visit(node)
        return None

    def visit_Expr(self, node: ast.Expr) -> Any:
        if isinstance(node.value, ast.Call):
            self._record_call(node.value)
        else:
            self.generic_visit(node)
        return None

    def visit_Call(self, node: ast.Call) -> Any:
        self._record_call(node)
        return None

    def visit_Try(self, node: ast.Try) -> Any:
        boundary = f"try:{node.lineno}"
        self._append(
            action="nx.error_boundary.enter",
            category="diagnostic",
            params={
                "boundary": boundary,
                "handler_count": len(node.handlers),
                "has_finally": bool(node.finalbody),
                "risk": "review",
            },
            line=node.lineno,
            review_status="needs_review",
        )
        self.error_boundaries.append(boundary)
        for statement in node.body:
            self.visit(statement)
        for handler in node.handlers:
            exception_path = self._expr_path(handler.type) if handler.type else "BaseException"
            self._append(
                action="nx.error_boundary.handler",
                category="diagnostic",
                params={
                    "boundary": boundary,
                    "exception": exception_path,
                    "binding": handler.name,
                    "risk": "review",
                },
                line=handler.lineno,
                review_status="needs_review",
            )
            for statement in handler.body:
                self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)
        if node.finalbody:
            self._append(
                action="nx.error_boundary.finally",
                category="diagnostic",
                params={"boundary": boundary, "risk": "review"},
                line=node.finalbody[0].lineno,
                review_status="needs_review",
            )
            for statement in node.finalbody:
                self.visit(statement)
        self.error_boundaries.pop()
        return None

    def visit_Raise(self, node: ast.Raise) -> Any:
        self._append(
            action="nx.error_boundary.raise",
            category="diagnostic",
            params={
                "exception": self._safe_value(node.exc) if node.exc else None,
                "risk": "review",
            },
            line=node.lineno,
            review_status="needs_review",
        )
        return None

    def visit_If(self, node: ast.If) -> Any:
        if self._is_main_guard(node.test):
            for statement in node.body:
                self.visit(statement)
            for statement in node.orelse:
                self.visit(statement)
            return None
        self._record_dynamic_control_flow("if", node.test, node.lineno)
        self.generic_visit(node)
        return None

    def visit_For(self, node: ast.For) -> Any:
        self._record_dynamic_control_flow("for", node.iter, node.lineno)
        self.generic_visit(node)
        return None

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> Any:
        self._record_dynamic_control_flow("while", node.test, node.lineno)
        self.generic_visit(node)
        return None

    def _bind_call_result(self, target: ast.expr, call: ast.Call) -> str | None:
        if not isinstance(target, ast.Name):
            return None
        api_path = self._expr_path(call.func)
        self.aliases[target.id] = f"{api_path}()" if api_path else target.id
        method = api_path.rsplit(".", 1)[-1]
        if self._is_builder_factory(method):
            builder_type = method.removeprefix("Create").removesuffix("Builder") or "Builder"
            self.builders[target.id] = _BuilderState(
                builder_id=target.id,
                builder_type=builder_type,
                factory_api=api_path,
                created_line=call.lineno,
            )
            self.builder_aliases[target.id] = target.id
        return target.id

    def _record_call(self, node: ast.Call, *, result_target: str | None = None) -> None:
        api_path = self._expr_path(node.func)
        method = api_path.rsplit(".", 1)[-1] if api_path else ""
        if method == "main":
            return
        builder_id = self._builder_id_for_call(node)
        trusted = self._is_nx_path(api_path) or builder_id is not None
        if not trusted:
            if api_path in _SAFE_PYTHON_CALLS:
                return
            self._append(
                action="nx.journal.untrusted_call",
                category="diagnostic",
                params={
                    "api": api_path or type(node.func).__name__,
                    "dynamic": not bool(api_path),
                    "risk": "blocked",
                    "reason": "call_is_outside_trusted_nxopen_aliases",
                },
                line=node.lineno,
                review_status="rejected",
            )
            return

        self.nxopen_seen = True
        arguments = [self._safe_value(argument) for argument in node.args]
        keywords = {
            keyword.arg or "**": self._safe_value(keyword.value)
            for keyword in node.keywords
        }
        action, category, risk = self._canonical_call(api_path, method, node)
        params: dict[str, JsonValue] = {
            "api": api_path,
            "args": arguments,
            "kwargs": keywords,
            "risk": risk,
        }

        if method == "FindObject":
            identifier = self._literal_string(node.args[0]) if node.args else None
            params["selector"] = assess_find_object(identifier).to_dict()
            risk = "review"
            params["risk"] = risk

        state = self.builders.get(builder_id or result_target or "")
        if action == "cam.builder.create":
            state = self.builders.get(result_target or "")
        elif action == "cam.builder.commit":
            if state is not None:
                if state.committed:
                    state.issues.append("builder_committed_more_than_once")
                if state.destroyed:
                    state.issues.append("builder_committed_after_destroy")
                state.committed = True
                state.commit_line = node.lineno
        elif action == "cam.builder.destroy":
            if state is not None:
                if state.destroyed:
                    state.issues.append("builder_destroyed_more_than_once")
                if not state.committed:
                    state.abandoned = True
                state.destroyed = True
                state.destroy_line = node.lineno
        if state is not None:
            if (
                state.destroyed
                and action not in {"cam.builder.destroy"}
                and node.lineno != state.destroy_line
            ):
                state.issues.append("builder_used_after_destroy")
            if action.startswith("cam.parameter.set.") and arguments:
                parameter_path = ".".join(api_path.split(".")[-2:])
                state.parameters[parameter_path] = (
                    arguments[0] if len(arguments) == 1 else arguments
                )
            params["lifecycle"] = state.lifecycle()

        if action in {
            "cam.output.machine_ready",
            "cam.output.postprocess",
            "nx.journal.execute",
            "nx.machine.control",
        }:
            params["args"] = []
            params["kwargs"] = {}
            params["payload_redacted"] = True
            params["reason"] = "machine_ready_output_forbidden"

        review_status = (
            "rejected"
            if risk == "blocked"
            else "needs_review"
            if risk == "review"
            else "unreviewed"
        )
        self._append(
            action=action,
            category=category,
            params=params,
            line=node.lineno,
            review_status=review_status,
        )

    def _record_property_set(
        self, target: ast.Attribute, value: ast.expr, line: int
    ) -> None:
        api_path = self._expr_path(target)
        builder_id = self._builder_state_id(self._root_name(target))
        state = self.builders.get(builder_id or "")
        if not self._is_nx_path(api_path) and state is None:
            self._append(
                action="nx.journal.untrusted_assignment",
                category="diagnostic",
                params={
                    "target": api_path or target.attr,
                    "risk": "blocked",
                    "reason": "assignment_is_outside_trusted_nxopen_aliases",
                },
                line=line,
                review_status="rejected",
            )
            return
        safe_value = self._safe_value(value)
        property_path = self._relative_attribute_path(target)
        if state is not None:
            if state.destroyed:
                state.issues.append("builder_used_after_destroy")
            state.parameters[property_path] = safe_value
        action = f"cam.parameter.set.{_snake(target.attr)}"
        params: dict[str, JsonValue] = {
            "api": api_path,
            "property": property_path,
            "value": safe_value,
            "risk": "review",
        }
        if state is not None:
            params["builder_id"] = state.builder_id
            params["lifecycle"] = state.lifecycle()
        self._append(
            action=action,
            category="parameter",
            params=params,
            line=line,
            review_status="needs_review",
        )

    def _record_object_access(self, binding: str, api_path: str, line: int) -> None:
        self._append(
            action="nx.object.access",
            category="object",
            params={
                "binding": binding,
                "api": api_path,
                "access_kind": "attribute",
                "risk": "safe",
            },
            line=line,
            review_status="unreviewed",
        )

    def _record_dynamic_control_flow(
        self, kind: str, expression: ast.AST, line: int
    ) -> None:
        self._append(
            action="nx.journal.dynamic_control_flow",
            category="diagnostic",
            params={
                "control_flow": kind,
                "expression": self._safe_value(expression),
                "branches_are_static_evidence_only": True,
                "risk": "review",
            },
            line=line,
            review_status="needs_review",
        )

    def _append(
        self,
        *,
        action: str,
        category: str,
        params: dict[str, JsonValue],
        line: int,
        review_status: str,
    ) -> None:
        payload = {
            "analysis_mode": "ast_static",
            "journal_imported": False,
            "journal_executed": False,
            **params,
        }
        if self.error_boundaries:
            payload["error_boundaries"] = list(self.error_boundaries)
        if self.scopes:
            payload["scope"] = ".".join(self.scopes)
        self.last_line = max(self.last_line, line)
        self.events.append(
            ActivityEvent(
                session_id=self.session_id,
                seq=len(self.events),
                product="nx",
                action=action,
                category=category,
                mode="manual",
                params=payload,
                source_file=self.source_file,
                source_line=line,
                source_mode="manual",
                view_level="L1",
                expertise_label="unlabeled",
                review_status=review_status,
            )
        )

    def _canonical_call(
        self, api_path: str, method: str, node: ast.Call
    ) -> tuple[str, str, str]:
        lowered = api_path.lower()
        if method == "GetSession":
            return "nx.session.access", "session", "safe"
        if method == "FindObject":
            group = self._find_group_name(node)
            return (
                f"cam.group.select.{group}" if group else "nx.object.find",
                "setup" if group else "object",
                "review",
            )
        if self._is_builder_factory(method):
            return "cam.builder.create", "operation", "review"
        if "camoperationcollection" in lowered and method == "Create":
            return "cam.operation.create", "operation", "review"
        if method in {"GenerateToolPath", "GenerateToolpaths"}:
            return "cam.toolpath.generate", "toolpath", "review"
        if method == "Commit":
            return "cam.builder.commit", "operation", "review"
        if method in {"Destroy", "Dispose"}:
            return "cam.builder.destroy", "operation", "review"
        if method == "SetUndoMark":
            return "nx.undo_mark.set", "transaction", "safe"
        if method == "SetUndoMarkName":
            return "nx.undo_mark.rename", "transaction", "safe"
        if method == "DeleteUndoMark":
            return "nx.undo_mark.delete", "transaction", "safe"
        if method in {"Save", "SaveAs", "SaveAll"}:
            return "nx.part.save", "data", "blocked"
        if method in {"SetName", "Rename"}:
            return "nx.object.rename", "object", "review"
        if (
            self._builder_id_for_call(node) is not None
            and method.startswith("Set")
            and len(method) > 3
        ):
            return (
                f"cam.parameter.set.{_snake(method[3:])}",
                "parameter",
                "review",
            )
        if "journal" in lowered and any(
            word in method.lower() for word in ("execute", "run", "play", "replay")
        ):
            return "nx.journal.execute", "execution", "blocked"
        if any(word in lowered for word in ("postprocess", "post_process", ".post")):
            return "cam.output.postprocess", "output", "blocked"
        if any(
            word in lowered
            for word in (
                "gcode",
                "g_code",
                "generateclsf",
                "outputclsf",
                "nccode",
                "nc_code",
            )
        ):
            return "cam.output.machine_ready", "output", "blocked"
        if any(
            word in lowered
            for word in (
                "machinecontrol",
                "machine_control",
                "sendtomachine",
                "send_to_machine",
            )
        ):
            return "nx.machine.control", "execution", "blocked"
        if any(word in lowered for word in ("verify", "simulate", "collision", "gouge")):
            return "cam.verify.run", "verification", "review"
        pieces = [_snake(piece) for piece in api_path.split(".")[-2:]]
        return "nx.api." + ".".join(pieces), "other", "review"

    def _find_group_name(self, node: ast.Call) -> str | None:
        if not node.args:
            return None
        value = self._literal_string(node.args[0])
        if value is None:
            return None
        upper = value.upper()
        for marker, group in _GROUP_NAMES.items():
            if marker in upper:
                return group
        return None

    def _builder_id_for_call(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Attribute):
            root = self._root_name(node.func.value)
            return self._builder_state_id(root)
        return None

    def _builder_state_id(self, name: str) -> str | None:
        resolved = self.builder_aliases.get(name, name)
        return resolved if resolved in self.builders else None

    @staticmethod
    def _is_builder_factory(method: str) -> bool:
        return method.startswith("Create") and method.endswith("Builder")

    def _is_nx_path(self, path: str) -> bool:
        normalized = path.removesuffix("()")
        return normalized == "NXOpen" or normalized.startswith("NXOpen.")

    def _expr_path(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        if isinstance(node, ast.Name):
            path = self.aliases.get(node.id, node.id)
            if path == "NXOpen" or path.startswith("NXOpen."):
                self.nxopen_seen = True
            return path
        if isinstance(node, ast.Attribute):
            parent = self._expr_path(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Call):
            path = self._expr_path(node.func)
            return f"{path}()" if path else ""
        if isinstance(node, ast.Subscript):
            return f"{self._expr_path(node.value)}[]"
        return ""

    def _safe_value(self, node: ast.AST | None) -> JsonValue:
        if node is None:
            return None
        value = self._literal(node)
        if value is not _UNSET:
            return value  # type: ignore[return-value]
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [self._safe_value(item) for item in node.elts]
        if isinstance(node, ast.Dict):
            result: dict[str, JsonValue] = {}
            for key, item in zip(node.keys, node.values):
                key_value = self._literal(key) if key is not None else "**"
                result[str(key_value)] = self._safe_value(item)
            return result
        if isinstance(node, ast.Name):
            resolved = self.aliases.get(node.id)
            result: dict[str, JsonValue] = {"reference": node.id}
            if resolved and resolved != node.id:
                result["resolved_path"] = resolved
            return result
        expression = self._expr_path(node)
        return {
            "kind": "dynamic_expression",
            "expression": expression or type(node).__name__,
            "requires_review": True,
        }

    @staticmethod
    def _literal(node: ast.AST | None) -> JsonValue | object:
        if node is None:
            return _UNSET
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError):
            return _UNSET
        return _json_safe(value)

    @staticmethod
    def _literal_string(node: ast.AST) -> str | None:
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError):
            return None
        return value if isinstance(value, str) else None

    @staticmethod
    def _root_name(node: ast.AST) -> str:
        current = node
        while isinstance(current, (ast.Attribute, ast.Subscript)):
            current = current.value
        return current.id if isinstance(current, ast.Name) else ""

    @staticmethod
    def _relative_attribute_path(node: ast.Attribute) -> str:
        pieces = [node.attr]
        current: ast.AST = node.value
        while isinstance(current, ast.Attribute):
            pieces.append(current.attr)
            current = current.value
        return ".".join(reversed(pieces))

    @staticmethod
    def _is_main_guard(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "__name__"
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Eq)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value == "__main__"
        )


def _snake(value: str) -> str:
    value = value.replace("()", "")
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower() or "unknown"


def _json_safe(value: Any) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


_UNSET = object()


@dataclass(frozen=True, slots=True)
class NxSourceSpanEvidence:
    start_byte: int
    end_byte: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    column_encoding: str = "unicode_scalar"

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "column_encoding": self.column_encoding,
        }


@dataclass(slots=True)
class NxFlowNodeEvidence:
    node_id: str
    node_type: str
    risk: str
    review_status: str
    source_span: NxSourceSpanEvidence
    source_role: str
    configuration: dict[str, JsonValue]
    compatibility_status: str = "needs_review"
    fidelity: str = "F2"
    opaque: dict[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class NxFlowEdgeEvidence:
    edge_id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str
    kind: str
    role: str
    condition: dict[str, JsonValue] | None
    priority: int | None


@dataclass(frozen=True, slots=True)
class NxFlowDefinitionEvidence:
    flow_id: str
    kind: str
    name: str
    nodes: tuple[NxFlowNodeEvidence, ...]
    edges: tuple[NxFlowEdgeEvidence, ...]


@dataclass(frozen=True, slots=True)
class NxJournalStaticAnalysis:
    source_file: str
    source_digest: str
    source_bytes: bytes
    encoding: str
    bom: str
    newline_profile: str
    session_name: str
    nxopen_seen: bool
    parser_contract: Mapping[str, JsonValue]
    flows: tuple[NxFlowDefinitionEvidence, ...]
    selectors: tuple[Mapping[str, JsonValue], ...]
    unsupported_regions: tuple[Mapping[str, JsonValue], ...]
    api_symbol_evidence: tuple[Mapping[str, JsonValue], ...]
    preconditions: tuple[Mapping[str, JsonValue], ...]
    diagnostics: tuple[Mapping[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class _PendingControl:
    node_id: str
    port: str = "next"
    role: str = "next"
    condition: dict[str, JsonValue] | None = None
    priority: int | None = None


@dataclass(frozen=True, slots=True)
class _StatementResult:
    entry_node_id: str
    exits: tuple[_PendingControl, ...]


@dataclass(slots=True)
class _StaticBuilderFlowState:
    builder_id: str
    flow_id: str
    factory_api: str
    created_node_id: str
    created_span: NxSourceSpanEvidence
    last_node_id: str
    commit_count: int = 0
    destroy_count: int = 0
    abandoned: bool = False
    destroyed: bool = False
    issues: list[str] = field(default_factory=list)
    conditional_transitions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _StaticUndoFlowState:
    undo_scope_id: str
    mark_binding: str | None
    open_node_id: str
    source_span: NxSourceSpanEvidence
    closed: bool = False
    issues: list[str] = field(default_factory=list)


class _NxFlowEvidenceExtractor:
    """Build NX-specific neutral evidence; frozen JSON shaping lives in nx_flow.py."""

    def __init__(
        self,
        *,
        source: str,
        source_bytes: bytes,
        source_file: str,
        source_digest: str,
        session_name: str,
        bom: str,
        token_count: int,
        limits: NxJournalLimits,
        target_version: str | None,
        target_symbols: Iterable[str] | None,
        selector_match_counts: Mapping[str, int] | None,
    ) -> None:
        self.source = source
        self.source_bytes = source_bytes
        self.source_file = source_file
        self.source_digest = source_digest
        self.session_name = session_name
        self.bom = bom
        self.token_count = token_count
        self.limits = limits
        self.target_version = target_version
        self.target_symbols = (
            frozenset(str(item) for item in target_symbols)
            if target_symbols is not None
            else None
        )
        self.selector_match_counts = dict(selector_match_counts or {})
        self.nxopen_seen = False
        self._base_aliases: dict[str, str] = {}
        self._local_functions: dict[str, ast.FunctionDef] = {}
        self._recursive_functions: set[str] = set()
        self._flows: list[NxFlowDefinitionEvidence] = []
        self._selectors: list[dict[str, JsonValue]] = []
        self._unsupported: list[dict[str, JsonValue]] = []
        self._api_evidence: list[dict[str, JsonValue]] = []
        self._diagnostics: list[dict[str, JsonValue]] = []
        self._node_counter = 0
        self._edge_counter = 0
        self._selector_counter = 0
        self._region_counter = 0
        self._line_byte_offsets = _line_byte_offsets(source)
        self._bom_length = (
            3
            if bom == "utf8" and source_bytes.startswith(b"\xef\xbb\xbf")
            else 0
        )

        self._flow_id = ""
        self._nodes: list[NxFlowNodeEvidence] = []
        self._edges: list[NxFlowEdgeEvidence] = []
        self._aliases: dict[str, str] = {}
        self._builder_aliases: dict[str, str] = {}
        self._builders: dict[str, _StaticBuilderFlowState] = {}
        self._undo_states: dict[str, _StaticUndoFlowState] = {}
        self._active_undo: list[str] = []
        self._control_context: list[str] = []

    def extract(self, tree: ast.Module) -> NxJournalStaticAnalysis:
        self._collect_module_symbols(tree)
        self._build_flow(
            flow_id="flow:main",
            kind="main",
            name="Module",
            statements=tree.body,
            scope_node=tree,
        )
        for name, function in self._local_functions.items():
            if function.decorator_list:
                continue
            self._build_flow(
                flow_id=f"flow:function:{_snake(name)}",
                kind="subflow",
                name=name,
                statements=function.body,
                scope_node=function,
                argument_names=[argument.arg for argument in function.args.args],
            )
        return NxJournalStaticAnalysis(
            source_file=self.source_file,
            source_digest=self.source_digest,
            source_bytes=self.source_bytes,
            encoding="utf-8",
            bom=self.bom,
            newline_profile=_newline_profile(self.source_bytes),
            session_name=self.session_name,
            nxopen_seen=self.nxopen_seen,
            parser_contract={
                "analysis_mode": "ast_token_static",
                "python_ast": "stdlib",
                "token_count": self.token_count,
                "resource_limits": {
                    "max_source_bytes": self.limits.max_source_bytes,
                    "max_ast_nodes": self.limits.max_ast_nodes,
                    "max_ast_depth": self.limits.max_ast_depth,
                    "max_tokens": self.limits.max_tokens,
                    "max_string_bytes": self.limits.max_string_bytes,
                    "max_diagnostics": self.limits.max_diagnostics,
                },
                "journal_imported": False,
                "journal_executed": False,
                "journal_compiled_for_execution": False,
                "journal_replayed": False,
            },
            flows=tuple(self._flows),
            selectors=tuple(self._selectors),
            unsupported_regions=tuple(self._unsupported),
            api_symbol_evidence=tuple(self._api_evidence),
            preconditions=tuple(_nx_preconditions()),
            diagnostics=tuple(self._diagnostics),
        )

    def _collect_module_symbols(self, tree: ast.Module) -> None:
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for imported in statement.names:
                    if imported.name == "NXOpen" or imported.name.startswith("NXOpen."):
                        local = imported.asname or imported.name.split(".", 1)[0]
                        self._base_aliases[local] = (
                            imported.name
                            if imported.asname
                            else imported.name.split(".", 1)[0]
                        )
                        self.nxopen_seen = True
            elif isinstance(statement, ast.ImportFrom):
                module = statement.module or ""
                if module == "NXOpen" or module.startswith("NXOpen."):
                    self.nxopen_seen = True
                    for imported in statement.names:
                        if imported.name != "*":
                            self._base_aliases[imported.asname or imported.name] = (
                                f"{module}.{imported.name}"
                            )
            elif isinstance(statement, ast.FunctionDef):
                self._local_functions[statement.name] = statement
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "NXOpen":
                self.nxopen_seen = True
        self._recursive_functions = _recursive_function_names(self._local_functions)

    def _build_flow(
        self,
        *,
        flow_id: str,
        kind: str,
        name: str,
        statements: Sequence[ast.stmt],
        scope_node: ast.AST,
        argument_names: Sequence[str] = (),
    ) -> None:
        self._flow_id = flow_id
        self._nodes = []
        self._edges = []
        self._aliases = dict(self._base_aliases)
        self._aliases.update({name: name for name in argument_names})
        self._builder_aliases = {}
        self._builders = {}
        self._undo_states = {}
        self._active_undo = []
        self._control_context = []

        start = self._add_node(
            "flow.start",
            scope_node,
            risk="safe",
            review_status="unreviewed",
            role="control",
            configuration={"flow_id": flow_id, "scope": name, "synthetic": True},
            synthetic=True,
        )
        exits = self._build_block(
            statements,
            (_PendingControl(start.node_id),),
        )
        self._finalize_resources()
        end = self._add_node(
            "flow.end",
            scope_node,
            risk="safe",
            review_status="unreviewed",
            role="control",
            configuration={"flow_id": flow_id, "scope": name, "synthetic": True},
            synthetic=True,
        )
        for pending in exits:
            self._connect(pending, end.node_id)
        self._flows.append(
            NxFlowDefinitionEvidence(
                flow_id=flow_id,
                kind=kind,
                name=name,
                nodes=tuple(self._nodes),
                edges=tuple(self._edges),
            )
        )

    def _build_block(
        self,
        statements: Sequence[ast.stmt],
        incoming: tuple[_PendingControl, ...],
    ) -> tuple[_PendingControl, ...]:
        pending = incoming
        for statement in statements:
            result = self._build_statement(statement)
            for edge in pending:
                self._connect(edge, result.entry_node_id)
            pending = result.exits
        return pending

    def _build_statement(self, node: ast.stmt) -> _StatementResult:
        if isinstance(node, ast.FunctionDef):
            if self._flow_id != "flow:main":
                return self._unsupported_statement(
                    node, "nested_function_or_closure_unsupported"
                )
            if node.decorator_list:
                return self._unsupported_statement(
                    node, "unknown_or_runtime_function_decorator"
                )
            subflow_id = f"flow:function:{_snake(node.name)}"
            flow_node = self._add_node(
                "flow.function.define",
                node,
                risk="safe",
                review_status="unreviewed",
                role="control",
                configuration={
                    "function_name": node.name,
                    "subflow_id": subflow_id,
                    "automatically_invoked": False,
                    "arguments": [argument.arg for argument in node.args.args],
                },
            )
            return self._simple_result(flow_node)
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef)):
            return self._unsupported_statement(node, "runtime_definition_unsupported")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return self._build_import(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            return self._build_assignment(node)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return self._build_call(node.value, result_target=None)
        if isinstance(node, ast.Expr):
            return self._build_expression_statement(node)
        if isinstance(node, ast.If):
            return self._build_if(node)
        if isinstance(node, (ast.For, ast.While)):
            return self._build_loop(node)
        if isinstance(node, ast.AsyncFor):
            return self._unsupported_statement(
                node, "async_iteration_unsupported"
            )
        if isinstance(node, ast.Try):
            return self._build_try(node)
        if isinstance(node, ast.Raise):
            flow_node = self._add_node(
                "flow.raise",
                node,
                risk="review",
                review_status="needs_review",
                role="control",
                configuration={
                    "exception": self._expression(node.exc) if node.exc else None,
                    "static_only": True,
                },
            )
            return _StatementResult(flow_node.node_id, ())
        if isinstance(node, ast.Return):
            flow_node = self._add_node(
                "flow.return",
                node,
                risk="review",
                review_status="needs_review",
                role="control",
                configuration={
                    "value": self._expression(node.value) if node.value else None
                },
            )
            return _StatementResult(flow_node.node_id, ())
        if isinstance(node, (ast.Break, ast.Continue)):
            flow_node = self._add_node(
                f"flow.{type(node).__name__.lower()}",
                node,
                risk="review",
                review_status="needs_review",
                role="control",
                configuration={"static_only": True},
            )
            return _StatementResult(flow_node.node_id, ())
        if isinstance(node, ast.Pass):
            flow_node = self._add_node(
                "flow.pass",
                node,
                risk="safe",
                review_status="unreviewed",
                role="control",
                configuration={},
            )
            return self._simple_result(flow_node)
        return self._unsupported_statement(
            node, f"ast_statement_{type(node).__name__.lower()}_unsupported"
        )

    def _build_import(self, node: ast.Import | ast.ImportFrom) -> _StatementResult:
        imported_names: list[str] = []
        nx_import = False
        if isinstance(node, ast.Import):
            for imported in node.names:
                imported_names.append(imported.name)
                if imported.name == "NXOpen" or imported.name.startswith("NXOpen."):
                    nx_import = True
                    local = imported.asname or imported.name.split(".", 1)[0]
                    self._aliases[local] = (
                        imported.name
                        if imported.asname
                        else imported.name.split(".", 1)[0]
                    )
        else:
            module = node.module or ""
            imported_names.append(module)
            if module == "NXOpen" or module.startswith("NXOpen."):
                nx_import = True
                for imported in node.names:
                    if imported.name != "*":
                        self._aliases[imported.asname or imported.name] = (
                            f"{module}.{imported.name}"
                        )
        if not nx_import:
            return self._unsupported_statement(node, "non_nx_import_unsupported")
        self.nxopen_seen = True
        flow_node = self._add_node(
            "nx.import",
            node,
            risk="safe",
            review_status="unreviewed",
            role="primary",
            configuration={
                "modules": imported_names,
                "imported_at_runtime": False,
                "evidence_only": True,
            },
        )
        return self._simple_result(flow_node)

    def _build_assignment(
        self, node: ast.Assign | ast.AnnAssign | ast.AugAssign
    ) -> _StatementResult:
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            if node.value is None:
                return self._unsupported_statement(node, "annotation_without_value")
            value = node.value
        else:
            targets = [node.target]
            value = node.value

        if _contains_unsupported_expression(value):
            return self._unsupported_statement(
                node, "dynamic_or_container_expression_unsupported"
            )
        if isinstance(value, ast.Call):
            target_name = next(
                (target.id for target in targets if isinstance(target, ast.Name)),
                None,
            )
            result = self._build_call(value, result_target=target_name)
            self._bind_assignment_targets(targets, value)
            return result

        attribute_target = next(
            (target for target in targets if isinstance(target, ast.Attribute)),
            None,
        )
        if attribute_target is not None:
            builder_id = self._builder_id_for_expr(attribute_target)
            if builder_id:
                return self._build_builder_configuration(
                    node,
                    builder_id=builder_id,
                    api_path=self._expr_path(attribute_target),
                    property_path=_relative_attribute_path(attribute_target),
                    value=value,
                )
            api_path = self._expr_path(attribute_target)
            if self._is_nx_path(api_path):
                flow_node = self._add_node(
                    f"cam.parameter.set.{_snake(attribute_target.attr)}",
                    node,
                    risk="review",
                    review_status="needs_review",
                    role="parameter",
                    configuration={
                        "api_symbol": api_path,
                        "property_path": _relative_attribute_path(attribute_target),
                        "value": self._expression(value),
                        "evidence_only": True,
                        "assigned_during_analysis": False,
                    },
                )
                self._record_api_evidence(api_path, flow_node)
                return self._simple_result(flow_node)

        self._bind_assignment_targets(targets, value)
        if _contains_journal_identifier(value):
            return self._build_selector(
                node,
                selector_source="JournalIdentifier",
                recorded_identifier=None,
                api_symbol=self._expr_path(value),
                result_target=next(
                    (target.id for target in targets if isinstance(target, ast.Name)),
                    None,
                ),
            )
        target_text = [
            self._expr_path(target) or type(target).__name__ for target in targets
        ]
        dynamic = _static_literal(value) is _UNSET and not isinstance(value, ast.Name)
        flow_node = self._add_node(
            "flow.assignment",
            node,
            risk="review" if dynamic else "safe",
            review_status="needs_review" if dynamic else "unreviewed",
            role="parameter" if not dynamic else "primary",
            configuration={
                "targets": target_text,
                "value": self._expression(value),
                "dynamic": dynamic,
            },
        )
        return self._simple_result(flow_node)

    def _build_expression_statement(self, node: ast.Expr) -> _StatementResult:
        if _contains_unsupported_expression(node.value):
            return self._unsupported_statement(
                node, "dynamic_expression_statement_unsupported"
            )
        flow_node = self._add_node(
            "flow.expression",
            node,
            risk="review",
            review_status="needs_review",
            role="primary",
            configuration={"expression": self._expression(node.value)},
        )
        return self._simple_result(flow_node)

    def _build_call(
        self, call: ast.Call, *, result_target: str | None
    ) -> _StatementResult:
        api_path = self._expr_path(call.func)
        method = api_path.rsplit(".", 1)[-1] if api_path else ""
        builder_id = self._builder_id_for_call(call)
        if self._is_safety_forbidden_call(api_path, method, call):
            return self._unsupported_statement(
                call,
                "live_or_machine_output_api_forbidden",
                code=(
                    "SAFETY_MACHINE_OUTPUT_FORBIDDEN"
                    if _is_machine_output_api(api_path)
                    else "SAFETY_LIVE_EXECUTION_FORBIDDEN"
                ),
                redact=True,
            )
        if api_path in self._local_functions:
            recursive = api_path in self._recursive_functions
            flow_node = self._add_node(
                "flow.subflow_call",
                call,
                risk="blocked" if recursive else "review",
                review_status="rejected" if recursive else "needs_review",
                role="primary",
                configuration={
                    "function_name": api_path,
                    "subflow_id": f"flow:function:{_snake(api_path)}",
                    "arguments": [self._expression(item) for item in call.args],
                    "executed_during_analysis": False,
                    "result_target": result_target,
                    "recursive": recursive,
                },
                compatibility_status="unsupported" if recursive else "needs_review",
            )
            if recursive:
                self._diagnostic(
                    "FLOW_SUBFLOW_RECURSION",
                    "blocker",
                    flow_node.node_id,
                    "Recursive local function flow is outside the MVP contract.",
                    "Replace recursion with a reviewed non-recursive flow.",
                )
            return self._simple_result(flow_node)
        if not api_path or (
            not self._is_nx_path(api_path)
            and builder_id is None
            and api_path not in _SAFE_PYTHON_CALLS
        ):
            return self._unsupported_statement(
                call, "dynamic_or_untrusted_call_unsupported"
            )
        if api_path in _SAFE_PYTHON_CALLS:
            if _MACHINE_OUTPUT_PATTERN.search(self._source_text(call)):
                return self._unsupported_statement(
                    call,
                    "machine_output_text_forbidden",
                    code="SAFETY_MACHINE_OUTPUT_FORBIDDEN",
                    redact=True,
                )
            flow_node = self._add_node(
                "flow.python.pure_call",
                call,
                risk="safe",
                review_status="unreviewed",
                role="primary",
                configuration={
                    "call": api_path,
                    "arguments": [self._expression(item) for item in call.args],
                    "executed_during_analysis": False,
                },
            )
            return self._simple_result(flow_node)

        self.nxopen_seen = True
        if method == "FindObject":
            identifier = (
                _static_literal(call.args[0]) if call.args else _UNSET
            )
            return self._build_selector(
                call,
                selector_source="FindObject",
                recorded_identifier=identifier if isinstance(identifier, str) else None,
                api_symbol=api_path,
                result_target=result_target,
            )
        if method == "SetUndoMark":
            return self._open_undo_scope(call, api_path, result_target)
        if method in {"SetUndoMarkName", "DeleteUndoMark"}:
            return self._update_undo_scope(call, api_path, method)
        if builder_id is not None:
            if method == "Commit":
                return self._commit_builder(call, api_path, builder_id, result_target)
            if method in {"Destroy", "Dispose"}:
                return self._destroy_builder(call, api_path, builder_id)
            if method.startswith("Set") and len(method) > 3:
                return self._build_builder_configuration(
                    call,
                    builder_id=builder_id,
                    api_path=api_path,
                    property_path=method[3:],
                    value=call.args[0] if call.args else None,
                )
            state = self._builders.get(builder_id)
            if state and state.destroyed:
                state.issues.append("builder_used_after_destroy")

        if _is_builder_factory(method):
            flow_node = self._add_api_node(
                "cam.builder.create",
                call,
                api_path=api_path,
                risk="review",
                result_target=result_target,
                arguments=call.args,
            )
            if result_target:
                self._builders[result_target] = _StaticBuilderFlowState(
                    builder_id=result_target,
                    flow_id=self._flow_id,
                    factory_api=api_path,
                    created_node_id=flow_node.node_id,
                    created_span=flow_node.source_span,
                    last_node_id=flow_node.node_id,
                )
                self._builder_aliases[result_target] = result_target
                flow_node.configuration["builder_id"] = result_target
                flow_node.configuration["lifecycle_transition"] = "create"
            return self._simple_result(flow_node)

        node_type, risk = _node_type_for_nx_call(api_path, method)
        flow_node = self._add_api_node(
            node_type,
            call,
            api_path=api_path,
            risk=risk,
            result_target=result_target,
            arguments=call.args,
        )
        return self._simple_result(flow_node)

    def _build_if(self, node: ast.If) -> _StatementResult:
        expression, unsupported_region = self._control_expression(node.test)
        branch = self._add_node(
            "flow.if",
            node.test,
            risk="blocked" if unsupported_region else "review",
            review_status="rejected" if unsupported_region else "needs_review",
            role="control",
            configuration={
                "condition": expression,
                "condition_evaluated": False,
                "both_branches_are_possible": True,
                "unsupported_region_id": unsupported_region,
            },
            compatibility_status="unsupported" if unsupported_region else "needs_review",
        )
        self._control_context.append(f"if:{node.lineno}")
        true_exits = self._build_block(
            node.body,
            (
                _PendingControl(
                    branch.node_id,
                    port="true",
                    role="true",
                    condition={"expression": expression, "expected": True},
                    priority=0,
                ),
            ),
        )
        false_exits = self._build_block(
            node.orelse,
            (
                _PendingControl(
                    branch.node_id,
                    port="false",
                    role="false",
                    condition={"expression": expression, "expected": False},
                    priority=1,
                ),
            ),
        )
        self._control_context.pop()
        merge = self._add_node(
            "flow.merge",
            node,
            risk="safe",
            review_status="unreviewed",
            role="control",
            configuration={"control_kind": "if", "synthetic": True},
            synthetic=True,
        )
        for pending in (*true_exits, *false_exits):
            self._connect(
                _PendingControl(
                    pending.node_id,
                    pending.port,
                    "merge",
                    pending.condition,
                    pending.priority,
                ),
                merge.node_id,
            )
        return self._simple_result(merge, entry_node_id=branch.node_id)

    def _build_loop(
        self, node: ast.For | ast.AsyncFor | ast.While
    ) -> _StatementResult:
        if isinstance(node, (ast.For, ast.AsyncFor)):
            expression_node = node.iter
            loop_kind = "async_for" if isinstance(node, ast.AsyncFor) else "for"
            target = self._expr_path(node.target) or type(node.target).__name__
        else:
            expression_node = node.test
            loop_kind = "while"
            target = None
        expression, unsupported_region = self._control_expression(expression_node)
        loop = self._add_node(
            "flow.loop",
            node,
            risk="blocked" if unsupported_region else "review",
            review_status="rejected" if unsupported_region else "needs_review",
            role="control",
            configuration={
                "loop_kind": loop_kind,
                "target": target,
                "expression": expression,
                "expression_evaluated": False,
                "iteration_count": None,
                "unsupported_region_id": unsupported_region,
            },
            compatibility_status="unsupported" if unsupported_region else "needs_review",
        )
        self._control_context.append(f"loop:{node.lineno}")
        body_exits = self._build_block(
            node.body,
            (
                _PendingControl(
                    loop.node_id,
                    port="body",
                    role="loop_body",
                    condition={"expression": expression, "continues": True},
                    priority=0,
                ),
            ),
        )
        self._control_context.pop()
        for pending in body_exits:
            self._connect(
                _PendingControl(
                    pending.node_id,
                    pending.port,
                    "loop_back",
                    None,
                    0,
                ),
                loop.node_id,
                target_port="repeat",
            )
        loop_exit = (
            _PendingControl(
                loop.node_id,
                port="exit",
                role="loop_exit",
                condition={"expression": expression, "continues": False},
                priority=1,
            ),
        )
        if node.orelse:
            loop_exit = self._build_block(node.orelse, loop_exit)
        merge = self._add_node(
            "flow.merge",
            node,
            risk="safe",
            review_status="unreviewed",
            role="control",
            configuration={"control_kind": "loop", "synthetic": True},
            synthetic=True,
        )
        for pending in loop_exit:
            self._connect(pending, merge.node_id)
        return self._simple_result(merge, entry_node_id=loop.node_id)

    def _build_try(self, node: ast.Try) -> _StatementResult:
        boundary = self._add_node(
            "flow.try",
            node,
            risk="review",
            review_status="needs_review",
            role="control",
            configuration={
                "handler_count": len(node.handlers),
                "has_else": bool(node.orelse),
                "has_finally": bool(node.finalbody),
                "exceptions_evaluated": False,
            },
        )
        self._control_context.append(f"try:{node.lineno}")
        body_exits = self._build_block(
            node.body,
            (_PendingControl(boundary.node_id, "protected", "protected", None, 0),),
        )
        if node.orelse:
            body_exits = self._build_block(
                node.orelse,
                tuple(
                    _PendingControl(item.node_id, item.port, "try_else")
                    for item in body_exits
                ),
            )
        handler_exits: list[_PendingControl] = []
        for index, handler in enumerate(node.handlers):
            exception = self._expr_path(handler.type) if handler.type else "BaseException"
            handler_node = self._add_node(
                "flow.except",
                handler,
                risk="review",
                review_status="needs_review",
                role="control",
                configuration={
                    "exception": exception,
                    "binding": handler.name,
                    "exception_match_evaluated": False,
                },
            )
            self._connect(
                _PendingControl(
                    boundary.node_id,
                    port="exception",
                    role="exception",
                    condition={"exception": exception},
                    priority=index,
                ),
                handler_node.node_id,
            )
            handler_exits.extend(
                self._build_block(
                    handler.body,
                    (_PendingControl(handler_node.node_id),),
                )
            )
        self._control_context.pop()
        protected_exits = [*body_exits, *handler_exits]
        if node.finalbody:
            finally_node = self._add_node(
                "flow.finally",
                node.finalbody[0],
                risk="review",
                review_status="needs_review",
                role="control",
                configuration={"always_after_try_region": True},
            )
            for pending in protected_exits or [
                _PendingControl(boundary.node_id, role="finally")
            ]:
                self._connect(
                    _PendingControl(
                        pending.node_id,
                        pending.port,
                        "finally",
                        pending.condition,
                        pending.priority,
                    ),
                    finally_node.node_id,
                )
            self._control_context.append(f"finally:{node.lineno}")
            protected_exits = list(
                self._build_block(
                    node.finalbody,
                    (_PendingControl(finally_node.node_id),),
                )
            )
            self._control_context.pop()
        merge = self._add_node(
            "flow.merge",
            node,
            risk="safe",
            review_status="unreviewed",
            role="control",
            configuration={"control_kind": "try", "synthetic": True},
            synthetic=True,
        )
        for pending in protected_exits:
            self._connect(
                _PendingControl(pending.node_id, pending.port, "merge"),
                merge.node_id,
            )
        return self._simple_result(merge, entry_node_id=boundary.node_id)

    def _build_builder_configuration(
        self,
        source_node: ast.AST,
        *,
        builder_id: str,
        api_path: str,
        property_path: str,
        value: ast.AST | None,
    ) -> _StatementResult:
        state = self._builders.get(builder_id)
        issues: list[str] = []
        if state is None:
            issues.append("builder_definition_unresolved")
        elif state.destroyed:
            issues.append("builder_used_after_destroy")
            state.issues.extend(issues)
        flow_node = self._add_node(
            "cam.builder.configure",
            source_node,
            risk="blocked" if issues else "review",
            review_status="rejected" if issues else "needs_review",
            role="parameter",
            configuration={
                "builder_id": builder_id,
                "api_symbol": api_path,
                "property_path": property_path,
                "value": self._expression(value) if value is not None else None,
                "lifecycle_transition": "configure",
                "issues": issues,
            },
            compatibility_status="unsupported" if issues else "needs_review",
        )
        if state is not None:
            self._dependency(state.last_node_id, flow_node.node_id, "configures")
            state.last_node_id = flow_node.node_id
        if api_path:
            self._record_api_evidence(api_path, flow_node)
        return self._simple_result(flow_node)

    def _commit_builder(
        self,
        call: ast.Call,
        api_path: str,
        builder_id: str,
        result_target: str | None,
    ) -> _StatementResult:
        state = self._builders.get(builder_id)
        issues: list[str] = []
        if state is None:
            issues.append("builder_definition_unresolved")
        else:
            if state.commit_count:
                issues.append("builder_committed_more_than_once")
            if state.destroyed:
                issues.append("builder_committed_after_destroy")
            if self._control_context:
                state.conditional_transitions.extend(self._control_context)
            state.commit_count += 1
            state.issues.extend(issues)
        flow_node = self._add_api_node(
            "cam.builder.commit",
            call,
            api_path=api_path,
            risk="blocked" if issues else "review",
            result_target=result_target,
            arguments=call.args,
            extra_configuration={
                "builder_id": builder_id,
                "lifecycle_transition": "commit",
                "issues": issues,
                "conditional_context": list(self._control_context),
            },
        )
        if state is not None:
            self._dependency(state.last_node_id, flow_node.node_id, "commits")
            state.last_node_id = flow_node.node_id
        return self._simple_result(flow_node)

    def _destroy_builder(
        self, call: ast.Call, api_path: str, builder_id: str
    ) -> _StatementResult:
        state = self._builders.get(builder_id)
        issues: list[str] = []
        if state is None:
            issues.append("builder_definition_unresolved")
        elif state.destroyed:
            issues.append("builder_destroyed_more_than_once")
            state.issues.extend(issues)

        abandon_node: NxFlowNodeEvidence | None = None
        if state is not None and state.commit_count == 0 and not state.destroyed:
            abandon_node = self._add_node(
                "cam.builder.abandon",
                call,
                risk="review",
                review_status="needs_review",
                role="primary",
                configuration={
                    "builder_id": builder_id,
                    "lifecycle_transition": "abandon",
                    "reason": "destroyed_without_commit",
                },
            )
            self._dependency(state.last_node_id, abandon_node.node_id, "abandons")
            state.last_node_id = abandon_node.node_id
            state.abandoned = True
        flow_node = self._add_api_node(
            "cam.builder.destroy",
            call,
            api_path=api_path,
            risk="blocked" if issues else "review",
            result_target=None,
            arguments=call.args,
            extra_configuration={
                "builder_id": builder_id,
                "lifecycle_transition": "destroy",
                "issues": issues,
            },
        )
        if state is not None:
            self._dependency(state.last_node_id, flow_node.node_id, "destroys")
            state.last_node_id = flow_node.node_id
            state.destroy_count += 1
            state.destroyed = True
        if abandon_node is not None:
            self._connect(_PendingControl(abandon_node.node_id), flow_node.node_id)
            return self._simple_result(
                flow_node, entry_node_id=abandon_node.node_id
            )
        return self._simple_result(flow_node)

    def _open_undo_scope(
        self, call: ast.Call, api_path: str, result_target: str | None
    ) -> _StatementResult:
        flow_node = self._add_api_node(
            "nx.undo.scope.open",
            call,
            api_path=api_path,
            risk="review",
            result_target=result_target,
            arguments=call.args,
        )
        scope_id = f"undo:{result_target or flow_node.node_id}"
        state = _StaticUndoFlowState(
            undo_scope_id=scope_id,
            mark_binding=result_target,
            open_node_id=flow_node.node_id,
            source_span=flow_node.source_span,
        )
        self._undo_states[scope_id] = state
        self._active_undo.append(scope_id)
        flow_node.configuration.update(
            {
                "undo_scope_id": scope_id,
                "lifecycle_transition": "open",
                "mark_binding_dynamic": result_target is None,
            }
        )
        return self._simple_result(flow_node)

    def _update_undo_scope(
        self, call: ast.Call, api_path: str, method: str
    ) -> _StatementResult:
        binding = call.args[0].id if call.args and isinstance(call.args[0], ast.Name) else None
        state = next(
            (
                value
                for value in self._undo_states.values()
                if value.mark_binding == binding and not value.closed
            ),
            None,
        )
        issues = [] if state else ["undo_scope_unresolved"]
        node_type = (
            "nx.undo.scope.rename"
            if method == "SetUndoMarkName"
            else "nx.undo.scope.close"
        )
        flow_node = self._add_api_node(
            node_type,
            call,
            api_path=api_path,
            risk="blocked" if issues else "review",
            result_target=None,
            arguments=call.args,
            extra_configuration={
                "undo_scope_id": state.undo_scope_id if state else None,
                "mark_binding": binding,
                "lifecycle_transition": "rename" if "Name" in method else "close",
                "issues": issues,
            },
        )
        if state:
            self._dependency(state.open_node_id, flow_node.node_id, "undo_scope")
            if method == "DeleteUndoMark":
                state.closed = True
                if state.undo_scope_id in self._active_undo:
                    if self._active_undo[-1] != state.undo_scope_id:
                        state.issues.append("undo_scope_closed_out_of_order")
                    self._active_undo.remove(state.undo_scope_id)
        else:
            self._diagnostic(
                "NX_UNDO_SCOPE_UNRESOLVED",
                "blocker",
                flow_node.node_id,
                "Undo mark reference cannot be resolved statically.",
                "Review the recorded mark binding and scope nesting.",
            )
        return self._simple_result(flow_node)

    def _build_selector(
        self,
        source_node: ast.AST,
        *,
        selector_source: str,
        recorded_identifier: str | None,
        api_symbol: str,
        result_target: str | None,
    ) -> _StatementResult:
        self._selector_counter += 1
        selector_id = f"selector:{self._selector_counter:04d}"
        match_key = recorded_identifier if recorded_identifier is not None else selector_id
        match_count = self.selector_match_counts.get(match_key)
        if match_count is not None and match_count < 0:
            raise NxJournalResourceLimitError(
                "selector match evidence count must be non-negative"
            )
        resolution = (
            "missing"
            if match_count == 0
            else "ambiguous"
            if match_count is not None and match_count > 1
            else "candidate"
            if match_count == 1
            else "unresolved"
        )
        blocked = True
        assessment = assess_find_object(recorded_identifier).to_dict()
        selector: dict[str, JsonValue] = {
            "selector_id": selector_id,
            "source": selector_source,
            "recorded_identifier": recorded_identifier,
            "fragile": True,
            "resolved": False,
            "resolution_status": resolution,
            "expected_cardinality": "exactly_one",
            "ambiguity_policy": "reject_zero_or_many",
            "observed_match_count": match_count,
            "selected_object": None,
            "unique_match_claimed": False,
            "result_target": result_target,
            "assessment": assessment,
        }
        self._selectors.append(selector)
        flow_node = self._add_node(
            (
                "nx.selector.find_object"
                if selector_source == "FindObject"
                else "nx.selector.journal_identifier"
            ),
            source_node,
            risk="blocked" if blocked else "review",
            review_status="rejected" if blocked else "needs_review",
            role="primary",
            configuration={
                "selector_id": selector_id,
                "api_symbol": api_symbol,
                "selector": selector,
                "evidence_only": True,
            },
            compatibility_status="unsupported" if blocked else "unknown",
        )
        selector["node_id"] = flow_node.node_id
        self._record_api_evidence(api_symbol, flow_node)
        selector_message = (
            "Selector evidence has zero or multiple matches; no object was selected."
            if resolution in {"missing", "ambiguous"}
            else "Recorded selector evidence is not a verified unique target."
        )
        self._diagnostic(
            "COMPAT_SELECTOR_UNRESOLVED",
            "blocker",
            flow_node.node_id,
            selector_message,
            "Provide a reviewed selector that verifies exactly one target.",
        )
        return self._simple_result(flow_node)

    def _add_api_node(
        self,
        node_type: str,
        source_node: ast.AST,
        *,
        api_path: str,
        risk: str,
        result_target: str | None,
        arguments: Sequence[ast.AST],
        extra_configuration: Mapping[str, JsonValue] | None = None,
    ) -> NxFlowNodeEvidence:
        configuration: dict[str, JsonValue] = {
            "api_symbol": api_path,
            "arguments": [self._expression(item) for item in arguments],
            "result_target": result_target,
            "evidence_only": True,
            "called_during_analysis": False,
        }
        if extra_configuration:
            configuration.update(extra_configuration)
        node = self._add_node(
            node_type,
            source_node,
            risk=risk,
            review_status="rejected" if risk == "blocked" else "needs_review",
            role="primary",
            configuration=configuration,
        )
        self._record_api_evidence(api_path, node)
        return node

    def _record_api_evidence(
        self, api_path: str, node: NxFlowNodeEvidence
    ) -> None:
        normalized = api_path.replace("()", "")
        uf_coverage_gap = ".UF" in normalized
        unresolved_instance_path = "[]" in normalized or "()" in api_path[:-2]
        coverage_gap = (
            "uf_wrapped_method_not_proven"
            if uf_coverage_gap
            else "recorded_instance_path_not_exact_stub_symbol"
            if unresolved_instance_path
            else None
        )
        if uf_coverage_gap:
            status = "unknown"
            node.risk = "blocked"
            node.review_status = "rejected"
            node.compatibility_status = "unsupported"
            self._diagnostic(
                "CAPABILITY_MISSING",
                "blocker",
                node.node_id,
                "UF wrapped API coverage is not proven by NX IntelliSense stubs.",
                "Keep the call blocked until target-version evidence is reviewed.",
            )
        elif unresolved_instance_path:
            status = "unresolved_recorded_instance_symbol"
        elif self.target_symbols is None:
            status = "target_stubs_not_supplied"
        elif normalized in self.target_symbols:
            status = "observed_in_target_stubs"
        else:
            status = "absent_from_target_stubs"
            node.risk = "blocked"
            node.review_status = "rejected"
            node.compatibility_status = "unsupported"
            self._diagnostic(
                "CAPABILITY_MISSING",
                "blocker",
                node.node_id,
                "The exact recorded API symbol is absent from the supplied target stubs.",
                "Review the target NX version and exact local stub contract.",
            )
        evidence: dict[str, JsonValue] = {
            "node_id": node.node_id,
            "api_symbol": api_path,
            "normalized_symbol": normalized,
            "target_version": self.target_version,
            "status": status,
            "coverage_gap": coverage_gap,
            "capability_claimed": False,
        }
        self._api_evidence.append(evidence)
        node.configuration["api_evidence"] = evidence

    def _add_node(
        self,
        node_type: str,
        source_node: ast.AST,
        *,
        risk: str,
        review_status: str,
        role: str,
        configuration: Mapping[str, JsonValue],
        compatibility_status: str = "needs_review",
        fidelity: str = "F2",
        opaque: Mapping[str, JsonValue] | None = None,
        synthetic: bool = False,
    ) -> NxFlowNodeEvidence:
        self._node_counter += 1
        node_id = f"node:{self._node_counter:06d}"
        span = self._span(source_node, synthetic=synthetic)
        node = NxFlowNodeEvidence(
            node_id=node_id,
            node_type=node_type,
            risk=risk,
            review_status=review_status,
            source_span=span,
            source_role=role,
            configuration=dict(configuration),
            compatibility_status=compatibility_status,
            fidelity=fidelity,
            opaque=dict(opaque) if opaque is not None else None,
        )
        self._nodes.append(node)
        if (
            self._active_undo
            and not node_type.startswith("nx.undo.")
            and (
                node_type.startswith("cam.builder.")
                or node_type.startswith("cam.operation.")
                or node_type.startswith("cam.parameter.")
                or node_type.startswith("nx.api.")
            )
        ):
            scope_ids = list(self._active_undo)
            node.configuration["undo_scope_ids"] = scope_ids
            for scope_id in scope_ids:
                state = self._undo_states[scope_id]
                self._dependency(state.open_node_id, node_id, "guards")
        return node

    def _connect(
        self,
        pending: _PendingControl,
        target_node_id: str,
        *,
        target_port: str = "in",
    ) -> None:
        self._edge_counter += 1
        self._edges.append(
            NxFlowEdgeEvidence(
                edge_id=f"edge:{self._edge_counter:06d}",
                source_node_id=pending.node_id,
                source_port=pending.port,
                target_node_id=target_node_id,
                target_port=target_port,
                kind="control",
                role=pending.role,
                condition=pending.condition,
                priority=pending.priority,
            )
        )

    def _dependency(self, source: str, target: str, role: str) -> None:
        self._edge_counter += 1
        self._edges.append(
            NxFlowEdgeEvidence(
                edge_id=f"edge:{self._edge_counter:06d}",
                source_node_id=source,
                source_port="dependency",
                target_node_id=target,
                target_port="dependency",
                kind="dependency",
                role=role,
                condition=None,
                priority=None,
            )
        )

    def _simple_result(
        self, node: NxFlowNodeEvidence, *, entry_node_id: str | None = None
    ) -> _StatementResult:
        return _StatementResult(
            entry_node_id or node.node_id,
            (_PendingControl(node.node_id),),
        )

    def _unsupported_statement(
        self,
        node: ast.AST,
        reason: str,
        *,
        code: str = "FLOW_UNKNOWN_SEMANTICS",
        redact: bool = False,
    ) -> _StatementResult:
        span = self._span(node)
        text = self._source_text(node)
        redact = (
            redact
            or bool(_MACHINE_OUTPUT_PATTERN.search(text))
            or _contains_absolute_path_literal(node)
        )
        region = self._register_unsupported_region(
            node,
            reason,
            code=code,
            redact=redact,
        )
        opaque: dict[str, JsonValue] = {
            "reason": reason,
            "source_span": span.to_dict(),
            "source_excerpt_hash": _sha256(self._slice(span)),
            "original_text": None if redact else text,
            "payload_redacted": redact,
            "blocker": True,
            "preservation_mode": "opaque",
            "diagnostic_code": code,
        }
        flow_node = self._add_node(
            "opaque.safety_forbidden" if redact else "opaque.unsupported",
            node,
            risk="blocked",
            review_status="rejected",
            role="opaque",
            configuration={
                "unsupported_region_id": region["region_id"],
                "reason_code": reason,
                "source_excerpt_hash": region["source_excerpt_hash"],
                "payload_redacted": redact,
                "executed_during_analysis": False,
            },
            compatibility_status="unsupported",
            fidelity="F3",
            opaque=opaque,
        )
        region["node_id"] = flow_node.node_id
        self._diagnostic(
            code,
            "blocker",
            flow_node.node_id,
            "Unsupported Journal syntax is preserved as a blocked opaque region.",
            "Review the exact source span; do not execute or silently rewrite it.",
        )
        return self._simple_result(flow_node)

    def _register_unsupported_region(
        self,
        node: ast.AST,
        reason: str,
        *,
        code: str,
        redact: bool,
    ) -> dict[str, JsonValue]:
        self._region_counter += 1
        span = self._span(node)
        text = self._source_text(node)
        region: dict[str, JsonValue] = {
            "region_id": f"unsupported:{self._region_counter:04d}",
            "ast_kind": type(node).__name__,
            "reason_code": reason,
            "source_digest": self.source_digest,
            "source_span": span.to_dict(),
            "source_excerpt_hash": _sha256(self._slice(span)),
            "original_text": None if redact else text,
            "payload_redacted": redact,
            "blocking_scope": self._flow_id,
            "preservation_mode": "opaque",
            "review_required": True,
            "blocker": True,
            "diagnostic_code": code,
        }
        self._unsupported.append(region)
        return region

    def _control_expression(
        self, node: ast.AST
    ) -> tuple[dict[str, JsonValue], str | None]:
        expression = self._expression(node)
        reason = _unsupported_expression_reason(node)
        if not reason:
            return expression, None
        region = self._register_unsupported_region(
            node,
            reason,
            code="FLOW_UNKNOWN_SEMANTICS",
            redact=bool(_MACHINE_OUTPUT_PATTERN.search(self._source_text(node))),
        )
        self._diagnostic(
            "FLOW_UNKNOWN_SEMANTICS",
            "blocker",
            self._flow_id,
            "A control expression contains unsupported dynamic semantics.",
            "Keep both control paths visible and review the exact source span.",
        )
        return expression, str(region["region_id"])

    def _diagnostic(
        self,
        code: str,
        severity: str,
        object_ref: str,
        message: str,
        remediation: str,
    ) -> None:
        if len(self._diagnostics) >= self.limits.max_diagnostics:
            raise NxJournalResourceLimitError(
                "NX Journal diagnostic count exceeds the configured limit"
            )
        self._diagnostics.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "object_ref": object_ref,
                "source_mapping_ids": [],
                "remediation": remediation,
            }
        )

    def _finalize_resources(self) -> None:
        for state in self._builders.values():
            issues = list(state.issues)
            if state.commit_count == 0 and not state.abandoned:
                issues.append("builder_not_committed_or_abandoned")
            if state.commit_count > 1:
                issues.append("builder_committed_more_than_once")
            if state.destroy_count == 0:
                issues.append("builder_not_destroyed")
            if state.destroy_count > 1:
                issues.append("builder_destroyed_more_than_once")
            if state.conditional_transitions:
                issues.append("builder_lifecycle_is_path_dependent")
            issues = list(dict.fromkeys(issues))
            if not issues:
                continue
            diagnostic_node = self._add_node(
                "nx.builder.lifecycle_incomplete",
                _SyntheticAstNode.from_span(state.created_span),
                risk="blocked",
                review_status="rejected",
                role="primary",
                configuration={
                    "builder_id": state.builder_id,
                    "factory_api": state.factory_api,
                    "commit_count": state.commit_count,
                    "destroy_count": state.destroy_count,
                    "abandoned": state.abandoned,
                    "destroyed": state.destroyed,
                    "conditional_transitions": list(state.conditional_transitions),
                    "issues": issues,
                },
                compatibility_status="unsupported",
            )
            self._dependency(state.last_node_id, diagnostic_node.node_id, "lifecycle")
            self._diagnostic(
                "NX_BUILDER_LIFECYCLE_INCOMPLETE",
                "blocker",
                diagnostic_node.node_id,
                "Builder lifecycle is incomplete, repeated, or path-dependent.",
                "Review create/configure/commit-or-abandon/destroy on every path.",
            )
        for state in self._undo_states.values():
            issues = list(state.issues)
            if not state.closed:
                issues.append("undo_scope_not_closed")
            if not issues:
                continue
            diagnostic_node = self._add_node(
                "nx.undo.scope_incomplete",
                _SyntheticAstNode.from_span(state.source_span),
                risk="blocked",
                review_status="rejected",
                role="primary",
                configuration={
                    "undo_scope_id": state.undo_scope_id,
                    "issues": list(dict.fromkeys(issues)),
                    "production_rollback_claimed": False,
                },
                compatibility_status="unsupported",
            )
            self._dependency(state.open_node_id, diagnostic_node.node_id, "undo_scope")
            self._diagnostic(
                "NX_UNDO_SCOPE_INCOMPLETE",
                "blocker",
                diagnostic_node.node_id,
                "Undo scope evidence is incomplete; no rollback guarantee is inferred.",
                "Review the recorded mark lifecycle and surrounding mutating calls.",
            )

    def _bind_assignment_targets(
        self, targets: Sequence[ast.expr], value: ast.AST
    ) -> None:
        path = self._expr_path(value)
        source_builder = self._builder_id_for_expr(value)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if path:
                self._aliases[target.id] = path
            if source_builder:
                self._builder_aliases[target.id] = source_builder

    def _builder_id_for_call(self, call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Attribute):
            return self._builder_id_for_expr(call.func.value)
        return None

    def _builder_id_for_expr(self, node: ast.AST) -> str | None:
        root = _root_name(node)
        builder_id = self._builder_aliases.get(root, root)
        return builder_id if builder_id in self._builders else None

    def _expr_path(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        if isinstance(node, ast.Name):
            path = self._aliases.get(node.id, node.id)
            if path == "NXOpen" or path.startswith("NXOpen."):
                self.nxopen_seen = True
            return path
        if isinstance(node, ast.Attribute):
            parent = self._expr_path(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Call):
            path = self._expr_path(node.func)
            return f"{path}()" if path else ""
        if isinstance(node, ast.Subscript):
            return f"{self._expr_path(node.value)}[]"
        return ""

    @staticmethod
    def _is_nx_path(path: str) -> bool:
        normalized = path.removesuffix("()")
        return normalized == "NXOpen" or normalized.startswith("NXOpen.")

    @staticmethod
    def _is_safety_forbidden_call(
        api_path: str, method: str, call: ast.Call
    ) -> bool:
        lowered = api_path.lower()
        if method in {"Save", "SaveAs", "SaveAll", "ExecuteJournal", "ReplayJournal"}:
            return True
        if "journal" in lowered and any(
            item in method.lower() for item in ("execute", "run", "play", "replay")
        ):
            return True
        if _is_machine_output_api(api_path):
            return True
        return False

    def _expression(self, node: ast.AST | None) -> dict[str, JsonValue]:
        if node is None:
            return {"kind": "none", "value": None}
        literal = _static_literal(node)
        if literal is not _UNSET:
            redacted_literal, changed = _redact_absolute_paths(literal)
            if changed:
                return {
                    "kind": "redacted_literal",
                    "value": redacted_literal,
                    "source_excerpt_hash": _sha256(self._slice(self._span(node))),
                    "requires_review": True,
                }
            return {"kind": "literal", "value": literal}  # type: ignore[dict-item]
        text = self._source_text(node)
        if _MACHINE_OUTPUT_PATTERN.search(text):
            return {
                "kind": "redacted_expression",
                "source_excerpt_hash": _sha256(self._slice(self._span(node))),
                "requires_review": True,
            }
        return {
            "kind": "expression",
            "source": text,
            "source_excerpt_hash": _sha256(self._slice(self._span(node))),
            "requires_review": True,
            "evaluated": False,
        }

    def _source_text(self, node: ast.AST) -> str:
        span = self._span(node)
        return self._slice(span).decode("utf-8", errors="replace")

    def _slice(self, span: NxSourceSpanEvidence) -> bytes:
        return self.source_bytes[span.start_byte : span.end_byte]

    def _span(
        self, node: ast.AST, *, synthetic: bool = False
    ) -> NxSourceSpanEvidence:
        if isinstance(node, _SyntheticAstNode):
            return node.span
        start_line = max(1, int(getattr(node, "lineno", 1) or 1))
        end_line = max(start_line, int(getattr(node, "end_lineno", start_line) or start_line))
        start_col_bytes = max(0, int(getattr(node, "col_offset", 0) or 0))
        end_col_bytes = max(
            start_col_bytes if end_line == start_line else 0,
            int(getattr(node, "end_col_offset", start_col_bytes) or start_col_bytes),
        )
        start_base = self._line_byte_offsets[min(start_line - 1, len(self._line_byte_offsets) - 1)]
        end_base = self._line_byte_offsets[min(end_line - 1, len(self._line_byte_offsets) - 1)]
        start_byte = self._bom_length + start_base + start_col_bytes
        end_byte = self._bom_length + end_base + end_col_bytes
        if synthetic:
            end_byte = start_byte
            end_line = start_line
            end_col_bytes = start_col_bytes
        start_column = _unicode_column(self.source, start_line, start_col_bytes)
        end_column = _unicode_column(self.source, end_line, end_col_bytes)
        return NxSourceSpanEvidence(
            start_byte=min(start_byte, len(self.source_bytes)),
            end_byte=min(max(start_byte, end_byte), len(self.source_bytes)),
            start_line=start_line,
            start_column=start_column,
            end_line=end_line,
            end_column=end_column,
        )


@dataclass(frozen=True, slots=True)
class _SyntheticAstNode(ast.AST):
    span: NxSourceSpanEvidence

    @classmethod
    def from_span(cls, span: NxSourceSpanEvidence) -> "_SyntheticAstNode":
        return cls(span=span)


def _parse_static_source(
    source: str,
    *,
    source_file: str,
    limits: NxJournalLimits,
) -> tuple[ast.Module, int]:
    try:
        source_bytes = source.encode("utf-8")
    except UnicodeEncodeError as error:
        raise NxJournalError("NX Journal source is not valid UTF-8 text") from error
    if len(source_bytes) > limits.max_source_bytes:
        raise NxJournalResourceLimitError(
            f"NX Journal exceeds {limits.max_source_bytes} source bytes"
        )
    token_count = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            token_count += 1
            if token_count > limits.max_tokens:
                raise NxJournalResourceLimitError(
                    f"NX Journal exceeds {limits.max_tokens} tokens"
                )
            if (
                token.type == tokenize.STRING
                and len(token.string.encode("utf-8")) > limits.max_string_bytes
            ):
                raise NxJournalResourceLimitError(
                    f"NX Journal string exceeds {limits.max_string_bytes} bytes"
                )
    except (tokenize.TokenError, IndentationError) as error:
        location = getattr(error, "args", (None, (1, 0)))[1]
        line, column = location if isinstance(location, tuple) else (1, 0)
        syntax = SyntaxError(str(error), (source_file, line, column + 1, ""))
        raise NxJournalSyntaxError(source_file, syntax) from error
    try:
        tree = ast.parse(source, filename=source_file)
    except (SyntaxError, ValueError) as error:
        syntax = (
            error
            if isinstance(error, SyntaxError)
            else SyntaxError(str(error), (source_file, 1, 1, ""))
        )
        raise NxJournalSyntaxError(source_file, syntax) from error
    except (MemoryError, RecursionError) as error:
        raise NxJournalResourceLimitError(
            "NX Journal AST parser exceeded memory or recursion limits"
        ) from error
    node_count = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        node_count += 1
        if node_count > limits.max_ast_nodes:
            raise NxJournalResourceLimitError(
                f"NX Journal exceeds {limits.max_ast_nodes} AST nodes"
            )
        if depth > limits.max_ast_depth:
            raise NxJournalResourceLimitError(
                f"NX Journal exceeds AST depth {limits.max_ast_depth}"
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value.encode("utf-8")) > limits.max_string_bytes:
                raise NxJournalResourceLimitError(
                    f"NX Journal string exceeds {limits.max_string_bytes} bytes"
                )
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, float)
            and not math.isfinite(node.value)
        ):
            raise NxJournalError("NX Journal contains a non-finite numeric literal")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree, token_count


def _reject_explicit_powermill_source(tree: ast.Module) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any("powermill" in name.lower() for name in names):
                raise NxUnsupportedSourceError(
                    "explicit PowerMill product evidence is rejected by the NX parser"
                )
        if isinstance(node, ast.Assign):
            product_target = any(
                isinstance(target, ast.Name)
                and target.id.lower() in {"product", "source_product"}
                for target in node.targets
            )
            value = _static_literal(node.value)
            if (
                product_target
                and isinstance(value, str)
                and value.strip().lower() == "powermill"
            ):
                raise NxUnsupportedSourceError(
                    "explicit PowerMill product evidence is rejected by the NX parser"
                )
        if isinstance(node, ast.Dict):
            pairs = zip(node.keys, node.values)
            for key_node, value_node in pairs:
                key = _static_literal(key_node)
                value = _static_literal(value_node)
                if (
                    isinstance(key, str)
                    and key.lower() == "product"
                    and isinstance(value, str)
                    and value.strip().lower() == "powermill"
                ):
                    raise NxUnsupportedSourceError(
                        "explicit PowerMill product evidence is rejected by the NX parser"
                    )


def _static_literal(node: ast.AST | None) -> JsonValue | object:
    if node is None:
        return _UNSET
    if isinstance(node, ast.Constant):
        return _json_safe(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_static_literal(item) for item in node.elts]
        if any(item is _UNSET for item in values):
            return _UNSET
        return values  # type: ignore[return-value]
    if isinstance(node, ast.Dict):
        result: dict[str, JsonValue] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                return _UNSET
            key = _static_literal(key_node)
            value = _static_literal(value_node)
            if key is _UNSET or value is _UNSET:
                return _UNSET
            result[str(key)] = value  # type: ignore[assignment]
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _static_literal(node.operand)
        if isinstance(operand, (int, float)) and not isinstance(operand, bool):
            return operand if isinstance(node.op, ast.UAdd) else -operand
    return _UNSET


def _contains_unsupported_expression(node: ast.AST) -> bool:
    return _unsupported_expression_reason(node) is not None


def _unsupported_expression_reason(node: ast.AST) -> str | None:
    unsupported = (
        ast.Lambda,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
        ast.NamedExpr,
    )
    for child in ast.walk(node):
        if isinstance(child, unsupported):
            return f"expression_{type(child).__name__.lower()}_unsupported"
        if isinstance(child, ast.Call):
            path = _plain_expr_path(child.func)
            if path in {"eval", "exec", "compile", "__import__", "getattr", "setattr"}:
                return f"dynamic_call_{path}_unsupported"
    return None


def _plain_expr_path(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _plain_expr_path(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        path = _plain_expr_path(node.func)
        return f"{path}()" if path else ""
    return ""


def _contains_journal_identifier(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Attribute) and child.attr == "JournalIdentifier"
        for child in ast.walk(node)
    )


def _recursive_function_names(
    functions: Mapping[str, ast.FunctionDef],
) -> set[str]:
    graph: dict[str, set[str]] = {}
    for name, function in functions.items():
        graph[name] = {
            child.func.id
            for child in ast.walk(function)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id in functions
        }
    recursive: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            recursive.update(path[path.index(name) :])
            return
        for target in graph.get(name, ()):
            visit(target, (*path, name))

    for name in graph:
        visit(name, ())
    return recursive


def _contains_absolute_path_literal(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and bool(_ABSOLUTE_PATH_PATTERN.match(child.value))
        for child in ast.walk(node)
    )


def _redact_absolute_paths(value: JsonValue | object) -> tuple[JsonValue | object, bool]:
    if isinstance(value, str) and _ABSOLUTE_PATH_PATTERN.match(value):
        return None, True
    if isinstance(value, list):
        changed = False
        result: list[JsonValue] = []
        for item in value:
            redacted, item_changed = _redact_absolute_paths(item)
            result.append(redacted)  # type: ignore[arg-type]
            changed = changed or item_changed
        return result, changed
    if isinstance(value, dict):
        changed = False
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            redacted, item_changed = _redact_absolute_paths(item)
            result[key] = redacted  # type: ignore[assignment]
            changed = changed or item_changed
        return result, changed
    return value, False


def _is_builder_factory(method: str) -> bool:
    return method.startswith("Create") and method.endswith("Builder")


def _node_type_for_nx_call(api_path: str, method: str) -> tuple[str, str]:
    lowered = api_path.lower()
    if method == "GetSession":
        return "nx.session.access", "safe"
    if "camoperationcollection" in lowered and method == "Create":
        return "cam.operation.create", "review"
    if method in {"GenerateToolPath", "GenerateToolpaths"}:
        return "cam.toolpath.generate", "review"
    if method in {"SetName", "Rename"}:
        return "nx.object.rename", "review"
    if any(item in lowered for item in ("verify", "simulate", "collision", "gouge")):
        return "cam.verify.inspect", "review"
    pieces = [_snake(piece) for piece in api_path.split(".")[-2:]]
    return "nx.api." + ".".join(pieces), "review"


def _is_machine_output_api(api_path: str) -> bool:
    lowered = api_path.lower()
    return bool(
        any(
            item in lowered
            for item in (
                "gcode",
                "g_code",
                "nccode",
                "nc_code",
                "generateclsf",
                "outputclsf",
            )
        )
        or _MACHINE_OUTPUT_PATTERN.search(api_path)
    )


def _root_name(node: ast.AST) -> str:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def _relative_attribute_path(node: ast.Attribute) -> str:
    pieces = [node.attr]
    current: ast.AST = node.value
    while isinstance(current, ast.Attribute):
        pieces.append(current.attr)
        current = current.value
    return ".".join(reversed(pieces))


def _line_byte_offsets(source: str) -> list[int]:
    offsets = [0]
    total = 0
    for line in source.splitlines(keepends=True):
        total += len(line.encode("utf-8"))
        offsets.append(total)
    if not source or not source.endswith(("\n", "\r")):
        offsets.append(total)
    return offsets


def _unicode_column(source: str, line: int, utf8_column: int) -> int:
    lines = source.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return 0
    prefix = lines[line - 1].encode("utf-8")[:utf8_column]
    return len(prefix.decode("utf-8", errors="ignore"))


def _newline_profile(source_bytes: bytes) -> str:
    value = source_bytes[3:] if source_bytes.startswith(b"\xef\xbb\xbf") else source_bytes
    crlf = value.count(b"\r\n")
    without_crlf = value.replace(b"\r\n", b"")
    lf = without_crlf.count(b"\n")
    cr = without_crlf.count(b"\r")
    styles = sum(bool(item) for item in (crlf, lf, cr))
    if styles > 1:
        return "mixed"
    if crlf:
        return "crlf"
    if lf:
        return "lf"
    if cr:
        return "cr"
    return "none"


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[7:]
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise NxJournalError("source_digest must be a SHA-256 hex digest")
    return f"sha256:{normalized}"


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _nx_preconditions() -> list[dict[str, JsonValue]]:
    return [
        {
            "precondition_id": f"precondition:{name}",
            "kind": name,
            "status": "required",
            "evidence_refs": [],
            "inferred": False,
        }
        for name in (
            "target_version_and_stubs",
            "instance_project_part_snapshot",
            "work_and_display_part",
            "units",
            "mcs",
            "stock",
            "fixture_and_check_geometry",
            "tool",
            "holder",
            "cam_setup_groups",
            "machine_kit",
        )
    ]
