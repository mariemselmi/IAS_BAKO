"""
Ingest a captured CAN log into the SQLite event store.

This is the pipeline for validation step 1 (log replay) from the poster.

Usage:
    python src/ingest.py data/raw/bms_log_car_Bako.txt \\
        --chassis BK26-0146 --db events.db
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.event_store import EventStore, StoredEvent  # noqa: E402
from src.replay import replay  # noqa: E402


def ingest(log: Path, db: Path, chassis: str) -> int:
    store = EventStore(db)
    try:
        events = (
            StoredEvent(
                case_id=ev.case,
                activity=ev.activity,
                timestamp=ev.timestamp,
                resource=ev.resource,
                value=ev.value,
                unit=ev.unit,
                expected=ev.expected,
                kind="can",
                raw_can_id=ev.raw_can_id,
                log_ts_ms=ev.log_ts_ms,
            )
            for ev in replay(log, chassis)
        )
        return store.bulk_insert(events)
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path)
    ap.add_argument("--chassis", required=True)
    ap.add_argument("--db", type=Path, default=Path("events.db"))
    args = ap.parse_args(argv)
    n = ingest(args.log, args.db, args.chassis)
    print(f"Ingested {n} events into {args.db}")
    store = EventStore(args.db)
    try:
        c = store.counts()
        print(f"Store totals: {c['total']} events across {c['cases']} chassis")
        print(f"By kind: {c['by_kind']}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
