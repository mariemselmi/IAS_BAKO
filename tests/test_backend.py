"""End-to-end checks for the backend: ingest, binding, events, alerts, gaps,
KPIs, reports and exports. The server clock is frozen and moved by hand."""

from __future__ import annotations
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend import clock  # noqa: E402
from backend.ingest import parse_line  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.runtime import Runtime  # noqa: E402

GW = "esp32-bms-001"
OK_PACK = "00 56 8C 13 60 02 00 00"       # SOC 86 %, 0.4 A, 60.8 V, no fault
FAULT_PACK = "00 56 8C 13 60 02 02 1B"    # fault level 2, error 27
EXTREMES = "44 0D 36 0D 3D 3B E8 03"      # spread 14 mV
CELLS = {0x18C828F4: "0D 3F 0D 38 0D 3A 0D 39", 0x18C928F4: "0D 3F 0D 38 0D 3A 0D 39",
         0x18CA28F4: "0D 3F 0D 38 0D 3A 0D 39", 0x18CB28F4: "0D 3F 0D 38 0D 3A 0D 39",
         0x18CC28F4: "0D 3A 0D 3C 0D 36 00 00"}
SENSORS = ('SENSOR_JSON:{"v12_dc_out":13.42,"v72_dc_in":61.8,"v72_mppt_in":0.0,"current_in":0.0,'
           '"current_out":0.0,"temp_mppt":24.5,"temp_dcdc":26.1,"temp_motor":23.9,"handbrake":1}')


def frame(can_id: int, hexdata: str) -> str:
    return f"Extended ID: 0x{can_id:08X} DLC: 8 Data: " + " ".join(f"0x{b}" for b in hexdata.split())


@pytest.fixture()
def env(tmp_path):
    clock.freeze(datetime(2026, 12, 2, 8, 0, 0, tzinfo=timezone.utc))  # 09:00 Tunis, shift A
    rt = Runtime(tmp_path / "t.db", data_dir=tmp_path / "var")
    app = create_app(rt, start_background=False)
    with TestClient(app) as client:
        yield rt, client
    clock.freeze(None)
    rt.db.close()


def stream(rt: Runtime, seconds: float, pack: str = OK_PACK, sensors: str = SENSORS, bms: bool = True) -> None:
    """Feed the gateway at nominal rates for `seconds`, 100 ms steps."""
    steps = int(seconds * 10)
    for i in range(steps):
        if bms:
            rt.ingest.handle_line(GW, frame(0x18FF28F4, pack))
            rt.ingest.handle_line(GW, frame(0x18FE28F4, EXTREMES))
            if i % 5 == 0:
                rt.ingest.handle_line(GW, frame(0x18B428F4, "3B 3B 3D 3D FF FF FF FF"))
            if i % 10 == 0:
                rt.ingest.handle_line(GW, frame(0x18FFE5F4, "B5 02 5E 01 00 00 00 00"))
            if i % 25 == 0:
                for cid, d in CELLS.items():
                    rt.ingest.handle_line(GW, frame(cid, d))
        if i % 20 == 0 and sensors:
            rt.ingest.handle_line(GW, sensors)
        clock.advance(0.1)
        if i % 10 == 9:
            rt.tick()
    rt.ingest.flush()


def ev(client, **body):
    r = client.post("/api/events", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def do_op(client, rt, session, code, minutes=1.0, value=None, result="ok", live=False):
    """One operation. live=True streams gateway data while it runs (BMS powered)."""
    ev(client, type="operation_start", session=session, operation=code)
    if live:
        stream(rt, minutes * 60)
    else:
        clock.advance(minutes * 60)
    ev(client, type="operation_end", session=session, operation=code, result=result, value=value,
       confirmed_by="op-07")


# ---- parser ------------------------------------------------------------------------------

def test_parse_lines():
    assert parse_line("HELLO esp32-bms-001").gateway == "esp32-bms-001"
    f = parse_line("Extended ID: 0x18FF28F4 DLC: 8 Data: 0x00 0x56 0x8C 0x13 0x60 0x02 0x00 0x00")
    assert f.kind == "frame" and f.can_id == 0x18FF28F4 and f.data == bytes.fromhex("00568C1360020000")
    f = parse_line("[11455ms] ID: 0x98FE28F4  DLC: 8 Data: 44 0D 36 0D 3D 3B E8 03")
    assert f.kind == "frame" and f.can_id == 0x18FE28F4  # priority bits masked
    assert parse_line(SENSORS).json["v12_dc_out"] == 13.42
    assert parse_line("Extended ID: 0x18FF28F4 DLC: 8 Data: 0x00").kind == "invalid"
    assert parse_line("SENSOR_JSON:{broken").kind == "invalid"
    assert parse_line("# status frames=1").kind == "comment"


# ---- sessions and validation ------------------------------------------------------------------

def test_session_rules(env):
    rt, c = env
    r = c.post("/api/sessions", json={"chassis": "BK-0123", "model": "BAKO-B1", "gateway": GW, "manager_id": "op-07"})
    assert r.status_code == 403  # operators cannot open sessions
    r = c.post("/api/sessions", json={"chassis": "BK-0123", "model": "BAKO-B1", "gateway": GW, "manager_id": "mgr-01"})
    assert r.status_code == 201
    sid = r.json()["session_id"]
    r = c.post("/api/sessions", json={"chassis": "BK-0123", "model": "BAKO-B1", "manager_id": "mgr-01"})
    assert r.status_code == 409
    r = c.post("/api/events", json={"type": "operation_start", "session": sid, "operation": "NOPE"})
    assert r.status_code == 422
    r = c.post("/api/events", json={"type": "confirmation", "session": sid, "operation": "P0-CHASSIS-SCAN"})
    assert r.status_code == 422  # result missing
    r = c.post("/api/events", json={"type": "phase_change", "session": sid, "phase": 9})
    assert r.status_code == 422
    # spec 6.2 example shape, resolved by chassis
    ev(c, type="operation_end", chassis="BK-0123", phase=0, operation="P0-CHASSIS-SCAN", station="S0", shift="A",
       result="ok", confirmed_by="op-07", client_time="2026-12-02T10:14:05+01:00")


def test_binding_assigns_frames(env):
    rt, c = env
    rt.ingest.handle_line(None, "HELLO " + GW)
    stream(rt, 1)  # unbound: live only, not stored
    assert rt.db.scalar("SELECT COUNT(*) FROM can_frames") == 0
    s = c.post("/api/sessions", json={"chassis": "BK-0001", "model": "BAKO-B1", "gateway": GW,
                                      "manager_id": "mgr-01"}).json()
    stream(rt, 1)
    assert rt.db.scalar("SELECT COUNT(*) FROM can_frames WHERE session_id = ?", (s["session_id"],)) > 10
    assert rt.db.scalar("SELECT COUNT(*) FROM measurements WHERE session_id = ? AND field = 'v12_dc_out'",
                        (s["session_id"],)) >= 1
    # gateway moves to another car
    s2 = c.post("/api/sessions", json={"chassis": "BK-0002", "model": "BAKO-B1", "manager_id": "mgr-01"}).json()
    ev(c, type="bind_gateway", session=s2["session_id"], gateway=GW, user="mgr-01")
    before = rt.db.scalar("SELECT COUNT(*) FROM can_frames WHERE session_id = ?", (s["session_id"],))
    stream(rt, 1)
    assert rt.db.scalar("SELECT COUNT(*) FROM can_frames WHERE session_id = ?", (s["session_id"],)) == before
    assert rt.db.scalar("SELECT COUNT(*) FROM can_frames WHERE session_id = ?", (s2["session_id"],)) > 10
    live = c.get("/api/live").json()
    assert {x["chassis_id"] for x in live["sessions"]} == {"BK-0001", "BK-0002"}


# ---- alerts ------------------------------------------------------------------------------------

def test_alert_rules(env):
    rt, c = env
    rt.ingest.handle_line(None, "HELLO " + GW)
    s = c.post("/api/sessions", json={"chassis": "BK-0010", "model": "BAKO-B1", "gateway": GW,
                                      "manager_id": "mgr-01"}).json()
    stream(rt, 2)
    assert c.get("/api/alerts").json() == []

    stream(rt, 1, pack=FAULT_PACK)
    alerts = c.get("/api/alerts").json()
    assert [a["rule"] for a in alerts] == ["bms_fault"]
    assert "level 2 code 27" in alerts[0]["message"]
    rt.ingest.handle_line(GW, 'ALERT:{"rule":"bms_fault","sms":true}')
    assert c.get("/api/alerts").json()[0]["sms_sent"] == 1
    stream(rt, 2, pack=FAULT_PACK)  # still active: no duplicate
    assert len(c.get("/api/alerts?status=all").json()) == 1

    # BMS silent after 5 s
    stream(rt, 7, bms=False)
    rules = {a["rule"] for a in c.get("/api/alerts").json()}
    assert "bms_silent" in rules

    # DC-DC out of range and temperatures from board sensors
    rt.ingest.handle_line(GW, SENSORS.replace("13.42", "10.2").replace('"temp_motor":23.9', '"temp_motor":88'))
    rules = {a["rule"] for a in c.get("/api/alerts").json()}
    assert {"dcdc_out_of_range", "temp_critical"} <= rules

    # acknowledgement
    aid = c.get("/api/alerts").json()[-1]["alert_id"]
    clock.advance(90)
    r = c.post(f"/api/alerts/{aid}/ack", json={"user": "sup-01"})
    assert r.status_code == 200 and r.json()["acknowledged_by"] == "sup-01"
    k = c.get("/api/kpi", params={"from": "2026-12-01", "to": "2026-12-03"}).json()["kpis"]
    assert k["alert_response_time"]["n"] == 1

    # escalation of unacknowledged critical alerts after 10 min -> SMS queued
    clock.advance(11 * 60)
    rt.tick()
    sms = c.get("/api/sms").json()
    assert any(m["reason"].startswith("escalation:") and len(m["text"]) <= 160 for m in sms)
    assert c.get("/api/alerts").json()[0]["escalated_at"]


def test_session_idle(env):
    rt, c = env
    c.post("/api/sessions", json={"chassis": "BK-0011", "model": "BAKO-B1", "manager_id": "mgr-01"})
    clock.advance(61 * 60)
    rt.tick()
    assert [a["rule"] for a in c.get("/api/alerts").json()] == ["session_idle"]


# ---- gaps -----------------------------------------------------------------------------------------

def test_gap_detection(env):
    rt, c = env
    rt.ingest.handle_line(None, "HELLO " + GW)
    s = c.post("/api/sessions", json={"chassis": "BK-0020", "model": "BAKO-B1", "gateway": GW,
                                      "manager_id": "mgr-01"}).json()
    sid = s["session_id"]
    stream(rt, 2, bms=False)
    ev(c, type="phase_change", session=sid, phase=3)
    stream(rt, 5, bms=False)  # phase 3 but BMS not connected yet: no CAN expected
    assert rt.db.scalar("SELECT COUNT(*) FROM gaps WHERE kind = 'can_missing'") == 0
    do_op(c, rt, sid, "P3-BMS-CONNECT", minutes=0.1)
    stream(rt, 10)
    assert rt.db.scalar("SELECT COUNT(*) FROM gaps WHERE kind = 'can_missing'") == 0
    # cell frames stop: only those are flagged
    for _ in range(100):
        rt.ingest.handle_line(GW, frame(0x18FF28F4, OK_PACK))
        rt.ingest.handle_line(GW, frame(0x18FE28F4, EXTREMES))
        clock.advance(0.1)
        rt.tick()
    keys = {g["key"] for g in rt.db.all("SELECT key FROM gaps WHERE kind = 'can_missing'")}
    assert "0x18C828F4" in keys and "0x18FF28F4" not in keys
    # gateway offline
    clock.advance(15)
    rt.tick()
    assert rt.db.scalar("SELECT COUNT(*) FROM gaps WHERE kind = 'gateway_offline'") == 1
    # a full outage is one gateway gap, not one gap per CAN message
    keys = {g["key"] for g in rt.db.all("SELECT key FROM gaps WHERE kind = 'can_missing'")}
    assert "0x18FF28F4" not in keys and "0x18FE28F4" not in keys, keys
    # unconfirmed operation
    ev(c, type="operation_start", session=sid, operation="P3-BATT-CHECK")
    ev(c, type="operation_end", session=sid, operation="P3-BATT-CHECK")
    clock.advance(16 * 60)
    rt.tick()
    assert rt.db.scalar("SELECT COUNT(*) FROM gaps WHERE kind = 'operation_unconfirmed'") == 1
    ev(c, type="confirmation", session=sid, operation="P3-BATT-CHECK", result="ok", confirmed_by="op-01")
    assert rt.db.scalar("SELECT COUNT(*) FROM gaps WHERE kind = 'operation_unconfirmed' AND ended_at IS NULL") == 0
    s = rt.db.one("SELECT * FROM sessions WHERE session_id = ?", (sid,))
    from backend.gaps import flagged_ratio
    fr = flagged_ratio(rt, s)
    assert fr["detected"] >= 2 and fr["rate"] == 1.0, fr


# ---- full build, KPIs, reports, exports --------------------------------------------------------------

def build_vehicle(rt, c, chassis, anomaly=False, rework=False):
    rt.ingest.handle_line(None, "HELLO " + GW)
    s = c.post("/api/sessions", json={"chassis": chassis, "model": "BAKO-B1", "gateway": GW,
                                      "manager_id": "mgr-01"}).json()
    sid = s["session_id"]
    for ph in rt.cfg.phases("BAKO-B1"):
        if ph["phase"]:
            ev(c, type="phase_change", session=sid, phase=ph["phase"], user="mgr-01")
        for op in ph["operations"]:
            value = None
            if op.get("check"):
                value = (op["check"]["min"] + op["check"]["max"]) / 2
            live = ph["phase"] > 3 or op["code"] == "P3-BATT-CHECK"
            do_op(c, rt, sid, op["code"], minutes=op["target_min"] * 0.01, value=value, live=live)
            if rework and op["code"] == "P1-AXLE-TORQUE":
                do_op(c, rt, sid, op["code"], minutes=0.2, value=90)
            if anomaly and op["code"] == "P2-HARNESS-CONT":
                a = ev(c, type="anomaly", session=sid, anomaly_type="wiring fault", station="S2",
                       description="open circuit on rear light", user="op-02")
                clock.advance(300)
                ev(c, type="intervention", session=sid, anomaly=a["anomaly_id"], action="re-crimped connector",
                   recheck="ok", user="op-02")
            if ph["phase"] >= 3:
                stream(rt, 0.5)
    r = c.post(f"/api/sessions/{sid}/complete", json={"user": "mgr-01"})
    assert r.status_code == 200, r.text
    return sid


def test_full_build_kpis_reports(env):
    rt, c = env
    sid1 = build_vehicle(rt, c, "BK-0101")
    clock.advance(600)
    sid2 = build_vehicle(rt, c, "BK-0102", anomaly=True, rework=True)

    s1 = rt.db.one("SELECT * FROM sessions WHERE session_id = ?", (sid1,))
    assert s1["status"] == "complete" and s1["record_pdf"]
    pdf = (rt.data_dir / s1["record_pdf"]).read_bytes()
    assert pdf.startswith(b"%PDF")

    h = c.get("/api/vehicles/BK-0102/history").json()
    rec = h["sessions"][0]
    assert all(op["status"] == "ok" for op in rec["route"])
    assert rec["anomalies"][0]["recheck_result"] == "ok"
    assert rec["battery"]["pack_v"]["last"] == pytest.approx(60.8)
    assert rec["completeness"]["complete"], rec["completeness"]

    k = c.get("/api/kpi", params={"from": "2026-12-01", "to": "2026-12-03"}).json()["kpis"]
    assert k["traceability_coverage"]["value"] == 1.0
    assert k["can_capture_rate"]["value"] > 0.95
    assert k["lead_time"]["n"] == 2
    assert k["rework_ratio"]["repeated"] == 1
    assert k["first_pass_rate"]["by_phase"]["2"] == 0.5
    assert k["mean_time_to_fix"]["by_type"]["wiring fault"]["mean"] == pytest.approx(5.0)
    assert k["work_in_progress"]["value"] == 0
    assert "S1" in k["waiting_time"]["by_station"]
    assert c.get("/api/kpi", params={"group_by": "station", "from": "2026-12-01", "to": "2026-12-03"}).status_code == 200
    assert c.get("/api/kpi", params={"group_by": "user"}).status_code == 422  # never by individual

    for view in ("overview", "where", "when", "time", "trends"):
        r = c.get(f"/api/analytics/{view}", params={"from": "2026-12-01", "to": "2026-12-03"})
        assert r.status_code == 200, (view, r.text)
    assert c.get("/api/analytics/where", params={"from": "2026-12-01", "to": "2026-12-03"}).json()["by_station"] == {"S2": 1}

    r = c.get("/api/reports/shift", params={"date": "2026-12-02", "shift": "A"})
    assert r.status_code == 200 and len(r.json()["sms"]) <= 160
    assert "Shift report" in c.get("/api/reports/shift", params={"date": "2026-12-02", "shift": "A",
                                                                 "format": "html"}).text
    r = c.get("/api/reports/weekly", params={"week": "2026-W49", "format": "pdf"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r = c.get("/api/reports/vehicle", params={"chassis": "BK-0101", "format": "pdf"})
    assert r.content.startswith(b"%PDF")

    csv_text = c.get("/api/export/csv").text
    assert csv_text.splitlines()[0].startswith("case,session,activity,timestamp,resource")
    assert "op-07" not in csv_text and "op-07" in c.get("/api/export/csv?include_users=true").text
    root = ET.fromstring(c.get("/api/export/xes").content)
    ns = {"x": "http://www.xes-standard.org/"}
    assert len(root.findall("x:trace", ns)) == 2

    # completed sessions accept no more events
    r = c.post("/api/events", json={"type": "phase_change", "session": sid1, "phase": 2})
    assert r.status_code == 409


def test_scheduled_shift_report(env):
    rt, c = env
    c.post("/api/sessions", json={"chassis": "BK-0200", "model": "BAKO-B1", "manager_id": "mgr-01"})
    clock.freeze(datetime(2026, 12, 2, 13, 5, tzinfo=timezone.utc))  # 14:05 Tunis, shift A ended
    rt.run_report_schedule()
    rows = c.get("/api/reports?type=shift").json()
    assert [r["period_key"] for r in rows] == ["2026-12-02/A"]
    assert c.get("/api/sms").json()[0]["reason"] == "shift:2026-12-02/A"
    rt.run_report_schedule()
    assert len(c.get("/api/reports?type=shift").json()) == 1  # once


def test_config_edit_validation(env):
    rt, c = env
    cfg = c.get("/api/config").json()
    bad = {**cfg, "models": {}}
    assert c.put("/api/config", json=bad).status_code == 422
