"""KPI engine and analytics views (spec O4, Table 2).

Everything is aggregated by vehicle, station, phase, shift or time. Nothing is
grouped by operator: individuals are never ranked."""

from __future__ import annotations
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from statistics import mean
from typing import TYPE_CHECKING, Any, Iterable

from . import clock
from .gaps import capture_rate, flagged_ratio, missing_operations

if TYPE_CHECKING:
    from .runtime import Runtime

KPI_NAMES = ["traceability_coverage", "can_capture_rate", "data_gaps_flagged", "lead_time",
             "operation_time", "waiting_time", "rework_ratio", "first_pass_rate",
             "mean_time_to_fix", "alert_response_time", "work_in_progress"]


# ---- range handling ---------------------------------------------------------------

def parse_range(rt: "Runtime", start: str | None, end: str | None) -> tuple[str, str]:
    now = clock.now()

    def conv(s: str, is_end: bool) -> datetime:
        if len(s) == 10:  # local calendar date
            d = datetime.fromisoformat(s).date()
            if is_end:
                d += timedelta(days=1)
            return datetime.combine(d, time(0), tzinfo=rt.cfg.tz).astimezone(timezone.utc)
        return clock.parse(s)

    e = conv(end, True) if end else now
    s = conv(start, False) if start else e - timedelta(days=7)
    if s >= e:
        raise ValueError("from must be before to")
    return clock.iso(s), clock.iso(e)


def _day(rt: "Runtime", ts: str) -> str:
    return rt.cfg.local(clock.parse(ts)).date().isoformat()


def _week(rt: "Runtime", ts: str) -> str:
    y, w, _ = rt.cfg.local(clock.parse(ts)).isocalendar()
    return f"{y}-W{w:02d}"


def _ratio(n: float, d: float) -> float | None:
    return (n / d) if d else None


def _stats(values: Iterable[float]) -> dict:
    v = [x for x in values if x is not None]
    return {"n": len(v), "mean": round(mean(v), 2) if v else None,
            "min": round(min(v), 2) if v else None, "max": round(max(v), 2) if v else None}


# ---- per-session building blocks ---------------------------------------------------

def sessions_in(rt: "Runtime", start: str, end: str) -> list[dict]:
    return rt.db.all("SELECT * FROM sessions WHERE opened_at < ? AND (closed_at IS NULL OR closed_at >= ?) "
                     "ORDER BY opened_at", (end, start))


def operation_instances(rt: "Runtime", session: dict) -> list[dict]:
    """Pairs operation_start -> operation_end per operation code."""
    rows = rt.db.all("SELECT type, operation, station, phase, timestamp FROM events WHERE session_id = ? "
                     "AND type IN ('operation_start','operation_end') ORDER BY timestamp, event_id",
                     (session["session_id"],))
    open_: dict[str, dict] = {}
    out: list[dict] = []
    for r in rows:
        if r["type"] == "operation_start":
            if r["operation"] in open_:  # restarted without end: close the first as abandoned
                out.append({**open_.pop(r["operation"]), "end": None, "duration_s": None})
            open_[r["operation"]] = {"operation": r["operation"], "station": r["station"], "phase": r["phase"],
                                     "start": r["timestamp"]}
        else:
            inst = open_.pop(r["operation"], None) or {"operation": r["operation"], "station": r["station"],
                                                       "phase": r["phase"], "start": None}
            inst["end"] = r["timestamp"]
            inst["duration_s"] = clock.seconds_between(inst["start"], inst["end"]) if inst["start"] else None
            out.append(inst)
    for inst in open_.values():
        out.append({**inst, "end": None, "duration_s": None})
    for inst in out:
        inst["target_min"] = (rt.cfg.operation(session["model"], inst["operation"]) or {}).get("target_min")
    out.sort(key=lambda i: i["start"] or i["end"] or "")
    return out


def record_complete(rt: "Runtime", session: dict) -> dict:
    """Is the build record of a completed vehicle complete? (traceability coverage)"""
    missing = missing_operations(rt, session)
    open_anoms = rt.db.scalar("SELECT COUNT(*) FROM anomalies WHERE session_id = ? AND closed_at IS NULL",
                              (session["session_id"],))
    cap = capture_rate(rt, session)
    min_rate = rt.cfg.t("can_capture_complete_pct", 95) / 100
    can_ok = cap["rate"] is not None and cap["rate"] >= min_rate
    reasons = []
    if missing:
        reasons.append(f"{len(missing)} operation(s) not confirmed")
    if open_anoms:
        reasons.append(f"{open_anoms} anomaly(ies) still open")
    if not can_ok:
        reasons.append("battery data missing" if cap["rate"] is None
                       else f"CAN capture {cap['rate'] * 100:.0f}% below {min_rate * 100:.0f}%")
    return {"complete": not reasons, "reasons": reasons, "missing_operations": missing,
            "open_anomalies": open_anoms, "can_capture": cap}


def phases_completed(rt: "Runtime", session: dict) -> list[dict]:
    """Phases the vehicle has left (moved past, or completed the session in)."""
    changes = rt.db.all("SELECT payload, timestamp FROM events WHERE session_id = ? AND type = 'phase_change' "
                        "ORDER BY timestamp", (session["session_id"],))
    import json
    left: dict[int, str] = {}
    current = 0
    for c in changes:
        p = json.loads(c["payload"] or "{}")
        frm, to = p.get("from", current), p.get("to", current)
        if to != frm:
            left[frm] = c["timestamp"]
        current = to
    if session["status"] == "complete":
        left[session["phase"]] = session["closed_at"]
    out = []
    for phase, ts in sorted(left.items()):
        n = rt.db.scalar("SELECT COUNT(*) FROM anomalies WHERE session_id = ? AND phase = ?",
                         (session["session_id"], phase))
        out.append({"phase": phase, "completed_at": ts, "anomalies": n})
    return out


# ---- KPIs -------------------------------------------------------------------------------

def compute(rt: "Runtime", start: str | None = None, end: str | None = None,
            group_by: str | None = None) -> dict:
    s, e = parse_range(rt, start, end)
    sessions = sessions_in(rt, s, e)
    completed = [x for x in sessions if x["status"] == "complete" and s <= x["closed_at"] < e]
    out: dict[str, Any] = {"from": s, "to": e, "kpis": {}}
    k = out["kpis"]

    # Traceability coverage
    by_day: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_week: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    total_ok = 0
    incomplete = []
    for x in completed:
        rc = record_complete(rt, x)
        ok = int(rc["complete"])
        total_ok += ok
        for key, bucket in ((_day(rt, x["closed_at"]), by_day), (_week(rt, x["closed_at"]), by_week)):
            bucket[key][0] += ok
            bucket[key][1] += 1
        if not ok:
            incomplete.append({"chassis": x["chassis_id"], "reasons": rc["reasons"]})
    k["traceability_coverage"] = {
        "value": _ratio(total_ok, len(completed)), "numerator": total_ok, "denominator": len(completed),
        "by_day": {d: _ratio(a, b) for d, (a, b) in sorted(by_day.items())},
        "by_week": {w: _ratio(a, b) for w, (a, b) in sorted(by_week.items())},
        "incomplete": incomplete}

    # CAN capture rate and data gaps flagged, per chassis/session
    cap_rows, gap_rows = [], []
    exp_sum = rec_sum = det_sum = flag_sum = 0
    for x in sessions:
        cap = capture_rate(rt, x)
        if cap["expected"]:
            cap_rows.append({"chassis": x["chassis_id"], "session": x["session_id"], "rate": cap["rate"],
                             "received": cap["received"], "expected": cap["expected"]})
            exp_sum += cap["expected"]
            rec_sum += cap["received"]
        fr = flagged_ratio(rt, x)
        gap_rows.append({"chassis": x["chassis_id"], "session": x["session_id"], **fr})
        det_sum += fr["detected"]
        flag_sum += fr["flagged"]
    k["can_capture_rate"] = {"value": _ratio(rec_sum, exp_sum), "by_session": cap_rows}
    k["data_gaps_flagged"] = {"value": _ratio(flag_sum, det_sum), "detected": det_sum, "flagged": flag_sum,
                              "by_chassis": gap_rows}

    # Lead time
    lead = [{"chassis": x["chassis_id"], "hours": round(clock.seconds_between(x["opened_at"], x["closed_at"]) / 3600, 2),
             "week": _week(rt, x["closed_at"])} for x in completed]
    lw: dict[str, list[float]] = defaultdict(list)
    for r in lead:
        lw[r["week"]].append(r["hours"])
    k["lead_time"] = {"unit": "h", **_stats(r["hours"] for r in lead), "by_vehicle": lead,
                      "by_week": {w: _stats(v) for w, v in sorted(lw.items())}}

    # Operation time, waiting time, rework
    op_t: dict[str, list[float]] = defaultdict(list)
    st_t: dict[str, list[float]] = defaultdict(list)
    wait: dict[str, list[float]] = defaultdict(list)
    performed_st: dict[str, int] = defaultdict(int)
    repeated_st: dict[str, int] = defaultdict(int)
    performed_ph: dict[int, int] = defaultdict(int)
    repeated_ph: dict[int, int] = defaultdict(int)
    targets: dict[str, float | None] = {}
    for x in sessions:
        insts = [i for i in operation_instances(rt, x) if (i["start"] or i["end"] or "") >= s
                 and (i["start"] or i["end"] or "") < e]
        seen: dict[str, int] = defaultdict(int)
        for i in insts:
            if i["start"] is None:
                continue
            seen[i["operation"]] += 1
            performed_st[i["station"]] += 1
            performed_ph[i["phase"]] += 1
            if seen[i["operation"]] > 1:
                repeated_st[i["station"]] += 1
                repeated_ph[i["phase"]] += 1
            if i["duration_s"] is not None:
                op_t[i["operation"]].append(i["duration_s"] / 60)
                st_t[i["station"]].append(i["duration_s"] / 60)
                targets[i["operation"]] = i["target_min"]
        done = [i for i in insts if i["start"] and i["end"]]
        done.sort(key=lambda i: i["start"])
        for prev, nxt in zip(done, done[1:]):
            w = clock.seconds_between(prev["end"], nxt["start"])
            if w is not None and w >= 0:
                wait[nxt["station"]].append(w / 60)
    k["operation_time"] = {
        "unit": "min",
        "by_operation": {op: {**_stats(v), "target_min": targets.get(op),
                              "vs_target": _ratio(mean(v), targets[op]) if targets.get(op) else None}
                         for op, v in sorted(op_t.items())},
        "by_station": {st: _stats(v) for st, v in sorted(st_t.items(), key=lambda kv: str(kv[0]))}}
    k["waiting_time"] = {"unit": "min", "by_station": {st: _stats(v) for st, v in sorted(wait.items(), key=lambda kv: str(kv[0]))}}
    perf = sum(performed_st.values())
    rep = sum(repeated_st.values())
    k["rework_ratio"] = {
        "value": _ratio(rep, perf), "repeated": rep, "performed": perf,
        "by_station": {st: _ratio(repeated_st[st], n) for st, n in sorted(performed_st.items(), key=lambda kv: str(kv[0]))},
        "by_phase": {str(p): _ratio(repeated_ph[p], n) for p, n in sorted(performed_ph.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))}}

    # First-pass rate per phase
    fp: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for x in sessions:
        for ph in phases_completed(rt, x):
            if not (s <= ph["completed_at"] < e):
                continue
            fp[ph["phase"]][1] += 1
            if ph["anomalies"] == 0:
                fp[ph["phase"]][0] += 1
    good = sum(a for a, _ in fp.values())
    tot = sum(b for _, b in fp.values())
    k["first_pass_rate"] = {"value": _ratio(good, tot), "by_phase": {str(p): _ratio(a, b) for p, (a, b) in sorted(fp.items())}}

    # Mean time to fix, per anomaly type
    fixes = rt.db.all("SELECT type, opened_at, closed_at FROM anomalies WHERE closed_at IS NOT NULL "
                      "AND closed_at >= ? AND closed_at < ?", (s, e))
    mt: dict[str, list[float]] = defaultdict(list)
    for f in fixes:
        mt[f["type"]].append(clock.seconds_between(f["opened_at"], f["closed_at"]) / 60)
    k["mean_time_to_fix"] = {"unit": "min", **_stats(v for vs in mt.values() for v in vs),
                             "by_type": {t: _stats(v) for t, v in sorted(mt.items())},
                             "open": rt.db.scalar("SELECT COUNT(*) FROM anomalies WHERE closed_at IS NULL")}

    # Alert response time, per rule
    acks = rt.db.all("SELECT rule, raised_at, acknowledged_at FROM alerts WHERE acknowledged_at IS NOT NULL "
                     "AND raised_at >= ? AND raised_at < ?", (s, e))
    ar: dict[str, list[float]] = defaultdict(list)
    for a in acks:
        ar[a["rule"]].append(clock.seconds_between(a["raised_at"], a["acknowledged_at"]) / 60)
    k["alert_response_time"] = {"unit": "min", **_stats(v for vs in ar.values() for v in vs),
                                "by_type": {t: _stats(v) for t, v in sorted(ar.items())},
                                "unacknowledged": rt.db.scalar("SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL")}

    k["work_in_progress"] = {"value": rt.db.scalar("SELECT COUNT(*) FROM sessions WHERE status = 'open'")}

    if group_by:
        out["kpis"] = _filter_group(k, group_by)
        out["group_by"] = group_by
    return out


_GROUP_KEYS = {
    "day": ["by_day"], "week": ["by_week"], "chassis": ["by_session", "by_chassis", "by_vehicle"],
    "session": ["by_session"], "vehicle": ["by_vehicle"], "operation": ["by_operation"],
    "station": ["by_station"], "phase": ["by_phase"], "anomaly_type": ["by_type"], "alert_type": ["by_type"],
}


def _filter_group(k: dict, group_by: str) -> dict:
    keys = _GROUP_KEYS.get(group_by)
    if keys is None:
        raise ValueError(f"group_by must be one of {', '.join(_GROUP_KEYS)}")
    out = {}
    for name, v in k.items():
        hit = {g: v[g] for g in keys if g in v}
        if hit:
            out[name] = {"value": v.get("value", v.get("mean")), **hit}
    return out


# ---- analytics views ------------------------------------------------------------------

def analytics(rt: "Runtime", view: str, start: str | None, end: str | None) -> dict:
    s, e = parse_range(rt, start, end)
    if view == "overview":
        kp = compute(rt, start, end)["kpis"]
        return {"from": s, "to": e, "summary": {
            "work_in_progress": kp["work_in_progress"]["value"],
            "completed": kp["traceability_coverage"]["denominator"],
            "traceability_coverage": kp["traceability_coverage"]["value"],
            "can_capture_rate": kp["can_capture_rate"]["value"],
            "data_gaps_flagged": kp["data_gaps_flagged"]["value"],
            "lead_time_h": kp["lead_time"]["mean"],
            "rework_ratio": kp["rework_ratio"]["value"],
            "first_pass_rate": kp["first_pass_rate"]["value"],
            "mean_time_to_fix_min": kp["mean_time_to_fix"]["mean"],
            "alert_response_min": kp["alert_response_time"]["mean"],
            "open_anomalies": kp["mean_time_to_fix"]["open"],
            "unacknowledged_alerts": kp["alert_response_time"]["unacknowledged"],
        }}
    if view == "where":
        rows = rt.db.all("SELECT type, station, phase FROM anomalies WHERE opened_at >= ? AND opened_at < ?", (s, e))
        return {"from": s, "to": e, "anomalies": len(rows),
                "by_station": _count(r["station"] for r in rows),
                "by_phase": _count(r["phase"] for r in rows),
                "by_type": _count(r["type"] for r in rows),
                "station_x_type": _count(f"{r['station']}|{r['type']}" for r in rows)}
    if view == "when":
        an = rt.db.all("SELECT opened_at FROM anomalies WHERE opened_at >= ? AND opened_at < ?", (s, e))
        al = rt.db.all("SELECT raised_at FROM alerts WHERE raised_at >= ? AND raised_at < ?", (s, e))
        ops = rt.db.all("SELECT timestamp, shift FROM events WHERE type = 'operation_end' AND timestamp >= ? "
                        "AND timestamp < ?", (s, e))
        hour = lambda ts: rt.cfg.local(clock.parse(ts)).hour  # noqa: E731
        return {"from": s, "to": e,
                "anomalies_by_hour": _count(hour(r["opened_at"]) for r in an),
                "anomalies_by_shift": _count(rt.cfg.shift_for(clock.parse(r["opened_at"])) for r in an),
                "alerts_by_hour": _count(hour(r["raised_at"]) for r in al),
                "operations_by_hour": _count(hour(r["timestamp"]) for r in ops),
                "operations_by_shift": _count(r["shift"] for r in ops)}
    if view == "time":
        kp = compute(rt, start, end)["kpis"]
        ops = kp["operation_time"]["by_operation"]
        over = sorted(((op, v["vs_target"]) for op, v in ops.items() if v["vs_target"]), key=lambda kv: -kv[1])
        waits = kp["waiting_time"]["by_station"]
        bottleneck = max(kp["operation_time"]["by_station"].items(),
                         key=lambda kv: (kv[1]["mean"] or 0) * kv[1]["n"], default=(None, None))[0]
        return {"from": s, "to": e, "operation_vs_target": ops, "over_target": over[:10],
                "station_time": kp["operation_time"]["by_station"], "waiting_by_station": waits,
                "bottleneck_station": bottleneck}
    if view == "trends":
        weeks: dict[str, dict] = defaultdict(lambda: {"completed": 0, "lead_h": [], "anomalies": 0, "alerts": 0})
        for x in rt.db.all("SELECT * FROM sessions WHERE status='complete' AND closed_at >= ? AND closed_at < ?", (s, e)):
            w = weeks[_week(rt, x["closed_at"])]
            w["completed"] += 1
            w["lead_h"].append(clock.seconds_between(x["opened_at"], x["closed_at"]) / 3600)
        for a in rt.db.all("SELECT opened_at FROM anomalies WHERE opened_at >= ? AND opened_at < ?", (s, e)):
            weeks[_week(rt, a["opened_at"])]["anomalies"] += 1
        for a in rt.db.all("SELECT raised_at FROM alerts WHERE raised_at >= ? AND raised_at < ?", (s, e)):
            weeks[_week(rt, a["raised_at"])]["alerts"] += 1
        return {"from": s, "to": e, "weeks": {
            w: {"completed": v["completed"], "lead_time_h": round(mean(v["lead_h"]), 2) if v["lead_h"] else None,
                "anomalies": v["anomalies"], "alerts": v["alerts"]} for w, v in sorted(weeks.items())}}
    raise ValueError("view must be overview, where, when, time or trends (vehicle: /api/vehicles/{chassis}/history)")


def _count(values: Iterable[Any]) -> dict:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[str(v) if v is not None else "unknown"] += 1
    return dict(sorted(out.items()))
