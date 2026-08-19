from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from ..models import ActivityEvent, JsonValue


_SKIP_METHODS = {
    "main",
    "GetSession",
    "SetUndoMark",
    "DeleteUndoMark",
    "SetUndoMarkName",
    "Destroy",
    "Dispose",
    "CleanUpFacetedFacesAndEdges",
    "Clear",
}

_GROUP_NAMES = {
    "PROGRAM": "program",
    "METHOD": "method",
    "TOOL": "tool",
    "GEOMETRY": "geometry",
    "WORKPIECE": "geometry",
    "MCS": "geometry",
}


class NxJournalAdapter:
    """Static NX Open journal parser; journal code is never imported or executed."""

    product = "nx"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".py"

    def parse(self, path: Path) -> Iterable[ActivityEvent]:
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
        tree = ast.parse(source, filename=source_file)
        visitor = _NxActionVisitor(session_id=session_id, source_file=source_file)
        visitor.visit(tree)
        return visitor.events


class _NxActionVisitor(ast.NodeVisitor):
    def __init__(self, *, session_id: str, source_file: str) -> None:
        self.session_id = session_id
        self.source_file = source_file
        self.aliases: dict[str, str] = {}
        self.events: list[ActivityEvent] = []

    def visit_Assign(self, node: ast.Assign) -> Any:
        if isinstance(node.value, ast.Call):
            self._record_call(node.value)
            for target in node.targets:
                self._bind_call_result(target, node.value)
        else:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    path = self._expr_path(node.value)
                    if path:
                        self.aliases[target.id] = path
                elif isinstance(target, ast.Attribute):
                    self._record_property_set(target, node.value, node.lineno)
            self.visit(node.value)
        return None

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        if node.value is None:
            return None
        if isinstance(node.value, ast.Call):
            self._record_call(node.value)
            self._bind_call_result(node.target, node.value)
        elif isinstance(node.target, ast.Attribute):
            self._record_property_set(node.target, node.value, node.lineno)
        else:
            path = self._expr_path(node.value)
            if isinstance(node.target, ast.Name) and path:
                self.aliases[node.target.id] = path
            self.visit(node.value)
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

    def _bind_call_result(self, target: ast.expr, call: ast.Call) -> None:
        if not isinstance(target, ast.Name):
            return
        method = self._expr_path(call.func).rsplit(".", 1)[-1]
        if method.startswith("Create") and len(method) > 6:
            alias = method[6:]
        elif method == "Create":
            alias = "Operation"
        elif method == "FindObject":
            group = self._find_group_name(call)
            alias = f"{group.title()}Group" if group else "FoundObject"
        elif method.startswith("Get") and len(method) > 3:
            alias = method[3:]
        else:
            alias = f"{method}Result"
        self.aliases[target.id] = alias

    def _record_call(self, node: ast.Call) -> None:
        api_path = self._expr_path(node.func)
        method = api_path.rsplit(".", 1)[-1]
        if not api_path or method in _SKIP_METHODS:
            return
        arguments = [self._safe_value(arg) for arg in node.args]
        keywords = {
            keyword.arg or "**": self._safe_value(keyword.value)
            for keyword in node.keywords
        }
        action = self._canonical_call(api_path, method, node)
        self._append(
            action=action,
            category=self._category(action),
            params={"api": api_path, "args": arguments, "kwargs": keywords},
            line=node.lineno,
        )

    def _record_property_set(
        self, target: ast.Attribute, value: ast.expr, line: int
    ) -> None:
        api_path = self._expr_path(target)
        property_name = target.attr
        safe_name = re.sub(r"(?<!^)(?=[A-Z])", "_", property_name).lower()
        action = f"cam.parameter.set.{safe_name}"
        self._append(
            action=action,
            category="parameter",
            params={"api": api_path, "value": self._safe_value(value)},
            line=line,
        )

    def _append(
        self,
        *,
        action: str,
        category: str,
        params: dict[str, JsonValue],
        line: int,
    ) -> None:
        self.events.append(
            ActivityEvent(
                session_id=self.session_id,
                seq=len(self.events),
                product="nx",
                action=action,
                category=category,
                params=params,
                source_file=self.source_file,
                source_line=line,
            )
        )

    def _canonical_call(self, api_path: str, method: str, node: ast.Call) -> str:
        lowered = api_path.lower()
        if method == "FindObject":
            group = self._find_group_name(node)
            return f"cam.group.select.{group}" if group else "cam.object.find"
        if "camoperationcollection" in lowered and method == "Create":
            return "cam.operation.create"
        if method in {"GenerateToolPath", "GenerateToolpaths"}:
            return "cam.toolpath.generate"
        if method == "Commit":
            return "cam.builder.commit"
        if method in {"Save", "SaveAs", "SaveAll"}:
            return "part.save"
        if method in {"SetName", "Rename"}:
            return "object.rename"
        if any(word in lowered for word in ("postprocess", "post_process", ".post")):
            return "cam.output.postprocess"
        if any(word in lowered for word in ("verify", "simulate", "collision", "gouge")):
            return "cam.verify.run"
        pieces = [
            re.sub(r"(?<!^)(?=[A-Z])", "_", piece).lower()
            for piece in api_path.split(".")[-2:]
        ]
        return "nx.api." + ".".join(pieces)

    def _find_group_name(self, node: ast.Call) -> str | None:
        if not node.args:
            return None
        value = self._literal(node.args[0])
        if not isinstance(value, str):
            return None
        upper = value.upper()
        for marker, group in _GROUP_NAMES.items():
            if marker in upper:
                return group
        return None

    @staticmethod
    def _category(action: str) -> str:
        if action.startswith("cam.group"):
            return "setup"
        if action.startswith("cam.operation") or action.startswith("cam.builder"):
            return "operation"
        if action.startswith("cam.toolpath"):
            return "toolpath"
        if action.startswith("cam.verify"):
            return "verification"
        if action.startswith("cam.output"):
            return "output"
        if action.startswith("part."):
            return "data"
        if action.startswith("cam.parameter"):
            return "parameter"
        return "other"

    def _expr_path(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = self._expr_path(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Call):
            return f"{self._expr_path(node.func)}()"
        if isinstance(node, ast.Subscript):
            return f"{self._expr_path(node.value)}[]"
        return ""

    def _safe_value(self, node: ast.AST) -> JsonValue:
        value = self._literal(node)
        if value is not _UNSET:
            return value  # type: ignore[return-value]
        expression = self._expr_path(node)
        return {"expression": expression or type(node).__name__}

    @staticmethod
    def _literal(node: ast.AST) -> JsonValue | object:
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError):
            return _UNSET
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, dict):
            return {str(key): item for key, item in value.items()}
        return str(value)


_UNSET = object()
