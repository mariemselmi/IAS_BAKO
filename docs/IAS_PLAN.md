# IAS PLAN: BAKO end-to-end production monitoring prototype

Single source of truth for the IAS Challenge (IEEE IAS, TSYP14) prototype work.
Last updated: 2026-11-23.

---

## 0. How to use this document

- **Read order for a new session:** section 1 (what we are building), section 4 (status),
  section 10 (next actions), then the work package you are touching.
- **IDs are stable.** Work packages are `A1`, `B2`, `C4`, ... Wow features are `W1`, ...
  Validation steps are `V1` to `V5`. Refer to them by ID in commits and messages.
- **Status tags:** `DONE` · `PARTIAL` · `TODO` · `BLOCKED-BAKO` (waits on BAKO input).
- **Update rule:** after each work session, update the status column, add a line to the
  decisions log (section 12) if anything was decided, and add any new gotcha to section 13.
- **Superseded file:** `context.md` describes an earlier framing ("verify each connection at
  the station"). The final poster replaced it. Use this plan and the poster instead.

---

## 1. What we are building (one paragraph)

**Problem (poster):** BAKO Motors cannot currently prove, measure or compare how each vehicle
was built. Production events, manual checks and CAN data are not joined to chassis identity,
so the cost of quality is unmeasurable. This is a compliance risk (ECE conformity of
production) and a scaling risk (~300 vehicles/year).

**Solution (poster, approach C):** a **hybrid digital build record**. One chronological record
per chassis that answers what happened, when, where, by whom and on which vehicle.
Human evidence (operator confirmations, tool readings, scans) covers the build before CAN
exists. A mobile CAN gateway adds machine evidence once the car powers up.
Tagline: **"CAN-centred, not CAN-only."**
Closing line: **"From quality that is good but unrecorded to quality that is provable."**

**Event log shape (process mining):** case = chassis, activity = operation,
timestamp, resource. Exported to IEEE 1849 (XES).

---

## 2. Files and where things live

| What | Path |
|---|---|
| Final poster (source of promises) | `C:\Users\dell\Desktop\IAS\BAKO_IAS_Poster_template-2.pdf` |
| Our code repository | `C:\Users\dell\Desktop\IAS\bako_can\` (git, branch `main`, local only so far) |
| ISS-board receiver firmware (flashed, working) | `bako_can\firmware\receiver_mcp2515\` |
| Fixed CAN library (bundled) | `bako_can\firmware\receiver_mcp2515\src\mcp_can\` |
| Python pipeline | `bako_can\src\` (decoder, replay, event store, ingest, serial ingest) |
| Real CAN log from the ISS team (recorded while charging) | `bako_can\data\raw\bms_log_car_Bako.txt` |
| Our first live car capture (not in git) | `bako_can\live.db` |
| BAKO CAN protocol v2 (partner doc) | `C:\Users\dell\Desktop\IAS\BAKO_CAN_Protocol_v2.docx` |
| ISS senior project (old repo, reference only) | `C:\Users\dell\Desktop\IAS\docs\Bako_OBD_ISS\Bako_OBD_ISS\` |
| Old UI prototype (3D twin, dashboard shell to reuse) | `C:\Users\dell\Desktop\IAS\BAKO Check Station.html` |
| Poster diagrams (SVG) | `C:\Users\dell\Desktop\IAS\fig-*.svg` |
| **Component connectivity checks (K01 to K25)** | `C:\Users\dell\Desktop\IAS\CONNECTIVITY_CHECKS.md` |
| **Station page: live battery connectivity check** | `bako_can\ui\connectivity_check.html` (run via `python -m http.server 8000`, open in Chrome or Edge) |
| BAKO internship reports (scanned PDFs, summarised in `context.md` section 8) | `report1.pdf`, `report2.pdf` |

---

## 3. Verified facts (measured, not assumed)

| Fact | Value | Source / date |
|---|---|---|
| CAN bus speed on the car | **250 kbit/s** | Live capture, 2026-11-23 |
| ISS board CAN module | MCP2515 module "HW-184", **8 MHz crystal** | Visual check, 2026-11-23 |
| CAN wires on the ISS board | Module's **blue screw terminal (H, L)**. Green CAN1/CAN2 are not connected to it | Board photo, 2026-11-23 |
| ISS board power | 12 V into the green "12V" terminal (J9). Serial over USB at **115200** baud | 2026-11-23 |
| Where we tapped | Battery's CAN line (appears to be the battery-to-charger line) | 2026-11-23 |
| What is on that line | **Battery (BMS) frames only** | Live capture + ISS firmware analysis |
| Bus mode needed there (no charger) | **MCP_NORMAL** (board acknowledges). Listen-only makes the battery repeat one frame forever | 2026-11-23 |
| Live capture quality | 74 s, 1,852 frames, 25.0 frames/s, **~100 %** of every frame type, 0 errors | `live.db`, 2026-11-23 |
| Battery frame rates | Pack + cell min/max 10 Hz, temperatures 2 Hz, charger request 1 Hz, each of 5 cell-voltage frames 0.4 Hz | `live.db` |
| Battery state on 2026-11-23 | 60.7 V, 12 % SOC, cells 3,192 to 3,202 mV, 24 to 25 °C, **fault level 2, error code 27 (meaning unknown)** | `live.db` |
| Handbrake, DC-DC, MPPT, GPS | **Not CAN devices** on today's car (plain wires). The `0x18Dxx01AA` frames in the BAKO doc were produced by the ISS board from its own analog inputs | Car photos + ISS firmware v2.0 |
| Motor controller (MCU) | **On CAN via the OBD-II port** (pin 6 CAN-H, pin 14 CAN-L). Frame `0x0CF181EF`: bus voltage, current, motor and MCU temperature, relay and precharge states, direction, faults. Sample: bus 59.7 V, motor 34 °C | `report1.pdf` (ISS team on a BAKO Bee). **Not yet verified by us** |
| Vehicle variants | BAKO Bee ~60 V system, B-Van 76.8 V | `report1.pdf`, `report2.pdf` |
| Build sequence | Chassis and body → mechanical and electrical installation → front and rear harness → electrical integration → final testing | `report1.pdf` |
| Decoder byte layouts | Repo layouts verified on real frames. The BAKO v2 doc is wrong for the battery frames, right for the auxiliary frames | Decoding the real log |
| Pipeline correctness | 8/8 decoder tests; replay 3,197 frames → 21,557 events, 0 unknown | `bako_can\tests` |

---

## 4. Poster promises → status

| Poster promise | Work package | Status |
|---|---|---|
| One chronological record per chassis | A1, B1 | TODO |
| Manager opens a build session; QR scan creates the record | B1, B2, B3 | TODO |
| Operation events, confirmations, tool readings (evidence before CAN) | B1, B2 | TODO |
| Configurable route (low volume, high mix) | A2 | BLOCKED-BAKO |
| Gateway tags every frame with chassis, session and phase | C4 | TODO |
| Listen-only, never disturb the vehicle bus | C5 | PARTIAL (see gap G1) |
| Raw frames retained | A1 | TODO (see gap G2) |
| Gateway-server time synchronised | C3 | TODO |
| Buffering during network loss | C2 | TODO |
| Anomaly → intervention → recheck recorded | A1, B1, C7 | TODO |
| Automatic cycle time and waiting time | D1 | TODO |
| Recorded declaration "Production complete, ready for verification" | B1, B2 | TODO |
| Export to IEEE 1849 XES | D2 | TODO |
| Analytics never rank individuals | D1, D3, E1 | TODO |
| Every electrical component checked; anything not connected is caught (team goal, 2026-11-23) | H1 to H8 | TODO (battery K01 to K03 readable today) |
| KPI: CAN capture rate 95 to 100 % | C1, V2 | **DONE on USB (~100 %)**, redo over Wi-Fi |
| KPI: traceability coverage 95 to 100 % | D1 | TODO |
| KPI: data gaps flagged 100 % | C6 | PARTIAL (station page) |
| KPI: lead time per vehicle calculated automatically | D1 | TODO |
| KPI: rework ratio measured for the first time | D1 | TODO |
| Gateway ~150 to 250 TND per unit | hardware | DONE (ISS board ~240 TND BOM) |
| V1 log replay | V1 | **DONE** |
| V2 bench test (bit rate, network loss, buffering) | V2 | PARTIAL (bit rate done) |
| V3 one-chassis pilot | V3 | TODO |
| V4 cross-check against an independent instrument | V4 | TODO |
| V5 multi-chassis pilot | V5 | TODO |

---

## 5. Gaps between the poster and reality (must be addressed, a judge can ask)

| ID | Gap | Plan |
|---|---|---|
| G1 | Poster says listen-only. On the car, listen-only failed: nothing acknowledges on the battery line without the charger, so we used normal mode | C5: test listen-only with the charger plugged in; ask BAKO about a second CAN line; document the rule honestly in the report |
| G2 | Poster says raw frames retained. We store decoded values only | A1: add a raw-frames table |
| G3 | On CAN: the battery (charger line) and the motor controller (OBD-II port). Handbrake, DC-DC, MPPT, GPS and the 12 V loads are not on CAN | Group H: ISS board sensor inputs, the current-signature test, tool readings and operator confirmations. See `CONNECTIVITY_CHECKS.md` |
| G4 | Poster says "mobile gateway" with network transport. Today it is USB to a laptop | C1 to C3 |

---

## 6. Target architecture

```
 OPERATOR PHONE / TABLET            CAR (state C, after safe power-up)
 scan QR, tap confirmations,        battery CAN line
 tool readings, flag anomaly              |
        |                           GATEWAY (ESP32 + MCP2515, ISS board)
        |                           decode-ready frames, buffer, time sync
        | HTTPS                            | Wi-Fi
        v                                  v
 +--------------------------------------------------------------+
 | SERVER (Python, FastAPI)                                      |
 |  sessions + route  |  event store (SQLite)  |  raw frames      |
 |  CAN checks -> anomalies  |  gap detection  |  KPI engine      |
 |  XES export -> process discovery (pm4py)                       |
 +--------------------------------------------------------------+
        |
        v
 MANAGER DASHBOARD: live timeline, 3D twin, KPIs, process map, build certificate
```

---

## 7. Work packages

### A. Build record core (everything depends on this)

| ID | Work | Done when | Depends on | Status |
|---|---|---|---|---|
| A1 | **Event model v2:** `sessions` table (one per chassis), `phase` and `state` (A/B/C) on every event, event `kind` (operation, confirmation, tool_reading, scan, anomaly, intervention, recheck, can_signal, gap), `raw_frames` table | Schema + tests; existing CAN ingest writes into it | none | TODO |
| A2 | **Configurable route:** one file listing phases and operations in order, each tagged with state and evidence type (CAN or human) | Route loads, validates, drives the record | BAKO station list | BLOCKED-BAKO (start with a draft route) |

### B. Traveller layer (human evidence, poster says ship first)

| ID | Work | Done when | Depends on | Status |
|---|---|---|---|---|
| B1 | **Server API:** open session (manager), record operation / confirmation / tool reading, anomaly → intervention → recheck, close with "Production complete, ready for verification" | Every action creates an event with server time | A1 | TODO |
| B2 | **Operator web app** (phone/tablet): scan chassis QR, one-tap confirmations, tool readings, flag anomaly; manager view to open and close sessions | A full route can be walked from a phone | B1, A2 | TODO |
| B3 | **QR labels** for chassis (and stations if useful) | Printable sheet generated | A2 | TODO |

### C. CAN layer (machine evidence)

| ID | Work | Done when | Depends on | Status |
|---|---|---|---|---|
| C1 | **Wi-Fi transport:** gateway sends frames to the server with a gateway ID | Live frames reach the server with no laptop on the car | none | TODO |
| C2 | **Buffering:** ring buffer on the ESP32 while Wi-Fi is down, resend with sequence numbers, server drops duplicates | 60 s Wi-Fi cut, zero frames lost | C1 | TODO |
| C3 | **Time sync:** gateway clock aligned to the server | Measured offset under a stated bound | C1 | TODO |
| C4 | **Session binding:** plugging a gateway into a car links it to that chassis's session; frames tagged with chassis, session, phase | Frames land in the right car's record automatically | C1, B1 | TODO |
| C5 | **Bus-mode rule** (gap G1): test listen-only with charger connected, confirm whether a second CAN line exists, write the rule + safety note | Rule tested and documented | BAKO | PARTIAL |
| C6 | **Gap detection:** expected rate per frame type; silence beyond it creates a `gap` event | Pull the CAN wire 10 s → gap flagged every time | A1 | PARTIAL (in the station page for battery frames; tested on simulated dropouts, not yet on the car) |
| C7 | **CAN checks per phase:** battery present, pack voltage in range, cell spread, fault codes. Failed check → `anomaly` event | Today's fault level 2 would raise an anomaly | A1, A2 | PARTIAL (14 battery checks in the station page; fault level 2 raises an anomaly; limits partly provisional; not yet in the server) |

### D. Analytics

| ID | Work | Done when | Depends on | Status |
|---|---|---|---|---|
| D1 | **KPI engine:** lead time per vehicle, cycle and waiting time per station, rework ratio, traceability coverage, CAN capture rate, gaps flagged. **By station, never by individual** | Every poster KPI computed from the database | A, B, C | TODO |
| D2 | **XES export (IEEE 1849) + process discovery** (pm4py in Python; Disco or ProM as alternatives) showing rework loops | Process map produced from a real record | A1 | TODO |
| D3 | **Manager dashboard:** cars in progress, full history per car, station KPIs, rework, gaps. Reuse the Check Station shell | "How was car X built?" answered in under 1 minute (poster baseline: 1 to 2 h) | D1 | TODO |

### E. Privacy and safety

| ID | Work | Done when | Depends on | Status |
|---|---|---|---|---|
| E1 | **Privacy (Tunisian Law 2004-63):** operator identity kept for traceability but pseudonymised; no screen ranks people; manager and operator roles | Checked against dashboard and exports | B, D | TODO |
| E2 | **Safety procedure:** connect the gateway only in state C after safe power-up; what not to touch (HV, orange cables) | One-page procedure agreed with BAKO | C5 | TODO |

### H. Connectivity checks (every component; full matrix in `CONNECTIVITY_CHECKS.md`)

Goal: prove every electrical component is connected and working, and catch anything that is not.
Each check (K01 to K25) becomes an event; a failure starts problem found → fix → recheck.

| ID | Work | Done when | Depends on | Status |
|---|---|---|---|---|
| H1 | **Component list per variant** (Bee, B-Van) confirmed with BAKO; complete K01 to K25 | Matrix reviewed by BAKO | Q11 | BLOCKED-BAKO |
| H2 | **OBD-II motor-controller CAN** (`CAN-M`): find what is on that bus, add a second MCP2515 or tap point, decode `0x0CF181EF` | MCU frames decoded and stored with the battery frames | Q7, Q8 | TODO |
| H3 | **Board sensor firmware** (`BOARD`): read DC in, DC out, MPPT in, HB, 2 current inputs, 3 temperature probes; verify the ACS712 type; calibrate against a multimeter | Each input reads within a stated error of the meter | none | TODO |
| H4 | **Current-signature test:** one current sensor on the 12 V DC-DC output; guided load sequence; compare each current step with its expected value | Unplugging one lamp is detected every time | H3, B2, Q10 | TODO |
| H5 | **Check rules per component** (extends C7): pass limits per check and per variant | Every K check has a rule and produces pass/fail | A2, H1 | TODO |
| H6 | **Guided check sequences in the operator app:** tool readings and confirmations for states A and B (earth resistance, connector push-pull, continuity) | A full state A/B check list can be done from the phone | B2 | TODO |
| H7 | **HV isolation test procedure** (K07), BAKO staff only | Procedure and recorded result format agreed | E2 | TODO |
| H8 | **Cross-checks between sources:** MCU bus voltage vs BMS pack voltage; dashboard charge level vs BMS | Mismatch raises a problem event | H2, B2 | TODO |

### F. Validation (poster's five steps)

| ID | Step | Done when | Depends on | Status |
|---|---|---|---|---|
| V1 | Log replay through the pipeline | 3,197 frames, 0 unknown | none | **DONE** |
| V2 | Bench test: bit rate, network loss, buffering | Bit rate confirmed; Wi-Fi cut with zero loss | C1, C2 | PARTIAL |
| V3 | **One-chassis pilot:** one car from first operation to "ready for verification", human + CAN events in one record | Complete record + XES + process map | A, B, C4, C6, D3 | TODO |
| V4 | **Cross-check:** CAN pack voltage vs multimeter; battery temperature vs thermometer | Values agree within stated tolerance, with photo | none | TODO (quick) |
| V5 | Multi-chassis pilot: 2 cars, 2 gateways at once | Two records built concurrently | all above + 2nd gateway (or a clearly labelled simulated one) | TODO |

### G. Deliverables required by the challenge

Technical report (5 to 7 pages), technical presentation, demo video, user manual,
architecture diagram, GitHub repository (required for finalists). Built from V1 to V4 results
and D2's process map.

---

## 8. Wow layer (the sophisticated end product)

Built **on top of** the core work packages, not instead of them. Every wow feature uses real
data from the record, nothing faked. Ranked by impact for a jury.

| ID | Feature | Why it impresses | Depends on |
|---|---|---|---|
| W1 | **Live build timeline:** one car's record grows in real time as operators tap and CAN frames arrive, drawn as the same swimlanes as the poster's AS-IS map (manager, operators, vehicle state, tools/CAN, records) | Shows AS-IS → TO-BE live: the empty "records" lane on the poster fills up in front of the jury | B1, C4 |
| W2 | **3D digital twin with live battery heatmap:** reuse the 3D car from `BAKO Check Station.html`; parts appear as phases complete; the battery shows **19 cells coloured by live cell voltage from CAN**; zones turn green / amber / red from real events | Real CAN data on a 3D model is instantly understandable to non-experts | B1, C4, C7 |
| W3 | **"Prove it" build certificate:** one click per chassis generates a PDF with the complete history, checks, rework and the recorded declaration | Directly delivers the poster's closing line: quality that is provable | D3 |
| W4 | **Live process map:** pm4py rediscovers the process from the event log and highlights rework loops and bottlenecks | Ties the demo to the research angle (process mining) | D2 |
| W5 | **Anomaly alert flow:** a failed CAN check (e.g. fault level 2) pops up on the operator's phone and walks them through intervention → recheck | Shows the system acting, not just recording | B2, C7 |
| W6 | **KPI wall:** big-screen manager view, each poster KPI shown measured vs target | Answers "did you deliver what you promised?" at a glance | D1 |
| W7 | **Signal health strip:** per-frame-type heartbeat with gaps highlighted | Makes "100 % data gaps flagged" visible | C6 |
| W8 | **Demo replay mode:** replay a recorded pilot or the real log at 1x to 10x speed | Insurance: the demo works even if the car or Wi-Fi fails on the day | A1 |
| W9 | **Car connectivity map:** the car drawn with every component K01 to K25 lit green, amber or red from its latest check; click a component to see its readings and history | One picture answers "is everything connected?" | H5 |

**Priority if time is short:** W1, W2, W3 give most of the effect. W8 protects the demo.

---

## 9. Explicitly optional (not promised on the poster)

- Cellular uplink (SIM800L on the ISS board).
- (Moved into scope on 2026-11-23: the ISS board sensor terminals, now work package H3.)

---

## 10. Build order (critical path)

1. **V4** cross-check and **C5** questions to BAKO (cheap, remove risk).
2. **A1** event model v2, then **A2** route (draft first, real list from BAKO).
3. **B1** API, **B2** operator app, **B3** QR labels.
4. **C1** Wi-Fi, **C2** buffering, **C3** time sync, **C4** session binding.
5. **C6** gap detection, **C7** CAN checks.
6. **H2** motor-controller CAN, **H3** board sensors, **H5** check rules, **H6** guided checks,
   **H4** current-signature test, **H8** cross-checks (H1 and H7 with BAKO).
7. **D1** KPIs, **D2** XES + process map, **D3** dashboard.
8. **V2** bench test, then **V3** one-chassis pilot = working prototype.
9. **W1, W2, W3, W9** (then W4 to W8).
10. **E1, E2**, **V5**, deliverables (G).

**Next action:** A1, designed together (agree event kinds and fields first, then build).
**Quick experiment worth doing first (H2, G1, Q8):** plug the board into the OBD-II port
(pin 6 H, pin 14 L, ground) in listen-only mode and record which frame IDs appear.
If both battery and motor-controller frames show up without repeats, one tap covers both and
listen-only works there.

---

## 11. Open questions for BAKO

| ID | Question | Blocks |
|---|---|---|
| Q1 | What does battery fault level 2, error code 27 mean? Should the car be charged (12 % on 2026-11-23)? | C7, safety |
| Q2 | Does the battery have a second CAN line to the dashboard or motor controller, separate from the charger line? | C5, gap G1 |
| Q3 | The real list of stations / phases and operations, in order | A2 |
| Q4 | Permission and a slot for the one-chassis pilot | V3 |
| Q5 | Which pins on the battery CAN connector are CAN-H, CAN-L and ground (for a clean, documented tap) | E2 |
| Q6 | Source for "~300 vehicles/year" used on the poster | report |
| Q7 to Q12 | Connectivity questions: motor-controller signal Excel, what is on the OBD-II bus, final-test checklist, 12 V loads and currents, component list per variant, critical connectors | group H (listed in `CONNECTIVITY_CHECKS.md` section 6) |

---

## 12. Decisions log

| Date | Decision |
|---|---|
| 2026-11-10 | Start from scratch, software first. Python + SQLite. Repo `bako_can`. |
| 2026-11-16 | Use the ESP32 with a 3.3 V transceiver for new builds; the ISS board (MCP2515) for now. |
| 2026-11-23 | ISS board runs our minimal receiver (`receiver_mcp2515`), not the ISS gateway firmware (which blocks and drops frames). |
| 2026-11-23 | Crystal set to 8 MHz, bus 250 kbit/s. |
| 2026-11-23 | Bus mode `MCP_NORMAL` on the battery line when no charger is connected (gap G1 to resolve). |
| 2026-11-23 | Fixed `mcp_can` 1.5.1 copy bundled in the sketch (DLC overflow crash). |
| 2026-11-23 | Plan organised by work package, not by dates. |
| 2026-11-23 | Built `ui/connectivity_check.html`: the BAKO Check Station idea on real data. Reads the board over Web Serial, runs 14 battery checks (K01 to K03) plus a charge-port test (K22), signal-health table (W7 seed), coverage chips K01 to K25 (W9 seed), records sessions, checks, anomalies, interventions and rechecks, exports JSON, CSV and raw log. Records stay in the browser until A1 adds the server. |
| 2026-11-24 | Station page redesigned to industrial operator-screen style (ISA-101: grey base, colour only for results, one primary action, test-step table, status bar). Added connection map (W9 seed), bus timing lanes with gap shading (W7), "What to fix" list, build record tab. Design rules and the generic-UI checklist in `bako_can\ui\UI_REVIEW.md`. No web fonts, so it works offline at the plant |
| 2026-11-23 | Scope extended: check the connectivity of every electrical component (group H, `CONNECTIVITY_CHECKS.md`). Uses five methods: battery CAN, motor-controller CAN (OBD-II), ISS board sensors, tool readings, operator confirmations. |

---

## 13. Gotchas and lessons learned

- **Crystal mismatch:** firmware set for 16 MHz on an 8 MHz module halves the real bit rate. The board still starts normally on the desk; you only see it on the car.
- **No acknowledgement = endless repeats:** a CAN sender with nobody acknowledging retries one frame forever, and other frames never appear. Fix: another node on the line (charger) or `MCP_NORMAL`.
- **`mcp_can` 1.5.1 crash:** the length field can read 9 to 15; the library copies that many bytes into an 8-byte buffer and corrupts its SPI pointer. Crash shows `EXCCAUSE 0x1c` with a garbage address. Fixed copy bundled.
- **Decode crash backtraces** with the IDE's tools: `xtensa-esp32-elf-addr2line.exe` from `Arduino15\packages\esp32\tools\esp-x32\...` against the sketch's `.elf` in `AppData\Local\arduino\sketches\...`.
- **Compile without uploading:** the Arduino IDE bundles `arduino-cli.exe` in `AppData\Local\Programs\Arduino IDE\resources\app\lib\backend\resources\`.
- **Upload stuck on "Connecting...":** hold BOOT; check the port in Device Manager; close the Serial Monitor.
- **Only one program can use the serial port:** close the Serial Monitor before `serial_ingest.py`.
- **`serial_ingest.py` baud:** the ISS-board sketch prints at 115200, so pass `--baud 115200` (default is 921600 for the plain ESP32 sketch).
- **Termination jumper J1** (yellow, on the CAN module): off when tapping a line that is already terminated; on when our board is one end of a two-device line.
- **The ISS board arrived running a test simulator** (fake data + SIM800L), not a CAN reader. Always check the boot banner.
- **Never use the HV battery negative as ground.** Use the 12 V side only.

---

## 14. Working agreements (how the user wants to work)

- Pair-program: design together, the user builds parts, Claude reviews. Avoid handing over large finished pieces unasked.
- "How do I X" means give steps, not run X on the user's machine.
- Plain, simple language. Visual aids help.
- Report prose: explanatory, plain words, no em dashes.
- Commit only when asked. Check with BAKO before anything public (the repo contains BAKO data).
