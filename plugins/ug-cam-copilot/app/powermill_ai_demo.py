#!/usr/bin/env python3
"""Local, dependency-free MVP for learning PowerMill and NX CAM workflows.

The adapter boundary is intentionally small: host applications only need to
export a text log, while this process normalizes events, mines repetitions,
and emits a reviewed automation draft. It never executes a generated macro.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import threading
import webbrowser
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
SAMPLE_DIR = ROOT / "samples"
SRC_DIR = ROOT.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from ugcam_ai.adapters.nx_journal import NxJournalAdapter  # noqa: E402
from ugcam_ai.models import normalize_event_mode  # noqa: E402


@dataclass(frozen=True)
class Event:
    index: int
    app: str
    action: str
    target: str = ""
    value: str = ""
    raw: str = ""
    timestamp: str = ""
    mode: str = "manual"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        return "|".join(
            (
                self.app.lower().strip(),
                self.action.lower().strip(),
                self.target.lower().strip(),
            )
        )


def _clean(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _classify_event_mode(
    value: Any = None,
    *,
    origin: str = "",
    action: str = "",
) -> str:
    if value is not None and _clean(value):
        return normalize_event_mode(value)
    normalized_origin = origin.lower()
    if any(
        marker in normalized_origin
        for marker in ("automation", "generated", "replay", "recipe", "script", "batch")
    ):
        return "automation"
    if any(
        marker in normalized_origin
        for marker in ("system", "background", "telemetry", "heartbeat", "service")
    ):
        return "system"
    if re.match(
        r"^(?:info|debug|trace|warning|error|status|heartbeat|telemetry|system)\b",
        action.strip(),
        re.IGNORECASE,
    ):
        return "system"
    return "manual"


def _event_from_mapping(index: int, item: dict[str, Any], source: str) -> Event:
    action = _clean(item.get("action") or item.get("command") or item.get("method") or "event")
    target = _clean(
        item.get("target")
        or item.get("category")
        or item.get("object")
        or item.get("entity")
    )
    value = _clean(item.get("value") or item.get("parameter") or item.get("args"))
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    app = _clean(item.get("app") or source or "unknown").lower()
    mode = _classify_event_mode(
        item.get("mode")
        or item.get("interaction_mode")
        or metadata.get("mode"),
        origin=_clean(item.get("source") or item.get("origin")),
        action=action,
    )
    return Event(
        index=index,
        app=app,
        action=action,
        target=target,
        value=value,
        raw=_clean(item.get("raw") or json.dumps(item, ensure_ascii=False)),
        timestamp=_clean(item.get("timestamp") or item.get("time")),
        mode=mode,
        metadata=metadata,
    )


def parse_events(text: str, source: str = "powermill", fmt: str = "auto") -> list[Event]:
    """Parse JSON/JSONL, PowerMill .mac, or a lightweight NX journal export."""
    text = text.strip()
    if not text:
        return []
    source = source.lower().strip() or "powermill"
    if fmt == "auto":
        if text.startswith("[") or text.startswith("{"):
            fmt = "json"
        elif source in {"nx", "ug", "siemens-nx"} or "NXOpen" in text:
            fmt = "nx-journal"
        else:
            fmt = "powermill-macro"

    events: list[Event] = []
    if fmt in {"json", "jsonl"}:
        try:
            parsed = json.loads(text) if fmt == "json" or text.startswith("[") else None
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            values = parsed if isinstance(parsed, list) else parsed.get("events", [parsed])
            return [
                _event_from_mapping(i, item, source)
                for i, item in enumerate(values)
                if isinstance(item, dict)
            ]
        for line in text.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(_event_from_mapping(len(events), item, source))
        return events

    if fmt == "nx-journal":
        try:
            parsed_nx = NxJournalAdapter().parse_source(text)
        except SyntaxError:
            parsed_nx = []
        if parsed_nx:
            return [
                Event(
                    index=index,
                    app="nx",
                    action=item.action,
                    target=item.category,
                    value=_clean(item.params.get("api")),
                    raw=f"{item.source_file}:{item.source_line}",
                    mode=item.mode,
                    metadata=item.to_dict(),
                )
                for index, item in enumerate(parsed_nx)
            ]

    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith(("//", "#", ";", "REM ")):
            continue
        if fmt == "nx-journal":
            # Keep the call name as the target so a journal can be reviewed
            # without trying to import NXOpen on the analysis machine.
            match = re.search(r"(?:\.|new )([A-Za-z_][A-Za-z0-9_]*)\s*\(", raw)
            target = match.group(1) if match else "journal"
            action = "api_call"
            value = raw[:240]
            app = "nx"
        else:
            tokens = raw.split(None, 2)
            action = tokens[0]
            target = tokens[1] if len(tokens) > 1 else ""
            value = tokens[2] if len(tokens) > 2 else ""
            app = "powermill"
        events.append(
            Event(
                index=len(events),
                app=app,
                action=action,
                target=target.strip('"'),
                value=value.strip('"'),
                raw=raw,
                mode=_classify_event_mode(action=action),
            )
        )
    return events


def _windows(events: list[Event], size: int) -> Iterable[tuple[Event, ...]]:
    for start in range(0, len(events) - size + 1):
        yield tuple(events[start : start + size])


@dataclass
class Recipe:
    id: str
    title: str
    app: str
    support: int
    confidence: float
    steps: list[dict[str, Any]]
    occurrences: list[list[int]]
    parameter_candidates: list[str]


def mine_recipes(events: list[Event], min_support: int = 2) -> list[Recipe]:
    """Find repeated contiguous action patterns, ignoring changing values."""
    if len(events) < 2:
        return []
    candidates: dict[tuple[str, ...], list[list[int]]] = {}
    max_size = min(5, len(events))
    for size in range(2, max_size + 1):
        for window in _windows(events, size):
            key = tuple(item.signature for item in window)
            candidates.setdefault(key, []).append([item.index for item in window])

    # Keep the longest useful pattern for each start position and remove
    # patterns subsumed by a longer one with the same occurrence count.
    ranked = sorted(
        (
            (key, occurrences)
            for key, occurrences in candidates.items()
            if len(occurrences) >= min_support
        ),
        key=lambda item: (len(item[0]), len(item[1])),
        reverse=True,
    )
    recipes: list[Recipe] = []
    covered: set[tuple[str, ...]] = set()
    for key, occurrences in ranked:
        if key in covered:
            continue
        covered.update(
            other_key
            for other_key, _ in ranked
            if len(other_key) < len(key)
            and any(
                tuple(key[i : i + len(other_key)]) == other_key
                for i in range(len(key) - len(other_key) + 1)
            )
        )
        representative = [events[i] for i in occurrences[0]]
        steps: list[dict[str, Any]] = []
        parameter_candidates: list[str] = []
        for offset, event in enumerate(representative):
            values = {
                events[occurrence[offset]].value
                for occurrence in occurrences
                if occurrence[offset] < len(events)
            }
            varying = len(values) > 1
            display_value = "<variable>" if varying else event.value
            if varying and event.target:
                parameter_candidates.append(event.target)
            steps.append(
                {
                    "index": offset,
                    "app": event.app,
                    "action": event.action,
                    "target": event.target,
                    "value": display_value,
                    "raw": event.raw,
                    "variable": varying,
                }
            )
        digest = hashlib.sha1("||".join(key).encode("utf-8")).hexdigest()[:10]
        title = " -> ".join(step["action"] for step in steps)
        recipes.append(
            Recipe(
                id=f"recipe-{digest}",
                title=title,
                app=representative[0].app,
                support=len(occurrences),
                confidence=round(min(0.99, 0.5 + 0.08 * len(occurrences) + 0.04 * len(steps)), 2),
                steps=steps,
                occurrences=occurrences,
                parameter_candidates=sorted(set(parameter_candidates)),
            )
        )
    return recipes[:12]


def next_suggestions(events: list[Event], recipes: list[Recipe]) -> list[dict[str, Any]]:
    if not events:
        return []
    tail = tuple(item.signature for item in events[-2:])
    suggestions: list[dict[str, Any]] = []
    for recipe in recipes:
        signatures = tuple(
            f'{step["app"]}|{step["action"].lower()}|{step["target"].lower()}'
            for step in recipe.steps
        )
        if len(signatures) > 2 and signatures[:2] == tail:
            suggestions.append(
                {
                    "recipe_id": recipe.id,
                    "next_action": recipe.steps[2],
                    "reason": f"该序列在日志中重复 {recipe.support} 次",
                    "confidence": recipe.confidence,
                }
            )
    return suggestions


def render_powermill_macro(recipe: Recipe) -> str:
    lines = [
        "// Generated by UG CAM Copilot MVP",
        "// REVIEW, ADAPT, AND TEST IN A COPY OF THE PROJECT BEFORE RUNNING.",
        f"// Learned recipe: {recipe.title}",
        f"// Support: {recipe.support}; confidence: {recipe.confidence}",
    ]
    if recipe.parameter_candidates:
        lines.append(f'// Parameter candidates: {", ".join(recipe.parameter_candidates)}')
    lines.append("// The lines below preserve recorded commands where possible.")
    for step in recipe.steps:
        raw = step.get("raw", "")
        if step["app"] == "powermill" and raw and not step["variable"]:
            lines.append(raw)
        else:
            value = step["value"]
            lines.append(
                f'// TODO: map {step["action"]} {step["target"]} {value} to a reviewed PowerMill command'
            )
    return "\n".join(lines) + "\n"


def render_nx_journal(recipe: Recipe) -> str:
    lines = [
        "# Generated by UG CAM Copilot MVP",
        "# REVIEW, ADAPT, AND TEST IN NX before running.",
        f"# Learned recipe: {recipe.title}",
        "# This is a reviewable stub; import NXOpen and replace TODO calls.",
        "import NXOpen",
        "",
        "the_session = NXOpen.Session.GetSession()",
        "work_part = the_session.Parts.Work",
        "",
    ]
    for step in recipe.steps:
        lines.append(
            f"# TODO: {step['action']} {step['target']} {step['value']}"
        )
    lines.append("")
    return "\n".join(lines)


SAMPLE_LOG = """[
  {"app":"powermill","action":"select","target":"toolpath","value":"ROUGH_A"},
  {"app":"powermill","action":"set","target":"tolerance","value":"0.05"},
  {"app":"powermill","action":"set","target":"stepdown","value":"2.0"},
  {"app":"powermill","action":"calculate","target":"toolpath","value":"ROUGH_A"},
  {"app":"powermill","action":"select","target":"toolpath","value":"ROUGH_B"},
  {"app":"powermill","action":"set","target":"tolerance","value":"0.05"},
  {"app":"powermill","action":"set","target":"stepdown","value":"2.0"},
  {"app":"powermill","action":"calculate","target":"toolpath","value":"ROUGH_B"},
  {"app":"powermill","action":"select","target":"toolpath","value":"ROUGH_C"},
  {"app":"powermill","action":"set","target":"tolerance","value":"0.04"},
  {"app":"powermill","action":"set","target":"stepdown","value":"1.8"},
  {"app":"powermill","action":"calculate","target":"toolpath","value":"ROUGH_C"}
]"""


def analyze_payload(payload: dict[str, Any]) -> dict[str, Any]:
    source = _clean(payload.get("source") or "powermill").lower()
    fmt = _clean(payload.get("format") or "auto").lower()
    text = _clean(payload.get("text") or SAMPLE_LOG)
    min_support = max(2, min(10, int(payload.get("min_support") or 2)))
    events = parse_events(text, source, fmt)
    recipes = mine_recipes(events, min_support)
    recipe_dicts = [asdict(recipe) for recipe in recipes]
    for recipe in recipe_dicts:
        recipe["powermill_macro"] = render_powermill_macro(Recipe(**{
            key: recipe[key] for key in Recipe.__dataclass_fields__
        }))
        recipe["nx_journal"] = render_nx_journal(Recipe(**{
            key: recipe[key] for key in Recipe.__dataclass_fields__
        }))
    return {
        "source": source,
        "format": fmt,
        "events": [asdict(event) for event in events],
        "recipes": recipe_dicts,
        "suggestions": next_suggestions(events, recipes),
        "summary": {
            "event_count": len(events),
            "recipe_count": len(recipes),
            "top_confidence": max((recipe.confidence for recipe in recipes), default=0),
        },
    }


class DemoHandler(BaseHTTPRequestHandler):
    result = analyze_payload({"text": SAMPLE_LOG})

    def _send(self, body: bytes, content_type: str = "application/json", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/":
            self._send((WEB_DIR / "index.html").read_bytes(), "text/html")
        elif path == "/api/health":
            self._send(b'{"ok":true,"service":"ug-cam-copilot"}')
        elif path == "/api/sample":
            if "source=nx" in parsed_url.query:
                self._send(
                    (SAMPLE_DIR / "nx-session.py").read_bytes(),
                    "text/plain",
                )
            else:
                self._send(SAMPLE_LOG.encode("utf-8"), "application/json")
        elif path == "/api/state":
            self._send(json.dumps(self.result, ensure_ascii=False).encode("utf-8"))
        else:
            self._send(b'{"error":"not found"}', status=404)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/analyze":
            self._send(b'{"error":"not found"}', status=404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            self.__class__.result = analyze_payload(payload)
            self._send(json.dumps(self.result, ensure_ascii=False).encode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(json.dumps({"error": str(exc)}).encode("utf-8"), status=400)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="UG CAM Copilot local MVP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"UG CAM Copilot demo: {url}")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
