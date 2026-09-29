from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters.cimatron_journal import CimatronJournalAdapter
from .connection_monitor import detect_cam_instances
from .models import (
    EVENT_MODES,
    SOURCE_MODES,
    ActivityEvent,
    EventPage,
    EventQuery,
    normalize_event_mode,
)
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
    "cimatron": frozenset({".py", ".cs", ".jsonl"}),
}
_CATEGORY_CONFIG_FIELDS = {
    "logs": "capture_logs",
    "instances": "detect_instances",
    "execution_audit": "audit_execution",
}
_RECORDER_STATES = frozenset(
    {"recording", "paused", "source_interrupted", "catching_up", "error"}
)
_MAX_INCREMENTAL_BYTES = 8 * 1024 * 1024
_MAX_SOURCE_LINE_BYTES = 64 * 1024 * 1024
_RECIPE_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEARCH_TERMS = re.compile(r"\w+", re.UNICODE)
_WINDOWS_QUOTED_PATH = re.compile(
    r"(?P<quote>['\"])(?P<path>(?:[A-Za-z]:[\\/]|\\\\)[^'\"\r\n]+)(?P=quote)"
)
_UNIX_QUOTED_PATH = re.compile(r"(?P<quote>['\"])(?P<path>/[^'\"\r\n]+)(?P=quote)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_micros(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.astimezone(timezone.utc).timestamp() * 1_000_000)


def _file_identity(stat: os.stat_result) -> str:
    return f"{stat.st_dev}:{stat.st_ino}"


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
        consent_source = str(value.get("consent_source", "none"))
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            consent=bool(value.get("consent", False)),
            consent_source=(
                consent_source
                if consent_source in {"none", "auto_install", "manual", "revoked"}
                else "none"
            ),
            enabled=bool(value.get("enabled", True)),
            auto_connect=bool(value.get("auto_connect", True)),
            include_existing=bool(value.get("include_existing", True)),
            capture_logs=bool(value.get("capture_logs", True)),
            detect_instances=bool(value.get("detect_instances", True)),
            audit_execution=bool(value.get("audit_execution", True)),
            operator_label=label if label in _CAPTURE_LABELS else "unlabeled",
            poll_interval_seconds=max(
                0.25,
                min(float(value.get("poll_interval_seconds", 1.0)), 30.0),
            ),
            salt=str(value.get("salt") or secrets.token_hex(16)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PrivacyRedactor:
    """Stable local pseudonymization for paths and source identities."""

    def __init__(self, salt: str) -> None:
        self.salt = salt
        self._session_ids: dict[tuple[str, str, int, str], str] = {}

    def token(self, kind: str, value: str, suffix: str = "") -> str:
        digest = hashlib.sha256(f"{self.salt}:{value}".encode("utf-8")).hexdigest()[:10]
        return f"<{kind.upper()}_{digest}>{suffix.lower()}"

    def source_id(self, product: str, path: Path) -> str:
        resolved = str(path.resolve()).casefold()
        return f"{product}-{self.token('source', resolved)[8:-1]}"

    def source_alias(self, product: str, path: Path) -> str:
        return f"{product}:{self.token('source', str(path.resolve()), path.suffix)}"

    def session_id(self, product: str, source_id: str, generation: int, value: str) -> str:
        key = (product, source_id, generation, value)
        cached = self._session_ids.get(key)
        if cached is not None:
            return cached
        token = self.token("session", f"{source_id}:{generation}:{value}")[9:-1]
        session_id = f"{product}:{token}"
        self._session_ids[key] = session_id
        return session_id

    def text(self, value: str) -> str:
        if "'" not in value and '"' not in value:
            return value

        def replace(match: re.Match[str]) -> str:
            path = match.group("path")
            suffix = Path(path.replace("\\", "/")).suffix
            return (
                f"{match.group('quote')}"
                f"{self.token('path', path, suffix)}"
                f"{match.group('quote')}"
            )

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
    """Append-only SQLite store with durable source cursors and query indexes."""

    _EVENT_COLUMNS = {
        "source_id": "TEXT NOT NULL DEFAULT ''",
        "source_mode": "TEXT NOT NULL DEFAULT 'manual'",
        "view_level": "TEXT",
        "instance_id": "TEXT",
        "project_id": "TEXT",
        "session_id": "TEXT NOT NULL DEFAULT ''",
        "event_timestamp": "TEXT",
        "event_time_us": "INTEGER",
        "search_text": "TEXT NOT NULL DEFAULT ''",
    }
    _SOURCE_COLUMNS = {
        "cursor_bytes": "INTEGER NOT NULL DEFAULT 0",
        "line_number": "INTEGER NOT NULL DEFAULT 0",
        "file_identity": "TEXT NOT NULL DEFAULT ''",
        "interrupted": "INTEGER NOT NULL DEFAULT 0",
    }

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=30,
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA temp_store=MEMORY")
        self.connection.execute("PRAGMA cache_size=-65536")
        self.connection.execute("PRAGMA mmap_size=268435456")
        self.connection.execute("PRAGMA wal_autocheckpoint=32768")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._closed = False
        self._fts_available = False
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS captured_events (
                capture_order INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                captured_at TEXT NOT NULL,
                source_id TEXT NOT NULL DEFAULT '',
                product TEXT NOT NULL,
                mode TEXT NOT NULL,
                source_mode TEXT NOT NULL DEFAULT 'manual',
                view_level TEXT,
                instance_id TEXT,
                project_id TEXT,
                session_id TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                action TEXT NOT NULL,
                event_timestamp TEXT,
                event_time_us INTEGER,
                search_text TEXT NOT NULL DEFAULT '',
                event_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_sources (
                source_id TEXT PRIMARY KEY,
                product TEXT NOT NULL,
                alias TEXT NOT NULL,
                generation INTEGER NOT NULL,
                cursor_bytes INTEGER NOT NULL DEFAULT 0,
                line_number INTEGER NOT NULL DEFAULT 0,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                file_identity TEXT NOT NULL DEFAULT '',
                event_count INTEGER NOT NULL,
                last_scanned TEXT NOT NULL,
                last_error TEXT NOT NULL,
                interrupted INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        event_schema_changed = self._ensure_columns(
            "captured_events",
            self._EVENT_COLUMNS,
        )
        self._ensure_columns("capture_sources", self._SOURCE_COLUMNS)
        if event_schema_changed:
            self._backfill_event_indexes()
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_capture_product_order
                ON captured_events(product, capture_order);
            CREATE INDEX IF NOT EXISTS idx_capture_source_mode_order
                ON captured_events(source_mode, capture_order);
            CREATE INDEX IF NOT EXISTS idx_capture_view_level_order
                ON captured_events(view_level, capture_order);
            CREATE INDEX IF NOT EXISTS idx_capture_instance_order
                ON captured_events(instance_id, capture_order);
            CREATE INDEX IF NOT EXISTS idx_capture_project_order
                ON captured_events(project_id, capture_order);
            CREATE INDEX IF NOT EXISTS idx_capture_session_order
                ON captured_events(session_id, capture_order);
            CREATE INDEX IF NOT EXISTS idx_capture_action_order
                ON captured_events(action, capture_order);
            CREATE INDEX IF NOT EXISTS idx_capture_category_order
                ON captured_events(category, capture_order);
            CREATE INDEX IF NOT EXISTS idx_capture_time_order
                ON captured_events(event_time_us, capture_order);
            """
        )
        self._create_fts(rebuild=event_schema_changed)
        self.connection.commit()

    def _ensure_columns(self, table: str, columns: Mapping[str, str]) -> bool:
        existing = {
            str(row[1]) for row in self.connection.execute(f"PRAGMA table_info({table})")
        }
        changed = False
        for name, definition in columns.items():
            if name not in existing:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )
                changed = True
        return changed

    def _backfill_event_indexes(self) -> None:
        rows = self.connection.execute(
            "SELECT capture_order, event_json FROM captured_events"
        ).fetchall()
        updates = []
        for capture_order, payload in rows:
            try:
                event = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                continue
            indexes = self._event_indexes(event)
            params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
            source_id = str(
                params.get("capture", {}).get("source_id", "")
                if isinstance(params.get("capture"), Mapping)
                else ""
            )
            updates.append((*indexes, source_id, capture_order))
        if updates:
            self.connection.executemany(
                """
                UPDATE captured_events
                SET source_mode=?, view_level=?, instance_id=?, project_id=?,
                    session_id=?, event_timestamp=?, event_time_us=?,
                    search_text=?, source_id=?
                WHERE capture_order=?
                """,
                updates,
            )

    def _create_fts(self, *, rebuild: bool) -> None:
        fts_exists = bool(
            self.connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'captured_events_fts'
                """
            ).fetchone()
        )
        try:
            self.connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS captured_events_fts
                USING fts5(
                    search_text,
                    content='captured_events',
                    content_rowid='capture_order'
                );
                CREATE TRIGGER IF NOT EXISTS captured_events_ai
                AFTER INSERT ON captured_events BEGIN
                    INSERT INTO captured_events_fts(rowid, search_text)
                    VALUES (new.capture_order, new.search_text);
                END;
                CREATE TRIGGER IF NOT EXISTS captured_events_ad
                AFTER DELETE ON captured_events BEGIN
                    INSERT INTO captured_events_fts(
                        captured_events_fts, rowid, search_text
                    ) VALUES ('delete', old.capture_order, old.search_text);
                END;
                CREATE TRIGGER IF NOT EXISTS captured_events_au
                AFTER UPDATE OF search_text ON captured_events BEGIN
                    INSERT INTO captured_events_fts(
                        captured_events_fts, rowid, search_text
                    ) VALUES ('delete', old.capture_order, old.search_text);
                    INSERT INTO captured_events_fts(rowid, search_text)
                    VALUES (new.capture_order, new.search_text);
                END;
                """
            )
            if rebuild or not fts_exists:
                self.connection.execute(
                    """
                    INSERT INTO captured_events_fts(captured_events_fts)
                    VALUES ('rebuild')
                    """
                )
            self._fts_available = True
        except sqlite3.OperationalError:
            self._fts_available = False

    @staticmethod
    def _event_indexes(
        event: Mapping[str, Any],
        *,
        search_text: str | None = None,
    ) -> tuple[str, str | None, str | None, str | None, str, str | None, int | None, str]:
        params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
        mode = str(event.get("mode") or params.get("mode") or "manual")
        source_mode = str(event.get("source_mode") or mode)
        timestamp = (
            str(event["timestamp"]) if event.get("timestamp") is not None else None
        )
        indexed_text = search_text or json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            source_mode,
            str(event["view_level"]) if event.get("view_level") is not None else None,
            str(event["instance_id"]) if event.get("instance_id") is not None else None,
            str(event["project_id"]) if event.get("project_id") is not None else None,
            str(event.get("session_id", "")),
            timestamp,
            _timestamp_micros(timestamp),
            indexed_text,
        )

    def close(self) -> None:
        if self._closed:
            return
        self.connection.close()
        self._closed = True

    def source_state(self, source_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT generation, cursor_bytes, line_number, size_bytes, mtime_ns,
                   file_identity, event_count, last_error, interrupted
            FROM capture_sources
            WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "generation": int(row[0]),
            "cursor_bytes": int(row[1]),
            "line_number": int(row[2]),
            "size_bytes": int(row[3]),
            "mtime_ns": int(row[4]),
            "file_identity": str(row[5]),
            "event_count": int(row[6]),
            "last_error": str(row[7]),
            "interrupted": bool(row[8]),
        }

    def commit_source_batch(
        self,
        events: Iterable[dict[str, Any]],
        *,
        source_id: str,
        product: str,
        alias: str,
        generation: int,
        cursor_bytes: int,
        line_number: int,
        size_bytes: int,
        mtime_ns: int,
        file_identity: str,
        error: str = "",
        interrupted: bool = False,
    ) -> int:
        rows = [self._event_row(event, source_id=source_id) for event in events]
        with self.connection:
            inserted = 0
            if rows:
                cursor = self.connection.executemany(
                    """
                    INSERT OR IGNORE INTO captured_events(
                        event_id, captured_at, source_id, product, mode,
                        source_mode, view_level, instance_id, project_id,
                        session_id, category, action, event_timestamp,
                        event_time_us, search_text, event_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                inserted = max(int(cursor.rowcount), 0)
            event_count = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM captured_events WHERE source_id = ?",
                    (source_id,),
                ).fetchone()[0]
            )
            self.connection.execute(
                """
                INSERT INTO capture_sources(
                    source_id, product, alias, generation, cursor_bytes,
                    line_number, size_bytes, mtime_ns, file_identity,
                    event_count, last_scanned, last_error, interrupted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    product=excluded.product,
                    alias=excluded.alias,
                    generation=excluded.generation,
                    cursor_bytes=excluded.cursor_bytes,
                    line_number=excluded.line_number,
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    file_identity=excluded.file_identity,
                    event_count=excluded.event_count,
                    last_scanned=excluded.last_scanned,
                    last_error=excluded.last_error,
                    interrupted=excluded.interrupted
                WHERE excluded.generation > capture_sources.generation
                   OR (
                        excluded.generation = capture_sources.generation
                        AND excluded.cursor_bytes >= capture_sources.cursor_bytes
                   )
                """,
                (
                    source_id,
                    product,
                    alias,
                    generation,
                    cursor_bytes,
                    line_number,
                    size_bytes,
                    mtime_ns,
                    file_identity,
                    event_count,
                    _utc_now(),
                    error,
                    int(interrupted),
                ),
            )
        return inserted

    def mark_source_interrupted(
        self,
        *,
        source_id: str,
        product: str,
        alias: str,
        error: str,
    ) -> None:
        previous = self.source_state(source_id)
        self.commit_source_batch(
            [],
            source_id=source_id,
            product=product,
            alias=alias,
            generation=int(previous["generation"]) if previous else 0,
            cursor_bytes=int(previous["cursor_bytes"]) if previous else 0,
            line_number=int(previous["line_number"]) if previous else 0,
            size_bytes=int(previous["size_bytes"]) if previous else 0,
            mtime_ns=int(previous["mtime_ns"]) if previous else 0,
            file_identity=str(previous["file_identity"]) if previous else "",
            error=error[:500],
            interrupted=True,
        )

    def insert_events(self, events: Iterable[dict[str, Any]], *, source_id: str) -> int:
        rows = [self._event_row(event, source_id=source_id) for event in events]
        if not rows:
            return 0
        with self.connection:
            cursor = self.connection.executemany(
                """
                INSERT OR IGNORE INTO captured_events(
                    event_id, captured_at, source_id, product, mode,
                    source_mode, view_level, instance_id, project_id,
                    session_id, category, action, event_timestamp,
                    event_time_us, search_text, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return max(int(cursor.rowcount), 0)

    def _event_row(self, event: Mapping[str, Any], *, source_id: str) -> tuple[Any, ...]:
        stored = dict(event)
        event_id = str(stored.pop("_event_id"))
        params = stored.get("params") if isinstance(stored.get("params"), Mapping) else {}
        capture = params.get("capture") if isinstance(params.get("capture"), Mapping) else {}
        payload = json.dumps(stored, ensure_ascii=False, separators=(",", ":"))
        indexes = self._event_indexes(stored, search_text=payload)
        return (
            event_id,
            str(capture.get("captured_at") or _utc_now()),
            source_id,
            str(stored.get("product", "unknown")),
            str(stored.get("mode") or params.get("mode") or "manual"),
            *indexes[:5],
            str(stored.get("category", "other")),
            str(stored.get("action", "cam.unknown")),
            indexes[5],
            indexes[6],
            indexes[7],
            payload,
        )

    def query_events(
        self,
        query: EventQuery,
        *,
        after_order: int | None,
    ) -> tuple[list[tuple[int, dict[str, Any]]], bool]:
        where: list[str] = []
        params: list[Any] = []

        def add_values(column: str, values: tuple[str, ...]) -> None:
            if not values:
                return
            placeholders = ",".join("?" for _ in values)
            where.append(f"ce.{column} IN ({placeholders})")
            params.extend(values)

        add_values("source_mode", query.source_modes)
        add_values("view_level", query.view_levels)
        add_values("product", query.products)
        add_values("instance_id", query.instance_ids)
        add_values("project_id", query.project_ids)
        add_values("session_id", query.session_ids)
        add_values("action", query.actions)
        add_values("category", query.categories)
        if query.from_time:
            where.append("ce.event_time_us >= ?")
            params.append(_timestamp_micros(query.from_time))
        if query.to_time:
            where.append("ce.event_time_us <= ?")
            params.append(_timestamp_micros(query.to_time))
        from_sql = "captured_events AS ce"
        if query.text.strip():
            terms = _SEARCH_TERMS.findall(query.text.casefold())
            if self._fts_available and terms:
                from_sql += (
                    " JOIN captured_events_fts"
                    " ON captured_events_fts.rowid = ce.capture_order"
                )
                where.append("captured_events_fts MATCH ?")
                params.append(" AND ".join(f'"{term}"' for term in terms))
            else:
                where.append("LOWER(ce.search_text) LIKE ?")
                params.append(f"%{query.text.casefold()}%")
        if after_order is not None:
            operator = ">" if query.sort == "asc" else "<"
            where.append(f"ce.capture_order {operator} ?")
            params.append(after_order)
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        direction = "ASC" if query.sort == "asc" else "DESC"
        params.append(query.limit + 1)
        rows = self.connection.execute(
            f"""
            SELECT ce.capture_order, ce.event_json
            FROM {from_sql}
            {where_sql}
            ORDER BY ce.capture_order {direction}
            LIMIT ?
            """,
            params,
        ).fetchall()
        has_more = len(rows) > query.limit
        page_rows = rows[: query.limit]
        return [
            (int(row[0]), json.loads(row[1]))
            for row in page_rows
        ], has_more

    def recent_events(
        self,
        *,
        limit: int = 500,
        product: str | None = None,
    ) -> list[dict[str, Any]]:
        query = EventQuery(
            products=(product,) if product else (),
            limit=max(1, min(int(limit), 10_000)),
            sort="desc",
        )
        rows, _ = self.query_events(query, after_order=None)
        return [event for _, event in reversed(rows)]

    def all_events(self) -> Iterable[dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT event_json FROM captured_events ORDER BY capture_order"
        )
        for row in cursor:
            yield json.loads(row[0])

    def counts(self) -> dict[str, Any]:
        total = int(
            self.connection.execute("SELECT COUNT(*) FROM captured_events").fetchone()[0]
        )
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
            SELECT source_id, product, alias, generation, cursor_bytes,
                   line_number, size_bytes, event_count, last_scanned,
                   last_error, interrupted
            FROM capture_sources
            ORDER BY product, alias
            """
        )
        result = []
        for row in rows:
            if row[10]:
                status = "source_interrupted"
            elif row[9]:
                status = "error"
            elif row[4] < row[6]:
                status = "catching_up"
            else:
                status = "recording"
            result.append(
                {
                    "source_id": row[0],
                    "product": row[1],
                    "alias": row[2],
                    "generation": int(row[3]),
                    "cursor_bytes": int(row[4]),
                    "line_number": int(row[5]),
                    "size_bytes": int(row[6]),
                    "event_count": int(row[7]),
                    "last_scanned": row[8],
                    "status": status,
                    "error": row[9],
                }
            )
        return result

    def clear(self) -> int:
        count = int(
            self.connection.execute("SELECT COUNT(*) FROM captured_events").fetchone()[0]
        )
        with self.connection:
            self.connection.execute("DELETE FROM captured_events")
            self.connection.execute("DELETE FROM capture_sources")
        return count


class RecorderService:
    """Visible, reversible, local-only incremental recorder for authorized sources."""

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
        self._last_scan_bytes = 0
        self._bytes_read_total = 0
        self._processes = {"nx": False, "powermill": False}
        self._instances: dict[str, list[dict[str, Any]]] = {
            "nx": [],
            "powermill": [],
        }
        self._last_process_check = 0.0
        self._last_discovery_check = 0.0
        self._known_sources: list[tuple[str, Path]] = []
        self._state = "recording" if self.active else "paused"
        self._state_reason = ""
        self._last_transition: dict[str, Any] | None = None
        self._last_source_transition: dict[str, Any] | None = None
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
        auto_connect: bool | None = None,
        include_existing: bool | None = None,
        capture_logs: bool | None = None,
        detect_instances: bool | None = None,
        audit_execution: bool | None = None,
        operator_label: str | None = None,
        poll_interval_seconds: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if consent is not None:
                self.config.consent = bool(consent)
                if consent:
                    source = consent_source or "manual"
                    self.config.consent_source = (
                        source if source in {"auto_install", "manual"} else "manual"
                    )
                else:
                    self.config.consent_source = "revoked"
                    self._instances = {"nx": [], "powermill": []}
                    self._processes = {"nx": False, "powermill": False}
                    self._last_process_check = 0.0
            if enabled is not None:
                if enabled and not self.config.consent:
                    raise ValueError("Local recording requires explicit consent first.")
                self.config.enabled = bool(enabled)
            if auto_connect is not None:
                self.config.auto_connect = bool(auto_connect)
                self._last_discovery_check = 0.0
            if include_existing is not None:
                self.config.include_existing = bool(include_existing)
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
                    raise ValueError(
                        "Operator label must be unlabeled, routine, or expert."
                    )
                self.config.operator_label = operator_label
            if poll_interval_seconds is not None:
                self.config.poll_interval_seconds = max(
                    0.25,
                    min(float(poll_interval_seconds), 30.0),
                )
            self._save_config()
            if not self.config.consent:
                self._transition("paused", "awaiting_consent")
            elif not self.config.enabled:
                self._transition("paused", "operator")
            else:
                self._transition("recording", "")
            self._wake.set()
            return self.status()

    def pause(self, category: str | None = None) -> dict[str, Any]:
        if category is None:
            return self.configure(enabled=False)
        field = _CATEGORY_CONFIG_FIELDS.get(category)
        if field is None:
            raise ValueError(f"Unknown recorder category: {category}")
        return self.configure(**{field: False})

    def resume(self, category: str | None = None) -> dict[str, Any]:
        if not self.config.consent:
            raise ValueError("Local recording requires explicit consent first.")
        if category is None:
            return self.configure(enabled=True)
        field = _CATEGORY_CONFIG_FIELDS.get(category)
        if field is None:
            raise ValueError(f"Unknown recorder category: {category}")
        return self.configure(**{field: True})

    def scan_now(self) -> int:
        return self._scan(force_discovery=True)

    def scan_once(self) -> int:
        return self._scan(force_discovery=False)

    def _scan(self, *, force_discovery: bool) -> int:
        with self._lock:
            self._last_scan_bytes = 0
            if not self.config.consent:
                self._transition("paused", "awaiting_consent")
                return 0
            if not self.config.enabled:
                self._transition("paused", "operator")
                return 0
            inserted = 0
            runtime_errors: list[str] = []
            interrupted = False
            catching_up = False
            now = time.monotonic()
            if self.config.detect_instances and now - self._last_process_check >= 2:
                self._refresh_connections()
            elif not self.config.detect_instances:
                self._instances = {"nx": [], "powermill": []}
                self._processes = {"nx": False, "powermill": False}

            if self.config.capture_logs and (
                force_discovery
                or now - self._last_discovery_check >= 5
                or not self._known_sources
            ):
                previous_sources = {
                    (product, str(path.resolve()).casefold()): (product, path)
                    for product, path in self._known_sources
                }
                discovered_sources = self._discover_sources()
                discovered_keys = {
                    (product, str(path.resolve()).casefold())
                    for product, path in discovered_sources
                }
                for key, (product, path) in previous_sources.items():
                    if key in discovered_keys:
                        continue
                    source_id = self.redactor.source_id(product, path)
                    self.store.mark_source_interrupted(
                        source_id=source_id,
                        product=product,
                        alias=self.redactor.source_alias(product, path),
                        error="Authorized source is unavailable.",
                    )
                    interrupted = True
                self._known_sources = discovered_sources
                self._last_discovery_check = now
            sources_to_scan = (
                self._known_sources if self.config.capture_logs else []
            )
            for product, path in sources_to_scan:
                source_id = self.redactor.source_id(product, path)
                alias = self.redactor.source_alias(product, path)
                try:
                    stat = path.stat()
                except OSError:
                    self.store.mark_source_interrupted(
                        source_id=source_id,
                        product=product,
                        alias=alias,
                        error="Authorized source is unavailable.",
                    )
                    interrupted = True
                    continue
                try:
                    added, source_catching_up = self._scan_source(
                        product=product,
                        path=path,
                        source_id=source_id,
                        source_alias=alias,
                        stat=stat,
                    )
                    inserted += added
                    catching_up = catching_up or source_catching_up
                except (
                    OSError,
                    UnicodeDecodeError,
                    ValueError,
                    SyntaxError,
                    json.JSONDecodeError,
                ) as error:
                    previous = self.store.source_state(source_id)
                    self.store.commit_source_batch(
                        [],
                        source_id=source_id,
                        product=product,
                        alias=alias,
                        generation=int(previous["generation"]) if previous else 0,
                        cursor_bytes=int(previous["cursor_bytes"]) if previous else 0,
                        line_number=int(previous["line_number"]) if previous else 0,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        file_identity=_file_identity(stat),
                        error=str(error)[:500],
                    )
                    runtime_errors.append(str(error)[:500])

            if self.config.capture_logs:
                source_statuses = {
                    source["status"] for source in self.store.sources()
                }
                interrupted = interrupted or "source_interrupted" in source_statuses
                catching_up = catching_up or "catching_up" in source_statuses
                if "error" in source_statuses and not runtime_errors:
                    runtime_errors.append("One or more authorized sources have errors.")

            self._last_scan = _utc_now()
            if runtime_errors:
                self._last_runtime_error = runtime_errors[0]
                self._transition("error", runtime_errors[0])
            elif interrupted:
                self._last_runtime_error = ""
                self._transition("source_interrupted", "authorized_source_unavailable")
            elif catching_up:
                self._last_runtime_error = ""
                self._transition("catching_up", "source_backlog")
            else:
                self._last_runtime_error = ""
                self._transition("recording", "")
            return inserted

    def _scan_source(
        self,
        *,
        product: str,
        path: Path,
        source_id: str,
        source_alias: str,
        stat: os.stat_result,
    ) -> tuple[int, bool]:
        previous = self.store.source_state(source_id)
        identity = _file_identity(stat)
        generation = int(previous["generation"]) if previous else 0
        cursor_bytes = int(previous["cursor_bytes"]) if previous else 0
        line_number = int(previous["line_number"]) if previous else 0
        rotated = bool(
            previous
            and (
                stat.st_size < cursor_bytes
                or (
                    previous["file_identity"]
                    and previous["file_identity"] != identity
                )
            )
        )
        if rotated:
            generation += 1
            cursor_bytes = 0
            line_number = 0
            self._last_source_transition = {
                "kind": "rotated_or_truncated",
                "source_id": source_id,
                "generation": generation,
                "at": _utc_now(),
            }
            self.store.commit_source_batch(
                [],
                source_id=source_id,
                product=product,
                alias=source_alias,
                generation=generation,
                cursor_bytes=0,
                line_number=0,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                file_identity=identity,
            )

        if previous is None and not self.config.include_existing:
            self.store.commit_source_batch(
                [],
                source_id=source_id,
                product=product,
                alias=source_alias,
                generation=generation,
                cursor_bytes=stat.st_size,
                line_number=0,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                file_identity=identity,
            )
            return 0, False

        if (
            cursor_bytes == stat.st_size
            and previous
            and not previous["last_error"]
            and not previous["interrupted"]
        ):
            return 0, False

        remaining = stat.st_size - cursor_bytes
        if remaining <= 0:
            self.store.commit_source_batch(
                [],
                source_id=source_id,
                product=product,
                alias=source_alias,
                generation=generation,
                cursor_bytes=cursor_bytes,
                line_number=line_number,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                file_identity=identity,
            )
            return 0, False

        read_size = min(remaining, _MAX_INCREMENTAL_BYTES)
        with path.open("rb") as stream:
            stream.seek(cursor_bytes)
            chunk = stream.read(read_size)
        if read_size < remaining:
            newline = chunk.rfind(b"\n")
            if newline < 0:
                if remaining > _MAX_SOURCE_LINE_BYTES:
                    raise ValueError("Source contains a record larger than 64 MiB.")
                return 0, True
            chunk = chunk[: newline + 1]
        if not chunk:
            return 0, cursor_bytes < stat.st_size

        encoding = "utf-8-sig" if cursor_bytes == 0 else "utf-8"
        text = chunk.decode(encoding, errors="strict")
        self._last_scan_bytes += len(chunk)
        self._bytes_read_total += len(chunk)
        raw_events = self._parse_source_segment(
            product=product,
            path=path,
            text=text,
            source_id=source_id,
            source_alias=source_alias,
            start_line=line_number,
            event_offset=int(previous["event_count"]) if previous else 0,
        )
        captured_at = _utc_now()
        events = [
            self._normalize_event(
                event,
                source_id=source_id,
                source_alias=source_alias,
                generation=generation,
                captured_at=captured_at,
                record_key=str(
                    event.pop(
                        "_capture_record_key",
                        f"{event.get('source_line', 0)}:{index}:{event.get('action', '')}",
                    )
                ),
            )
            for index, event in enumerate(raw_events)
        ]
        new_cursor = cursor_bytes + len(chunk)
        new_line_number = line_number + chunk.count(b"\n")
        inserted = self.store.commit_source_batch(
            events,
            source_id=source_id,
            product=product,
            alias=source_alias,
            generation=generation,
            cursor_bytes=new_cursor,
            line_number=new_line_number,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            file_identity=identity,
        )
        return inserted, new_cursor < stat.st_size

    def _parse_source_segment(
        self,
        *,
        product: str,
        path: Path,
        text: str,
        source_id: str,
        source_alias: str,
        start_line: int,
        event_offset: int,
    ) -> list[dict[str, Any]]:
        if path.suffix.lower() == ".jsonl":
            structured = self._parse_activity_jsonl(text, start_line=start_line)
            if structured is not None:
                return structured
        if product == "powermill":
            raw_events = parse_log(text).to_activity_events()
        elif product == "nx":
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
            for index, event in enumerate(raw_events):
                event["session_id"] = source_id
                event["seq"] = event_offset + index
        else:
            raw_events = [
                event.to_dict()
                for event in CimatronJournalAdapter().parse_source(
                    text,
                    source_file=source_alias,
                    session_name=source_id,
                )
            ]
            for index, event in enumerate(raw_events):
                event["session_id"] = source_id
                event["seq"] = event_offset + index
        for index, event in enumerate(raw_events):
            local_line = int(event.get("source_line", 0) or 0)
            event["source_line"] = start_line + local_line
            if product == "powermill":
                event["seq"] = event_offset + index
            event["_capture_record_key"] = (
                f"{event['source_line']}:{index}:{event.get('action', '')}"
            )
        return raw_events

    @staticmethod
    def _parse_activity_jsonl(
        text: str,
        *,
        start_line: int = 0,
    ) -> list[dict[str, Any]] | None:
        events: list[dict[str, Any]] = []
        for local_line, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"JSONL line {start_line + local_line} must be an object."
                )
            required = {"session_id", "seq", "product", "action"}
            if not required.issubset(value):
                return None
            event = dict(value)
            event.setdefault("source_line", start_line + local_line)
            event["_capture_record_key"] = f"line:{start_line + local_line}"
            events.append(event)
        return events or None

    def _normalize_event(
        self,
        event: Mapping[str, Any],
        *,
        source_id: str,
        source_alias: str,
        generation: int,
        captured_at: str,
        record_key: str,
    ) -> dict[str, Any]:
        raw = dict(event)
        raw.pop("_event_id", None)
        raw.pop("_capture_record_key", None)
        product = str(raw.get("product", "unknown")).lower()
        params = (
            dict(raw["params"]) if isinstance(raw.get("params"), Mapping) else {}
        )
        source_mode_value = raw.get("source_mode")
        source_mode = (
            str(source_mode_value).strip().lower()
            if source_mode_value is not None
            else None
        )
        if source_mode not in SOURCE_MODES:
            source_mode = None
        mode = normalize_event_mode(raw.get("mode") or params.get("mode"))
        if source_mode == "execution_audit":
            mode = "automation"
        elif source_mode in EVENT_MODES:
            mode = source_mode
        raw_session_id = str(raw.get("session_id", "session"))
        existing_capture = (
            dict(params["capture"])
            if isinstance(params.get("capture"), Mapping)
            else {}
        )
        existing_capture.update(
            {
                "captured_at": captured_at,
                "source_id": source_id,
                "source_generation": generation,
                "operator_label": self.config.operator_label,
                "privacy": "redacted-local-v1",
            }
        )
        params["mode"] = mode
        params["capture"] = existing_capture
        normalized = raw
        normalized.update(
            {
                "schema_version": int(raw.get("schema_version", 1)),
                "session_id": self.redactor.session_id(
                    product,
                    source_id,
                    generation,
                    raw_session_id,
                ),
                "seq": int(raw.get("seq", 0)),
                "product": product,
                "action": str(raw.get("action", "cam.unknown")),
                "category": str(raw.get("category", "other")),
                "mode": mode,
                "source_mode": source_mode or mode,
                "view_level": str(raw.get("view_level") or "L1"),
                "expertise_label": str(
                    raw.get("expertise_label") or self.config.operator_label
                ),
                "params": params,
                "source_file": source_alias,
                "source_line": int(raw.get("source_line", 0) or 0),
            }
        )
        normalized = self.redactor.value(normalized)
        identity = f"{source_id}\0{generation}\0{record_key}"
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

        explicit = {
            (product, str(path.resolve()).casefold())
            for product, paths in self.explicit_sources.items()
            for path in paths
            if path.suffix.lower() in _SOURCE_EXTENSIONS[product]
        }
        found: dict[tuple[str, str], Path] = {}
        for product, roots in configured.items():
            extensions = _SOURCE_EXTENSIONS[product]
            for root in roots:
                key = (product, str(root.resolve()).casefold())
                if key in explicit:
                    found[key] = root
                    if not root.is_dir():
                        continue
                if root.is_file() and root.suffix.lower() in extensions:
                    found[key] = root
                    continue
                if not root.is_dir():
                    continue
                try:
                    for candidate in root.rglob("*"):
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
                            found[
                                (product, str(candidate.resolve()).casefold())
                            ] = candidate
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

    def _transition(self, state: str, reason: str) -> None:
        if state not in _RECORDER_STATES:
            raise ValueError(f"Invalid recorder state: {state}")
        if state != self._state or reason != self._state_reason:
            self._last_transition = {
                "from": self._state,
                "to": state,
                "reason": reason,
                "at": _utc_now(),
            }
        self._state = state
        self._state_reason = reason

    def status(self, *, refresh_connections: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.config.consent and (
                refresh_connections
                or time.monotonic() - self._last_process_check >= 2
            ):
                self._refresh_connections(force=refresh_connections)
            state = self._state
            if not self.config.consent:
                # Retained for the existing HTTP/UI contract; pause_reason is authoritative.
                state = "awaiting_consent"
            elif not self.config.enabled:
                state = "paused"
            return {
                "state": state,
                "recorder_state": self._state,
                "state_reason": self._state_reason,
                "last_transition": (
                    dict(self._last_transition) if self._last_transition else None
                ),
                "last_source_transition": (
                    dict(self._last_source_transition)
                    if self._last_source_transition
                    else None
                ),
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
                "uploads_enabled": False,
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
                "last_scan_bytes": self._last_scan_bytes,
                "bytes_read_total": self._bytes_read_total,
                "last_error": self._last_runtime_error,
            }

    def query_events(
        self,
        query: EventQuery | Mapping[str, Any],
    ) -> EventPage:
        event_query = (
            query if isinstance(query, EventQuery) else EventQuery.from_dict(query)
        )
        with self._lock:
            after_order = self._decode_cursor(event_query)
            rows, has_more = self.store.query_events(
                event_query,
                after_order=after_order,
            )
            events = [ActivityEvent.from_dict(event) for _, event in rows]
            next_cursor = (
                self._encode_cursor(event_query, rows[-1][0])
                if has_more and rows
                else None
            )
            return EventPage(
                events=events,
                has_more=has_more,
                next_cursor=next_cursor,
            )

    def _query_fingerprint(self, query: EventQuery) -> str:
        value = query.to_dict(include_cursor=False)
        for name in (
            "source_modes",
            "view_levels",
            "products",
            "instance_ids",
            "project_ids",
            "session_ids",
            "actions",
            "categories",
        ):
            value[name] = sorted(set(value[name]))
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _encode_cursor(self, query: EventQuery, capture_order: int) -> str:
        value = {
            "v": 1,
            "capture_order": capture_order,
            "sort": query.sort,
            "query": self._query_fingerprint(query),
        }
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = hmac.new(
            self.config.salt.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()[:32]
        return f"{encoded}.{signature}"

    def _decode_cursor(self, query: EventQuery) -> int | None:
        if query.cursor is None:
            return None
        try:
            encoded, signature = query.cursor.rsplit(".", 1)
            expected = hmac.new(
                self.config.salt.encode("utf-8"),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()[:32]
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            padding = "=" * (-len(encoded) % 4)
            value = json.loads(
                base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
            )
            if (
                value.get("v") != 1
                or value.get("sort") != query.sort
                or value.get("query") != self._query_fingerprint(query)
            ):
                raise ValueError
            capture_order = int(value["capture_order"])
            if capture_order < 1:
                raise ValueError
            return capture_order
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("Invalid or query-mismatched EventQuery cursor") from error

    def recent_events(
        self,
        *,
        limit: int = 500,
        product: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return self.store.recent_events(limit=limit, product=product)

    def record_execution(
        self,
        *,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> int:
        with self._lock:
            if (
                not self.config.consent
                or not self.config.enabled
                or not self.config.audit_execution
            ):
                return 0
            product = str(request.get("product", "unknown")).lower()
            action = str(request.get("action", "cam.execution.unknown"))
            command = str(request.get("command", ""))
            recipe_hash = str(request.get("recipe_hash", ""))
            captured_at = _utc_now()
            task_id = str(request.get("task_id", ""))
            sequence = time.time_ns()
            event: dict[str, Any] = {
                "schema_version": 1,
                "session_id": (
                    f"{product}:execution:{task_id or recipe_hash[:12] or 'unreviewed'}"
                ),
                "seq": sequence,
                "product": product,
                "action": action,
                "category": "execution",
                "mode": "automation",
                "source_mode": "execution_audit",
                "view_level": "L4",
                "expertise_label": self.config.operator_label,
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
                "duration_ms": (
                    int(result["duration_ms"])
                    if result.get("duration_ms") is not None
                    else None
                ),
                "timestamp": captured_at,
                "command_response": self.redactor.value(dict(result)),
                "_event_id": hashlib.sha256(
                    (
                        f"{task_id}:{captured_at}:{product}:{action}:{sequence}"
                    ).encode("utf-8")
                ).hexdigest(),
            }
            if request.get("target_instance_id"):
                event["instance_id"] = str(request["target_instance_id"])
            if request.get("project_id"):
                event["project_id"] = str(request["project_id"])
            if request.get("target_version"):
                event["target_version"] = str(request["target_version"])
            if _RECIPE_HASH.fullmatch(recipe_hash):
                event["recipe_hash"] = recipe_hash
            return self.store.insert_events(
                [event],
                source_id="execution-gateway",
            )

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
                except Exception as error:  # pragma: no cover - service boundary
                    with self._lock:
                        self._last_runtime_error = str(error)[:500]
                        self._transition("error", self._last_runtime_error)
            wait_seconds = (
                0.01
                if self._state == "catching_up"
                else self.config.poll_interval_seconds
            )
            self._wake.wait(wait_seconds)
            self._wake.clear()


# Existing HTTP and integration code imports CaptureService.
CaptureService = RecorderService
