from __future__ import annotations

import json
import re
from typing import Any

from .models import ActivityEvent, CommandEvent, ParseDiagnostic, ParseResult, Session
from .profiles import PowerMillProfile


_SESSION_MARKER = re.compile(r"^\s*(?:#|//)\s*session\s*:\s*(.+?)\s*$", re.IGNORECASE)
_BRACKET_TIMESTAMP = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^\]]+)\]\s*(?P<command>.+)$"
)
_PIPE_TIMESTAMP = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^|]+)\|\s*(?P<command>.+)$"
)
_PROMPT = re.compile(r"^(?:POWERMILL|PMILL)\s*>\s*", re.IGNORECASE)
_NOISE = re.compile(r"^(?:INFO|DEBUG|TRACE|WARNING)\s*[:|-]", re.IGNORECASE)


def normalize_command(command: str) -> str:
    """Collapse whitespace outside quotes while preserving literal content."""

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
            if pending_space and output and output[-1] != " ":
                output.append(" ")
            pending_space = False
            quote = char
            output.append(char)
        elif char.isspace():
            pending_space = True
        else:
            if pending_space and output and output[-1] != " ":
                output.append(" ")
            pending_space = False
            output.append(char)
        index += 1
    return "".join(output).strip()


def _command_from_json(
    data: dict[str, Any],
) -> tuple[
    str,
    str | None,
    str,
    str | None,
    str | None,
    bool,
    ActivityEvent | None,
]:
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    required = {"session_id", "seq", "product", "action"}
    activity_event = ActivityEvent.from_dict(data) if required.issubset(data) else None
    command = (
        data.get("command")
        or data.get("macro")
        or data.get("text")
        or params.get("command")
        or params.get("raw_command")
        or (activity_event.action if activity_event is not None else None)
    )
    if not isinstance(command, str):
        raise ValueError("JSON event has no string command, macro, or text field")
    timestamp = data.get("timestamp")
    source = data.get("source") or data.get("source_file") or "jsonl"
    session_id = data.get("session_id")
    mode = (
        data.get("source_mode")
        or data.get("mode")
        or data.get("origin")
        or data.get("interaction_mode")
        or params.get("mode")
        or params.get("origin")
    )
    return (
        command,
        str(timestamp) if timestamp else None,
        str(source),
        str(session_id) if session_id else None,
        str(mode) if mode else None,
        False,
        activity_event,
    )


def _extract_line(
    line: str,
) -> tuple[
    str,
    str | None,
    str,
    str | None,
    str | None,
    bool,
    ActivityEvent | None,
]:
    stripped = line.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        decoded = json.loads(stripped)
        if not isinstance(decoded, dict):
            raise ValueError("JSON line must be an object")
        return _command_from_json(decoded)

    timestamp = None
    source = "macro-log"
    match = _BRACKET_TIMESTAMP.match(stripped) or _PIPE_TIMESTAMP.match(stripped)
    if match:
        timestamp = match.group("timestamp").strip()
        stripped = match.group("command").strip()
    prompted = bool(_PROMPT.match(stripped))
    stripped = _PROMPT.sub("", stripped)
    return stripped, timestamp, source, None, None, prompted, None


def parse_log(text: str, profile: PowerMillProfile | None = None) -> ParseResult:
    profile = profile or PowerMillProfile()
    sessions: list[Session] = []
    diagnostics: list[ParseDiagnostic] = []
    current = Session("session-1")
    sequence = 0
    ignored = 0
    lines = text.splitlines()

    def finish_current() -> None:
        nonlocal current
        if current.events:
            sessions.append(current)

    for line_number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        marker = _SESSION_MARKER.match(stripped)
        if marker:
            finish_current()
            current = Session(marker.group(1).strip())
            continue
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            ignored += 1
            continue
        if _NOISE.match(stripped):
            ignored += 1
            continue

        try:
            (
                command,
                timestamp,
                source,
                session_id,
                explicit_mode,
                prompted,
                activity_event,
            ) = _extract_line(raw)
        except (json.JSONDecodeError, ValueError) as error:
            diagnostics.append(ParseDiagnostic(line_number, str(error), raw))
            continue

        normalized = normalize_command(command)
        if not normalized:
            ignored += 1
            continue

        if session_id and current.name != session_id:
            finish_current()
            current = Session(session_id)

        sequence += 1
        product = activity_event.product if activity_event else "powermill"
        action = activity_event.action if activity_event else profile.action(normalized)
        category = activity_event.category if activity_event else profile.category(normalized)
        mode = (
            activity_event.mode
            if activity_event
            else profile.mode(
                normalized,
                source=source,
                prompted=prompted,
                explicit=explicit_mode,
            )
        )
        current.events.append(
            CommandEvent(
                sequence=sequence,
                line_number=line_number,
                command=command.strip(),
                normalized=normalized,
                operation=profile.operation(normalized),
                product=product,
                action=action,
                category=category,
                mode=mode,
                source=source,
                timestamp=timestamp,
                safety=profile.assess(normalized),
                activity_event=activity_event,
            )
        )

    finish_current()
    return ParseResult(
        sessions=sessions,
        diagnostics=diagnostics,
        input_lines=len(lines),
        ignored_lines=ignored,
    )
