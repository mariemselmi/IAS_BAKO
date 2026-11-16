"""
BAKO CAN frame decoder.

Decodes the 22-frame BAKO vehicle protocol.
- The 7 O'CELL BMS frames follow the layouts in Bako_OBD_ISS/README.md,
  which were verified against the real vehicle capture.
- The 12 auxiliary frames (0x18Dxx01AA) follow BAKO_CAN_Protocol_v2.docx.

Each decode returns a list of (signal, value, unit, expected_range) tuples,
one per physical quantity in the frame. These become events in the event log.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable

# Many logs show the J1939 priority bits set differently.
# We mask them off so 0x18FF28F4 and 0x98FF28F4 are treated as the same PGN.
PRIORITY_MASK = 0x1FFFFFFF  # keep the low 29 bits


@dataclass(frozen=True)
class Signal:
    name: str
    value: float | int | str | bool
    unit: str
    expected: str  # human-readable expected range, used by the event log


def u16_be(b: bytes, i: int) -> int:
    return (b[i] << 8) | b[i + 1]


def u16_le(b: bytes, i: int) -> int:
    return b[i] | (b[i + 1] << 8)


def i16_le(b: bytes, i: int) -> int:
    v = u16_le(b, i)
    return v - 0x10000 if v >= 0x8000 else v


def i32_le(b: bytes, i: int) -> int:
    v = b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)
    return v - 0x100000000 if v >= 0x80000000 else v


# --- BMS frames (O'CELL) -----------------------------------------------------

def _cells(data: bytes, start_index: int) -> list[Signal]:
    """Decode 4 cell voltages, big-endian uint16, 1 mV/bit."""
    out: list[Signal] = []
    for i in range(4):
        mv = u16_be(data, i * 2)
        if mv == 0:  # last frame pads unused cells
            continue
        out.append(Signal(f"cell_{start_index + i}_mv", mv, "mV", "2500 to 3750"))
    return out


def decode_18C828F4(data: bytes) -> list[Signal]: return _cells(data, 1)
def decode_18C928F4(data: bytes) -> list[Signal]: return _cells(data, 5)
def decode_18CA28F4(data: bytes) -> list[Signal]: return _cells(data, 9)
def decode_18CB28F4(data: bytes) -> list[Signal]: return _cells(data, 13)
def decode_18CC28F4(data: bytes) -> list[Signal]: return _cells(data, 17)


def decode_18B428F4(data: bytes) -> list[Signal]:
    """Four temperature sensors, raw-40 = C, 0xFF = absent."""
    out: list[Signal] = []
    for i in range(4):
        raw = data[i]
        if raw == 0xFF:
            continue
        out.append(Signal(f"battery_temp_{i + 1}_c", raw - 40, "C", "10 to 40"))
    return out


def decode_18FF28F4(data: bytes) -> list[Signal]:
    """
    Pack summary (100 ms). Layout from the repo README, verified on the
    real capture against the known pack-voltage / SOC values.
      byte 0: status flags
      byte 1: SOC (percent)
      bytes 2-3 LE: pack current, (value - 5000) * 0.1 A (negative = charging)
      bytes 4-5 LE: pack voltage, * 0.1 V
      byte 6: fault level
      byte 7: error code
    """
    flags = data[0]
    soc = data[1]
    current_a = (u16_le(data, 2) - 5000) * 0.1
    pack_v = u16_le(data, 4) * 0.1
    fault_level = data[6]
    error_code = data[7]
    return [
        Signal("bms_present", True, "", "heartbeat"),
        Signal("pack_v", round(pack_v, 2), "V", "58 to 72"),
        Signal("pack_current_a", round(current_a, 2), "A", "-50 to 25"),
        Signal("soc", soc, "%", "0 to 100"),
        Signal("fault_level", fault_level, "", "0"),
        Signal("error_code", error_code, "", "0"),
        Signal("charging", bool(flags & 0x02), "", "status"),
        Signal("discharging", bool(flags & 0x04), "", "status"),
        Signal("bms_ready", bool(flags & 0x08), "", "status"),
    ]


def decode_18FE28F4(data: bytes) -> list[Signal]:
    """
    Cell extremes + temperature pair + discharge limit (100 ms).
      bytes 0-1 LE: max cell mV
      bytes 2-3 LE: min cell mV
      byte 4: max temp (raw-40, 0xFF = absent)
      byte 5: min temp (raw-40, 0xFF = absent)
      bytes 6-7 LE: max discharge current / 10 A
    """
    cell_max = u16_le(data, 0)
    cell_min = u16_le(data, 2)
    out = [
        Signal("cell_max_mv", cell_max, "mV", "2500 to 3750"),
        Signal("cell_min_mv", cell_min, "mV", "2500 to 3750"),
        Signal("cell_spread_mv", cell_max - cell_min, "mV", "below 30"),
    ]
    for i, name in ((4, "temp_max_c"), (5, "temp_min_c")):
        raw = data[i]
        if raw != 0xFF:
            out.append(Signal(name, raw - 40, "C", "10 to 40"))
    out.append(Signal("max_discharge_a", u16_le(data, 6) * 0.1, "A", "above 10"))
    return out


def decode_18FFE5F4(data: bytes) -> list[Signal]:
    """
    Charging request (500 ms). Verified on the real capture:
    bytes B5 02 5E 01 give 69.3 V and 35.0 A, which matches 19 * 3.65 V exactly.
      bytes 0-1 LE: max charge voltage, * 0.1 V
      bytes 2-3 LE: max charge current, * 0.1 A
      byte 4 bit 0: 0 = charger start signal
    """
    return [
        Signal("max_charge_v", u16_le(data, 0) * 0.1, "V", "up to 69.3"),
        Signal("max_charge_a", u16_le(data, 2) * 0.1, "A", "up to 35"),
        Signal("charger_start", (data[4] & 0x01) == 0, "", "status"),
    ]


# --- Auxiliary frames (BAKO VCU/sensors, 0x18Dxx01AA) ------------------------

def decode_18D001AA(data: bytes) -> list[Signal]:
    return [Signal("solar_pre_mppt_current_a", u16_le(data, 0) * 0.1, "A", "above 0.3")]


def decode_18D101AA(data: bytes) -> list[Signal]:
    return [Signal("solar_pre_mppt_voltage_v", u16_le(data, 0) * 0.1, "V", "above 15")]


def decode_18D201AA(data: bytes) -> list[Signal]:
    return [
        Signal("mppt_present", True, "", "heartbeat"),
        Signal("solar_post_mppt_current_a", u16_le(data, 0) * 0.1, "A", "above 0.3"),
        Signal("mppt_mode", data[2], "", "0=off,1=mppt,2=cv,3=float"),
        Signal("mppt_efficiency_pct", data[3], "%", "above 80"),
    ]


def decode_18D301AA(data: bytes) -> list[Signal]:
    return [
        Signal("dcdc_present", True, "", "heartbeat"),
        Signal("dcdc_12v_v", u16_le(data, 0) * 0.01, "V", "13.2 to 14.4"),
        Signal("dcdc_64v_input_v", u16_le(data, 2) * 0.1, "V", "58 to 72"),
    ]


def _single_temp(name: str, warn_c: int, fault_c: int):
    def _decode(data: bytes) -> list[Signal]:
        raw = data[0]
        if raw == 0xFF:
            return []
        return [Signal(name, raw - 40, "C", f"below {warn_c}")]
    return _decode


decode_18D401AA = _single_temp("motor_temp_c", 80, 100)
decode_18D501AA = _single_temp("mppt_temp_c", 60, 80)
decode_18D601AA = _single_temp("dcdc_temp_c", 60, 80)


def decode_18D701AA(data: bytes) -> list[Signal]:
    return [Signal("handbrake_engaged", bool(data[0] & 0x01), "", "operator action")]


def decode_18D801AA(data: bytes) -> list[Signal]:
    lat_raw = i32_le(data, 0)
    lon_raw = i32_le(data, 4)
    if lat_raw == 0x7FFFFFFF or lon_raw == 0x7FFFFFFF:
        return []
    return [
        Signal("gnss_lat_deg", round(lat_raw / 1_000_000, 6), "deg", "fixed"),
        Signal("gnss_lon_deg", round(lon_raw / 1_000_000, 6), "deg", "fixed"),
    ]


def decode_18D901AA(data: bytes) -> list[Signal]:
    fix_byte = data[4]
    fix = {0x00: "none", 0x01: "2D", 0x03: "3D"}.get(fix_byte, f"0x{fix_byte:02X}")
    return [
        Signal("gnss_present", True, "", "heartbeat"),
        Signal("gnss_fix", fix, "", "2D or 3D"),
        Signal("gnss_speed_kmh", u16_le(data, 0) * 0.1, "km/h", "ground speed"),
        Signal("gnss_alt_m", i16_le(data, 2) * 0.1, "m", "altitude"),
        Signal("gnss_satellites_proxy", 1 if fix != "none" else 0, "", "at least 4"),
    ]


def decode_18DA01AA(data: bytes) -> list[Signal]:
    flags = data[3]
    return [
        Signal("battery_fault_level", data[0], "", "0=ok"),
        Signal("battery_error_code", u16_le(data, 1), "", "0x0000"),
        Signal("battery_ready", bool(flags & 0x01), "", "status"),
        Signal("battery_charging", bool(flags & 0x02), "", "status"),
        Signal("battery_discharging", bool(flags & 0x04), "", "status"),
    ]


def decode_18DB01AA(data: bytes) -> list[Signal]:
    return [
        Signal("charge_limit_max_v", u16_le(data, 0) * 0.1, "V", "up to 69.3"),
        Signal("charge_limit_max_a", u16_le(data, 2) * 0.1, "A", "up to 35"),
        Signal("charge_limit_enabled", bool(data[4] & 0x01), "", "status"),
    ]


# --- Dispatch table ----------------------------------------------------------

DECODERS: dict[int, Callable[[bytes], list[Signal]]] = {
    0x18C828F4: decode_18C828F4,
    0x18C928F4: decode_18C928F4,
    0x18CA28F4: decode_18CA28F4,
    0x18CB28F4: decode_18CB28F4,
    0x18CC28F4: decode_18CC28F4,
    0x18B428F4: decode_18B428F4,
    0x18FF28F4: decode_18FF28F4,
    0x18FE28F4: decode_18FE28F4,
    0x18FFE5F4: decode_18FFE5F4,
    0x18D001AA: decode_18D001AA,
    0x18D101AA: decode_18D101AA,
    0x18D201AA: decode_18D201AA,
    0x18D301AA: decode_18D301AA,
    0x18D401AA: decode_18D401AA,
    0x18D501AA: decode_18D501AA,
    0x18D601AA: decode_18D601AA,
    0x18D701AA: decode_18D701AA,
    0x18D801AA: decode_18D801AA,
    0x18D901AA: decode_18D901AA,
    0x18DA01AA: decode_18DA01AA,
    0x18DB01AA: decode_18DB01AA,
}


FRAME_SUBSYSTEM: dict[int, str] = {
    0x18C828F4: "battery", 0x18C928F4: "battery", 0x18CA28F4: "battery",
    0x18CB28F4: "battery", 0x18CC28F4: "battery", 0x18B428F4: "battery",
    0x18FF28F4: "battery", 0x18FE28F4: "battery", 0x18FFE5F4: "battery",
    0x18D001AA: "solar",   0x18D101AA: "solar",   0x18D201AA: "solar",
    0x18D301AA: "dcdc",    0x18D401AA: "motor",   0x18D501AA: "solar",
    0x18D601AA: "dcdc",    0x18D701AA: "vcu",     0x18D801AA: "gnss",
    0x18D901AA: "gnss",    0x18DA01AA: "battery", 0x18DB01AA: "battery",
}


def decode(can_id: int, data: bytes) -> list[Signal]:
    """Decode one CAN frame. Returns [] for unknown IDs."""
    canonical = can_id & PRIORITY_MASK
    fn = DECODERS.get(canonical)
    if fn is None:
        return []
    return fn(data)


def is_known(can_id: int) -> bool:
    """True if this frame ID is part of the BAKO protocol."""
    return (can_id & PRIORITY_MASK) in DECODERS


def subsystem_of(can_id: int) -> str:
    return FRAME_SUBSYSTEM.get(can_id & PRIORITY_MASK, "unknown")
