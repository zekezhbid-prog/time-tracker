"""SQLite storage. Times are stored as UTC ISO-8601 strings."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    -- casefold()-ed name: SQLite's NOCASE only folds ASCII, so "Мария" != "мария" there.
    name_key   TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS time_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    task        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    stopped_at  TEXT
);

-- At most one running timer per employee, enforced by the database itself.
CREATE UNIQUE INDEX IF NOT EXISTS one_running_timer
    ON time_entries(employee_id) WHERE stopped_at IS NULL;

CREATE INDEX IF NOT EXISTS entries_by_employee_time
    ON time_entries(employee_id, started_at);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
