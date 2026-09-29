from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from cam_automation.models import ActivityEvent


_CSHARP_CALL = re.compile(
    r"(?P<call>[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)+)"
    r"\s*\((?P<args>.*)\)\s*;?\s*$"
)
_VERSION = re.compile(r"\bCimatron(?:\s+E)?\s+(20\d{2}(?:\.\d+)?)\b", re.IGNORECASE)
_DANGEROUS_CALL = re.compile(
    r"(?:^|\.)(?:eval|exec|compile|system|popen|start|run|invoke|loadassembly)$",
    re.IGNORECASE,
)
_MACHINE_OUTPUT = re.compile(
    r"(?:postprocess|post_process|gcode|g_code|ncprogram|nc_program|machinecode|"
    r"machine_code|clsf|dnc|mdi)",
    re.IGNORECASE,
)
_MAX_SOURCE_BYTES = 8 * 1024 * 1024


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        if isinstance(node, ast.Name):
            return {"expression": "name", "identifier": node.id}
        return {"expression": type(node).__name__}


def _split_csharp_arguments(value: str) -> list[str]:
    arguments: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    escaped = False
    for character in value:
        if quote:
            current.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {'"', "'"}:
            quote = character
            current.append(character)
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
        if character == "," and depth == 0:
            arguments.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if current or value.strip():
        arguments.append("".join(current).strip())
    return arguments


def _csharp_literal(value: str) -> Any:
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.casefold() in {"true", "false"}:
        return candidate.casefold() == "true"
    if candidate.casefold() == "null":
        return None
    if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
        return candidate[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    try:
        return int(candidate)
    except ValueError:
        try:
            return float(candidate)
        except ValueError:
            return {"expression": "csharp", "kind": "opaque"}


def _source_language(source_name: str, source: str) -> str:
    suffix = Path(source_name).suffix.casefold()
    if suffix == ".cs":
        return "csharp"
    if suffix == ".py":
        return "python"
    if re.search(r"^\s*(?:using\s+[A-Za-z_]|namespace\s+[A-Za-z_])", source, re.MULTILINE):
        return "csharp"
    return "python"


def _action_for(call: str) -> tuple[str, str, str, list[str]]:
    normalized = call.strip()
    if _MACHINE_OUTPUT.search(normalized):
        return (
            "cam.source.call",
            "output",
            "blocked",
            ["machine-ready output and postprocessing are permanently blocked"],
        )
    if _DANGEROUS_CALL.search(normalized):
        return (
            "cam.source.call",
            "safety",
            "blocked",
            ["dynamic code, external process, or reflection-like execution is not analyzed"],
        )
    return "cam.source.call", "api", "review", [
        "Cimatron API call is static evidence until a fixture-backed semantic mapping exists"
    ]


class CimatronJournalAdapter:
    """Statically parse Cimatron 2026 Journaling source without importing it."""

    product = "cimatron"

    def parse_source(
        self,
        source: str,
        *,
        source_file: str = "cimatron-journal",
        session_name: str | None = None,
        target_version: str | None = None,
    ) -> Iterable[ActivityEvent]:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Cimatron Journal source must be non-empty.")
        if len(source.encode("utf-8")) > _MAX_SOURCE_BYTES:
            raise ValueError("Cimatron Journal source exceeds the 8 MiB static-analysis limit.")
        language = _source_language(source_file, source)
        version_match = _VERSION.search(source)
        resolved_version = target_version or (
            f"Cimatron {version_match.group(1)}" if version_match else None
        )
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        session_id = session_name or f"cimatron:{Path(source_file).stem or 'journal'}:{digest}"
        calls = self._python_calls(source) if language == "python" else self._csharp_calls(source)
        for sequence, (line_number, call, arguments, keyword_arguments) in enumerate(calls):
            action, category, risk, reasons = _action_for(call)
            yield ActivityEvent(
                session_id=session_id,
                seq=sequence,
                product=self.product,
                action=action,
                category=category,
                mode="manual",
                source_mode="manual",
                view_level="L1",
                params={
                    "api": call,
                    "arguments": arguments,
                    "keyword_arguments": keyword_arguments,
                    "source_kind": "cimatron_journal",
                    "source_language": language,
                    "mapping_confidence": "blocked" if risk == "blocked" else "opaque",
                    "risk": risk,
                    "reasons": reasons,
                    "hierarchy": [self.product, category, action],
                },
                source_file=source_file,
                source_line=line_number,
                target_version=resolved_version,
            )

    @staticmethod
    def _python_calls(
        source: str,
    ) -> list[tuple[int, str, list[Any], dict[str, Any]]]:
        tree = ast.parse(source, mode="exec")
        calls: list[tuple[int, str, list[Any], dict[str, Any]]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _call_name(node.func)
            if not call:
                continue
            calls.append(
                (
                    int(getattr(node, "lineno", 0) or 0),
                    call,
                    [_literal(argument) for argument in node.args],
                    {
                        keyword.arg or "**": _literal(keyword.value)
                        for keyword in node.keywords
                    },
                )
            )
        calls.sort(key=lambda item: (item[0], item[1]))
        return calls

    @staticmethod
    def _csharp_calls(
        source: str,
    ) -> list[tuple[int, str, list[Any], dict[str, Any]]]:
        calls: list[tuple[int, str, list[Any], dict[str, Any]]] = []
        for line_number, raw_line in enumerate(source.splitlines(), 1):
            line = raw_line.split("//", 1)[0].strip()
            if not line:
                continue
            match = _CSHARP_CALL.search(line)
            if match is None:
                continue
            call = re.sub(r"\s+", "", match.group("call"))
            arguments = [
                _csharp_literal(item)
                for item in _split_csharp_arguments(match.group("args"))
            ]
            calls.append((line_number, call, arguments, {}))
        return calls


__all__ = ["CimatronJournalAdapter"]
