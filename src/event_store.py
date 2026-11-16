"""
SQLite event store for the hybrid build record.

One table, one row per event. The schema is deliberately generic so human
events from the operator tablet (phase sign-offs, torque readings, photos)
land in exactly the same table as machine events from the CAN gateway.

That means the event log is a single chronological list per chassis, which
is what process mining expects: case, activity, timestamp, resource.
"""

from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    TEXT    NOT NULL,         -- chassis number, our process-mining case
    activity   TEXT    NOT NULL,         -- what happened (signal name or operator action)
    timestamp  TEXT    NOT NULL,         -- ISO-8601 UTC, millisecond precision
    resource   TEXT    NOT NULL,         -- subsystem or operator name
    value      TEXT,                     -- stringified, allows mixed types
    unit       TEXT,
    expected   TEXT,                     -- human-readable expected range
    phase      TEXT,                     -- stop 1..7, or 'intake', 'close'
    state      TEXT,                     -- A, B, or C per the poster
    kind       TEXT NOT NULL DEFAULT 'can',  -- 'can' | 'operator' | 'anomaly' | 'intervention' | 'recheck'
    raw_can_id TEXT,                     -- hex, when kind='can'
    log_ts_ms  INTEGER                   -- original log offset, for replay provenance
);

CREATE INDEX IF NOT EXISTS ix_events_case_ts ON events (case_id, timestamp);
CREATE INDEX IF NOT EXISTS ix_events_activity ON events (activity);
"""


@dataclass
class StoredEvent:
    case_id: str
    activity: str
    timestamp: str
    resource: str
    value: str | None = None
    unit: str | None = None
    expected: str | None = None
    phase: str | None = None
    state: str | None = None
    kind: str = "can"
    raw_can_id: str | None = None
    log_ts_ms: int | None = None


class EventStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def insert(self, event: StoredEvent) -> int:
        cur = self.conn.execute(
            """INSERT INTO events
               (case_id, activity, timestamp, resource, value, unit, expected,
                phase, state, kind, raw_can_id, log_ts_ms)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event.case_id, event.activity, event.timestamp, event.resource,
             event.value, event.unit, event.expected, event.phase, event.state,
             event.kind, event.raw_can_id, event.log_ts_ms),
        )
        return cur.lastrowid or 0

    def bulk_insert(self, events: Iterable[StoredEvent]) -> int:
        n = 0
        with self.transaction():
            for ev in events:
                self.insert(ev)
                n += 1
        return n

    def events_for(self, case_id: str) -> list[StoredEvent]:
        rows = self.conn.execute(
            """SELECT case_id, activity, timestamp, resource, value, unit,
                      expected, phase, state, kind, raw_can_id, log_ts_ms
               FROM events WHERE case_id = ? ORDER BY timestamp, id""",
            (case_id,),
        ).fetchall()
        return [StoredEvent(*r) for r in rows]

    def cases(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT case_id FROM events ORDER BY case_id"
        ).fetchall()
        return [r[0] for r in rows]

    def counts(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        cases = self.conn.execute(
            "SELECT COUNT(DISTINCT case_id) FROM events"
        ).fetchone()[0]
        by_kind = dict(self.conn.execute(
            "SELECT kind, COUNT(*) FROM events GROUP BY kind"
        ).fetchall())
        return {"total": total, "cases": cases, "by_kind": by_kind}
