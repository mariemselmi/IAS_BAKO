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
    sender/              ESP32 bench sender, replays the recorded log onto a bench bus
  tools/
    make_sender_frames.py   builds firmware/sender/frames.h from the recorded log
  tests/
    test_decoder.py      decoder checks against known frames from the real capture
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

## Status

| Part | State |
|---|---|
| Decoder, all 22 frames | working, 8/8 tests pass on real frames |
| Recorded log ingest | working, 3,197 frames -> 21,557 events, 0 unknown |
| Live serial ingest | tested with the recording standing in for the port; not yet run against a board |
| Receiver and sender firmware | written, not yet compiled or flashed |
| Operator (human) events | not started; the table already has the columns |
| Stop and state tagging (A, B, C) | not started; columns exist, not populated |
| XES export (IEEE 1849) | not started |
| Dashboard | not started |

## Data note

`data/raw/bms_log_car_Bako.txt` is a capture from a BAKO vehicle. Check with
BAKO before pushing this repository anywhere public.
