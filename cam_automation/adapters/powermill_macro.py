from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO, Mapping, Protocol, Sequence, runtime_checkable

from cam_automation.profiles.powermill import PowerMillProfile


ActivityEvent = dict[str, Any]
CommandResponse = dict[str, Any]
SnapshotMetadata = dict[str, Any]

_SESSION_MARKER = re.compile(r"^\s*(?:#|//)\s*session\s*:\s*(.+?)\s*$", re.IGNORECASE)
_BRACKET_TIMESTAMP = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^\]]+)\]\s*(?P<command>.*)$"
)
_PIPE_TIMESTAMP = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^|]+)\|\s*(?P<command>.*)$"
)
_PROMPT = re.compile(r"^(?:POWERMILL|PMILL)\s*>\s*", re.IGNORECASE)
_NOISE = re.compile(r"^(?:INFO|DEBUG|TRACE|WARNING)\s*[:|-]", re.IGNORECASE)
_TOKEN = re.compile(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|[^\s]+')
_DECLARATION = re.compile(
    r"^\s*(?:(?:PARAMETER|PARAM)\s+|(?:STRING|REAL|INT|INTEGER|BOOL|BOOLEAN)\s+)"
    r"(?P<name>\$?[A-Za-z_][A-Za-z0-9_]*)(?:\s*=\s*(?P<default>.+?))?\s*$",
    re.IGNORECASE,
)
_FUNCTION = re.compile(
    r"^\s*FUNCTION\s+(?P<name>\$?[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<parameters>.*)\)\s*(?:\{)?\s*$",
    re.IGNORECASE,
)
_MACHINE_CODE = re.compile(
    r"(?:^\s*%?\s*[GMT]\d+\b|\bN\d+\s+[GMT]\d+\b|\bMACHINE[_ ]?CODE\b)",
    re.IGNORECASE | re.MULTILINE,
)
_UNSAFE_ARGUMENT_KEY = re.compile(
    r"(?:command|macro|script|nc(?:_|)code|g(?:_|)code|postprocess|machine_control)",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_command(command: str) -> str:
    output: list[str] = []
    quote = ""
    pending_space = False
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            output.append(char)
            if char == quote:
                if index + 1 < len(command) and command[index + 1] == quote:
                    output.append(command[index + 1])
                    index += 1
                else:
                    quote = ""
        elif char in {'"', "'"}:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            quote = char
            output.append(char)
        elif char.isspace():
            pending_space = True
        else:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            output.append(char)
        index += 1
    return "".join(output).strip()


def _decoded_token(token: str) -> tuple[Any, bool]:
    if len(token) >= 2 and token[0] in {'"', "'"} and token[-1] == token[0]:
        quote = token[0]
        return token[1:-1].replace(quote * 2, quote), True
    if re.fullmatch(r"[-+]?\d+", token):
        return int(token), False
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?", token):
        return float(token), False
    return token, False


def _split_parameters(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    quote = ""
    depth = 0
    index = 0
    while index < len(value):
        char = value[index]
        if quote:
            if char == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    index += 1
                else:
                    quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
        index += 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _declared_default_gaps(command: str, payload: Mapping[str, Any]) -> list[str]:
    gaps: list[str] = []
    parameters = payload.get("parameters")
    defaults = payload.get("defaults")
    defaults_map = defaults if isinstance(defaults, Mapping) else {}
    if isinstance(parameters, list):
        for parameter in parameters:
            if isinstance(parameter, Mapping):
                name = str(parameter.get("name", "")).strip()
                if name and "default" not in parameter:
                    gaps.append(name)
            elif isinstance(parameter, str) and parameter not in defaults_map:
                gaps.append(parameter)

    declaration = _DECLARATION.match(command)
    if declaration and declaration.group("default") is None:
        gaps.append(declaration.group("name"))

    function = _FUNCTION.match(command)
    if function:
        for parameter in _split_parameters(function.group("parameters")):
            match = _DECLARATION.match(parameter)
            if match and match.group("default") is None:
                gaps.append(match.group("name"))
    return list(dict.fromkeys(gaps))


def _ordered_arguments(command: str, operation: str) -> list[dict[str, Any]]:
    tokens = _TOKEN.findall(command)
    operation_tokens = len(operation.split())
    arguments: list[dict[str, Any]] = []
    for position, token in enumerate(tokens[operation_tokens:]):
        value, quoted = _decoded_token(token)
        arguments.append(
            {
                "position": position,
                "raw": token,
                "value": value,
                "quoted": quoted,
            }
        )
    return arguments


def _unsafe_argument_path(value: Any, path: str = "arguments") -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if _UNSAFE_ARGUMENT_KEY.fullmatch(str(key)) and item not in (None, "", False):
                return item_path
            unsafe = _unsafe_argument_path(item, item_path)
            if unsafe:
                return unsafe
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            unsafe = _unsafe_argument_path(item, f"{path}[{index}]")
            if unsafe:
                return unsafe
    return None


class PowerMillAdapter:
    """Standalone, non-executing adapter for PowerMill macro and log evidence."""

    def __init__(
        self,
        *,
        source_name: str = "powermill:inline",
        instance_id: str | None = None,
        project_id: str | None = None,
        target_version: str | None = None,
        profile: PowerMillProfile | None = None,
    ) -> None:
        self.source_name = source_name
        self.instance_id = instance_id
        self.project_id = project_id
        self.target_version = target_version
        self.profile = profile or PowerMillProfile()
        self.diagnostics: list[dict[str, Any]] = []
        self.flow_diagnostics: list[dict[str, Any]] = []

    def parse(self, source: str | Path | IO[str]) -> list[ActivityEvent]:
        text, source_file = self._read_source(source)
        self.diagnostics = []
        events: list[ActivityEvent] = []
        session_name = "session-1"
        sequence_by_session: dict[str, int] = {}

        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            marker = _SESSION_MARKER.match(stripped)
            if marker:
                session_name = marker.group(1).strip()
                continue
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            if _NOISE.match(stripped):
                continue

            try:
                record = self._extract_record(raw_line)
            except (json.JSONDecodeError, ValueError) as error:
                self.diagnostics.append(
                    {"line_number": line_number, "message": str(error), "raw_line": raw_line}
                )
                continue

            command = record["command"]
            if not command.strip():
                continue
            if record.get("product") not in (None, "", "powermill"):
                self.diagnostics.append(
                    {
                        "line_number": line_number,
                        "message": "non-PowerMill JSONL event was not parsed",
                        "raw_line": raw_line,
                    }
                )
                continue

            event_session = str(record.get("session_id") or session_name)
            sequence = sequence_by_session.get(event_session, 0)
            sequence_by_session[event_session] = sequence + 1
            normalized = _normalize_command(command)
            operation = self.profile.operation(normalized)
            action = self._action(normalized)
            safety = self.profile.assess(normalized)
            risk_level = safety.level
            risk_reasons = list(safety.reasons)
            if (
                risk_level == "safe"
                and not action.startswith("powermill.macro.")
                and self.profile.assess_query(normalized).level != "safe"
            ):
                risk_level = "review"
                risk_reasons = [
                    "state-changing PowerMill action requires dry-run and human review"
                ]
            source_hint = str(record.get("source") or source_file)
            explicit_source_mode = str(record.get("source_mode") or "").strip()
            explicit_mode = record.get("mode")
            if explicit_source_mode == "execution_audit":
                source_mode = "execution_audit"
                legacy_mode = "automation"
            else:
                source_mode = self.profile.mode(
                    normalized,
                    source=source_hint,
                    prompted=bool(record.get("prompted")),
                    explicit=explicit_source_mode or (
                        str(explicit_mode) if explicit_mode else None
                    ),
                )
                legacy_mode = source_mode
            default_gaps = _declared_default_gaps(normalized, record)
            arguments = _ordered_arguments(normalized, operation)
            event: ActivityEvent = {
                "schema_version": 1,
                "session_id": event_session,
                "seq": sequence,
                "product": "powermill",
                "action": action,
                "category": self._category(action, normalized),
                "mode": legacy_mode,
                "params": {
                    "raw_line": raw_line,
                    "raw_command": command,
                    "normalized_command": normalized,
                    "operation": operation,
                    "arguments": arguments,
                    "argument_order": [item["raw"] for item in arguments],
                    "default_value_gaps": default_gaps,
                    "risk": risk_level,
                    "risk_reasons": risk_reasons,
                    "source": source_hint,
                },
                "source_file": str(record.get("source_file") or source_file),
                "source_line": line_number,
                "duration_ms": record.get("duration_ms"),
                "timestamp": record.get("timestamp"),
                "source_mode": source_mode,
                "view_level": "L1",
                "expertise_label": str(record.get("expertise_label") or "unlabeled"),
                "review_status": (
                    "needs_review"
                    if risk_level != "safe" or default_gaps
                    else "unreviewed"
                ),
                "recipe_hash": None,
            }
            self._copy_optional_context(event, record)
            events.append(event)
        return events

    def parse_lossless(
        self,
        source: bytes | bytearray | str | Path | IO[str] | IO[bytes],
    ) -> "LosslessPowerMillDocument":
        """Parse offline evidence without changing or executing the source."""

        return PowerMillMacroParser(
            source_name=self.source_name,
            profile=self.profile,
        ).parse(source)

    def import_flow(
        self,
        source: bytes | bytearray | str | Path | IO[str] | IO[bytes],
        *,
        include_sources: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Return a frozen-shape FlowGraph without invoking a CAM transport."""

        from cam_automation.adapters.powermill_flow import PowerMillFlowImporter

        importer = PowerMillFlowImporter(
            source_name=self.source_name,
            target_versions=([self.target_version] if self.target_version else ()),
            include_sources=include_sources,
            profile=self.profile,
        )
        graph = importer.import_source(source)
        self.flow_diagnostics = list(importer.diagnostics)
        return graph

    parse_flow = import_flow

    def _read_source(self, source: str | Path | IO[str]) -> tuple[str, str]:
        if isinstance(source, Path):
            return source.read_text(encoding="utf-8-sig"), str(source)
        if hasattr(source, "read"):
            stream = source
            text = stream.read()
            if not isinstance(text, str):
                raise TypeError("PowerMill source stream must return text")
            name = getattr(stream, "name", self.source_name)
            return text, str(name)
        if not isinstance(source, str):
            raise TypeError("PowerMill source must be text, a Path, or a text stream")
        if "\n" not in source and "\r" not in source:
            candidate = Path(source)
            try:
                if candidate.is_file():
                    return candidate.read_text(encoding="utf-8-sig"), str(candidate)
            except OSError:
                pass
        return source, self.source_name

    def _extract_record(self, raw_line: str) -> dict[str, Any]:
        stripped = raw_line.strip()
        if stripped.startswith("{"):
            decoded = json.loads(stripped)
            if not isinstance(decoded, dict):
                raise ValueError("JSONL line must be an object")
            params = decoded.get("params") if isinstance(decoded.get("params"), Mapping) else {}
            command = (
                decoded.get("command")
                or decoded.get("macro")
                or decoded.get("text")
                or params.get("command")
                or params.get("raw_command")
            )
            if not isinstance(command, str):
                raise ValueError("JSONL event has no string command, macro, or text field")
            record = dict(decoded)
            record["command"] = command
            if "parameters" not in record and "parameters" in params:
                record["parameters"] = params["parameters"]
            if "defaults" not in record and "defaults" in params:
                record["defaults"] = params["defaults"]
            return record

        timestamp: str | None = None
        command = stripped
        match = _BRACKET_TIMESTAMP.match(command) or _PIPE_TIMESTAMP.match(command)
        if match:
            timestamp = match.group("timestamp").strip()
            command = match.group("command")
        prompted = bool(_PROMPT.match(command))
        command = _PROMPT.sub("", command)
        return {
            "command": command.strip(),
            "timestamp": timestamp,
            "source": "macro-log",
            "prompted": prompted,
        }

    def _action(self, command: str) -> str:
        if _FUNCTION.match(command):
            return "powermill.macro.function.define"
        if _DECLARATION.match(command):
            return "powermill.macro.parameter.define"
        return self.profile.action(command)

    def _category(self, action: str, command: str) -> str:
        if action.startswith("powermill.macro."):
            return "macro"
        return self.profile.category(command)

    def _copy_optional_context(
        self,
        event: ActivityEvent,
        record: Mapping[str, Any],
    ) -> None:
        instance_id = record.get("instance_id") or self.instance_id
        project_id = record.get("project_id") if "project_id" in record else self.project_id
        target_version = record.get("target_version") or self.target_version
        if instance_id:
            event["instance_id"] = str(instance_id)
        if project_id is not None:
            event["project_id"] = str(project_id)
        if target_version:
            event["target_version"] = str(target_version)


@runtime_checkable
class PowerMillTransport(Protocol):
    """Narrow PowerMill read-only query and snapshot boundary."""

    name: str
    offline: bool

    def query(self, instance_id: str, command: str | Mapping[str, Any]) -> CommandResponse:
        ...

    def snapshot(self, instance_id: str) -> SnapshotMetadata:
        ...


class FixturePowerMillTransport:
    """Completely offline, per-instance fixture transport."""

    name = "fixture"
    offline = True

    def __init__(
        self,
        fixtures: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        profile: PowerMillProfile | None = None,
    ) -> None:
        self.profile = profile or PowerMillProfile()
        self._fixtures: dict[str, dict[str, Any]] = {
            str(instance_id): dict(value)
            for instance_id, value in (fixtures or {}).items()
        }
        self._connected: set[str] = set()
        self._locks: dict[str, threading.RLock] = {}
        self._sequence: dict[str, int] = {}

    @classmethod
    def from_file(cls, path: str | Path) -> "FixturePowerMillTransport":
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, Mapping) or not isinstance(payload.get("instances"), Mapping):
            raise ValueError("fixture transport file must contain an instances object")
        return cls(payload["instances"])

    def connect(self, descriptor: Mapping[str, Any]) -> None:
        instance_id = str(descriptor.get("instance_id", ""))
        if not instance_id.startswith("powermill:"):
            raise ValueError("fixture transport only accepts PowerMill instance descriptors")
        fixture = self._fixtures.setdefault(instance_id, {})
        fixture.setdefault("target_version", descriptor.get("target_version"))
        fixture.setdefault("project_id", descriptor.get("project_id"))
        fixture.setdefault("snapshot", {})
        fixture.setdefault("responses", {})
        self._connected.add(instance_id)
        self._locks.setdefault(instance_id, threading.RLock())

    def disconnect(self, instance_id: str) -> None:
        self._connected.discard(instance_id)

    def is_connected(self, instance_id: str) -> bool:
        return instance_id in self._connected

    def capabilities(self, instance_id: str) -> tuple[str, ...]:
        if instance_id not in self._fixtures:
            return ()
        return ("macro.parse", "fixture.query", "fixture.snapshot")

    def query(self, instance_id: str, command: str | Mapping[str, Any]) -> CommandResponse:
        lock = self._locks.setdefault(instance_id, threading.RLock())
        with lock:
            operation, arguments, task_id = self._query_parts(command)
            safety = self.profile.assess_query(operation)
            if safety.level != "safe":
                return self._response(
                    instance_id,
                    task_id,
                    "rejected",
                    error={
                        "code": "unsafe_query",
                        "message": "; ".join(safety.reasons),
                    },
                )
            unsafe_path = _unsafe_argument_path(arguments)
            if unsafe_path:
                return self._response(
                    instance_id,
                    task_id,
                    "rejected",
                    error={
                        "code": "unsafe_query_arguments",
                        "message": f"Unsafe executable payload is not allowed at {unsafe_path}.",
                    },
                )
            if instance_id not in self._fixtures or instance_id not in self._connected:
                return self._response(
                    instance_id,
                    task_id,
                    "disconnected",
                    error={
                        "code": "instance_disconnected",
                        "message": "No offline fixture connection exists for this instance.",
                    },
                )

            fixture = self._fixtures[instance_id]
            responses = fixture.get("responses")
            response_fixture = (
                responses.get(operation)
                if isinstance(responses, Mapping)
                else None
            )
            if isinstance(response_fixture, Mapping):
                fixture_status = str(response_fixture.get("status") or "succeeded")
                structured = dict(response_fixture.get("structured_response") or {})
                raw_response = response_fixture.get("raw_response")
                fixture_error = response_fixture.get("error")
            else:
                fixture_status = "succeeded"
                structured = {
                    "operation": operation,
                    "arguments": dict(arguments),
                    "result": {},
                }
                raw_response = "Offline fixture query completed; no CAM command was sent."
                fixture_error = None
            if raw_response is not None and _MACHINE_CODE.search(str(raw_response)):
                return self._response(
                    instance_id,
                    task_id,
                    "rejected",
                    error={
                        "code": "machine_output_redacted",
                        "message": (
                            "Fixture response resembled machine-ready output "
                            "and was discarded."
                        ),
                    },
                )
            structured.update(
                {
                    "transport": self.name,
                    "offline": True,
                    "commands_sent": 0,
                }
            )
            return self._response(
                instance_id,
                task_id,
                fixture_status,
                raw_response=str(raw_response) if raw_response is not None else None,
                structured_response=structured if fixture_status == "succeeded" else None,
                error=(
                    fixture_error
                    if isinstance(fixture_error, Mapping)
                    else (
                        {
                            "code": f"fixture_{fixture_status}",
                            "message": f"Offline fixture returned {fixture_status}.",
                        }
                        if fixture_status != "succeeded"
                        else None
                    )
                ),
            )

    def snapshot(self, instance_id: str) -> SnapshotMetadata:
        fixture = self._fixtures.get(instance_id)
        if fixture is None or instance_id not in self._connected:
            return {
                "schema_version": 1,
                "snapshot_id": None,
                "product": "powermill",
                "target_instance_id": instance_id,
                "project_id": None,
                "target_version": None,
                "captured_at": _utc_now(),
                "content_hash": None,
                "status": "disconnected",
                "transport": self.name,
                "offline": True,
            }
        snapshot = fixture.get("snapshot")
        value = dict(snapshot) if isinstance(snapshot, Mapping) else {}
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            "schema_version": 1,
            "snapshot_id": value.get("snapshot_id") or f"fixture:{digest[:16]}",
            "product": "powermill",
            "target_instance_id": instance_id,
            "project_id": value.get("project_id", fixture.get("project_id")),
            "target_version": value.get("target_version", fixture.get("target_version")),
            "captured_at": value.get("captured_at") or _utc_now(),
            "content_hash": value.get("content_hash") or f"sha256:{digest}",
            "status": "succeeded",
            "transport": self.name,
            "offline": True,
            "metadata": value.get("metadata", {}),
        }

    def _query_parts(
        self,
        command: str | Mapping[str, Any],
    ) -> tuple[str, Mapping[str, Any], str]:
        if isinstance(command, str):
            operation = _normalize_command(command)
            arguments: Mapping[str, Any] = {}
            task_id = ""
        elif isinstance(command, Mapping):
            operation = str(command.get("operation") or command.get("command") or "").strip()
            value = command.get("arguments")
            arguments = value if isinstance(value, Mapping) else {}
            task_id = str(command.get("task_id") or "")
        else:
            raise TypeError("PowerMill query must be a string or mapping")
        return operation, arguments, task_id

    def _response(
        self,
        instance_id: str,
        task_id: str,
        status: str,
        *,
        raw_response: str | None = None,
        structured_response: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> CommandResponse:
        sequence = self._sequence.get(instance_id, 0) + 1
        self._sequence[instance_id] = sequence
        fixture = self._fixtures.get(instance_id, {})
        now = _utc_now()
        suffix = hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:10]
        return {
            "schema_version": 1,
            "response_id": f"fixture-response:{suffix}:{sequence}",
            "task_id": task_id or f"fixture-query:{suffix}:{sequence}",
            "product": "powermill",
            "target_version": str(fixture.get("target_version") or ""),
            "target_instance_id": instance_id,
            "project_id": str(fixture.get("project_id") or ""),
            "status": status,
            "started_at": now,
            "completed_at": now,
            "duration_ms": 0,
            "raw_response": raw_response,
            "structured_response": (
                dict(structured_response) if structured_response is not None else None
            ),
            "diff_report": None,
            "error": dict(error) if error is not None else None,
        }


class PowerMillOfflineImportError(ValueError):
    """Structured, fail-closed error raised by the offline parser."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PowerMillSourceToken:
    token_id: str
    index: int
    kind: str
    raw: str
    start_byte: int
    end_byte: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    @property
    def source_span(self) -> dict[str, Any]:
        return {
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "column_encoding": "unicode_scalar",
        }

    def as_json(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "index": self.index,
            "kind": self.kind,
            "raw": self.raw,
            "source_span": self.source_span,
        }


@dataclass(frozen=True)
class PowerMillStatement:
    statement_id: str
    index: int
    kind: str
    command: str
    operation: str
    action: str
    raw_line: str
    source_span: dict[str, Any]
    command_span: dict[str, Any]
    token_ids: tuple[str, ...]
    envelope: dict[str, Any] | None
    metadata: dict[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "index": self.index,
            "kind": self.kind,
            "command": self.command,
            "operation": self.operation,
            "action": self.action,
            "raw_line": self.raw_line,
            "source_span": dict(self.source_span),
            "command_span": dict(self.command_span),
            "token_ids": list(self.token_ids),
            "envelope": dict(self.envelope) if self.envelope is not None else None,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class LosslessPowerMillDocument:
    source_name: str
    original_bytes: bytes
    text: str
    encoding: str
    bom: str
    newline_profile: str
    content_hash: str
    tokens: tuple[PowerMillSourceToken, ...]
    statements: tuple[PowerMillStatement, ...]
    end_line: int
    end_column: int

    def round_trip_bytes(self) -> bytes:
        return self.original_bytes

    def token_by_id(self) -> dict[str, PowerMillSourceToken]:
        return {token.token_id: token for token in self.tokens}


@dataclass(frozen=True)
class _DecodedPowerMillSource:
    text: str
    encoding: str
    bom: str
    codec: str
    bom_length: int


@dataclass(frozen=True)
class _PowerMillLine:
    line_number: int
    start_character: int
    content: str
    newline: str


_FLOW_DECLARATION = re.compile(
    r"^\s*(?P<type>INT|INTEGER|REAL|STRING|BOOL|BOOLEAN|ENTITY|OBJECT)"
    r"(?:\s+(?:ARRAY|LIST))?\s+(?P<name>\$?[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*=\s*(?P<expression>.*))?\s*(?P<brace>\{)?\s*$",
    re.IGNORECASE,
)
_FLOW_ASSIGNMENT = re.compile(
    r"^\s*(?P<name>\$[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<expression>.+?)\s*$",
    re.IGNORECASE,
)
_FLOW_FUNCTION = re.compile(
    r"^\s*FUNCTION\s+(?P<name>\$?[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<parameters>.*)\)\s*(?P<brace>\{)?\s*$",
    re.IGNORECASE,
)
_FLOW_IF = re.compile(r"^\s*IF\s+(?P<condition>.+?)\s*\{?\s*$", re.IGNORECASE)
_FLOW_ELSEIF = re.compile(
    r"^\s*\}?\s*ELSEIF\s+(?P<condition>.+?)\s*\{?\s*$",
    re.IGNORECASE,
)
_FLOW_ELSE = re.compile(r"^\s*\}?\s*ELSE\s*\{?\s*$", re.IGNORECASE)
_FLOW_SWITCH = re.compile(r"^\s*SWITCH\s+(?P<expression>.+?)\s*\{?\s*$", re.IGNORECASE)
_FLOW_CASE = re.compile(r"^\s*CASE\s+(?P<expression>.+?)\s*:?\s*$", re.IGNORECASE)
_FLOW_DEFAULT = re.compile(r"^\s*DEFAULT\s*:?\s*$", re.IGNORECASE)
_FLOW_FOREACH = re.compile(
    r"^\s*FOREACH\s+(?P<variable>\$?[A-Za-z_][A-Za-z0-9_]*)\s+IN\s+"
    r"(?P<expression>.+?)\s*\{?\s*$",
    re.IGNORECASE,
)
_FLOW_WHILE = re.compile(r"^\s*WHILE\s+(?P<condition>.+?)\s*\{?\s*$", re.IGNORECASE)
_FLOW_DO = re.compile(r"^\s*DO\s*\{?\s*$", re.IGNORECASE)
_FLOW_DO_WHILE = re.compile(
    r"^\s*\}\s*WHILE\s+(?P<condition>.+?)\s*;?\s*$",
    re.IGNORECASE,
)
_FLOW_CALL = re.compile(
    r"^\s*CALL\s+(?P<name>\$?[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*\((?P<arguments>.*)\))?\s*$",
    re.IGNORECASE,
)
_FLOW_INCLUDE = re.compile(r"^\s*INCLUDE\s+(?P<path>.+?)\s*$", re.IGNORECASE)
_FLOW_MACRO = re.compile(
    r"^\s*MACRO(?:\s+RUN|\s+EXECUTE)?\s+(?P<path>(?:\"[^\"]*\"|'[^']*'|\S+))"
    r"(?P<arguments>.*)$",
    re.IGNORECASE,
)
_FLOW_DOCOMMAND = re.compile(r"^\s*DOCOMMAND\s+(?P<expression>.+?)\s*$", re.IGNORECASE)
_FLOW_NX = re.compile(
    r"(?im)^\s*(?:from|import)\s+NXOpen\b|\bNXOpen\.|"
    r"\b(?:UF)?Session\.Get(?:UF)?Session\s*\(|\bJournalIdentifier\b"
)
_FLOW_ACTUAL_MACHINE_OUTPUT = re.compile(
    rb"(?im)^\s*(?:"
    rb"%\s*$"
    rb"|(?:N\d+[\t ]+)?(?:G\d+|M\d+)(?:\s|$)"
    rb"|(?:GOTO|FEDRAT|LOADTL|PARTNO)/"
    rb")"
)
_FLOW_VENDOR = re.compile(
    r"^(?:PLUGIN|ADDIN|VENDOR|POWERTOOLS|VERICUT)(?:\s|$)|"
    r"\b(?:PLUGIN|ADDIN|VENDOR)[._:-]",
    re.IGNORECASE,
)
_FLOW_SELECTION = re.compile(
    r"\b(?:SELECT|SELECTION|PICK|MULTISELECT|ENTITYSELECT)\b",
    re.IGNORECASE,
)
_FLOW_STATE = re.compile(
    r"^(?:ACTIVATE|DEACTIVATE|FORM|MODE|DIALOGS|GRAPHICS|LOCK|UNLOCK)\b|"
    r"\bACTIVE_(?:ENTITY|TOOL|TOOLPATH|WORKPLANE)\b",
    re.IGNORECASE,
)
_FLOW_KNOWN_COMMANDS = frozenset(
    {
        "ACCEPT",
        "ACTIVATE",
        "CALCULATE",
        "CANCEL",
        "COPY",
        "CREATE",
        "DIALOGS",
        "DRAW",
        "ECHO",
        "EDIT",
        "EXPORT",
        "FORM",
        "IMPORT",
        "KEEP",
        "MODE",
        "PRINT",
        "PROJECT",
        "RENAME",
        "RESET",
        "SAVE",
        "SIZE",
        "STATUS",
        "UNDRAW",
        "WRITE",
    }
)
_MAX_SOURCE_BYTES = 10 * 1024 * 1024
_MAX_SOURCE_STRING_BYTES = 1024 * 1024


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def contains_machine_ready_output(value: bytes | str) -> bool:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return _FLOW_ACTUAL_MACHINE_OUTPUT.search(raw) is not None


def _decode_powermill_bytes(value: bytes) -> _DecodedPowerMillSource:
    if value.startswith(b"\xef\xbb\xbf"):
        return _DecodedPowerMillSource(
            value[3:].decode("utf-8"),
            "utf-8",
            "utf8",
            "utf-8",
            3,
        )
    if value.startswith(b"\xff\xfe"):
        return _DecodedPowerMillSource(
            value[2:].decode("utf-16-le"),
            "utf-16-le",
            "utf16le",
            "utf-16-le",
            2,
        )
    if value.startswith(b"\xfe\xff"):
        return _DecodedPowerMillSource(
            value[2:].decode("utf-16-be"),
            "utf-16-be",
            "utf16be",
            "utf-16-be",
            2,
        )
    try:
        return _DecodedPowerMillSource(value.decode("utf-8"), "utf-8", "none", "utf-8", 0)
    except UnicodeDecodeError:
        try:
            return _DecodedPowerMillSource(
                value.decode("windows-1252"),
                "windows-1252",
                "none",
                "windows-1252",
                0,
            )
        except UnicodeDecodeError:
            return _DecodedPowerMillSource(
                value.decode("latin-1"),
                "unknown",
                "none",
                "latin-1",
                0,
            )


def _newline_profile(text: str) -> str:
    crlf = len(re.findall(r"\r\n", text))
    without_crlf = text.replace("\r\n", "")
    lf = without_crlf.count("\n")
    cr = without_crlf.count("\r")
    kinds = sum(bool(count) for count in (crlf, lf, cr))
    if kinds == 0:
        return "none"
    if kinds > 1:
        return "mixed"
    if crlf:
        return "crlf"
    if lf:
        return "lf"
    return "cr"


def _source_lines(text: str) -> list[_PowerMillLine]:
    lines: list[_PowerMillLine] = []
    position = 0
    line_number = 1
    while position < len(text):
        match = re.search(r"\r\n|\r|\n", text[position:])
        if match is None:
            lines.append(_PowerMillLine(line_number, position, text[position:], ""))
            position = len(text)
            break
        newline_start = position + match.start()
        newline_end = position + match.end()
        lines.append(
            _PowerMillLine(
                line_number,
                position,
                text[position:newline_start],
                text[newline_start:newline_end],
            )
        )
        position = newline_end
        line_number += 1
    if not lines and text == "":
        return []
    return lines


def _character_byte_offsets(
    text: str,
    *,
    codec: str,
    bom_length: int,
) -> list[int]:
    offsets = [bom_length]
    current = bom_length
    for character in text:
        current += len(character.encode(codec))
        offsets.append(current)
    return offsets


def _span(
    *,
    start_byte: int,
    end_byte: int,
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
) -> dict[str, Any]:
    return {
        "start_byte": start_byte,
        "end_byte": end_byte,
        "start_line": start_line,
        "start_column": start_column,
        "end_line": end_line,
        "end_column": end_column,
        "column_encoding": "unicode_scalar",
    }


def _count_braces(command: str) -> tuple[int, int]:
    opens = 0
    closes = 0
    quote = ""
    index = 0
    while index < len(command):
        character = command[index]
        if quote:
            if character == quote:
                if index + 1 < len(command) and command[index + 1] == quote:
                    index += 1
                else:
                    quote = ""
        elif character in {'"', "'"}:
            quote = character
        elif character == "{":
            opens += 1
        elif character == "}":
            closes += 1
        index += 1
    return opens, closes


def _constant_string(expression: str) -> str | None:
    value = expression.strip()
    if len(value) < 2 or value[0] not in {'"', "'"} or value[-1] != value[0]:
        return None
    quote = value[0]
    index = 1
    output: list[str] = []
    while index < len(value) - 1:
        character = value[index]
        if character == quote:
            if index + 1 < len(value) - 1 and value[index + 1] == quote:
                output.append(quote)
                index += 2
                continue
            return None
        output.append(character)
        index += 1
    return "".join(output)


def _unquote(value: str) -> str:
    decoded, quoted = _decoded_token(value.strip())
    return str(decoded) if quoted else value.strip()


def _classify_flow_statement(
    command: str,
    profile: PowerMillProfile,
) -> tuple[str, str, str, dict[str, Any]]:
    normalized = _normalize_command(command)
    operation = profile.operation(normalized)
    opens, closes = _count_braces(normalized)
    metadata: dict[str, Any] = {
        "opens_block": opens,
        "closes_block": closes,
    }

    match = _FLOW_DO_WHILE.match(normalized)
    if match:
        metadata["condition"] = match.group("condition").strip()
        return (
            "do_while_condition",
            "WHILE",
            "powermill.control.do_while",
            metadata,
        )
    match = _FLOW_ELSEIF.match(normalized)
    if match:
        metadata["condition"] = match.group("condition").strip()
        return "elseif", "ELSEIF", "powermill.control.elseif", metadata
    if _FLOW_ELSE.match(normalized):
        return "else", "ELSE", "powermill.control.else", metadata
    match = _FLOW_FUNCTION.match(normalized)
    if match:
        parameters: list[dict[str, Any]] = []
        for raw_parameter in _split_parameters(match.group("parameters")):
            declaration = _FLOW_DECLARATION.match(raw_parameter)
            if declaration is None:
                parameters.append(
                    {
                        "raw": raw_parameter,
                        "name": "unknown",
                        "value_type": "unknown",
                        "recorded_default": "unknown",
                    }
                )
                continue
            expression = declaration.group("expression")
            parameters.append(
                {
                    "raw": raw_parameter,
                    "name": declaration.group("name"),
                    "value_type": declaration.group("type").upper(),
                    "recorded_default": (
                        expression.strip() if expression is not None else "unknown"
                    ),
                }
            )
        metadata.update(
            {
                "name": match.group("name"),
                "parameters": parameters,
            }
        )
        return (
            "function",
            "FUNCTION",
            "powermill.function.declare",
            metadata,
        )
    match = _FLOW_IF.match(normalized)
    if match:
        metadata["condition"] = match.group("condition").strip()
        return "if", "IF", "powermill.control.if", metadata
    match = _FLOW_SWITCH.match(normalized)
    if match:
        metadata["expression"] = match.group("expression").strip()
        return "switch", "SWITCH", "powermill.control.switch", metadata
    match = _FLOW_CASE.match(normalized)
    if match:
        metadata["expression"] = match.group("expression").strip()
        return "case", "CASE", "powermill.control.case", metadata
    if _FLOW_DEFAULT.match(normalized):
        return "default", "DEFAULT", "powermill.control.default", metadata
    match = _FLOW_FOREACH.match(normalized)
    if match:
        metadata.update(
            {
                "variable": match.group("variable"),
                "expression": match.group("expression").strip(),
            }
        )
        return "foreach", "FOREACH", "powermill.control.foreach", metadata
    match = _FLOW_WHILE.match(normalized)
    if match:
        metadata["condition"] = match.group("condition").strip()
        return "while", "WHILE", "powermill.control.while", metadata
    if _FLOW_DO.match(normalized):
        return "do", "DO", "powermill.control.do", metadata
    if re.fullmatch(r"\s*BREAK\s*;?\s*", normalized, re.IGNORECASE):
        return "break", "BREAK", "powermill.control.break", metadata
    if re.fullmatch(r"\s*CONTINUE\s*;?\s*", normalized, re.IGNORECASE):
        return "continue", "CONTINUE", "powermill.control.continue", metadata
    if re.match(r"^\s*RETURN(?:\s|$)", normalized, re.IGNORECASE):
        metadata["expression"] = re.sub(
            r"^\s*RETURN\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        ).strip()
        return "return", "RETURN", "powermill.function.return", metadata
    match = _FLOW_CALL.match(normalized)
    if match:
        metadata.update(
            {
                "name": match.group("name"),
                "arguments": _split_parameters(match.group("arguments") or ""),
            }
        )
        return "call", "CALL", "powermill.function.call", metadata
    match = _FLOW_INCLUDE.match(normalized)
    if match:
        metadata["path"] = _unquote(match.group("path"))
        return "include", "INCLUDE", "powermill.include", metadata
    match = _FLOW_MACRO.match(normalized)
    if match:
        metadata.update(
            {
                "path": _unquote(match.group("path")),
                "arguments": _split_parameters(match.group("arguments").strip()),
            }
        )
        return "macro_call", "MACRO", "powermill.macro.call", metadata
    match = _FLOW_DOCOMMAND.match(normalized)
    if match:
        expression = match.group("expression").strip()
        constant = _constant_string(expression)
        metadata.update(
            {
                "expression": expression,
                "constant_command": constant,
                "tainted": constant is None,
            }
        )
        return "docommand", "DOCOMMAND", "powermill.command.dynamic", metadata
    match = _FLOW_DECLARATION.match(normalized)
    if match:
        metadata.update(
            {
                "name": match.group("name"),
                "value_type": match.group("type").upper(),
                "expression": match.group("expression"),
                "recorded_value": (
                    match.group("expression").strip()
                    if match.group("expression") is not None
                    else "unknown"
                ),
            }
        )
        return (
            "variable_declare",
            match.group("type").upper(),
            "powermill.variable.declare",
            metadata,
        )
    match = _FLOW_ASSIGNMENT.match(normalized)
    if match:
        metadata.update(
            {
                "name": match.group("name"),
                "expression": match.group("expression").strip(),
            }
        )
        return "variable_assign", "ASSIGN", "powermill.variable.assign", metadata
    interaction = re.match(
        r"^\s*(INPUT|QUERY|MESSAGE|FILESELECT)\b(?P<arguments>.*)$",
        normalized,
        re.IGNORECASE,
    )
    if interaction:
        keyword = interaction.group(1).upper()
        metadata.update(
            {
                "interaction": keyword.lower(),
                "arguments": interaction.group("arguments").strip(),
                "recorded_value": "unknown",
            }
        )
        return (
            "interaction",
            keyword,
            f"powermill.interaction.{keyword.lower()}",
            metadata,
        )
    if re.match(r"^\s*NOGUI\b", normalized, re.IGNORECASE):
        metadata.update(
            {
                "gui_mode": "nogui",
                "graphics_state": "unknown",
                "recorded_prior_state": "unknown",
            }
        )
        return "nogui", "NOGUI", "powermill.gui.nogui", metadata
    if normalized.strip() == "}":
        return "block_end", "BLOCK_END", "powermill.control.block_end", metadata
    if _FLOW_SELECTION.search(normalized):
        metadata.update(
            {
                "selection_state": "unknown",
                "active_entity": "unknown",
            }
        )
        return "selection", operation, "powermill.selection", metadata
    if _FLOW_STATE.search(normalized):
        metadata.update(
            {
                "prior_state": "unknown",
                "selection_state": "unknown",
                "dialog_state": "unknown",
            }
        )
        return "state", operation, "powermill.state.change", metadata
    if _FLOW_VENDOR.search(normalized):
        metadata["unsupported_reason"] = "vendor capability has no reviewed manifest"
        return (
            "vendor_opaque",
            operation,
            "powermill.unsupported.vendor",
            metadata,
        )
    if operation.split()[0] in _FLOW_KNOWN_COMMANDS:
        return "command", operation, profile.action(normalized), metadata
    metadata["unsupported_reason"] = "unknown PowerMill command"
    return (
        "unknown_opaque",
        operation,
        "powermill.unsupported.command",
        metadata,
    )


class PowerMillMacroParser:
    """Lossless, offline-only PowerMill macro and command-log parser."""

    def __init__(
        self,
        *,
        source_name: str = "powermill-inline.mac",
        profile: PowerMillProfile | None = None,
    ) -> None:
        self.source_name = source_name
        self.profile = profile or PowerMillProfile()

    def parse(
        self,
        source: bytes | bytearray | str | Path | IO[str] | IO[bytes],
    ) -> LosslessPowerMillDocument:
        original_bytes, source_name = self._read_bytes(source)
        if len(original_bytes) > _MAX_SOURCE_BYTES:
            raise PowerMillOfflineImportError(
                "RESOURCE_LIMIT_EXCEEDED",
                "PowerMill source exceeds the 10 MiB offline import limit.",
            )
        if contains_machine_ready_output(original_bytes):
            raise PowerMillOfflineImportError(
                "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
                "Machine-ready content is not accepted by the offline PowerMill importer.",
            )

        decoded = _decode_powermill_bytes(original_bytes)
        if self._looks_like_foreign_product(decoded.text, source_name):
            raise PowerMillOfflineImportError(
                "FLOW_PRODUCT_MIXED",
                "Non-PowerMill input is not accepted by the PowerMill parser.",
            )

        offsets = _character_byte_offsets(
            decoded.text,
            codec=decoded.codec,
            bom_length=decoded.bom_length,
        )
        lines = _source_lines(decoded.text)
        if any(
            len(line.content.encode(decoded.codec)) > _MAX_SOURCE_STRING_BYTES
            for line in lines
        ):
            raise PowerMillOfflineImportError(
                "RESOURCE_LIMIT_EXCEEDED",
                "PowerMill source contains a string larger than 1 MiB.",
            )
        tokens: list[PowerMillSourceToken] = []
        statements: list[PowerMillStatement] = []
        if decoded.bom_length:
            tokens.append(
                PowerMillSourceToken(
                    token_id="token:pm:000000",
                    index=0,
                    kind="trivia.bom",
                    raw="\ufeff",
                    start_byte=0,
                    end_byte=decoded.bom_length,
                    start_line=1,
                    start_column=0,
                    end_line=1,
                    end_column=0,
                )
            )

        for line in lines:
            self._parse_line(
                line,
                offsets=offsets,
                tokens=tokens,
                statements=statements,
            )

        end_line = lines[-1].line_number if lines else 1
        end_column = len(lines[-1].content) if lines else 0
        if lines and lines[-1].newline:
            end_line += 1
            end_column = 0
        document = LosslessPowerMillDocument(
            source_name=source_name,
            original_bytes=original_bytes,
            text=decoded.text,
            encoding=decoded.encoding,
            bom=decoded.bom,
            newline_profile=_newline_profile(decoded.text),
            content_hash=_sha256_bytes(original_bytes),
            tokens=tuple(tokens),
            statements=tuple(statements),
            end_line=end_line,
            end_column=end_column,
        )
        self._assert_lossless(document, decoded)
        return document

    def _read_bytes(
        self,
        source: bytes | bytearray | str | Path | IO[str] | IO[bytes],
    ) -> tuple[bytes, str]:
        if isinstance(source, Path):
            return source.read_bytes(), source.name
        if isinstance(source, (bytes, bytearray)):
            return bytes(source), self._display_name(self.source_name)
        if hasattr(source, "read"):
            value = source.read()
            name = self._display_name(str(getattr(source, "name", self.source_name)))
            if isinstance(value, bytes):
                return value, name
            if isinstance(value, str):
                return value.encode("utf-8"), name
            raise TypeError("PowerMill source stream must return text or bytes")
        if not isinstance(source, str):
            raise TypeError("PowerMill source must be bytes, text, a Path, or a stream")
        if "\n" not in source and "\r" not in source:
            candidate = Path(source)
            try:
                if candidate.is_file():
                    return candidate.read_bytes(), candidate.name
            except OSError:
                pass
        return source.encode("utf-8"), self._display_name(self.source_name)

    @staticmethod
    def _display_name(value: str) -> str:
        name = Path(value).name
        return name or "powermill-inline.mac"

    @staticmethod
    def _looks_like_foreign_product(text: str, source_name: str) -> bool:
        if Path(source_name).suffix.casefold() == ".py":
            return True
        semantic_lines = "\n".join(
            line
            for line in text.splitlines()
            if not line.lstrip().startswith(("#", "//"))
        )
        if _FLOW_NX.search(semantic_lines):
            return True
        for line in semantic_lines.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, Mapping):
                product = str(payload.get("product", "")).strip().casefold()
                if product and product != "powermill":
                    return True
        return False

    def _parse_line(
        self,
        line: _PowerMillLine,
        *,
        offsets: Sequence[int],
        tokens: list[PowerMillSourceToken],
        statements: list[PowerMillStatement],
    ) -> None:
        content = line.content
        content_start = line.start_character
        content_end = content_start + len(content)
        full_end = content_end + len(line.newline)
        stripped = content.strip()
        command = ""
        command_start_column = len(content) - len(content.lstrip())
        command_end_column = len(content.rstrip())
        envelope: dict[str, Any] | None = None
        semantic_record: Mapping[str, Any] | None = None
        log_noise = bool(_NOISE.match(stripped))

        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, Mapping):
                semantic_record = payload
                params = payload.get("params") if isinstance(payload.get("params"), Mapping) else {}
                value = (
                    payload.get("command")
                    or payload.get("macro")
                    or payload.get("text")
                    or params.get("command")
                    or params.get("raw_command")
                )
                if isinstance(value, str):
                    command = value
                    command_start_column = len(content) - len(content.lstrip())
                    command_end_column = len(content.rstrip())
                    envelope = {
                        "kind": "jsonl",
                        "raw": content,
                        "timestamp": payload.get("timestamp"),
                        "source": payload.get("source"),
                        "source_span": self._line_span(
                            line,
                            offsets,
                            0,
                            len(content),
                        ),
                    }
                else:
                    command = stripped
                    command_start_column = len(content) - len(content.lstrip())
                    command_end_column = len(content.rstrip())
                    envelope = {
                        "kind": "jsonl",
                        "raw": content,
                        "timestamp": payload.get("timestamp"),
                        "source": payload.get("source"),
                        "source_span": self._line_span(
                            line,
                            offsets,
                            0,
                            len(content),
                        ),
                    }

        if (
            not command
            and stripped
            and not log_noise
            and not stripped.startswith(("#", "//"))
        ):
            command_start_column, timestamp, prompted = self._command_start(content)
            command_end_column = len(content.rstrip())
            command = content[command_start_column:command_end_column]
            if timestamp is not None or prompted or command_start_column:
                envelope = {
                    "kind": "command_log",
                    "raw": content[:command_start_column],
                    "timestamp": timestamp,
                    "prompted": prompted,
                    "source_span": self._line_span(
                        line,
                        offsets,
                        0,
                        command_start_column,
                    ),
                }

        token_start_index = len(tokens)
        if log_noise:
            self._append_token(
                tokens,
                "trivia.log_noise",
                content,
                line,
                offsets,
                0,
                len(content),
            )
        elif envelope is not None and envelope["kind"] == "jsonl":
            self._append_token(
                tokens,
                "envelope.jsonl",
                content,
                line,
                offsets,
                0,
                len(content),
            )
        else:
            if command_start_column:
                self._append_token(
                    tokens,
                    "envelope" if envelope is not None else "trivia.whitespace",
                    content[:command_start_column],
                    line,
                    offsets,
                    0,
                    command_start_column,
                )
            self._tokenize_segment(
                content,
                line,
                offsets,
                tokens,
                command_start_column,
                command_end_column,
            )
            if command_end_column < len(content):
                self._append_token(
                    tokens,
                    "trivia.whitespace",
                    content[command_end_column:],
                    line,
                    offsets,
                    command_end_column,
                    len(content),
                )
        if line.newline:
            self._append_token(
                tokens,
                "trivia.newline",
                line.newline,
                line,
                offsets,
                len(content),
                len(content) + len(line.newline),
                end_line=line.line_number + 1,
                span_end_column=0,
            )

        if not command:
            return
        kind, operation, action, metadata = _classify_flow_statement(command, self.profile)
        if semantic_record is not None:
            metadata["jsonl"] = True
            metadata["source_mode"] = semantic_record.get("source_mode", "unknown")
            metadata["target_version"] = semantic_record.get("target_version")
            if not any(
                isinstance(semantic_record.get(field), str)
                for field in ("command", "macro", "text")
            ) and not (
                isinstance(semantic_record.get("params"), Mapping)
                and any(
                    isinstance(semantic_record["params"].get(field), str)
                    for field in ("command", "raw_command")
                )
            ):
                metadata["parse_error"] = "JSONL record has no string command field"
        statement_index = len(statements)
        statement_id = f"statement:pm:{statement_index + 1:06d}"
        token_ids = tuple(token.token_id for token in tokens[token_start_index:])
        statements.append(
            PowerMillStatement(
                statement_id=statement_id,
                index=statement_index,
                kind=kind,
                command=command,
                operation=operation,
                action=action,
                raw_line=content + line.newline,
                source_span=_span(
                    start_byte=offsets[content_start],
                    end_byte=offsets[full_end],
                    start_line=line.line_number,
                    start_column=0,
                    end_line=(
                        line.line_number + 1 if line.newline else line.line_number
                    ),
                    end_column=0 if line.newline else len(content),
                ),
                command_span=self._line_span(
                    line,
                    offsets,
                    command_start_column,
                    command_end_column,
                ),
                token_ids=token_ids,
                envelope=envelope,
                metadata=metadata,
            )
        )

    @staticmethod
    def _command_start(content: str) -> tuple[int, str | None, bool]:
        leading = len(content) - len(content.lstrip())
        remainder = content[leading:]
        timestamp: str | None = None
        timestamp_match = _BRACKET_TIMESTAMP.match(remainder) or _PIPE_TIMESTAMP.match(remainder)
        if timestamp_match:
            timestamp = timestamp_match.group("timestamp").strip()
            command = timestamp_match.group("command")
            leading += timestamp_match.start("command")
            remainder = command
        prompt_match = _PROMPT.match(remainder)
        prompted = prompt_match is not None
        if prompt_match:
            leading += prompt_match.end()
            remainder = remainder[prompt_match.end():]
        whitespace = len(remainder) - len(remainder.lstrip())
        return leading + whitespace, timestamp, prompted

    @staticmethod
    def _line_span(
        line: _PowerMillLine,
        offsets: Sequence[int],
        start_column: int,
        end_column: int,
    ) -> dict[str, Any]:
        start_character = line.start_character + start_column
        end_character = line.start_character + end_column
        return _span(
            start_byte=offsets[start_character],
            end_byte=offsets[end_character],
            start_line=line.line_number,
            start_column=start_column,
            end_line=line.line_number,
            end_column=end_column,
        )

    def _tokenize_segment(
        self,
        content: str,
        line: _PowerMillLine,
        offsets: Sequence[int],
        tokens: list[PowerMillSourceToken],
        start_column: int,
        end_column: int,
    ) -> None:
        index = start_column
        while index < end_column:
            character = content[index]
            start = index
            if character.isspace():
                index += 1
                while index < end_column and content[index].isspace():
                    index += 1
                kind = "trivia.whitespace"
            elif content.startswith("//", index) or (
                character == "#"
                and not content[start_column:index].strip()
            ):
                index = end_column
                kind = "trivia.comment"
            elif character in {'"', "'"}:
                quote = character
                index += 1
                terminated = False
                while index < end_column:
                    if content[index] == quote:
                        if index + 1 < end_column and content[index + 1] == quote:
                            index += 2
                            continue
                        index += 1
                        terminated = True
                        break
                    index += 1
                kind = "string" if terminated else "error.unterminated_string"
            elif character == "$":
                index += 1
                while index < end_column and (
                    content[index].isalnum() or content[index] == "_"
                ):
                    index += 1
                kind = "variable"
            elif character.isalpha() or character == "_":
                index += 1
                while index < end_column and (
                    content[index].isalnum() or content[index] in "_."
                ):
                    index += 1
                kind = "word"
            elif character.isdigit() or (
                character in "+-"
                and index + 1 < end_column
                and content[index + 1].isdigit()
            ):
                index += 1
                while index < end_column and (
                    content[index].isdigit() or content[index] in ".eE+-"
                ):
                    index += 1
                kind = "number"
            else:
                index += 1
                kind = "symbol"
            self._append_token(
                tokens,
                kind,
                content[start:index],
                line,
                offsets,
                start,
                index,
            )

    @staticmethod
    def _append_token(
        tokens: list[PowerMillSourceToken],
        kind: str,
        raw: str,
        line: _PowerMillLine,
        offsets: Sequence[int],
        start_column: int,
        source_end_column: int,
        *,
        end_line: int | None = None,
        span_end_column: int | None = None,
    ) -> None:
        if raw == "":
            return
        index = len(tokens)
        start_character = line.start_character + start_column
        end_character = line.start_character + source_end_column
        tokens.append(
            PowerMillSourceToken(
                token_id=f"token:pm:{index:06d}",
                index=index,
                kind=kind,
                raw=raw,
                start_byte=offsets[start_character],
                end_byte=offsets[end_character],
                start_line=line.line_number,
                start_column=start_column,
                end_line=end_line or line.line_number,
                end_column=(
                    span_end_column
                    if span_end_column is not None
                    else source_end_column
                ),
            )
        )

    @staticmethod
    def _assert_lossless(
        document: LosslessPowerMillDocument,
        decoded: _DecodedPowerMillSource,
    ) -> None:
        token_text = "".join(
            token.raw for token in document.tokens if token.kind != "trivia.bom"
        )
        if token_text != decoded.text:
            raise PowerMillOfflineImportError(
                "SOURCE_SILENT_REWRITE",
                "The lossless token stream did not preserve the complete source text.",
            )
        if document.round_trip_bytes() != document.original_bytes:
            raise PowerMillOfflineImportError(
                "SOURCE_SILENT_REWRITE",
                "The unedited PowerMill source bytes changed during import.",
            )
