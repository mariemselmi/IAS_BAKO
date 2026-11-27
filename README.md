# bako_can

CAN data capture and event logging for the BAKO end-to-end production
monitoring project (IAS Challenge, TSYP14, Phase 2).

The code reads the BAKO vehicle's CAN bus, either from a recorded log or live
from an ESP32 gateway, translates every frame into readable values, and stores
them as events in a SQLite database. Each event has a case (the chassis
number), an activity (what was measured), a timestamp and a resource (which
part of the car sent it). That is the shape process-mining tools expect, and
it is the machine half of the hybrid build record described on the poster.

The repository now also holds the **software of the hybrid digital build record**
(`BAKO_Software_Specification.pdf`, draft v0.1): gateway firmware, backend,
operator/manager phone app and dashboard. See [Production monitoring software](#production-monitoring-software).

## What is in the repository

```
bako_can/
  backend/               production monitoring backend (FastAPI + SQLite), spec section 4.2
    main.py              REST API + WebSocket (spec 6.4), serves the app and the dashboard
    ingest.py            gateway TCP ingest: timestamps every line, binds it to chassis/session
    records.py           sessions, gateway binding, operator/manager events (I3)
    alerts.py            alert rules (Table 1), escalation, SMS requests to the gateway
    gaps.py              gap detection: missing BMS frames, gateway offline, unconfirmed operations
    kpi.py               KPI engine (Table 2) and analytics views
    reports.py           vehicle build record PDF, shift report, weekly PDF
    export.py            CSV and IEEE 1849 XES export
    static/app.html      operator / manager phone app (/app)
    static/dashboard.html  dashboard (/dashboard)
    static/twin.js       3D digital twin from the Check Station concept page, driven by real phase,
                         operation, anomaly and alert data; static/motion.css holds the shared motion
    static/theme.css     shared design tokens: neutral surfaces, colour for state only (pass, running,
                         fault), IBM Plex type with Barlow Condensed for large numerals
    static/vendor/       three.js r128 (MIT) and the fonts (SIL OFL), served locally so it all works offline
  config/reference.json  I4 reference data: route, expected CAN messages, thresholds, contacts,
                         stations, shifts, users, gateways (DRAFT route until BAKO sends theirs)
  firmware/gateway/      ISS board gateway: CAN + sensors + Wi-Fi uplink + local alerts/SMS + LEDs
  tools/gateway_sim.py   gateway simulator over TCP (replays the recording, injects faults)
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
    test_backend.py      end-to-end backend checks (ingest, alerts, gaps, KPIs, reports, exports)
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
| Backend (ingest, binding, event log, alerts, gaps, KPIs, reports, API, WebSocket) | working, 17/17 tests pass; run end to end with the simulator replaying the real recording |
| Operator / manager phone app (`/app`) | working in the browser; QR scan needs a browser with BarcodeDetector (Chrome on Android), otherwise type the chassis ID |
| Dashboard (`/dashboard`) | working: live view, vehicle history, analytics, alerts, reports, exports |
| Gateway firmware (`firmware/gateway`) | compiles (PlatformIO, ESP32); **not yet flashed or tested on the board**; pins, dividers, ACS712 variant, probe addresses and cellular module still to confirm |
| XES export (IEEE 1849) | working (`/api/export/xes`) |
| Stop and state tagging (A, B, C) | not started |

## Data note

`data/raw/bms_log_car_Bako.txt` is a capture from a BAKO vehicle. Check with
BAKO before pushing this repository anywhere public.

## Production monitoring software

Implements `BAKO_Software_Specification.pdf` (draft v0.1, 2026-11-23).

```
vehicle (BMS CAN, 12 V / 72 V lines, probes)
   -> gateway firmware (ISS board)  --TCP/Wi-Fi-->  backend (FastAPI + SQLite)  -->  dashboard
                 |  SMS                                   ^           |
                 v                                        |           v  SMS requests, config, time
             supervisor                    operator / manager app     gateway
```

### Run it without hardware

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m backend                     # HTTP on :8000, gateway TCP on :5000
```

On macOS port 5000 is taken by the AirPlay receiver; use `--tcp-port 5050` (and `--port 5050` for the simulator).

In a second terminal, start a simulated gateway (replays the real BAKO recording, plus board sensors):

```bash
.venv/bin/python tools/gateway_sim.py --fault-at 30 --overheat-at 60
```

- Phone app: `http://<pc-ip>:8000/app`. Pick a user at the top (manager to open sessions), scan or type a
  chassis ID, open the session with gateway `esp32-bms-001`, then start, finish and confirm operations.
- Dashboard: `http://<pc-ip>:8000/dashboard`.
- API docs: `http://<pc-ip>:8000/docs`.
- Tests: `.venv/bin/python -m pytest tests`.

Data lives in `var/` (database, build record PDFs, reports, anomaly photos).

### Flash the gateway (ISS board)

1. Edit `firmware/gateway/config.h`: `GATEWAY_ID`, Wi-Fi name and password, backend PC address and
   TCP port, and the board items still open (ADC divider ratios, ACS712 variant, LED pins, SIM pins).
   Do not commit real Wi-Fi passwords.
2. `cd firmware/gateway && pio run -t upload` (or Arduino IDE: open `gateway.ino`, board "ESP32 Dev
   Module", install OneWire and DallasTemperature).
3. Open the serial monitor at 115200 baud: copy the printed DS18B20 addresses into `PROBES` in
   `config.h` (spec open item 4) and flash again.
4. In the dashboard, Gateways should show the board as connected. `POST /api/gateways/<id>/config`
   pushes the thresholds and the supervisor phone number to it.

### What the backend does

| Spec | Implemented as |
|---|---|
| Ingest | TCP server; `HELLO`, `Extended ID: ...` frames (and the older `[ms] ID:` format), `SENSOR_JSON:`; every line timestamped on arrival |
| Binding | `gateway_bindings` (gateway, session, from, to); frames and measurements stored against the session the gateway is bound to at arrival time; a gateway moved to another car is re-bound from the app |
| Event log | `events` table, case = chassis, activity, timestamp, resource = station or gateway; operator ID kept separately |
| Gap detection | missing BMS message (3 x its period, min 2 s, only after `P3-BMS-CONNECT`), gateway offline (10 s), operation ended without confirmation (15 min), operations never confirmed at completion. A full gateway outage is one gap, not one per message |
| KPI engine | all 11 KPIs of Table 2, `GET /api/kpi?from&to&group_by` (never by individual) |
| Alerts | Table 1 rules, with hysteresis; escalation SMS when a critical alert is not acknowledged within 10 min |
| Reports | build record PDF at completion, shift report (web page + SMS summary) at the end of each shift, weekly PDF on Monday, CSV and XES on demand |
| O6 | backend -> gateway lines `SMS <id> <phone> <text>`, `TIME <unix>`, `CONFIG {json}` |

Additions to the spec's line protocol, both sent by the gateway: `ALERT:{"rule":..,"sms":true}` when it
raised an alert locally (marks the alert `sms_sent`), and `SMS_RESULT <id> OK|ERR` after an SMS request.

### Decisions to review

- **Who sends alert SMS.** The gateway evaluates the SMS rules and sends the SMS itself, as in the spec,
  so alerts still work when the backend is down. The backend evaluates the same rules for the dashboard,
  and only asks the gateway to send SMS for escalations and shift reports. Set `contacts.sms_mode` to
  `"backend"` to have the backend request every alert SMS instead.
- **CAN mode: normal, not listen-only.** The spec asks for listen-only, but the car test of
  2026-11-23 showed the BMS repeats one frame forever when no node acknowledges (no charger
  plugged in). The gateway therefore runs the MCP2515 in normal mode: it acknowledges frames but
  never sends a message of its own. Switch `CAN_MODE` to `MCP_LISTENONLY` in
  `firmware/gateway/config.h` only on a line where another node acknowledges.
- **Thresholds marked "to define"** are provisional in `config/reference.json`: cell spread 50 mV,
  session idle 60 min, escalation 10 min, unconfirmed operation 15 min.
- **Complete record** (traceability coverage) means: every route operation confirmed, no open anomaly,
  and CAN capture at least 95 %.
- **Roles.** Opening a session and declaring completion need a user with the manager role. There is no
  login yet: users pick their ID in the app (pilot on the plant network only).
- **Route.** `config/reference.json` holds a draft 7-phase route for model `BAKO-B1`; replace it with
  BAKO's real phases, operations, stations and target times (spec open item 5). `PUT /api/config` edits it.
