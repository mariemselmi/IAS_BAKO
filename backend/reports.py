"""Vehicle build record, shift report and weekly report (spec O3, O5)."""

from __future__ import annotations
import html
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fpdf import FPDF

from . import clock, kpi
from .db import dumps, loads
from .gaps import capture_rate, missing_operations
from .records import session_events

if TYPE_CHECKING:
    from .runtime import Runtime


# ---- O3 build record ------------------------------------------------------------------

def vehicle_history(rt: "Runtime", chassis: str) -> dict | None:
    ch = rt.db.one("SELECT * FROM chassis WHERE chassis_id = ?", (chassis,))
    if ch is None:
        return None
    sessions = []
    for s in rt.db.all("SELECT * FROM sessions WHERE chassis_id = ? ORDER BY opened_at", (chassis,)):
        sessions.append(session_record(rt, s))
    return {"chassis": ch, "sessions": sessions}


def session_record(rt: "Runtime", s: dict) -> dict:
    sid = s["session_id"]
    events = session_events(rt, sid)
    ops = kpi.operation_instances(rt, s)
    confirmations: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e["operation"] and (e["type"] in ("confirmation", "manual_check") or
                               (e["type"] == "operation_end" and (e["payload"] or {}).get("result"))):
            p = e["payload"] or {}
            confirmations[e["operation"]].append({"at": e["timestamp"], "result": p.get("result"),
                                                  "value": p.get("value"), "unit": p.get("unit"),
                                                  "in_spec": p.get("in_spec"), "by": e["user"]})
    route = []
    for op in rt.cfg.operations(s["model"]):
        inst = [i for i in ops if i["operation"] == op["code"]]
        route.append({**op, "instances": inst, "confirmations": confirmations.get(op["code"], []),
                      "status": _op_status(inst, confirmations.get(op["code"], []))})
    anomalies = rt.db.all("SELECT * FROM anomalies WHERE session_id = ? ORDER BY opened_at", (sid,))
    for a in anomalies:
        a["interventions"] = rt.db.all("SELECT * FROM interventions WHERE anomaly_id = ? ORDER BY created_at", (a["id"],))
    alerts = rt.db.all("SELECT * FROM alerts WHERE session_id = ? ORDER BY raised_at", (sid,))
    gaps = rt.db.all("SELECT * FROM gaps WHERE session_id = ? ORDER BY started_at", (sid,))
    bindings = rt.db.all("SELECT * FROM gateway_bindings WHERE session_id = ? ORDER BY from_ts", (sid,))
    return {
        "session": s, "phase_name": rt.cfg.phase_name(s["model"], s["phase"]),
        "bindings": bindings, "route": route, "events": events,
        "anomalies": anomalies, "alerts": alerts, "gaps": gaps,
        "battery": battery_evidence(rt, sid), "sensors": sensor_evidence(rt, sid),
        "can_capture": capture_rate(rt, s),
        "completeness": kpi.record_complete(rt, s) if s["status"] == "complete" else None,
        "missing_operations": missing_operations(rt, s),
        "lead_time_h": round(clock.seconds_between(s["opened_at"], s["closed_at"]) / 3600, 2) if s["closed_at"] else None,
    }


def _alert_phase(a: dict) -> int | None:
    rule, msg = a["rule"], a.get("message") or ""
    if rule.startswith("bms_") or rule == "cell_imbalance":
        return 3
    if rule == "dcdc_out_of_range":
        return 2
    if rule.startswith("temp_"):
        return 1 if "temp_motor" in msg else 4 if "temp_mppt" in msg else 2 if "temp_dcdc" in msg else 3
    return None


def phase_states(rt: "Runtime", s: dict) -> dict[int, str | None]:
    """Per-phase state for the phase strip: fail | run | pass | None (same rules as twin.js)."""
    sid = s["session_id"]
    ops = kpi.operation_instances(rt, s)
    last: dict[str, str] = {}
    for e in rt.db.all("SELECT operation, payload FROM events WHERE session_id = ? AND operation IS NOT NULL AND "
                       "type IN ('confirmation','manual_check','operation_end') ORDER BY timestamp, event_id", (sid,)):
        r = (loads(e["payload"]) or {}).get("result")
        if r:
            last[e["operation"]] = r
    anom = {r["phase"] for r in rt.db.all("SELECT phase FROM anomalies WHERE session_id = ? AND closed_at IS NULL", (sid,))}
    crit = rt.db.all("SELECT rule, message FROM alerts WHERE session_id = ? AND acknowledged_at IS NULL AND "
                     "cleared_at IS NULL AND severity = 'critical'", (sid,))
    crit_phases = {_alert_phase(a) for a in crit}
    out: dict[int, str | None] = {}
    for p in range(7):
        statuses = []
        for op in rt.cfg.operations(s["model"]):
            if op["phase"] != p:
                continue
            inst = [i for i in ops if i["operation"] == op["code"]]
            c = [{"result": last[op["code"]]}] if op["code"] in last else []
            statuses.append(_op_status(inst, c))
        if p in anom or p in crit_phases or "not_ok" in statuses:
            out[p] = "fail"
        elif "in_progress" in statuses or "unconfirmed" in statuses:
            out[p] = "run"
        elif statuses and all(x == "ok" for x in statuses):
            out[p] = "pass"
        else:
            out[p] = None
    return out


def _op_status(inst: list[dict], conf: list[dict]) -> str:
    if conf:
        return "ok" if conf[-1]["result"] == "ok" else "not_ok"
    if any(i["end"] for i in inst):
        return "unconfirmed"
    if inst:
        return "in_progress"
    return "pending"


def battery_evidence(rt: "Runtime", session_id: int) -> dict:
    fields = ["pack_v", "pack_current_a", "soc", "fault_level", "error_code", "cell_max_mv", "cell_min_mv",
              "cell_spread_mv", "temp_max_c", "temp_min_c"]
    return _evidence(rt, session_id, fields, cells=True)


def sensor_evidence(rt: "Runtime", session_id: int) -> dict:
    return _evidence(rt, session_id, ["v12_dc_out", "v72_dc_in", "v72_mppt_in", "current_in", "current_out",
                                      "handbrake", "temp_mppt", "temp_dcdc", "temp_motor"])


def _evidence(rt: "Runtime", session_id: int, fields: list[str], cells: bool = False) -> dict:
    out = {}
    for f in fields:
        r = rt.db.one("SELECT COUNT(*) n, MIN(value) lo, MAX(value) hi, MIN(server_time) first_at, "
                      "MAX(server_time) last_at, unit FROM measurements WHERE session_id = ? AND field = ?",
                      (session_id, f))
        if r and r["n"]:
            last = rt.db.one("SELECT value FROM measurements WHERE session_id = ? AND field = ? "
                             "ORDER BY server_time DESC, id DESC LIMIT 1", (session_id, f))
            out[f] = {**r, "last": last["value"] if last else None}
    if cells:
        last_cells = rt.db.all(
            "SELECT field, value FROM measurements m WHERE session_id = ? AND field LIKE 'cell\\_%\\_mv' ESCAPE '\\' "
            "AND field NOT IN ('cell_max_mv','cell_min_mv','cell_spread_mv') AND server_time = "
            "(SELECT MAX(server_time) FROM measurements WHERE session_id = m.session_id AND field = m.field)",
            (session_id,))
        if last_cells:
            out["cells_last"] = {r["field"]: r["value"] for r in
                                 sorted(last_cells, key=lambda r: int(r["field"].split("_")[1]))}
    return out


# ---- PDF helpers ----------------------------------------------------------------------

_REPL = {"—": "-", "–": "-", "−": "-", "°": " deg", "→": "->", "≤": "<=",
         "≥": ">=", "≠": "!=", "’": "'", "“": '"', "”": '"', "×": "x"}


def _t(v: Any) -> str:
    s = "" if v is None else str(v)
    for a, b in _REPL.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


class _Pdf(FPDF):
    def __init__(self, title: str, subtitle: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.doc_title, self.subtitle = title, subtitle
        self.set_auto_page_break(True, margin=15)
        self.set_margins(14, 14, 14)
        self.alias_nb_pages()

    def header(self) -> None:
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(17, 84, 60)
        self.cell(0, 7, _t(self.doc_title), new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(90, 90, 90)
        self.cell(0, 5, _t(self.subtitle), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(17, 84, 60)
        self.line(14, self.get_y() + 1, 196, self.get_y() + 1)
        self.ln(4)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-11)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, _t(f"BAKO production monitoring - generated {clock.iso()[:19]} UTC - page {self.page_no()}/{{nb}}"),
                  align="C")

    def h2(self, text: str) -> None:
        if self.get_y() > 260:
            self.add_page()
        self.ln(2)
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(17, 84, 60)
        self.cell(0, 6, _t(text), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def kv(self, pairs: list[tuple[str, Any]]) -> None:
        self.set_font("Helvetica", "", 8.5)
        for k, v in pairs:
            self.set_font("Helvetica", "B", 8.5)
            self.cell(48, 5, _t(k))
            self.set_font("Helvetica", "", 8.5)
            self.multi_cell(0, 5, _t(v), new_x="LMARGIN", new_y="NEXT")

    def table(self, headers: list[str], rows: list[list[Any]], widths: list[float]) -> None:
        self.set_font("Helvetica", "B", 7.5)
        self.set_fill_color(230, 238, 233)
        for h, w in zip(headers, widths):
            self.cell(w, 5.5, _t(h), border=1, fill=True)
        self.ln()
        self.set_font("Helvetica", "", 7.5)
        if not rows:
            self.cell(sum(widths), 5, "none", border=1, new_x="LMARGIN", new_y="NEXT")
            return
        for r in rows:
            if self.get_y() > 275:
                self.add_page()
            for v, w in zip(r, widths):
                s = _t(v)
                while self.get_string_width(s) > w - 1.5 and len(s) > 3:
                    s = s[:-2]
                self.cell(w, 5, s, border=1)
            self.ln()


def _local(rt: "Runtime", ts: str | None) -> str:
    if not ts:
        return "-"
    return rt.cfg.local(clock.parse(ts)).strftime("%Y-%m-%d %H:%M:%S")


def _hm(rt: "Runtime", ts: str | None) -> str:
    if not ts:
        return "-"
    return rt.cfg.local(clock.parse(ts)).strftime("%d/%m %H:%M")


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.1f} %"


def build_record_pdf(rt: "Runtime", session: dict) -> Path:
    rec = session_record(rt, session)
    s = rec["session"]
    pdf = _Pdf(f"Vehicle build record - {s['chassis_id']}",
               f"Model {s['model']} - session {s['session_id']} - "
               f"{'production complete, ready for verification' if s['status'] == 'complete' else 'IN PROGRESS'}")
    pdf.add_page()
    comp = rec["completeness"]
    pdf.h2("Summary")
    pdf.kv([
        ("Chassis", s["chassis_id"]), ("Model", s["model"]),
        ("Session opened", f"{_local(rt, s['opened_at'])} by {s['manager_id']}"),
        ("Declared complete", _local(rt, s["closed_at"])),
        ("Lead time", f"{rec['lead_time_h']} h" if rec["lead_time_h"] is not None else "-"),
        ("Gateways", ", ".join(f"{b['gateway_id']} ({_hm(rt, b['from_ts'])} - {_hm(rt, b['to_ts'])})"
                               for b in rec["bindings"]) or "none"),
        ("CAN capture rate", f"{_pct(rec['can_capture']['rate'])} ({rec['can_capture']['received']} of "
                             f"{rec['can_capture']['expected']} BMS frames)"),
        ("Record complete", "YES" if comp and comp["complete"] else
         ("NO - " + "; ".join(comp["reasons"]) if comp else "session still open")),
    ])

    pdf.h2("Route: operations and confirmations")
    rows = []
    for op in rec["route"]:
        inst = op["instances"][-1] if op["instances"] else {}
        conf = op["confirmations"][-1] if op["confirmations"] else {}
        dur = inst.get("duration_s")
        val = "" if conf.get("value") is None else f"{conf['value']:g} {conf.get('unit') or ''}"
        rows.append([op["phase"], op["code"], op["station"], _hm(rt, inst.get("start")), _hm(rt, inst.get("end")),
                     f"{dur / 60:.0f}/{op.get('target_min')}" if dur is not None else f"-/{op.get('target_min')}",
                     len(op["instances"]), val, conf.get("by") or "", op["status"].upper()])
    pdf.table(["Ph", "Operation", "Stn", "Start", "End", "Min/tgt", "Runs", "Value", "Confirmed by", "Result"],
              rows, [7, 38, 10, 20, 20, 16, 10, 20, 22, 19])

    pdf.h2("Anomalies and interventions")
    rows = []
    for a in rec["anomalies"]:
        rows.append([a["id"], a["type"], a["station"], a["phase"], _hm(rt, a["opened_at"]), _hm(rt, a["closed_at"]),
                     (a["description"] or "")[:60], (a["recheck_result"] or "open").upper()])
        for i in a["interventions"]:
            rows.append(["", "  intervention", "", "", _hm(rt, i["created_at"]), "", i["action"][:60],
                         (i["recheck_result"] or "").upper()])
    pdf.table(["#", "Type", "Stn", "Ph", "Opened", "Closed", "Description / action", "Re-check"],
              rows, [8, 30, 10, 8, 20, 20, 68, 18])

    pdf.h2("Battery evidence (O'CELL BMS over CAN)")
    rows = [[f, v["n"], f"{v['lo']:g}", f"{v['hi']:g}", f"{v['last']:g}" if v["last"] is not None else "-",
             v["unit"] or "", _hm(rt, v["first_at"]), _hm(rt, v["last_at"])]
            for f, v in rec["battery"].items() if f != "cells_last"]
    pdf.table(["Signal", "Samples", "Min", "Max", "Last", "Unit", "First", "Last at"],
              rows, [36, 16, 20, 20, 20, 14, 28, 28])
    cells = rec["battery"].get("cells_last")
    if cells:
        pdf.set_font("Helvetica", "", 7.5)
        pdf.multi_cell(0, 4.5, _t("Last cell voltages (mV): " + ", ".join(
            f"{k.split('_')[1]}: {v:.0f}" for k, v in cells.items())), new_x="LMARGIN", new_y="NEXT")

    pdf.h2("Board sensor evidence (12 V / 72 V lines, probes)")
    rows = [[f, v["n"], f"{v['lo']:g}", f"{v['hi']:g}", f"{v['last']:g}" if v["last"] is not None else "-",
             v["unit"] or ""] for f, v in rec["sensors"].items()]
    pdf.table(["Field", "Samples", "Min", "Max", "Last", "Unit"], rows, [40, 20, 25, 25, 25, 20])

    pdf.h2("Alerts")
    rows = [[a["alert_id"], a["rule"], a["severity"], a["message"][:55], _hm(rt, a["raised_at"]),
             _hm(rt, a["acknowledged_at"]), "yes" if a["sms_sent"] else "no"] for a in rec["alerts"]]
    pdf.table(["#", "Rule", "Severity", "Message", "Raised", "Acked", "SMS"], rows, [9, 30, 16, 67, 22, 22, 16])

    pdf.h2("Data gaps flagged")
    rows = [[g["kind"], g["key"] or "", _hm(rt, g["started_at"]), _hm(rt, g["ended_at"]), (g["detail"] or "")[:60]]
            for g in rec["gaps"]]
    pdf.table(["Kind", "Key", "From", "To", "Detail"], rows, [36, 26, 22, 22, 76])

    pdf.h2("Chronological event log")
    rows = []
    for e in rec["events"]:
        p = e["payload"] or {}
        detail = p.get("result") or p.get("message") or p.get("anomaly_type") or p.get("action") or \
            p.get("detail") or p.get("gateway") or (f"{p.get('from')} -> {p.get('to')}" if e["type"] == "phase_change" else "")
        rows.append([_local(rt, e["timestamp"])[5:], e["type"], e["phase"], e["operation"] or "", e["station"] or "",
                     e["user"] or e["resource"] or "", str(detail)[:40]])
    pdf.table(["Time", "Event", "Ph", "Operation", "Stn", "By", "Detail"], rows, [28, 28, 7, 34, 10, 22, 53])

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.multi_cell(0, 4, _t("Operator identities are recorded for traceability only (Tunisian Organic Law 2004-63). "
                            "SMS alerts are notifications only; the BMS remains the safety system of the vehicle."),
                   new_x="LMARGIN", new_y="NEXT")
    folder = rt.data_dir / "records"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{s['chassis_id']}-session{s['session_id']}.pdf"
    pdf.output(str(path))
    return path


# ---- O5 shift and weekly reports ------------------------------------------------------------

def shift_report(rt: "Runtime", date: str, shift_id: str) -> dict:
    day = datetime.fromisoformat(date).replace(hour=12, tzinfo=rt.cfg.tz)
    start, end = rt.cfg.shift_window(shift_id, day)
    s, e = clock.iso(start), clock.iso(end)
    k = kpi.compute(rt, s, e)["kpis"]
    ops = rt.db.scalar("SELECT COUNT(*) FROM events WHERE type = 'operation_end' AND timestamp >= ? AND timestamp < ?", (s, e))
    completed = rt.db.all("SELECT chassis_id FROM sessions WHERE status='complete' AND closed_at >= ? AND closed_at < ?", (s, e))
    anomalies = rt.db.all("SELECT a.*, s.chassis_id FROM anomalies a JOIN sessions s USING(session_id) "
                          "WHERE a.opened_at >= ? AND a.opened_at < ?", (s, e))
    alerts = rt.db.all("SELECT a.*, s.chassis_id FROM alerts a LEFT JOIN sessions s USING(session_id) "
                       "WHERE a.raised_at >= ? AND a.raised_at < ?", (s, e))
    gaps = rt.db.scalar("SELECT COUNT(*) FROM gaps WHERE started_at >= ? AND started_at < ?", (s, e))
    wip = rt.db.all("SELECT chassis_id, phase, station FROM sessions WHERE status = 'open'")
    rep = {"date": date, "shift": shift_id, "from": s, "to": e,
           "completed": [c["chassis_id"] for c in completed], "operations_done": ops,
           "anomalies": anomalies, "anomalies_open": sum(1 for a in anomalies if not a["closed_at"]),
           "alerts": alerts, "alerts_unacknowledged": sum(1 for a in alerts if not a["acknowledged_at"]),
           "gaps_flagged": gaps, "work_in_progress": wip,
           "rework_ratio": k["rework_ratio"]["value"], "first_pass_rate": k["first_pass_rate"]["value"]}
    rep["sms"] = sms_summary(rep)
    return rep


def sms_summary(rep: dict) -> str:
    from .alerts import sms_text
    return sms_text(f"BAKO shift {rep['shift']} {rep['date']}: {len(rep['completed'])} done, "
                    f"{rep['operations_done']} ops, {len(rep['anomalies'])} anomalies ({rep['anomalies_open']} open), "
                    f"{len(rep['alerts'])} alerts ({rep['alerts_unacknowledged']} unacked), "
                    f"{rep['gaps_flagged']} gaps, WIP {len(rep['work_in_progress'])}")


def shift_report_html(rt: "Runtime", rep: dict) -> str:
    esc = html.escape
    rows_a = "".join(f"<tr><td>{esc(_hm(rt, a['opened_at']))}</td><td>{esc(a['chassis_id'])}</td><td>{esc(a['station'] or '')}</td>"
                     f"<td>{esc(a['type'])}</td><td>{'closed' if a['closed_at'] else '<b>open</b>'}</td></tr>"
                     for a in rep["anomalies"]) or "<tr><td colspan=5>none</td></tr>"
    rows_l = "".join(f"<tr><td>{esc(_hm(rt, a['raised_at']))}</td><td>{esc(a.get('chassis_id') or a['gateway_id'] or '')}</td>"
                     f"<td>{esc(a['message'])}</td><td>{esc(_hm(rt, a['acknowledged_at']))}</td></tr>"
                     for a in rep["alerts"]) or "<tr><td colspan=4>none</td></tr>"
    wip = "".join(f"<li>{esc(w['chassis_id'])} - phase {w['phase']} - {esc(w['station'] or '')}</li>"
                  for w in rep["work_in_progress"]) or "<li>none</li>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shift report {esc(rep['date'])} {esc(rep['shift'])}</title>
<style>body{{font:15px/1.45 system-ui,sans-serif;margin:16px auto;max-width:860px;padding:0 16px;color:#1d2321;background:#f4f5f4}}
h1{{font-size:20px;color:#11543c}}table{{border-collapse:collapse;width:100%;background:#fff}}td,th{{border:1px solid #cfd6d2;padding:4px 8px;text-align:left}}
.k{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}}.k div{{background:#fff;border:1px solid #cfd6d2;padding:8px}}.k b{{display:block;font-size:22px}}</style></head>
<body><h1>Shift report - {esc(rep['date'])} shift {esc(rep['shift'])}</h1>
<p>{esc(_local(rt, rep['from']))} to {esc(_local(rt, rep['to']))}</p>
<div class="k"><div><b>{len(rep['completed'])}</b>vehicles completed</div><div><b>{rep['operations_done']}</b>operations done</div>
<div><b>{len(rep['anomalies'])}</b>anomalies ({rep['anomalies_open']} open)</div><div><b>{len(rep['alerts'])}</b>alerts ({rep['alerts_unacknowledged']} unacked)</div>
<div><b>{rep['gaps_flagged']}</b>data gaps flagged</div><div><b>{_pct(rep['first_pass_rate'])}</b>first-pass rate</div></div>
<h2>Completed</h2><p>{esc(', '.join(rep['completed']) or 'none')}</p>
<h2>Work in progress</h2><ul>{wip}</ul>
<h2>Anomalies</h2><table><tr><th>Time</th><th>Chassis</th><th>Station</th><th>Type</th><th>Status</th></tr>{rows_a}</table>
<h2>Alerts</h2><table><tr><th>Raised</th><th>Where</th><th>Message</th><th>Acknowledged</th></tr>{rows_l}</table>
<p><small>SMS sent to supervisor: {esc(rep['sms'])}</small></p></body></html>"""


def weekly_report(rt: "Runtime", week: str) -> dict:
    y, w = week.split("-W")
    monday = datetime.fromisocalendar(int(y), int(w), 1)
    start = datetime.combine(monday.date(), datetime.min.time(), tzinfo=rt.cfg.tz).astimezone(timezone.utc)
    end = start + timedelta(days=7)
    k = kpi.compute(rt, clock.iso(start), clock.iso(end))
    where = kpi.analytics(rt, "where", clock.iso(start), clock.iso(end))
    tm = kpi.analytics(rt, "time", clock.iso(start), clock.iso(end))
    return {"week": week, "from": clock.iso(start), "to": clock.iso(end), "kpis": k["kpis"], "where": where, "time": tm}


def weekly_report_pdf(rt: "Runtime", week: str) -> Path:
    rep = weekly_report(rt, week)
    k = rep["kpis"]
    pdf = _Pdf(f"Weekly production report - {week}", f"{_local(rt, rep['from'])[:10]} to "
                                                      f"{_local(rt, clock.iso(clock.parse(rep['to']) - timedelta(seconds=1)))[:10]}")
    pdf.add_page()
    pdf.h2("Key performance indicators")
    pdf.table(["KPI", "Value", "Detail"], [
        ["Vehicles completed", k["traceability_coverage"]["denominator"], ""],
        ["Traceability coverage", _pct(k["traceability_coverage"]["value"]),
         f"{k['traceability_coverage']['numerator']} complete records"],
        ["CAN capture rate", _pct(k["can_capture_rate"]["value"]), ""],
        ["Data gaps flagged", _pct(k["data_gaps_flagged"]["value"]),
         f"{k['data_gaps_flagged']['flagged']} of {k['data_gaps_flagged']['detected']} detected"],
        ["Lead time (mean)", f"{k['lead_time']['mean']} h" if k["lead_time"]["mean"] is not None else "-",
         f"min {k['lead_time']['min']} / max {k['lead_time']['max']}"],
        ["Rework ratio", _pct(k["rework_ratio"]["value"]),
         f"{k['rework_ratio']['repeated']} of {k['rework_ratio']['performed']} operations"],
        ["First-pass rate", _pct(k["first_pass_rate"]["value"]), ""],
        ["Mean time to fix", f"{k['mean_time_to_fix']['mean']} min" if k["mean_time_to_fix"]["mean"] is not None else "-",
         f"{k['mean_time_to_fix']['open']} anomalies open now"],
        ["Alert response time", f"{k['alert_response_time']['mean']} min" if k["alert_response_time"]["mean"] is not None else "-",
         f"{k['alert_response_time']['unacknowledged']} unacknowledged now"],
        ["Work in progress", k["work_in_progress"]["value"], "open sessions"],
    ], [55, 35, 92])
    pdf.h2("Vehicles")
    pdf.table(["Chassis", "Lead time (h)", "Week"], [[v["chassis"], v["hours"], v["week"]] for v in k["lead_time"]["by_vehicle"]],
              [60, 40, 40])
    inc = k["traceability_coverage"]["incomplete"]
    if inc:
        pdf.h2("Incomplete build records")
        pdf.table(["Chassis", "Why"], [[i["chassis"], "; ".join(i["reasons"])] for i in inc], [40, 142])
    pdf.h2("Operation time vs target (min)")
    pdf.table(["Operation", "Runs", "Mean", "Target", "Mean/target"],
              [[op, v["n"], v["mean"], v["target_min"], _pct(v["vs_target"])] for op, v in k["operation_time"]["by_operation"].items()],
              [60, 20, 30, 30, 42])
    pdf.h2("Stations: waiting time and rework")
    stations = sorted(set(k["waiting_time"]["by_station"]) | set(k["rework_ratio"]["by_station"]), key=str)
    pdf.table(["Station", "Mean wait (min)", "Rework ratio"],
              [[st, (k["waiting_time"]["by_station"].get(st) or {}).get("mean", "-"),
                _pct(k["rework_ratio"]["by_station"].get(st))] for st in stations], [40, 50, 50])
    if rep["time"]["bottleneck_station"]:
        pdf.kv([("Bottleneck station", rep["time"]["bottleneck_station"])])
    pdf.h2("Where anomalies occur")
    w = rep["where"]
    pdf.kv([("By station", ", ".join(f"{a}: {b}" for a, b in w["by_station"].items()) or "none"),
            ("By phase", ", ".join(f"{a}: {b}" for a, b in w["by_phase"].items()) or "none"),
            ("By type", ", ".join(f"{a}: {b}" for a, b in w["by_type"].items()) or "none")])
    pdf.h2("First-pass rate by phase")
    pdf.table(["Phase", "First-pass rate"], [[p, _pct(v)] for p, v in k["first_pass_rate"]["by_phase"].items()], [40, 50])
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.multi_cell(0, 4, "Aggregated by station, phase and time. Individuals are never ranked.", new_x="LMARGIN", new_y="NEXT")
    folder = rt.data_dir / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"weekly-{week}.pdf"
    pdf.output(str(path))
    return path


def store_report(rt: "Runtime", type_: str, key: str, path: Path | None, summary: Any) -> None:
    rt.db.execute("INSERT INTO reports (type, period_key, created_at, path, summary) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(type, period_key) DO UPDATE SET created_at = excluded.created_at, path = excluded.path, "
                  "summary = excluded.summary",
                  (type_, key, clock.iso(), str(path.relative_to(rt.data_dir)) if path else None, dumps(summary)))


def list_reports(rt: "Runtime", type_: str | None = None) -> list[dict]:
    rows = rt.db.all("SELECT * FROM reports " + ("WHERE type = ? " if type_ else "") + "ORDER BY created_at DESC LIMIT 200",
                     (type_,) if type_ else ())
    for r in rows:
        r["summary"] = loads(r["summary"])
    return rows
