"""Gateway simulator: stands in for the ISS board on the TCP uplink.

Replays a recorded CAN log (default: the real BAKO capture) at its own timing,
adds board sensor JSON every 2 s, answers SMS requests, and can inject faults:

    python tools/gateway_sim.py                              # loop the recording forever
    python tools/gateway_sim.py --fault-at 30 --silent-at 60 --overheat-at 90 --dcdc-low-at 120
    python tools/gateway_sim.py --gateway esp32-bms-002 --no-bms   # board sensors only (before BMS power-up)

The backend timestamps every line on arrival, exactly as for the real board.
"""

from __future__ import annotations
import argparse
import json
import math
import random
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.replay import read_log  # noqa: E402


def frame_line(can_id: int, data: bytes) -> str:
    return f"Extended ID: 0x{can_id:08X} DLC: {len(data)} Data: " + " ".join(f"0x{b:02X}" for b in data)


def sensors(t: float, args) -> dict:
    v12 = 13.4 + 0.05 * math.sin(t / 7)
    if args.dcdc_low_at is not None and t >= args.dcdc_low_at:
        v12 = 10.4
    motor = 24 + 0.02 * t
    if args.overheat_at is not None and t >= args.overheat_at:
        motor = 88.0
    sun = max(0.0, math.sin(t / 60))
    return {"v12_dc_out": round(v12, 2), "v72_dc_in": round(61.8 - 0.001 * t, 1),
            "v72_mppt_in": round(32 * sun, 1), "current_in": round(1.8 * sun, 2), "current_out": round(1.6 * sun, 2),
            "v12_handbrake": 12.6, "temp_mppt": round(25 + 3 * sun + random.uniform(-0.2, 0.2), 1),
            "temp_dcdc": round(27 + random.uniform(-0.2, 0.2), 1), "temp_motor": round(motor, 1), "handbrake": 1}


def downlink(sock: socket.socket, stop: threading.Event) -> None:
    buf = b""
    while not stop.is_set():
        try:
            chunk = sock.recv(4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            text = line.decode(errors="replace").strip()
            print(f"<< {text}")
            if text.startswith("SMS "):
                parts = text.split(" ", 3)
                if len(parts) >= 2:
                    try:
                        sock.sendall(f"SMS_RESULT {parts[1]} OK\n".encode())
                    except OSError:
                        break


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--gateway", default="esp32-bms-001")
    ap.add_argument("--log", type=Path, default=ROOT / "data" / "raw" / "bms_log_car_Bako.txt")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed factor")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = forever)")
    ap.add_argument("--no-bms", action="store_true", help="send board sensors only")
    ap.add_argument("--fault-at", type=float, help="BMS fault (level 2, code 27) from t seconds")
    ap.add_argument("--silent-at", type=float, help="BMS goes silent for 10 s at t seconds")
    ap.add_argument("--overheat-at", type=float, help="motor probe at 88 C from t seconds")
    ap.add_argument("--dcdc-low-at", type=float, help="DC-DC 12 V output drops to 10.4 V from t seconds")
    args = ap.parse_args()

    frames = list(read_log(args.log))
    if not frames:
        print("no frames in log", file=sys.stderr)
        return 1
    span_ms = frames[-1].ts_ms - frames[0].ts_ms + 100

    sock = socket.create_connection((args.host, args.port), timeout=5)
    sock.settimeout(None)
    stop = threading.Event()
    threading.Thread(target=downlink, args=(sock, stop), daemon=True).start()

    def send(line: str) -> None:
        sock.sendall((line + "\n").encode())

    send(f"HELLO {args.gateway}")
    print(f">> HELLO {args.gateway}  ({len(frames)} frames per loop, {span_ms / 1000:.0f} s)")
    t0 = time.monotonic()
    next_sensor = 0.0
    sent = 0
    loop = 0
    try:
        while True:
            base = loop * span_ms - frames[0].ts_ms
            for f in frames:
                t_log = (base + f.ts_ms) / 1000.0
                t_wall = t_log / args.speed
                while True:
                    now = time.monotonic() - t0
                    if now >= next_sensor:
                        send("SENSOR_JSON:" + json.dumps(sensors(now * args.speed, args), separators=(",", ":")))
                        next_sensor += 2.0 / args.speed
                    if now >= t_wall:
                        break
                    time.sleep(min(t_wall - now, max(0.0, next_sensor - now), 0.05))
                if args.duration and now >= args.duration:
                    raise KeyboardInterrupt
                if args.no_bms:
                    continue
                if args.silent_at is not None and args.silent_at <= t_log < args.silent_at + 10:
                    continue
                data = bytearray(f.data)
                if f.can_id & 0x1FFFFFFF == 0x18FF28F4:
                    if args.fault_at is not None and t_log >= args.fault_at:
                        data[6], data[7] = 2, 27
                    else:
                        data[6] = data[7] = 0
                send(frame_line(f.can_id & 0x1FFFFFFF, bytes(data)))
                sent += 1
            loop += 1
            print(f"   loop {loop} done, {sent} frames sent")
    except (KeyboardInterrupt, BrokenPipeError, ConnectionError):
        pass
    finally:
        stop.set()
        sock.close()
    print(f"sent {sent} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
