from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .connection_monitor import detect_cam_instances
from .parser import parse_log


_PLUGIN_SRC = Path(__file__).resolve().parents[1] / "plugins" / "ug-cam-copilot" / "src"
if str(_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SRC))

try:
    from ugcam_ai.adapters.nx_journal import NxJournalAdapter
except ImportError:  # pragma: no cover - incomplete source checkout
    NxJournalAdapter = None  # type: ignore[assignment,misc]


_CAPTURE_LABELS = frozenset({"unlabeled", "routine", "expert"})
_SOURCE_EXTENSIONS = {
    "nx": frozenset({".py", ".jsonl"}),
    "powermill": frozenset({".mac", ".log", ".jsonl"}),
}
_WINDOWS_QUOTED_PATH = re.compile(
    r"(?P<quote>['\"])(?P<path>(?:[A-Za-z]:[\\/]|\\\\)[^'\"\r\n]+)(?P=quote)"
)
_UNIX_QUOTED_PATH = re.compile(r"(?P<quote>['\"])(?P<path>/[^'\"\r\n]+)(?P=quote)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CaptureConfig:
    schema_version: int = 1
    consent: bool = False
    consent_source: str = "none"
    enabled: bool = True
    auto_connect: bool = True
    include_existing: bool = True
    capture_logs: bool = True
    detect_instances: bool = True
    audit_execution: bool = True
    operator_label: str = "unlabeled"
    poll_interval_seconds: float = 1.0
    salt: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CaptureConfig":
        label = str(value.get("operator_label", "unlabeled"))
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            consent=bool(value.get("consent", False)),
            consent_source=str(value.get("consent_source", "none"))
            if str(value.get("consent_source", "none"))
            in {"none", "auto_install", "manual", "revoked"}
            else "none",
            enabled=bool(value.get("enabled", True)),
            auto_connect=bool(value.get("auto_connect", True)),
            include_existing=bool(value.get("include_existing", True)),
            capture_logs=bool(value.get("capture_logs", True)),
            detect_instances=bool(value.get("detect_instances", True)),
            audit_execution=bool(value.get("audit_execution", True)),
            operator_label=label if label in _CAPTURE_LABELS else "unlabeled",
            poll_interval_seconds=max(
                0.25, min(float(value.get("poll_interval_seconds", 1.0)), 30.0)
            ),
            salt=str(value.get("salt") or secrets.token_hex(16)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PrivacyRedactor:
    """Stable local pseudonymization for paths and source identities."""

    def __init__(self, salt: str) -> None:
        self.salt = salt

    def token(self, kind: str, value: str, suffix: str = "") -> str:
        digest = hashlib.sha256(f"{self.salt}:{value}".encode("utf-8")).hexdigest()[:10]
        return f"<{kind.upper()}_{digest}>{suffix.lower()}"

    def source_id(self, product: str, path: Path) -> str:
        resolved = str(path.resolve()).casefold()
        return f"{product}-{self.token('source', resolved)[8:-1]}"

    def source_alias(self, product: str, path: Path) -> str:
        return f"{product}:{self.token('source', str(path.resolve()), path.suffix)}"

    def session_id(self, product: str, source_id: str, generation: int, value: str) -> str:
        token = self.token("session", f"{source_id}:{generation}:{value}")[9:-1]
        return f"{product}:{token}"

    def text(self, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            path = match.group("path")
            suffix = Path(path.replace("\\", "/")).suffix
            return f"{match.group('quote')}{self.token('path', path, suffix)}{match.group('quote')}"

        redacted = _WINDOWS_QUOTED_PATH.sub(replace, value)
        return _UNIX_QUOTED_PATH.sub(replace, redacted)

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return [self.value(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): self.value(item) for key, item in value.items()}
        return value


class CaptureStore:
    """Append-only local event store with source checkpoints and filter indexes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA temp_store=MEMORY")
        self._closed = False
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS captured_events (
                capture_order INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                captured_at TEXT NOT NULL,
                product TEXT NOT NULL,
                mode TEXT NOT NULL,
                category TEXT NOT NULL,
                action TEXT NOT NULL,
                event_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_capture_filters
                ON captured_events(product, mode, category, action, capture_order);
            CREATE TABLE IF NOT EXISTS capture_sources (
                source_id TEXT PRIMARY KEY,
                product TEXT NOT NULL,
                alias TEXT NOT NULL,
                generation INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                event_count INTEGER NOT NULL,
                last_scanned TEXT NOT NULL,
                last_error TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        if self._closed:
            return
        self.connection.close()
        self._closed = True

    def source_state(self, source_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT generation, size_bytes, mtime_ns, event_count, last_error
            FROM capture_sources
            WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "generation": row[0],
            "size_bytes": row[1],
            "mtime_ns": row[2],
            "event_count": row[3],
            "last_error": row[4],
        }

    def save_source(
        self,
        *,
        source_id: str,
        product: str,
        alias: str,
        generation: int,
        size_bytes: int,
        mtime_ns: int,
        event_count: int,
        error: str = "",
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO capture_sources(
                    source_id, product, alias, generation, size_bytes, mtime_ns,
                    event_count, last_scanned, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    product=excluded.product,
                    alias=excluded.alias,
                    generation=excluded.generation,
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    event_count=excluded.event_count,
                    last_scanned=excluded.last_scanned,
                    last_error=excluded.last_error
                """,
                (
                    source_id,
                    product,
                    alias,
                    generation,
                    size_bytes,
                    mtime_ns,
                    event_count,
                    _utc_now(),
                    error,
                ),
            )

    def insert_events(self, events: Iterable[dict[str, Any]]) -> int:
        rows = []
        for event in events:
            stored = dict(event)
            event_id = str(stored.pop("_event_id"))
            params = stored.get("params") if isinstance(stored.get("params"), dict) else {}
            rows.append(
                (
                    event_id,
                    str(params.get("capture", {}).get("captured_at") or _utc_now()),
                    str(stored.get("product", "unknown")),
                    str(stored.get("mode") or params.get("mode") or "manual"),
                    str(stored.get("category", "other")),
                    str(stored.get("action", "cam.unknown")),
                    json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                )
            )
        if not rows:
            return 0
        before = self.connection.total_changes
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO captured_events(
                    event_id, captured_at, product, mode, category, action, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return self.connection.total_changes - before

    def recent_events(self, *, limit: int = 500, product: str | None = None) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 10_000))
        if product:
            rows = self.connection.execute(
                """
                SELECT event_json FROM captured_events
                WHERE product = ?
                ORDER BY capture_order DESC LIMIT ?
                """,
                (product, bounded),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT event_json FROM captured_events
                ORDER BY capture_order DESC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def all_events(self) -> Iterable[dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT event_json FROM captured_events ORDER BY capture_order"
        )
        for row in cursor:
            yield json.loads(row[0])

    def counts(self) -> dict[str, Any]:
        total = int(self.connection.execute("SELECT COUNT(*) FROM captured_events").fetchone()[0])
        products = {
            str(row[0]): int(row[1])
            for row in self.connection.execute(
                "SELECT product, COUNT(*) FROM captured_events GROUP BY product"
            )
        }
        return {"total": total, "products": products}

    def sources(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT source_id, product, alias, generation, event_count, last_scanned, last_error
            FROM capture_sources
            ORDER BY product, alias
            """
        )
        return [
            {
                "source_id": row[0],
                "product": row[1],
                "alias": row[2],
                "generation": row[3],
                "event_count": row[4],
                "last_scanned": row[5],
                "status": "error" if row[6] else "monitoring",
                "error": row[6],
            }
            for row in rows
        ]

    def clear(self) -> int:
        count = int(self.connection.execute("SELECT COUNT(*) FROM captured_events").fetchone()[0])
        with self.connection:
            self.connection.execute("DELETE FROM captured_events")
            self.connection.execute("DELETE FROM capture_sources")
        return count


class CaptureService:
    """Consent-gated background collector for local CAM log and journal files."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        source_paths: Mapping[str, Iterable[str | Path]] | None = None,
        start_background: bool = True,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "config.json"
        self.config = self._load_config()
        self.redactor = PrivacyRedactor(self.config.salt)
        self.store = CaptureStore(self.data_dir / "capture.db")
        self.explicit_sources = {
            product: [Path(path) for path in paths]
            for product, paths in (source_paths or {}).items()
            if product in _SOURCE_EXTENSIONS
        }
        self.inboxes = {
            product: self.data_dir / "inbox" / product for product in _SOURCE_EXTENSIONS
        }
        for inbox in self.inboxes.values():
            inbox.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_scan: str | None = None
        self._last_runtime_error = ""
        self._processes = {"nx": False, "powermill": False}
        self._instances: dict[str, list[dict[str, Any]]] = {
            "nx": [],
            "powermill": [],
        }
        self._last_process_check = 0.0
        self._last_discovery_check = 0.0
        self._known_sources: list[tuple[str, Path]] = []
        self._thread: threading.Thread | None = None
        self._closed = False
        if start_background:
            self._thread = threading.Thread(
                target=self._run,
                name="cam-capture-service",
                daemon=True,
            )
            self._thread.start()

    @property
    def active(self) -> bool:
        return self.config.consent and self.config.enabled

    def _load_config(self) -> CaptureConfig:
        if self.config_path.is_file():
            try:
                value = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    config = CaptureConfig.from_dict(value)
                    self._save_config(config)
                    return config
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        config = CaptureConfig(salt=secrets.token_hex(16))
        self._save_config(config)
        return config

    def _save_config(self, config: CaptureConfig | None = None) -> None:
        value = config or self.config
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.config_path)

    def configure(
        self,
        *,
        consent: bool | None = None,
        consent_source: str | None = None,
        enabled: bool | None = None,
        capture_logs: bool | None = None,
        detect_instances: bool | None = None,
        audit_execution: bool | None = None,
        operator_label: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if consent is not None:
                self.config.consent = bool(consent)
                if consent:
                    source = consent_source or "manual"
                    if source not in {"auto_install", "manual"}:
                        source = "manual"
                    self.config.consent_source = source
                else:
                    self.config.consent_source = "revoked"
                if not self.config.consent:
                    self._instances = {"nx": [], "powermill": []}
                    self._processes = {"nx": False, "powermill": False}
                    self._last_process_check = 0.0
            if enabled is not None:
                if enabled and not self.config.consent:
                    raise ValueError("Local recording requires explicit consent first.")
                self.config.enabled = bool(enabled)
            if capture_logs is not None:
                self.config.capture_logs = bool(capture_logs)
            if detect_instances is not None:
                self.config.detect_instances = bool(detect_instances)
                if not self.config.detect_instances:
                    self._instances = {"nx": [], "powermill": []}
                    self._processes = {"nx": False, "powermill": False}
            if audit_execution is not None:
                self.config.audit_execution = bool(audit_execution)
            if operator_label is not None:
                if operator_label not in _CAPTURE_LABELS:
                    raise ValueError("Operator label must be unlabeled, routine, or expert.")
                self.config.operator_label = operator_label
            self._save_config()
            self._wake.set()
            return self.status()

    def scan_once(self) -> int:
        with self._lock:
            if not self.active:
                return 0
            inserted = 0
            now = time.monotonic()
            if self.config.detect_instances and now - self._last_process_check >= 2:
                self._refresh_connections()
            elif not self.config.detect_instances:
                self._instances = {"nx": [], "powermill": []}
                self._processes = {"nx": False, "powermill": False}
            if (
                self.config.capture_logs
                and (now - self._last_discovery_check >= 5 or not self._known_sources)
            ):
                self._known_sources = self._discover_sources()
                self._last_discovery_check = now
            if not self.config.capture_logs:
                self._known_sources = []
            for product, path in self._known_sources:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                source_id = self.redactor.source_id(product, path)
                alias = self.redactor.source_alias(product, path)
                previous = self.store.source_state(source_id)
                if (
                    previous
                    and previous["size_bytes"] == stat.st_size
                    and previous["mtime_ns"] == stat.st_mtime_ns
                ):
                    continue
                generation = int(previous["generation"]) if previous else 0
                if previous and stat.st_size < int(previous["size_bytes"]):
                    generation += 1
                if stat.st_size > 64 * 1024 * 1024:
                    self.store.save_source(
                        source_id=source_id,
                        product=product,
                        alias=alias,
                        generation=generation,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        event_count=int(previous["event_count"]) if previous else 0,
                        error="Source exceeds the 64 MiB capture limit.",
                    )
                    continue
                try:
                    events = self._parse_source(
                        product=product,
                        path=path,
                        source_id=source_id,
                        source_alias=alias,
                        generation=generation,
                    )
                    added = self.store.insert_events(events)
                    inserted += added
                    prior_count = int(previous["event_count"]) if previous else 0
                    self.store.save_source(
                        source_id=source_id,
                        product=product,
                        alias=alias,
                        generation=generation,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        event_count=prior_count + added,
                    )
                except (OSError, UnicodeDecodeError, ValueError, SyntaxError, json.JSONDecodeError) as error:
                    self.store.save_source(
                        source_id=source_id,
                        product=product,
                        alias=alias,
                        generation=generation,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        event_count=int(previous["event_count"]) if previous else 0,
                        error=str(error)[:500],
                    )
            self._last_scan = _utc_now()
            self._last_runtime_error = ""
            return inserted

    def _parse_source(
        self,
        *,
        product: str,
        path: Path,
        source_id: str,
        source_alias: str,
        generation: int,
    ) -> list[dict[str, Any]]:
        text = path.read_text(encoding="utf-8-sig", errors="strict")
        if path.suffix.lower() == ".jsonl":
            structured = self._parse_activity_jsonl(text)
            if structured is not None:
                return [
                    self._normalize_event(
                        event,
                        source_id=source_id,
                        source_alias=source_alias,
                        generation=generation,
                    )
                    for event in structured
                ]
        if product == "powermill":
            parsed = parse_log(text)
            raw_events = parsed.to_activity_events()
        else:
            if NxJournalAdapter is None:
                raise ValueError("NX Journal adapter is unavailable.")
            raw_events = [
                event.to_dict()
                for event in NxJournalAdapter().parse_source(
                    text,
                    source_file=source_alias,
                    session_name=source_id,
                )
            ]
        return [
            self._normalize_event(
                event,
                source_id=source_id,
                source_alias=source_alias,
                generation=generation,
            )
            for event in raw_events
        ]

    @staticmethod
    def _parse_activity_jsonl(text: str) -> list[dict[str, Any]] | None:
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {line_number} must be an object.")
            required = {"session_id", "seq", "product", "action"}
            if not required.issubset(value):
                return None
            event = dict(value)
            event.setdefault("source_line", line_number)
            events.append(event)
        return events or None

    def _normalize_event(
        self,
        event: Mapping[str, Any],
        *,
        source_id: str,
        source_alias: str,
        generation: int,
    ) -> dict[str, Any]:
        product = str(event.get("product", "unknown")).lower()
        raw_session = str(event.get("session_id", "session"))
        params = dict(event.get("params", {})) if isinstance(event.get("params"), Mapping) else {}
        mode = str(event.get("mode") or params.get("mode") or "manual")
        params["mode"] = mode
        params["capture"] = {
            "captured_at": _utc_now(),
            "source_id": source_id,
            "source_generation": generation,
            "operator_label": self.config.operator_label,
            "privacy": "redacted-local-v1",
        }
        normalized = {
            "schema_version": int(event.get("schema_version", 1)),
            "session_id": self.redactor.session_id(
                product, source_id, generation, raw_session
            ),
            "seq": int(event.get("seq", 0)),
            "product": product,
            "action": str(event.get("action", "cam.unknown")),
            "category": str(event.get("category", "other")),
            "mode": mode,
            "params": self.redactor.value(params),
            "source_file": source_alias,
            "source_line": int(event.get("source_line", 0) or 0),
            "duration_ms": event.get("duration_ms"),
            "timestamp": event.get("timestamp"),
        }
        identity = json.dumps(
            [
                source_id,
                generation,
                raw_session,
                normalized["seq"],
                normalized["source_line"],
                normalized["action"],
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        normalized["_event_id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return normalized

    def _discover_sources(self) -> list[tuple[str, Path]]:
        configured: dict[str, list[Path]] = {
            product: list(paths) for product, paths in self.explicit_sources.items()
        }
        configured.setdefault("nx", []).append(self.inboxes["nx"])
        configured.setdefault("powermill", []).append(self.inboxes["powermill"])
        configured["nx"].extend(self._environment_paths("CAM_NX_LOG_PATHS"))
        configured["powermill"].extend(
            self._environment_paths("CAM_POWERMILL_LOG_PATHS")
        )
        if self.config.auto_connect:
            configured["nx"].extend(self._environment_paths("UGII_USER_DIR"))
            configured["nx"].extend(self._environment_paths("UGII_TMP_DIR"))
            configured["powermill"].extend(
                self._environment_paths("POWERMILL_USER_DIR")
            )
            configured["powermill"].extend(self._environment_paths("PMILL_USER_DIR"))
            local_app_data = os.environ.get("LOCALAPPDATA")
            roaming_app_data = os.environ.get("APPDATA")
            if local_app_data:
                configured["powermill"].append(
                    Path(local_app_data) / "Autodesk" / "PowerMill"
                )
            if roaming_app_data:
                configured["powermill"].append(
                    Path(roaming_app_data) / "Autodesk" / "PowerMill"
                )

        found: dict[tuple[str, str], Path] = {}
        for product, roots in configured.items():
            extensions = _SOURCE_EXTENSIONS[product]
            for root in roots:
                if root.is_file() and root.suffix.lower() in extensions:
                    found[(product, str(root.resolve()).casefold())] = root
                    continue
                if not root.is_dir():
                    continue
                try:
                    candidates = root.rglob("*")
                    for candidate in candidates:
                        try:
                            relative = candidate.relative_to(root)
                        except ValueError:
                            continue
                        if len(relative.parts) > 4:
                            continue
                        if (
                            candidate.is_file()
                            and not candidate.is_symlink()
                            and candidate.suffix.lower() in extensions
                        ):
                            found[(product, str(candidate.resolve()).casefold())] = candidate
                            if len(found) >= 512:
                                break
                except OSError:
                    continue
        return sorted(
            ((product, path) for (product, _), path in found.items()),
            key=lambda item: (item[0], str(item[1]).casefold()),
        )

    @staticmethod
    def _environment_paths(name: str) -> list[Path]:
        value = os.environ.get(name, "")
        return [Path(item.strip()) for item in value.split(os.pathsep) if item.strip()]

    def _refresh_connections(self, *, force: bool = False) -> None:
        if not self.config.detect_instances:
            self._instances = {"nx": [], "powermill": []}
            self._processes = {"nx": False, "powermill": False}
            self._last_process_check = time.monotonic()
            return
        self._instances = detect_cam_instances(force=force)
        self._processes = {
            product: bool(instances)
            for product, instances in self._instances.items()
        }
        self._last_process_check = time.monotonic()

    def status(self, *, refresh_connections: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.config.consent and (
                refresh_connections
                or time.monotonic() - self._last_process_check >= 2
            ):
                self._refresh_connections(force=refresh_connections)
            state = (
                "awaiting_consent"
                if not self.config.consent
                else "recording" if self.config.enabled else "paused"
            )
            return {
                "state": state,
                "consent": self.config.consent,
                "consent_source": self.config.consent_source,
                "auto_authorized": self.config.consent_source == "auto_install",
                "consent_reversible": True,
                "enabled": self.config.enabled,
                "auto_connect": self.config.auto_connect,
                "categories": {
                    "logs": self.config.capture_logs,
                    "instances": self.config.detect_instances,
                    "execution_audit": self.config.audit_execution,
                },
                "local_only": True,
                "redaction": "redacted-local-v1",
                "operator_label": self.config.operator_label,
                "counts": self.store.counts(),
                "sources": self.store.sources(),
                "processes": dict(self._processes),
                "instances": {
                    product: [dict(instance) for instance in instances]
                    for product, instances in self._instances.items()
                },
                "last_scan": self._last_scan,
                "last_error": self._last_runtime_error,
            }

    def recent_events(self, *, limit: int = 500, product: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return self.store.recent_events(limit=limit, product=product)

    def record_execution(
        self,
        *,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> int:
        with self._lock:
            if not self.config.consent or not self.config.audit_execution:
                return 0
            product = str(request.get("product", "unknown")).lower()
            action = str(request.get("action", "cam.execution.unknown"))
            command = str(request.get("command", ""))
            recipe_hash = str(request.get("recipe_hash", "unreviewed"))
            captured_at = _utc_now()
            sequence = time.time_ns()
            event = {
                "schema_version": 1,
                "session_id": f"{product}:execution:{recipe_hash[:12] or 'unreviewed'}",
                "seq": sequence,
                "product": product,
                "action": action,
                "category": "execution",
                "mode": "automation",
                "params": {
                    "command": self.redactor.text(command),
                    "risk": str(request.get("risk", "blocked")),
                    "mode": "automation",
                    "target_instance_id": str(
                        request.get("target_instance_id", "")
                    ),
                    "execution": self.redactor.value(dict(result)),
                    "capture": {
                        "captured_at": captured_at,
                        "source_id": "execution-gateway",
                        "source_generation": 0,
                        "operator_label": self.config.operator_label,
                        "privacy": "redacted-local-v1",
                    },
                },
                "source_file": "local:execution-gateway",
                "source_line": 0,
                "duration_ms": result.get("duration_ms"),
                "timestamp": captured_at,
                "_event_id": hashlib.sha256(
                    f"{captured_at}:{product}:{action}:{sequence}".encode("utf-8")
                ).hexdigest(),
            }
            return self.store.insert_events([event])

    def export_jsonl(self) -> bytes:
        with self._lock:
            payload = "".join(
                json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                for event in self.store.all_events()
            )
        return payload.encode("utf-8")

    def clear(self) -> int:
        with self._lock:
            return self.store.clear()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            if self._closed:
                return
            self.store.close()
            self._closed = True

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.active:
                try:
                    self.scan_once()
                except Exception as error:  # pragma: no cover - defensive service boundary
                    self._last_runtime_error = str(error)[:500]
            self._wake.wait(self.config.poll_interval_seconds)
            self._wake.clear()
