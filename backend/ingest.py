"""Gateway ingest (spec 4.2 Ingest + Binding, data format 6.1).

Gateways connect over TCP and send one line per message:
    HELLO esp32-bms-001
    Extended ID: 0x18FF28F4 DLC: 8 Data: 0x00 0x56 0x8C 0x13 0x60 0x02 0x00 0x00
    SENSOR_JSON:{"v12_dc_out":13.42, ...}
    ALERT:{"rule":"bms_fault","sms":true}        (gateway reports a local alert / SMS)
    SMS_RESULT 12 OK                             (answer to an SMS request)
    # anything                                   (status line, ignored)
The legacy serial format "[11455ms] ID: 0x18FE28F4  DLC: 8 Data: 44 0D ..." is
also accepted, so recorded logs can be replayed through the same path.

Every line is timestamped on arrival. Frames and measurements are assigned to
the session the gateway is bound to at that moment.

Downlink to the gateway (spec O6), one line each:
    SMS <id> <phone> <text>
    TIME <unix-seconds>
    CONFIG <json>
"""

from __future__ import annotations
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.can_decoder import decode, is_known

from . import clock
from .records import session_for_gateway

if TYPE_CHECKING:
    from .runtime import Runtime

BMS_IDS = {0x18FF28F4, 0x18FE28F4, 0x18C828F4, 0x18C928F4, 0x18CA28F4,
           0x18CB28F4, 0x18CC28F4, 0x18B428F4, 0x18FFE5F4}

SENSOR_UNITS = {
    "v12_dc_out": "V", "v72_dc_in": "V", "v72_mppt_in": "V", "current_in": "A",
    "current_out": "A", "v12_handbrake": "V", "handbrake": "", "temp_mppt": "C",
    "temp_dcdc": "C", "temp_motor": "C",
}

_EXT_RE = re.compile(r"(?:Extended|Standard)\s+ID:\s*0x([0-9A-Fa-f]+)\s+DLC:\s*(\d+)\s+Data:\s*(.*)$")
_LEGACY_RE = re.compile(r"\[(\d+)ms\]\s+ID:\s+0x([0-9A-Fa-f]+)\s+DLC:\s+(\d+)\s+Data:\s*([0-9A-Fa-f ]*)")


@dataclass
class Line:
    kind: str                 # hello | frame | sensor | alert | sms_result | comment | invalid
    gateway: str | None = None
    can_id: int | None = None
    dlc: int | None = None
    data: bytes | None = None
    json: dict | None = None
    text: str | None = None


def parse_line(raw: str) -> Line:
    s = raw.strip()
    if not s:
        return Line("comment", text="")
    if s.startswith("#"):
        return Line("comment", text=s)
    if s.startswith("HELLO"):
        parts = s.split()
        if len(parts) == 2 and re.fullmatch(r"[A-Za-z0-9._-]{1,40}", parts[1]):
            return Line("hello", gateway=parts[1])
        return Line("invalid", text=s)
    if s.startswith("SENSOR_JSON:"):
        try:
            obj = json.loads(s[len("SENSOR_JSON:"):])
        except json.JSONDecodeError:
            return Line("invalid", text=s)
        return Line("sensor", json=obj) if isinstance(obj, dict) else Line("invalid", text=s)
    if s.startswith("ALERT:"):
        try:
            obj = json.loads(s[len("ALERT:"):])
        except json.JSONDecodeError:
            return Line("invalid", text=s)
        return Line("alert", json=obj) if isinstance(obj, dict) else Line("invalid", text=s)
    if s.startswith("SMS_RESULT"):
        return Line("sms_result", text=s)
    m = _EXT_RE.search(s)
    if m:
        tokens = m.group(3).split()
        try:
            data = bytes(int(t, 16) for t in tokens)
        except ValueError:
            return Line("invalid", text=s)
        dlc = int(m.group(2))
        if dlc > 8 or len(data) != dlc:
            return Line("invalid", text=s)
        return Line("frame", can_id=int(m.group(1), 16) & 0x1FFFFFFF, dlc=dlc, data=data)
    m = _LEGACY_RE.search(s)
    if m:
        data = bytes.fromhex(m.group(4).replace(" ", ""))
        dlc = int(m.group(3))
        if dlc > 8 or len(data) != dlc:
            return Line("invalid", text=s)
        return Line("frame", can_id=int(m.group(2), 16) & 0x1FFFFFFF, dlc=dlc, data=data)
    return Line("invalid", text=s)


@dataclass
class GatewayState:
    gateway_id: str
    connected: bool = False
    peer: str | None = None
    connected_at: str | None = None
    last_line_at: str | None = None
    last_bms_at: str | None = None
    first_bms_at: str | None = None
    session_id: int | None = None
    frames: int = 0
    invalid_lines: int = 0
    unknown_ids: int = 0
    frames_by_id: dict[int, int] = field(default_factory=dict)
    last_by_id: dict[int, str] = field(default_factory=dict)
    battery: dict[str, Any] = field(default_factory=dict)
    sensors: dict[str, Any] = field(default_factory=dict)
    sensors_at: str | None = None
    last_snapshot_at: str | None = None

    def view(self) -> dict:
        now = clock.now()
        age = clock.seconds_between(self.last_line_at, clock.iso(now)) if self.last_line_at else None
        return {
            "gateway_id": self.gateway_id, "connected": self.connected, "peer": self.peer,
            "connected_at": self.connected_at, "last_line_at": self.last_line_at,
            "last_line_age_s": round(age, 1) if age is not None else None,
            "last_bms_at": self.last_bms_at, "session_id": self.session_id,
            "frames": self.frames, "invalid_lines": self.invalid_lines, "unknown_ids": self.unknown_ids,
            "frames_by_id": {f"0x{k:08X}": v for k, v in self.frames_by_id.items()},
            "battery": self.battery, "sensors": self.sensors, "sensors_at": self.sensors_at,
        }


class Ingestor:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self.frame_buf: list[tuple] = []
        self.meas_buf: list[tuple] = []
        self.server: asyncio.base_events.Server | None = None
        self._last_push: dict[str, float] = {}

    # ---- state ---------------------------------------------------------------
    def state(self, gateway_id: str) -> GatewayState:
        st = self.rt.live.get(gateway_id)
        if st is None:
            st = GatewayState(gateway_id)
            st.session_id = session_for_gateway(self.rt, gateway_id)
            self.rt.live[gateway_id] = st
        return st

    def refresh_binding(self, gateway_id: str) -> None:
        st = self.rt.live.get(gateway_id)
        if st is not None:
            st.session_id = session_for_gateway(self.rt, gateway_id)
            st.first_bms_at = None  # BMS-silent applies per binding
            st.last_bms_at = None

    # ---- line handling (sync, used by TCP server, simulator and tests) -------
    def handle_line(self, gateway_id: str | None, raw: str) -> str | None:
        """Process one line. Returns the gateway id (set by HELLO)."""
        line = parse_line(raw)
        if line.kind == "hello":
            st = self.state(line.gateway)
            st.connected = True
            st.connected_at = st.last_line_at = clock.iso()
            st.session_id = session_for_gateway(self.rt, line.gateway)
            self.rt.gaps.on_gateway_seen(line.gateway)
            self._push(st, force=True)
            return line.gateway
        if gateway_id is None:
            return None  # nothing is accepted before HELLO
        st = self.state(gateway_id)
        now = clock.iso()
        st.last_line_at = now
        self.rt.gaps.on_gateway_seen(gateway_id)
        if line.kind == "frame":
            self._on_frame(st, line, now)
        elif line.kind == "sensor":
            self._on_sensor(st, line.json or {}, now)
        elif line.kind == "alert":
            self.rt.alerts.on_gateway_alert(st, line.json or {})
        elif line.kind == "sms_result":
            self._on_sms_result(line.text or "")
        elif line.kind == "invalid":
            st.invalid_lines += 1
        return gateway_id

    def _on_frame(self, st: GatewayState, line: Line, now: str) -> None:
        can_id = line.can_id
        st.frames += 1
        st.frames_by_id[can_id] = st.frames_by_id.get(can_id, 0) + 1
        st.last_by_id[can_id] = now
        if not is_known(can_id):
            st.unknown_ids += 1
        if st.session_id is not None:
            self.frame_buf.append((st.session_id, st.gateway_id, now, can_id, line.dlc, line.data.hex().upper()))
        if can_id in BMS_IDS:
            if st.first_bms_at is None:
                st.first_bms_at = now
                self.rt.on_bms_online(st)
            st.last_bms_at = now
            self.rt.gaps.on_frame(st, can_id)
        for sig in decode(can_id, line.data):
            st.battery[sig.name] = sig.value
        if can_id in (0x18FF28F4, 0x18FE28F4, 0x18B428F4):
            self.rt.alerts.evaluate_battery(st)
        self._maybe_snapshot(st, now)
        self._feed(st, can_id, line.data, now)
        self._push(st)

    def _feed(self, st: GatewayState, can_id: int, data: bytes, now: str) -> None:
        """Sampled raw frames for the dashboard's bus feed (about 4 per second per gateway)."""
        t = clock.now().timestamp()
        key = "feed:" + st.gateway_id
        if t - self._last_push.get(key, 0) < 0.25:
            return
        self._last_push[key] = t
        self.rt.hub.publish("frame", {"gateway": st.gateway_id, "session_id": st.session_id, "t": now,
                                      "can_id": f"0x{can_id:08X}", "data": data.hex(" ").upper()})

    def _on_sensor(self, st: GatewayState, obj: dict, now: str) -> None:
        clean: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(v, bool):
                v = int(v)
            if isinstance(v, (int, float)):
                clean[k] = v
            elif v is None:
                clean[k] = None
        st.sensors.update(clean)
        st.sensors_at = now
        if st.session_id is not None:
            for k, v in clean.items():
                if v is not None:
                    self.meas_buf.append((st.session_id, st.gateway_id, now, k, float(v), SENSOR_UNITS.get(k, "")))
        self.rt.alerts.evaluate_sensors(st)
        self._push(st, force=True)

    def _maybe_snapshot(self, st: GatewayState, now: str) -> None:
        """Battery evidence for the build record: decoded BMS values every N s."""
        if st.session_id is None:
            return
        every = float(self.rt.cfg.data.get("measurements", {}).get("bms_snapshot_s", 5))
        age = clock.seconds_between(st.last_snapshot_at, now)
        if st.last_snapshot_at is not None and age is not None and age < every:
            return
        st.last_snapshot_at = now
        fields = self.rt.cfg.data.get("measurements", {}).get("bms_fields", [])
        units = {"pack_v": "V", "pack_current_a": "A", "soc": "%", "cell_max_mv": "mV",
                 "cell_min_mv": "mV", "cell_spread_mv": "mV", "temp_max_c": "C", "temp_min_c": "C"}
        for f in fields:
            v = st.battery.get(f)
            if isinstance(v, (int, float)):
                self.meas_buf.append((st.session_id, st.gateway_id, now, f, float(v), units.get(f, "")))
        for k, v in st.battery.items():  # individual cells and probes
            if (k.startswith("cell_") and k.endswith("_mv") and k[5:-3].isdigit()) or k.startswith("battery_temp_"):
                self.meas_buf.append((st.session_id, st.gateway_id, now, k, float(v), "mV" if k.startswith("cell") else "C"))

    def _on_sms_result(self, text: str) -> None:
        parts = text.split()
        if len(parts) >= 3 and parts[1].isdigit():
            status = "ok" if parts[2].upper() == "OK" else "error"
            self.rt.db.execute("UPDATE sms_outbox SET status = ? WHERE id = ?", (status, int(parts[1])))

    def _push(self, st: GatewayState, force: bool = False) -> None:
        """Live view: at most one update per gateway per second."""
        t = clock.now().timestamp()
        if not force and t - self._last_push.get(st.gateway_id, 0) < 1.0:
            return
        self._last_push[st.gateway_id] = t
        self.rt.hub.publish("live", st.view())

    def flush(self) -> int:
        frames, meas = self.frame_buf, self.meas_buf
        self.frame_buf, self.meas_buf = [], []
        self.rt.db.executemany(
            "INSERT INTO can_frames (session_id, gateway_id, server_time, can_id, dlc, data) VALUES (?,?,?,?,?,?)",
            frames)
        self.rt.db.executemany(
            "INSERT INTO measurements (session_id, gateway_id, server_time, field, value, unit) VALUES (?,?,?,?,?,?)",
            meas)
        return len(frames)

    # ---- TCP server ------------------------------------------------------------
    async def serve(self, host: str, port: int) -> None:
        self.server = await asyncio.start_server(self._client, host, port, limit=8192)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        gw: str | None = None
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=120)
                except asyncio.TimeoutError:
                    break
                except (asyncio.LimitOverrunError, ValueError):
                    continue  # over-long line, skip it
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace")
                new_gw = self.handle_line(gw, text)
                if new_gw and new_gw != gw:
                    if gw:
                        self._disconnected(gw, writer)
                    gw = new_gw
                    self.rt.writers[gw] = writer
                    st = self.state(gw)
                    st.peer = f"{peer[0]}:{peer[1]}" if peer else None
                    writer.write(f"TIME {int(clock.now().timestamp())}\n".encode())
                    await writer.drain()
                    self.rt.flush_sms()
        except (ConnectionError, OSError):
            pass
        finally:
            if gw:
                self._disconnected(gw, writer)
            try:
                writer.close()
            except Exception:
                pass

    def _disconnected(self, gw: str, writer: asyncio.StreamWriter) -> None:
        if self.rt.writers.get(gw) is writer:
            self.rt.writers.pop(gw, None)
            st = self.state(gw)
            st.connected = False
            self._push(st, force=True)

    def send(self, gateway_id: str, line: str) -> bool:
        writer = self.rt.writers.get(gateway_id)
        if writer is None or writer.is_closing():
            return False
        writer.write((line.replace("\n", " ") + "\n").encode())
        return True
