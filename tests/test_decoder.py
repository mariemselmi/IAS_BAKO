"""
Spot checks for the CAN decoder on known-good frames from the real BAKO
capture. These values were verified against the repo README interpretation
and the telemetry example file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.can_decoder import decode  # noqa: E402


def _signals(can_id: int, hex_data: str) -> dict:
    data = bytes.fromhex(hex_data.replace(" ", ""))
    return {s.name: s.value for s in decode(can_id, data)}


def test_pack_summary_0x18FF28F4():
    # 3B 4A F1 12 83 02 00 00 was observed repeatedly in the real log.
    s = _signals(0x18FF28F4, "3B 4A F1 12 83 02 00 00")
    # SOC in byte 1 = 0x4A = 74 %, which matches the telemetry example.
    assert s["soc"] == 74, s
    # Pack voltage, bytes 4-5 LE = 0x0283 = 643 then * 0.1 = 64.3 V.
    assert s["pack_v"] == 64.3, s


def test_charge_request_0x18FFE5F4():
    # B5 02 5E 01 00 00 00 00
    # max charge voltage = 0x02B5 = 693 then * 0.1 = 69.3 V = 19 * 3.65 V.
    s = _signals(0x18FFE5F4, "B5 02 5E 01 00 00 00 00")
    assert s["max_charge_v"] == 69.3, s
    assert s["max_charge_a"] == 35.0, s


def test_cell_extremes_0x18FE28F4():
    # 44 0D 36 0D 3D 3B E8 03
    # max cell mV = 0x0D44 = 3396, min cell = 0x0D36 = 3382, spread = 14.
    s = _signals(0x18FE28F4, "44 0D 36 0D 3D 3B E8 03")
    assert s["cell_max_mv"] == 3396, s
    assert s["cell_min_mv"] == 3382, s
    assert s["cell_spread_mv"] == 14, s


def test_temperatures_0x18B428F4():
    # 3B 3B 3D 3D FF FF FF FF
    # 0x3B = 59 -> 59 - 40 = 19 C; 0x3D = 61 -> 21 C. Four sensors, 0xFF padding.
    s = _signals(0x18B428F4, "3B 3B 3D 3D FF FF FF FF")
    assert s["battery_temp_1_c"] == 19, s
    assert s["battery_temp_3_c"] == 21, s


def test_priority_bits_are_masked():
    # 0x98FE28F4 is the same PGN as 0x18FE28F4 with different priority bits.
    s1 = _signals(0x98FE28F4, "44 0D 36 0D 3D 3B E8 03")
    s2 = _signals(0x18FE28F4, "44 0D 36 0D 3D 3B E8 03")
    assert s1 == s2


def test_cell_voltages_0x18C828F4():
    # Example from the real log: 0C FD 0C FE 0C FE 0C FE
    # Each pair is big-endian uint16, mV. 0x0CFD = 3325 mV.
    s = _signals(0x18C828F4, "0C FD 0C FE 0C FE 0C FE")
    assert s["cell_1_mv"] == 3325, s
    assert s["cell_4_mv"] == 3326, s


def test_last_cell_frame_drops_padded_zero_0x18CC28F4():
    # 0C FB 0C FB 0C FA 00 00 -- last slot is padding for cell 20, absent.
    s = _signals(0x18CC28F4, "0C FB 0C FB 0C FA 00 00")
    assert s["cell_17_mv"] == 3323, s
    assert s["cell_19_mv"] == 3322, s
    assert "cell_20_mv" not in s


def test_unknown_id_returns_nothing():
    # 0x18FF50E5 appeared in the old poster prototype but is not a real frame.
    s = _signals(0x18FF50E5, "00 00 00 00 00 00 00 00")
    assert s == {}


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
