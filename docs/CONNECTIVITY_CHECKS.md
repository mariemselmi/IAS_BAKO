# CONNECTIVITY CHECKS: every component, how we prove it is connected

Companion to `IAS_PLAN.md` (work package group H). Last updated: 2026-11-23.

**Goal:** for every electrical component of the car, record evidence that it is connected
and working, and detect anything that is not connected as it should be. Every check becomes
an event in the build record. A failed check starts **problem found → fix → recheck**.

**Status of this document:** a draft built from what we have seen (car photos, CAN captures,
BAKO protocol doc, ISS firmware, `report1.pdf`, `report2.pdf`). The component list, expected
values and thresholds must be confirmed with BAKO (questions at the end).

---

## 1. The five ways we can check something

| Tag | Method | What it sees | Ready? |
|---|---|---|---|
| `CAN-B` | **Battery CAN line**, read by our gateway | Battery management system: pack, 19 cells, 4 temperatures, faults, contactors, charge-cable flag | **Working** (2026-11-23, ~100 % capture) |
| `CAN-M` | **OBD-II port CAN** (pin 6 H, pin 14 L), read by our gateway | Motor controller (MCU): bus voltage and current, motor and controller temperature, relay and precharge states, direction, running, faults. Frame `0x0CF181EF` | Shown working by the ISS team on a BAKO Bee (`report1.pdf`). **We need BAKO's signal Excel** to decode it |
| `BOARD` | **ISS board sensor inputs** | DC in (up to ~72 V), DC out (12 V), MPPT in, HB (handbrake, 12 V level), 2 current inputs (ACS712), 3 temperature probes (DS18B20) | Hardware exists. **Firmware does not read them yet** |
| `TOOL` | **Handheld instrument**, value typed into the app by the operator | Multimeter (resistance, continuity, voltage), clamp meter (current), insulation tester (BAKO only) | Needs the operator app (B2) |
| `OPER` | **Operator confirmation**, guided by the app | Things no sensor sees: connector seated, display boots, GPS LED on | Needs the operator app (B2) |

Each check is stored with: car, time, check ID (below), method, value, expected range,
pass/fail, stop, state (A/B/C), who or what.

---

## 2. Build states and when each method becomes possible

Build sequence from `report1.pdf`: chassis and body → mechanical and electrical installation
→ front and rear harness assembly → electrical integration → final testing.

| State | Stage | Car powered? | Methods available |
|---|---|---|---|
| A | Bare chassis, body | No | `TOOL`, `OPER` |
| B | Mechanical and electrical installation, harness assembly | No | `TOOL`, `OPER` |
| C | Electrical integration, final testing | **Yes**, after safe power-up | `CAN-B`, `CAN-M`, `BOARD`, `TOOL`, `OPER` |

---

## 3. Component check matrix

### Power path (high voltage side)

| ID | Component | State | How we check | Pass when | A failure points to |
|---|---|---|---|---|---|
| K01 | Battery pack and BMS | C | `CAN-B` | Summary frame at 10 Hz; 19 cells and 4 temperature sensors reporting; pack voltage and cells in range; cell spread under limit; fault level 0 | No frames: BMS CAN connector or BMS power. A missing cell: cell sense wire. A fault code: battery fault |
| K02 | Battery CAN link | C | `CAN-B` | All 9 battery frame types at their nominal rates; receive errors 0; no gaps | Loose CAN connector, termination, wiring |
| K03 | Main contactors (BMS side) | C | `CAN-B` | Charge and discharge contactor flags closed when expected | Contactor not closing, contactor wiring |
| K04 | HV path to the motor controller (breaker, fuses, precharge, main relay) | C | `CAN-M` + `CAN-B` | MCU bus voltage within a few volts of the BMS pack voltage; precharge and relay states as expected | Bus voltage 0: breaker off, blown fuse, relay or HV connector open |
| K05 | Motor controller (MCU) | C | `CAN-M` | Frame `0x0CF181EF` present at its rate; controller working status OK; no MCU fault | No frame: MCU power, MCU CAN wiring, OBD connector. Fault: MCU or its inputs |
| K06 | Motor and motor temperature sensor | C | `CAN-M` + `OPER` action | Motor temperature plausible (not absent); on a stand test, running and direction follow the operator's input | Temperature absent: sensor unplugged. No motion: motor phase or encoder wiring |
| K07 | HV isolation (HV parts to chassis) | B/C, before power-up | `TOOL` (insulation tester), **BAKO only** | Isolation resistance above the UN R100 minimum (100 ohm per volt DC) | Insulation damage, pinched cable, moisture |

### Low-voltage supply (12 V side)

| ID | Component | State | How we check | Pass when | A failure points to |
|---|---|---|---|---|---|
| K08 | DC-DC converter (HV to 12 V) | C | `BOARD` (DC in, DC out) | Input near pack voltage; output 13.2 to 14.4 V | Output 0: DC-DC input not connected, DC-DC fault, output connector |
| K09 | 12 V bus, relay block, low-voltage fuses | C | `BOARD` (DC out + current) | 12 V present; current steps appear when loads switch (section 4) | No step for one load: that fuse, relay or connector |
| K10 | Chassis earth and ground straps | A/B | `TOOL` (resistance) | Under 0.1 ohm between earth point and battery 12 V negative | Loose bolt, paint under the lug, missing strap |
| K11 | Front and rear harness connectors | B | `OPER` (push-pull test) + `TOOL` (continuity on critical circuits) | Every listed connector confirmed seated; critical circuits continuous | Unseated connector: the plant's most frequent defect |

### 12 V loads (checked with the current-signature test, section 4)

| ID | Component | State | How we check | Pass when | A failure points to |
|---|---|---|---|---|---|
| K12 | Headlights, left and right (low, high) | C | `BOARD` current step + `OPER` | Current step within expected range per lamp | Step too small: one lamp out or connector loose. No step: fuse, relay, switch |
| K13 | Tail and position lights | C | `BOARD` + `OPER` | Expected step | Same |
| K14 | Brake lights (also proves the brake switch) | C | `BOARD` + `OPER` (press brake) | Expected step when the pedal is pressed | No step: brake switch or lamp circuit |
| K15 | Indicators and hazard | C | `BOARD` + `OPER` | Expected flashing step | Flasher relay, lamp, connector |
| K16 | Horn | C | `BOARD` + `OPER` (press horn) | Step above about 2 A | Horn connector, relay, button |
| K17 | Wiper and washer pump | C | `BOARD` + `OPER` | Expected step | Motor or pump connector |
| K18 | Dashboard display | C | `OPER` + data cross-check | Boots, and the charge level shown equals the BMS value on `CAN-B` | Blank: display power or connector. Wrong value: data link to the display |

### Switches and driver inputs

| ID | Component | State | How we check | Pass when | A failure points to |
|---|---|---|---|---|---|
| K19 | Handbrake switch | C | `BOARD` (HB) + `OPER` action | HB input toggles 0 V and 12 V as the lever is pulled and released | No toggle: switch unplugged or misadjusted |
| K20 | Ignition / key, throttle, direction selector | C | `CAN-M` + `OPER` action | MCU direction and control-mode signals follow the operator's actions | Signal does not change: that input's wiring |

### Solar and charging

| ID | Component | State | How we check | Pass when | A failure points to |
|---|---|---|---|---|---|
| K21 | Solar panel and MPPT | C | `BOARD` (MPPT in + current) | Panel voltage above about 15 V in daylight; charge current above about 0.3 A; correct polarity | 0 V: panel connector. Negative: connector reversed (in BAKO's known fault list) |
| K22 | Charge port and on-board charging | C | `CAN-B` (plug the charger in) | Charge-cable flag and charging flag set; pack current negative | Flag not set: charge-port wiring or detection line |

### Other

| ID | Component | State | How we check | Pass when | A failure points to |
|---|---|---|---|---|---|
| K23 | GPS module | C | `OPER` (power or fix LED) | LED on, fix within a set time | Power or antenna connector |
| K24 | Temperature probes on DC-DC, MPPT, motor (if fitted) | C | `BOARD` (DS18B20) | Each probe reads a plausible value | Probe missing or unplugged |
| K25 | Gateway itself | C | system | Gateway online, time synced, buffer empty | Our own equipment, not the car |

---

## 4. The current-signature test (one sensor checks every 12 V load)

The ISS board has only two current inputs, so we cannot put a sensor on every lamp.
Instead, one current sensor goes on the **DC-DC 12 V output**, which feeds every load:

1. The app guides the operator: "switch on left headlight", "press horn", ...
2. The board measures the **jump in total current** at each step.
3. Each jump is compared with the expected current for that load.

A jump that is missing or too small means that load, its fuse, relay or connector is not
connected properly. One sensor, one guided sequence, every 12 V load covered (K12 to K17).
Expected currents per load must come from BAKO or be measured on a known-good car first.

---

## 5. Hardware and software needed

| Need | For | Notes |
|---|---|---|
| Second MCP2515 module on the ISS board (headers J17 to J19 exist) or a second gateway | `CAN-M` on the OBD-II port | First test whether the battery is also on the OBD-II bus (see Q8): then one tap covers both |
| OBD-II plug and cable | `CAN-M` | Pins 6 (H), 14 (L), plus ground |
| Firmware that reads the ISS board inputs | `BOARD` | The ISS gateway firmware already has the formulas (12 V and 72 V dividers, ACS712, DS18B20); port them into our receiver |
| Check that the fitted ACS712 matches the firmware | `BOARD` | BOM says ACS712 30 A (66 mV/A) but the ISS code assumes 100 mV/A (20 A version). Read the chip marking |
| Calibration against a multimeter | `BOARD` | Each input, against a known value |
| Guided check sequences in the operator app | `TOOL`, `OPER`, section 4 | Part of B2 |
| Check rules engine | all | Part of C7: thresholds per check, per vehicle variant |

**Vehicle variants:** BAKO Bee uses about 60 V, the B-Van 76.8 V (`report1.pdf`, `report2.pdf`).
Thresholds must be stored per variant in the route (A2).

**Safety:** DC in and MPPT in carry up to ~72 V (more on a B-Van). The insulation test (K07)
and any connection to HV points are done by BAKO staff only. Never use the HV battery negative
as ground.

---

## 6. Questions for BAKO (this document)

| ID | Question |
|---|---|
| Q7 | The **motor controller signal Excel** (conversion rules for `0x0CF181EF` and any other MCU frames) |
| Q8 | Which modules are on the **OBD-II port's CAN bus**? Is the battery on it too, or only on the charger line? |
| Q9 | The **final-test checklist**: what is tested today, with which instruments and limits |
| Q10 | The list of **12 V loads** and their expected currents (or a known-good car to measure) |
| Q11 | The full **component list per variant** (Bee, B-Van), so K01 to K25 can be completed |
| Q12 | Which connectors are considered **critical** for the push-pull and continuity checks (K11) |
