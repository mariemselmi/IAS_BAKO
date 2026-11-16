"""
Live ingest: read frames from the ESP32 receiver over USB serial, decode them,
and write events into the SQLite event store as they arrive.

The receiver firmware prints the same line format as the recorded log, so this
reuses the parser and decoder from replay.py and can_decoder.py.

Usage, live (needs: pip install pyserial):
    python src/serial_ingest.py --port COM5 --chassis BK26-0146
    python src/serial_ingest.py --port /dev/ttyUSB0 --chassis BK26-0146

Usage, without hardware (feeds a recorded log as if it were the serial port):
    python src/serial_ingest.py --file data/raw/bms_log_car_Bako.txt --chassis BK26-0146
    python src/serial_ingest.py --file data/raw/bms_log_car_Bako.txt --chassis BK26-0146 --realtime

Stop with Ctrl+C. Events already received are committed.
"""

from __future__ import annotations
import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.can_decoder import decode, subsystem_of  # noqa: E402
from src.event_store import EventStore, StoredEvent  # noqa: E402
from src.replay import parse_line  # noqa: E402

COMMIT_EVERY_EVENTS = 500
COMMIT_EVERY_SECONDS = 1.0


def lines_from_serial(port: str, baud: int) -> Iterator[str]:
    try:
        import serial  # pyserial
    except ImportError:
        sys.exit("pyserial is not installed. Run: pip install pyserial")
    with serial.Serial(port, baud, timeout=1) as ser:
        print(f"Listening on {port} at {baud} baud. Ctrl+C to stop.")
        while True:
            raw = ser.readline()
            if raw:
                yield raw.decode("ascii", errors="replace")


def lines_from_file(path: Path, realtime: bool) -> Iterator[str]:
    """Stand-in for the serial port: yields the log lines, optionally paced
    with the original gaps between frames."""
    prev_ms: int | None = None
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if realtime:
                fr = parse_line(line)
                if fr is not None:
                    if prev_ms is not None and fr.ts_ms > prev_ms:
                        time.sleep((fr.ts_ms - prev_ms) / 1000)
                    prev_ms = fr.ts_ms
            yield line


def run(lines: Iterator[str], store: EventStore, chassis: str) -> dict:
    stats = {"lines": 0, "frames": 0, "unknown_frames": 0, "events": 0, "status_lines": 0}
    pending = 0
    last_commit = time.monotonic()
    try:
        for line in lines:
            stats["lines"] += 1
            if line.startswith("#"):
                # Health lines from the firmware; show them, do not store them.
                stats["status_lines"] += 1
                print(line.rstrip())
                continue
            frame = parse_line(line)
            if frame is None:
                continue
            stats["frames"] += 1
            signals = decode(frame.can_id, frame.data)
            if not signals:
                stats["unknown_frames"] += 1
                continue
            # Live capture: the event time is when the PC received it.
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            resource = subsystem_of(frame.can_id)
            can_hex = f"0x{frame.can_id:08X}"
            for sig in signals:
                store.insert(StoredEvent(
                    case_id=chassis, activity=sig.name, timestamp=ts,
                    resource=resource, value=str(sig.value), unit=sig.unit,
                    expected=sig.expected, kind="can",
                    raw_can_id=can_hex, log_ts_ms=frame.ts_ms,
                ))
                stats["events"] += 1
                pending += 1
            now = time.monotonic()
            if pending >= COMMIT_EVERY_EVENTS or now - last_commit >= COMMIT_EVERY_SECONDS:
                store.conn.commit()
                pending = 0
                last_commit = now
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        store.conn.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--port", help="serial port of the receiver, e.g. COM5 or /dev/ttyUSB0")
    src.add_argument("--file", type=Path, help="recorded log to feed instead of a serial port")
    ap.add_argument("--baud", type=int, default=921600, help="must match SERIAL_BAUD in receiver.ino")
    ap.add_argument("--realtime", action="store_true", help="with --file, keep the original timing")
    ap.add_argument("--chassis", required=True, help="chassis number, the case id")
    ap.add_argument("--db", type=Path, default=Path("events.db"))
    args = ap.parse_args(argv)

    lines = (lines_from_serial(args.port, args.baud) if args.port
             else lines_from_file(args.file, args.realtime))
    store = EventStore(args.db)
    try:
        stats = run(lines, store, args.chassis)
    finally:
        store.close()
    print(f"Frames: {stats['frames']}  unknown: {stats['unknown_frames']}  "
          f"events stored: {stats['events']}  into {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
