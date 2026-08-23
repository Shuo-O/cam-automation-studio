from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

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


class NxJournalError(ValueError):
    """Base error for rejected or invalid NX Journal sources."""


class NxUnsupportedSourceError(NxJournalError):
    """Raised when a source is not an NX Open Python Journal."""


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
            "destroyed": self.destroyed,
            "commit_line": self.commit_line,
            "destroy_line": self.destroy_line,
            "parameters": dict(self.parameters),
            "issues": list(self.issues),
            "complete": self.committed and self.destroyed and not self.issues,
        }


class NxJournalAdapter:
    """Parse NX Open Python Journals through AST inspection only."""

    product = "nx"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".py"

    def parse(self, path: Path) -> Iterable[ActivityEvent]:
        if not self.supports(path):
            raise NxUnsupportedSourceError(
                f"NX Journal adapter accepts only .py files, not {path.suffix or '<none>'}"
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
        try:
            tree = ast.parse(source, filename=source_file)
        except SyntaxError as error:
            raise NxJournalSyntaxError(source_file, error) from error
        visitor = _NxActionVisitor(session_id=session_id, source_file=source_file)
        visitor.visit(tree)
        if not visitor.nxopen_seen:
            raise NxUnsupportedSourceError(
                "source does not import or reference NXOpen; PowerMill and generic Python "
                "inputs are not accepted by the NX Journal adapter"
            )
        visitor.finalize()
        return visitor.events


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
            if state.committed and state.destroyed and not state.issues:
                continue
            issues = list(state.issues)
            if not state.committed:
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
                    state.issues.append("builder_destroyed_without_commit")
                state.destroyed = True
                state.destroy_line = node.lineno
        if state is not None:
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
            "rejected" if risk == "blocked" else "needs_review" if risk == "review" else "unreviewed"
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
