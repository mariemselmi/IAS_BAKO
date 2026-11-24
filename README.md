# bako_can

CAN data capture and event logging for the BAKO end-to-end production
monitoring project (IAS Challenge, TSYP14, Phase 2).

The code reads the BAKO vehicle's CAN bus, either from a recorded log or live
from an ESP32 gateway, translates every frame into readable values, and stores
them as events in a SQLite database. Each event has a case (the chassis
number), an activity (what was measured), a timestamp and a resource (which
part of the car sent it). That is the shape process-mining tools expect, and
it is the machine half of the hybrid build record described on the poster.

## What is in the repository

```
bako_can/
  src/
    can_decoder.py       decoder for the 22-frame BAKO CAN protocol
    replay.py            parser for the log format, emits one event per signal
    event_store.py       SQLite event store, one table for machine and human events
    ingest.py            recorded log -> decode -> store
    serial_ingest.py     live ESP32 serial -> decode -> store
  firmware/
    receiver/            ESP32 gateway, listen-only, prints frames over USB
    receiver_mcp2515/    same, for the ISS project board (MCP2515 module, 8 MHz crystal),
                         with a fixed copy of the mcp_can library in src/mcp_can
    sender/              ESP32 bench sender, replays the recorded log onto a bench bus
  ui/
    connectivity_check.html  station page: live battery checks, record and export
  tools/
    make_sender_frames.py   builds firmware/sender/frames.h from the recorded log
  tests/
    test_decoder.py      decoder checks against known frames from the real capture
  docs/
    IAS_PLAN.md          project plan, status and decisions (copy; master is in the IAS folder)
    CONNECTIVITY_CHECKS.md  every car component and how its connection is checked (copy)
  data/raw/
    bms_log_car_Bako.txt real capture from a BAKO vehicle, 3,197 frames
```

## Running it without hardware

Requires Python 3.10 or newer. No extra packages.

```bash
python tests/test_decoder.py
python src/ingest.py data/raw/bms_log_car_Bako.txt --chassis BK26-0146 --db events.db
python src/replay.py data/raw/bms_log_car_Bako.txt --chassis BK26-0146 --stats
```

To rehearse the live path with the recording standing in for the gateway:

```bash
python src/serial_ingest.py --file data/raw/bms_log_car_Bako.txt --chassis BK26-0146 --realtime
```

## Running it with hardware

Parts for a bench test (no car needed):

| Qty | Part | Use |
|---|---|---|
| 2 | ESP32 dev board (ESP32-DevKitC) | one receiver, one bench sender |
| 2 | 3.3 V CAN transceiver, SN65HVD230 or TJA1051T/3 | one per ESP32 |
| 2 | 120 ohm resistor | bus termination, one at each end of the bench bus |
| 2 | USB cable | power and data |
| | jumper wire, a short twisted pair | bus and signal wiring |

The ESP32's built-in CAN controller (TWAI) is used, so no MCP2515 is needed.

Wiring, same on both boards:

| ESP32 | Transceiver |
|---|---|
| GPIO 5 | TX (CTX / D) |
| GPIO 4 | RX (CRX / R) |
| 3V3 | VCC |
| GND | GND |

Join CAN-H to CAN-H and CAN-L to CAN-L between the two transceivers, put one
120 ohm resistor across CAN-H and CAN-L at each end, and share GND.

Steps:

1. Set the bus speed in both sketches (`CAN_BITRATE_250K` or `CAN_BITRATE_500K`).
   **Confirm the real value with BAKO first:** the ISS repository says
   250 kbit/s and `BAKO_CAN_Protocol_v2.docx` says 500 kbit/s.
2. Generate the sender's frame table: `python tools/make_sender_frames.py`
3. Flash `firmware/sender` to one board and `firmware/receiver` to the other
   (Arduino IDE or arduino-cli, board "ESP32 Dev Module").
4. Install pyserial: `pip install -r requirements.txt`
5. Start live ingest on the receiver's port:
   `python src/serial_ingest.py --port COM5 --chassis BK26-0146`

On a real vehicle, only the receiver is used. Do not add a 120 ohm resistor
there, the vehicle bus is already terminated. Never connect the bench sender
to a vehicle.

## Using the ISS project board

The ISS board carries an ESP32 and an MCP2515 CAN module (HW-184), so it uses
`firmware/receiver_mcp2515`, not `firmware/receiver`.

- The module's crystal reads **8.000**, so the sketch uses `MCP_8MHZ`. Check
  yours: a 16 MHz module needs `MCP_16MHZ`.
- CAN-H and CAN-L come out on the module's **blue screw terminal** (H and L).
  The green CAN1/CAN2 terminals on the main board are not connected to it.
- The sketch bundles a fixed copy of the `mcp_can` 1.5.1 library in
  `src/mcp_can`. The stock library crashes the ESP32 when a frame's length field
  reads above 8 (it overruns an 8-byte buffer and corrupts its SPI pointer).
- The sketch prints at 115200 baud, so live ingest needs `--baud 115200`:
  `python src/serial_ingest.py --port COM6 --baud 115200 --chassis BK26-0146 --db live.db`

## Connectivity check page

`ui/connectivity_check.html` is the station screen. It reads the board directly over USB
(Chrome and Edge can open serial ports), decodes the battery messages and checks, for
components K01 to K03 (see `CONNECTIVITY_CHECKS.md`):

battery talking · messages acknowledged (no repeats) · all 9 message types arriving ·
no data gaps · 19 of 19 cells · 4 of 4 temperature sensors · pack voltage, every cell,
balance and temperature in range · pack voltage equals the sum of the cells · no battery
fault · main contactor closed · charge level for release. Plus a charge-port test (K22).

The screen follows industrial operator-screen practice (ISA-101): grey base, colour only
for results, one primary action, a test-step table, and a status bar. `ui/UI_REVIEW.md`
lists the design rules and the checklist of generic-looking patterns it avoids.

- **Verdict banner:** NO DATA, READY, TESTING, then a full-width PASS or FAIL.
- **One button** that changes with the state: Connect gateway, Start test (or Space),
  Re-test after fix.
- **What to fix:** after a failure, each problem, the likely cause, and the part it points to.
- **Test table:** 14 automatic tests plus the manual charge-port step, with limit, measured
  value and result.
- **Connection map:** battery, CAN link, gateway and station, plus the parts not monitored yet.
- **Battery, live:** readouts, contactor flags, a 19-cell bar chart, 4 temperatures.
- **Tabs:** bus timing (every frame on a 10 s timeline, gaps shaded red), build record (every
  event, exportable as JSON or CSV with the raw CAN log), component coverage K01 to K25, CAN trace.

To run it:

1. Close the Arduino Serial Monitor (only one program can use the port).
2. In the `bako_can` folder: `python -m http.server 8000`
3. Open `http://localhost:8000/ui/connectivity_check.html` in **Chrome or Edge**.
4. **Connect gateway**, pick the board's port (e.g. COM6), keep 115200 baud.

Without the car: **Demo recording** (status bar) replays the ISS recording; **Replay file**
replays any saved log file.

Tested on 2026-11-23: the ISS recording passes all 14 checks; the car's real frames from
2026-11-23 raise the battery fault (level 2, code 27) and a 12 % charge warning; a repeat
storm and a mid-check loss of the cell messages are both detected. Spread, temperature and
charge limits are provisional until BAKO confirms them.

## Tested on the BAKO car (2026-11-23)

ISS board running `receiver_mcp2515`, wired to the battery's CAN line, car on,
no charger plugged in.

| Finding | Detail |
|---|---|
| Bus speed | **250 kbit/s** confirmed |
| Bus mode | **`MCP_NORMAL` needed.** In listen-only mode the battery repeated one frame without end and the cell and temperature frames never arrived: nothing on this line acknowledges when the charger is unplugged. The ISS recording was made while charging, when the charger acknowledges. Listen-only with the charger connected is expected to work but has not been tested yet |
| Capture | 74 s, 1,852 frames, 25.0 frames/s, 0 unknown IDs, 0 receive errors, 12,491 events stored |
| Capture rate | ~100 %: every frame type arrived at its nominal rate (pack and cell extremes 10 Hz, temperatures 2 Hz, charger request 1 Hz, cell voltages 0.4 Hz) |
| What is on this line | Battery (BMS) frames only. Handbrake, DC-DC, MPPT and GPS are not CAN devices on today's car |
| Battery at the time | 60.7 V, 12 % charge, cells 3,192 to 3,202 mV, 24 to 25 °C, fault level 2, error code 27 (meaning unknown, to ask BAKO) |

## Status

| Part | State |
|---|---|
| Decoder, all 22 frames | working, 8/8 tests pass on real frames |
| Recorded log ingest | working, 3,197 frames -> 21,557 events, 0 unknown |
| Live serial ingest | working on the car with the ISS board, ~100 % capture |
| ISS board firmware (`receiver_mcp2515`) | flashed and tested on the car |
| Plain ESP32 receiver and bench sender | compile cleanly, not yet flashed (needs the two-board bench kit) |
| Connectivity check page (`ui/`) | working on recordings and test scenarios; not yet run live on the car |
| Operator (human) events | not started; the table already has the columns |
| Stop and state tagging (A, B, C) | not started; columns exist, not populated |
| XES export (IEEE 1849) | not started |
| Dashboard | not started |

## Data note

`data/raw/bms_log_car_Bako.txt` is a capture from a BAKO vehicle. Check with
BAKO before pushing this repository anywhere public.
