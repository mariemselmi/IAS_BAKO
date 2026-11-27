"""SQLite storage for the build record (spec section 6.3 entities).

One connection shared by the API threads and the asyncio ingest loop,
serialised with a lock. Volume is low (about one vehicle per day), so this is
plenty; CAN frames are written in batches by the ingest flusher.
"""

from __future__ import annotations
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS chassis (
    chassis_id  TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    chassis_id    TEXT NOT NULL REFERENCES chassis(chassis_id),
    model         TEXT NOT NULL,
    manager_id    TEXT,
    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    status        TEXT NOT NULL DEFAULT 'open',      -- open | complete
    phase         INTEGER NOT NULL DEFAULT 0,
    station       TEXT,
    last_event_at TEXT,
    record_pdf    TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_chassis ON sessions (chassis_id);

CREATE TABLE IF NOT EXISTS gateway_bindings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_id  TEXT NOT NULL,
    session_id  INTEGER NOT NULL REFERENCES sessions(session_id),
    from_ts     TEXT NOT NULL,
    to_ts       TEXT
);
CREATE INDEX IF NOT EXISTS ix_bind_gw ON gateway_bindings (gateway_id, from_ts);

-- Event log: case = chassis, activity = type (+ operation), timestamp, resource.
CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER REFERENCES sessions(session_id),
    chassis_id  TEXT,
    type        TEXT NOT NULL,
    phase       INTEGER,
    operation   TEXT,
    station     TEXT,
    shift       TEXT,
    user        TEXT,
    resource    TEXT,
    timestamp   TEXT NOT NULL,
    client_time TEXT,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_session ON events (session_id, timestamp);
CREATE INDEX IF NOT EXISTS ix_events_type ON events (type, timestamp);

CREATE TABLE IF NOT EXISTS can_frames (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER,
    gateway_id  TEXT NOT NULL,
    server_time TEXT NOT NULL,
    can_id      INTEGER NOT NULL,
    dlc         INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_frames_session ON can_frames (session_id, can_id, server_time);

CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER,
    gateway_id  TEXT NOT NULL,
    server_time TEXT NOT NULL,
    field       TEXT NOT NULL,
    value       REAL,
    unit        TEXT
);
CREATE INDEX IF NOT EXISTS ix_meas_session ON measurements (session_id, field, server_time);

CREATE TABLE IF NOT EXISTS anomalies (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(session_id),
    type           TEXT NOT NULL,
    phase          INTEGER,
    station        TEXT,
    description    TEXT,
    photo          TEXT,
    opened_by      TEXT,
    opened_at      TEXT NOT NULL,
    closed_at      TEXT,
    recheck_result TEXT
);

CREATE TABLE IF NOT EXISTS interventions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    anomaly_id     INTEGER NOT NULL REFERENCES anomalies(id),
    session_id     INTEGER NOT NULL,
    action         TEXT NOT NULL,
    recheck_result TEXT,
    user           TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER,
    gateway_id      TEXT,
    rule            TEXT NOT NULL,
    severity        TEXT NOT NULL,               -- warning | critical
    message         TEXT NOT NULL,
    value           TEXT,
    raised_at       TEXT NOT NULL,
    cleared_at      TEXT,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    sms_sent        INTEGER NOT NULL DEFAULT 0,
    escalated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_open ON alerts (acknowledged_at, rule);

CREATE TABLE IF NOT EXISTS gaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER,
    gateway_id  TEXT,
    kind        TEXT NOT NULL,      -- can_missing | gateway_offline | operation_unconfirmed | operation_missing
    key         TEXT,               -- CAN id hex or operation code
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_gaps_session ON gaps (session_id, kind);

CREATE TABLE IF NOT EXISTS sms_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT NOT NULL,
    text        TEXT NOT NULL,
    reason      TEXT,
    gateway_id  TEXT,
    created_at  TEXT NOT NULL,
    sent_at     TEXT,
    status      TEXT NOT NULL DEFAULT 'queued'   -- queued | sent | ok | error
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,      -- vehicle | shift | weekly
    period_key  TEXT NOT NULL,      -- chassis id, 2026-12-02/A, 2026-W49
    created_at  TEXT NOT NULL,
    path        TEXT,
    summary     TEXT,
    UNIQUE (type, period_key)
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur.lastrowid or cur.rowcount

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        with self.lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self.lock:
            r = self.conn.execute(sql, tuple(params)).fetchone()
            return dict(r) if r else None

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        with self.lock:
            r = self.conn.execute(sql, tuple(params)).fetchone()
            return r[0] if r else None


def dumps(payload: Any) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def loads(s: str | None) -> Any:
    return json.loads(s) if s else None
