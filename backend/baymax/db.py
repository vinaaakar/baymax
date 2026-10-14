"""SQLite persistence (single file, WAL mode, schema migrations)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        request TEXT NOT NULL,
        status TEXT NOT NULL,
        source TEXT NOT NULL,
        plan TEXT NOT NULL DEFAULT '[]',
        current_step INTEGER NOT NULL DEFAULT 0,
        progress REAL,
        approval TEXT,
        result TEXT,
        error TEXT,
        verified INTEGER NOT NULL DEFAULT 0,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS task_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'preference',
        source TEXT NOT NULL DEFAULT 'user',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        source TEXT NOT NULL,
        task_id TEXT,
        ts REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        paired_at REAL NOT NULL,
        last_seen REAL,
        revoked INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        risk TEXT NOT NULL,
        outcome TEXT NOT NULL,
        detail TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        path TEXT NOT NULL,
        task_id TEXT,
        meta TEXT NOT NULL DEFAULT '{}',
        validated INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
}


class Database:
    """Thread-safe wrapper around one sqlite connection."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    def _migrate(self) -> None:
        self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
        for version in sorted(MIGRATIONS):
            if version > current:
                self._conn.executescript(MIGRATIONS[version])
                current = version
        self._conn.execute("DELETE FROM schema_version")
        self._conn.execute("INSERT INTO schema_version VALUES (?)", (current,))

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
