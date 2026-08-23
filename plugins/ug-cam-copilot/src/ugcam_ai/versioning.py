from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class NxVersionContractError(ValueError):
    """Base error for local NX Python stub inspection."""


class NxStubsNotFoundError(NxVersionContractError):
    pass


class NxStubParseError(NxVersionContractError):
    pass


class NxStubVersionMissingError(NxVersionContractError):
    pass


class NxStubVersionMismatchError(NxVersionContractError):
    def __init__(self, expected: str, detected: str) -> None:
        self.expected = expected
        self.detected = detected
        super().__init__(
            f"NX Python stubs version mismatch: expected {expected!r}, "
            f"detected {detected!r}"
        )


@dataclass(frozen=True, slots=True)
class NxApiMember:
    name: str
    kind: str
    signatures: tuple[str, ...]
    source_lines: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "signatures": list(self.signatures),
            "source_lines": list(self.source_lines),
        }


@dataclass(frozen=True, slots=True)
class NxApiClass:
    name: str
    bases: tuple[str, ...]
    members: Mapping[str, NxApiMember]
    attributes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "bases": list(self.bases),
            "members": {
                name: member.to_dict() for name, member in sorted(self.members.items())
            },
            "attributes": list(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class NxApiModule:
    name: str
    source_file: str
    classes: Mapping[str, NxApiClass]
    functions: Mapping[str, NxApiMember]
    constants: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_file": self.source_file,
            "classes": {
                name: value.to_dict() for name, value in sorted(self.classes.items())
            },
            "functions": {
                name: value.to_dict() for name, value in sorted(self.functions.items())
            },
            "constants": list(self.constants),
        }


@dataclass(frozen=True, slots=True)
class NxVersionContract:
    stub_root: str
    target_version: str
    python_minor: str | None
    modules: Mapping[str, NxApiModule]
    symbols: frozenset[str]
    metadata_source: str

    @classmethod
    def load_stubs(
        cls,
        path: str | Path,
        *,
        expected_version: str | None = None,
    ) -> "NxVersionContract":
        supplied = Path(path)
        stub_root = _resolve_stub_root(supplied)
        candidates = sorted(
            {
                *stub_root.rglob("*.py"),
                *stub_root.rglob("*.pyi"),
            }
        )
        selected_by_module: dict[str, Path] = {}
        for file in candidates:
            if "__pycache__" in file.parts:
                continue
            module_name = _module_name(stub_root, file)
            existing = selected_by_module.get(module_name)
            if existing is None or file.suffix.lower() == ".pyi":
                selected_by_module[module_name] = file
        stub_files = sorted(selected_by_module.values())
        if not stub_files:
            raise NxStubsNotFoundError(
                f"no .pyi or .py NX Python stubs found under {stub_root}"
            )

        metadata, metadata_source = _load_metadata(stub_root)
        modules: dict[str, NxApiModule] = {}
        symbols: set[str] = set()
        discovered_constants: dict[str, str] = {}
        for file in stub_files:
            try:
                source = file.read_text(encoding="utf-8-sig")
                tree = ast.parse(source, filename=str(file))
            except (OSError, UnicodeError, SyntaxError) as error:
                line = getattr(error, "lineno", 0) or 0
                raise NxStubParseError(
                    f"cannot parse NX Python stub {file}:{line}: {error}"
                ) from error
            module_name = _module_name(stub_root, file)
            module, constants = _parse_module(module_name, file, tree)
            modules[module_name] = module
            discovered_constants.update(constants)
            symbols.add(module_name)
            for class_name, api_class in module.classes.items():
                class_symbol = f"{module_name}.{class_name}"
                symbols.add(class_symbol)
                symbols.update(
                    f"{class_symbol}.{member_name}"
                    for member_name in api_class.members
                )
                symbols.update(
                    f"{class_symbol}.{attribute}" for attribute in api_class.attributes
                )
            symbols.update(
                f"{module_name}.{function_name}"
                for function_name in module.functions
            )

        detected_version = (
            _metadata_text(metadata, "target_version", "nx_version", "version")
            or discovered_constants.get("NX_VERSION")
            or discovered_constants.get("TARGET_VERSION")
            or discovered_constants.get("__version__")
            or _version_from_path(stub_root)
        )
        if not detected_version:
            raise NxStubVersionMissingError(
                "NX Python stubs do not declare a target version in structured "
                "metadata, stub constants, or the installation path"
            )
        if expected_version and _normalize_version(expected_version) != _normalize_version(
            detected_version
        ):
            raise NxStubVersionMismatchError(expected_version, detected_version)
        python_minor = (
            _metadata_text(metadata, "python_minor", "python_version")
            or discovered_constants.get("PYTHON_VERSION")
        )
        return cls(
            stub_root=str(stub_root.resolve()),
            target_version=detected_version,
            python_minor=python_minor,
            modules=modules,
            symbols=frozenset(symbols),
            metadata_source=metadata_source,
        )

    def supports(self, api_symbol: str) -> bool:
        return api_symbol in self.symbols

    def require(self, api_symbol: str) -> None:
        if not self.supports(api_symbol):
            raise NxVersionContractError(
                f"{api_symbol!r} is absent from NX stubs for {self.target_version}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stub_root": self.stub_root,
            "target_version": self.target_version,
            "python_minor": self.python_minor,
            "metadata_source": self.metadata_source,
            "modules": {
                name: module.to_dict() for name, module in sorted(self.modules.items())
            },
        }


def _resolve_stub_root(path: Path) -> Path:
    if not path.exists():
        raise NxStubsNotFoundError(f"NX installation or Python stubs path is missing: {path}")
    if path.is_file():
        if path.suffix.lower() not in {".py", ".pyi"}:
            raise NxStubsNotFoundError(f"unsupported NX stubs file: {path}")
        return path.parent
    candidates = []
    if path.name.lower() == "pythonstubs":
        candidates.append(path)
    candidates.extend(
        [
            path / "UGOPEN" / "pythonStubs",
            path / "ugopen" / "pythonStubs",
            path / "pythonStubs",
            path,
        ]
    )
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        if (candidate / "NXOpen").is_dir():
            return candidate
        if any(candidate.glob("NXOpen*.pyi")) or any(candidate.glob("NXOpen*.py")):
            return candidate
    raise NxStubsNotFoundError(
        f"cannot locate UGOPEN/pythonStubs beneath NX installation path: {path}"
    )


def _load_metadata(stub_root: Path) -> tuple[dict[str, Any], str]:
    candidates = [
        stub_root / "nx_version.json",
        stub_root / "version.json",
        stub_root.parent / "nx_version.json",
        stub_root.parent.parent / "nx_version.json",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise NxStubParseError(
                f"invalid structured NX stubs metadata {candidate}: {error}"
            ) from error
        if not isinstance(value, dict):
            raise NxStubParseError(
                f"NX stubs metadata must be a JSON object: {candidate}"
            )
        return value, str(candidate.resolve())
    return {}, "stub_ast_or_installation_path"


def _parse_module(
    module_name: str, file: Path, tree: ast.Module
) -> tuple[NxApiModule, dict[str, str]]:
    classes: dict[str, NxApiClass] = {}
    function_nodes: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    constants: list[str] = []
    constant_values: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = _parse_class(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_nodes.setdefault(node.name, []).append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name, value in _assignment_values(node):
                constants.append(name)
                if isinstance(value, str):
                    constant_values[name] = value
    functions = {
        name: _parse_member(name, nodes, kind="function")
        for name, nodes in function_nodes.items()
    }
    return (
        NxApiModule(
            name=module_name,
            source_file=str(file.resolve()),
            classes=classes,
            functions=functions,
            constants=tuple(sorted(set(constants))),
        ),
        constant_values,
    )


def _parse_class(node: ast.ClassDef) -> NxApiClass:
    member_nodes: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    member_kinds: dict[str, str] = {}
    attributes: list[str] = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            member_nodes.setdefault(child.name, []).append(child)
            if any(_decorator_name(item).endswith("property") for item in child.decorator_list):
                member_kinds[child.name] = "property"
            else:
                member_kinds.setdefault(child.name, "method")
        elif isinstance(child, (ast.Assign, ast.AnnAssign)):
            attributes.extend(name for name, _ in _assignment_values(child))
    members = {
        name: _parse_member(name, nodes, kind=member_kinds[name])
        for name, nodes in member_nodes.items()
    }
    return NxApiClass(
        name=node.name,
        bases=tuple(_annotation_text(base) for base in node.bases),
        members=members,
        attributes=tuple(sorted(set(attributes))),
    )


def _parse_member(
    name: str,
    nodes: list[ast.FunctionDef | ast.AsyncFunctionDef],
    *,
    kind: str,
) -> NxApiMember:
    return NxApiMember(
        name=name,
        kind=kind,
        signatures=tuple(_signature(node) for node in nodes),
        source_lines=tuple(node.lineno for node in nodes),
    )


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    arguments: list[str] = []
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(
        node.args.defaults
    )
    for argument, default in zip(positional, defaults):
        text = argument.arg
        if argument.annotation is not None:
            text += f": {_annotation_text(argument.annotation)}"
        if default is not None:
            text += f" = {_annotation_text(default)}"
        arguments.append(text)
    if node.args.vararg:
        arguments.append(f"*{node.args.vararg.arg}")
    elif node.args.kwonlyargs:
        arguments.append("*")
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        text = argument.arg
        if argument.annotation is not None:
            text += f": {_annotation_text(argument.annotation)}"
        if default is not None:
            text += f" = {_annotation_text(default)}"
        arguments.append(text)
    if node.args.kwarg:
        arguments.append(f"**{node.args.kwarg.arg}")
    result = f"({', '.join(arguments)})"
    if node.returns is not None:
        result += f" -> {_annotation_text(node.returns)}"
    return result


def _assignment_values(
    node: ast.Assign | ast.AnnAssign,
) -> list[tuple[str, Any]]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    value_node = node.value
    try:
        value = ast.literal_eval(value_node) if value_node is not None else None
    except (ValueError, TypeError, SyntaxError):
        value = None
    return [
        (target.id, value) for target in targets if isinstance(target, ast.Name)
    ]


def _module_name(stub_root: Path, file: Path) -> str:
    relative = file.relative_to(stub_root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or file.stem


def _decorator_name(node: ast.AST) -> str:
    return _annotation_text(node)


def _annotation_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return type(node).__name__


def _metadata_text(metadata: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _version_from_path(path: Path) -> str | None:
    for part in reversed(path.parts):
        match = re.search(r"(?i)(?:^|[^a-z])NX[\s_-]?(\d{2,4})(?:$|[^0-9])", part)
        if match:
            return f"NX {match.group(1)}"
    return None


def _normalize_version(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())
