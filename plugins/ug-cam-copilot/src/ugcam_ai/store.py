from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import ActivityEvent, OPTIONAL_ACTIVITY_FIELDS


class ActivityStore:
    """SQLite event store tuned for local batch imports."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA temp_store=MEMORY")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def __enter__(self) -> "ActivityStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                product TEXT NOT NULL,
                source_file TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                event_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                product TEXT NOT NULL,
                action TEXT NOT NULL,
                category TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'manual',
                params_json TEXT NOT NULL,
                source_file TEXT NOT NULL,
                source_line INTEGER NOT NULL,
                duration_ms INTEGER,
                timestamp TEXT,
                schema_version INTEGER NOT NULL,
                extensions_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (session_id, seq),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
            CREATE INDEX IF NOT EXISTS idx_events_product_session
                ON events(product, session_id, seq);
            CREATE INDEX IF NOT EXISTS idx_events_action
                ON events(product, action);
            """
        )
        columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(events)")
        }
        if "mode" not in columns:
            self.connection.execute(
                "ALTER TABLE events ADD COLUMN mode TEXT NOT NULL DEFAULT 'manual'"
            )
        if "extensions_json" not in columns:
            self.connection.execute(
                "ALTER TABLE events ADD COLUMN extensions_json TEXT NOT NULL DEFAULT '{}'"
            )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_events_log_filters
                ON events(product, mode, category, action)
            """
        )

    def replace_session(self, events: Iterable[ActivityEvent]) -> int:
        batch = list(events)
        if not batch:
            return 0
        first = batch[0]
        if any(item.session_id != first.session_id for item in batch):
            raise ValueError("replace_session accepts exactly one session")
        source_sha = self._source_hash(first.source_file)
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for item in batch:
            value = item.to_dict()
            extensions = {
                name: value[name] for name in OPTIONAL_ACTIVITY_FIELDS if name in value
            }
            rows.append(
                (
                    item.session_id,
                    item.seq,
                    item.product,
                    item.action,
                    item.category,
                    value["mode"],
                    json.dumps(value["params"], ensure_ascii=False, separators=(",", ":")),
                    item.source_file,
                    item.source_line,
                    item.duration_ms,
                    item.timestamp,
                    item.schema_version,
                    json.dumps(
                        extensions, ensure_ascii=False, separators=(",", ":")
                    ),
                )
            )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO sessions(
                    session_id, product, source_file, source_sha256, imported_at, event_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    product=excluded.product,
                    source_file=excluded.source_file,
                    source_sha256=excluded.source_sha256,
                    imported_at=excluded.imported_at,
                    event_count=excluded.event_count
                """,
                (
                    first.session_id,
                    first.product,
                    first.source_file,
                    source_sha,
                    now,
                    len(rows),
                ),
            )
            self.connection.execute(
                "DELETE FROM events WHERE session_id = ?", (first.session_id,)
            )
            self.connection.executemany(
                """
                INSERT INTO events(
                    session_id, seq, product, action, category, mode, params_json,
                    source_file, source_line, duration_ms, timestamp, schema_version,
                    extensions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def load_sessions(self, *, product: str) -> dict[str, list[ActivityEvent]]:
        grouped: dict[str, list[ActivityEvent]] = defaultdict(list)
        cursor = self.connection.execute(
            """
            SELECT session_id, seq, product, action, category, mode, params_json,
                   source_file, source_line, duration_ms, timestamp, schema_version,
                   extensions_json
            FROM events
            WHERE product = ?
            ORDER BY session_id, seq
            """,
            (product,),
        )
        for row in cursor:
            value = {
                "session_id": row[0],
                "seq": row[1],
                "product": row[2],
                "action": row[3],
                "category": row[4],
                "mode": row[5],
                "params": json.loads(row[6]),
                "source_file": row[7],
                "source_line": row[8],
                "duration_ms": row[9],
                "timestamp": row[10],
                "schema_version": row[11],
            }
            extensions = json.loads(row[12] or "{}")
            if isinstance(extensions, dict):
                value.update(extensions)
            grouped[row[0]].append(ActivityEvent.from_dict(value))
        return dict(grouped)

    @staticmethod
    def _source_hash(source_file: str) -> str:
        path = Path(source_file)
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return hashlib.sha256(source_file.encode("utf-8")).hexdigest()
