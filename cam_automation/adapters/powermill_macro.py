from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO, Mapping, Protocol, runtime_checkable

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
