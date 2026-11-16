"""
Replay a captured CAN log through the decoder, producing events.

The log file format, as produced by the ESP32 serial dump:
    [11455ms] ID: 0x18FE28F4  DLC: 8 Data: 44 0D 36 0D 3D 3B E8 03

Each decoded Signal becomes an event with:
    case       the chassis number (passed in; one log = one build session)
    activity   signal name (e.g. "pack_v")
    timestamp  the frame's log time, added to a session start time
    resource   subsystem the signal comes from ("battery", "solar", ...)
    value      numeric or string value
    unit       e.g. "V", "A", "mV"
    expected   human-readable expected range, for the build record

Usage:
    python -m src.replay data/raw/bms_log_car_Bako.txt --chassis BK26-0146
"""

from __future__ import annotations
import argparse
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

# Allow running as a script or as a module
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.can_decoder import decode, is_known, subsystem_of, Signal  # noqa: E402
else:
    from .can_decoder import decode, is_known, subsystem_of, Signal

FRAME_RE = re.compile(
    r"\[(\d+)ms\]\s+ID:\s+0x([0-9A-Fa-f]+)\s+DLC:\s+(\d+)\s+Data:\s+([0-9A-Fa-f ]+)"
)


@dataclass
class RawFrame:
    ts_ms: int
    can_id: int
    dlc: int
    data: bytes


@dataclass
class Event:
    case: str              # chassis number
    activity: str          # signal name
    timestamp: str         # ISO-8601 UTC
    resource: str          # subsystem producing the signal
    value: str             # stringified so sqlite is happy with mixed types
    unit: str
    expected: str
    raw_can_id: str        # hex, for traceability
    log_ts_ms: int         # original frame time

    def as_dict(self) -> dict:
        return asdict(self)


def parse_line(line: str) -> RawFrame | None:
    m = FRAME_RE.search(line)
    if not m:
        return None
    ts_ms = int(m.group(1))
    can_id = int(m.group(2), 16)
    dlc = int(m.group(3))
    data = bytes.fromhex(m.group(4).replace(" ", ""))
    return RawFrame(ts_ms=ts_ms, can_id=can_id, dlc=dlc, data=data)


def read_log(path: Path) -> Iterator[RawFrame]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            frame = parse_line(line)
            if frame is not None:
                yield frame


def replay(path: Path, chassis: str,
           session_start: datetime | None = None) -> Iterator[Event]:
    """Walk through the log file and emit one Event per decoded signal."""
    if session_start is None:
        session_start = datetime.now(timezone.utc)
    for frame in read_log(path):
        signals = decode(frame.can_id, frame.data)
        if not signals:
            continue
        ts = session_start + timedelta(milliseconds=frame.ts_ms)
        resource = subsystem_of(frame.can_id)
        can_id_hex = f"0x{frame.can_id:08X}"
        for sig in signals:
            yield Event(
                case=chassis,
                activity=sig.name,
                timestamp=ts.isoformat(timespec="milliseconds"),
                resource=resource,
                value=str(sig.value),
                unit=sig.unit,
                expected=sig.expected,
                raw_can_id=can_id_hex,
                log_ts_ms=frame.ts_ms,
            )


def _frame_stats(path: Path) -> dict:
    counts: dict[str, int] = {}
    total = 0
    unknown = 0
    for frame in read_log(path):
        total += 1
        key = f"0x{frame.can_id:08X}"
        counts[key] = counts.get(key, 0) + 1
        if not decode(frame.can_id, frame.data):
            unknown += 1
    return {"frames_total": total, "frames_unknown": unknown, "by_id": counts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay a BAKO CAN log as events.")
    ap.add_argument("log", type=Path, help="path to the .txt log file")
    ap.add_argument("--chassis", required=True, help="chassis number (case id)")
    ap.add_argument("--stats", action="store_true",
                    help="print frame-count statistics instead of events")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many events (0 = all)")
    args = ap.parse_args(argv)

    if args.stats:
        stats = _frame_stats(args.log)
        print(f"Total frames: {stats['frames_total']}")
        print(f"Unknown IDs:  {stats['frames_unknown']}")
        print("By CAN ID:")
        for fid, n in sorted(stats["by_id"].items(), key=lambda kv: -kv[1]):
            marker = " " if is_known(int(fid, 16)) else "?"
            print(f"  {marker} {fid}  x {n}")
        return 0

    n = 0
    for ev in replay(args.log, args.chassis):
        n += 1
        print(f"{ev.timestamp}  {ev.case}  {ev.resource:8s}  "
              f"{ev.activity:24s}  {ev.value} {ev.unit}")
        if args.limit and n >= args.limit:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
