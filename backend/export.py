"""Data export (spec O5): CSV event log and IEEE 1849 XES for process mining.

Each event: case = chassis, activity, timestamp, resource (station or gateway).
Operator ids are left out unless include_users=True (data protection: they are
kept for traceability, not for analysis)."""

from __future__ import annotations
import csv
import io
from typing import TYPE_CHECKING, Iterator
from xml.sax.saxutils import escape, quoteattr

from .db import loads

if TYPE_CHECKING:
    from .runtime import Runtime

LIFECYCLE = {"operation_start": "start", "operation_end": "complete"}


def _activity(e: dict) -> str:
    if e["type"] in ("operation_start", "operation_end"):
        return e["operation"]
    if e["type"] in ("confirmation", "manual_check"):
        return f"{e['type']}:{e['operation']}"
    if e["type"] in ("alert", "gap"):
        p = e["payload"] or {}
        return f"{e['type']}:{p.get('rule') or p.get('kind')}"
    if e["type"] == "phase_change":
        return f"phase_{(e['payload'] or {}).get('to')}"
    return e["type"]


def events(rt: "Runtime", start: str | None = None, end: str | None = None,
           chassis: str | None = None) -> list[dict]:
    q = "SELECT * FROM events WHERE session_id IS NOT NULL"
    p: list = []
    if start:
        q += " AND timestamp >= ?"
        p.append(start)
    if end:
        q += " AND timestamp < ?"
        p.append(end)
    if chassis:
        q += " AND chassis_id = ?"
        p.append(chassis)
    rows = rt.db.all(q + " ORDER BY chassis_id, session_id, timestamp, event_id", p)
    for r in rows:
        r["payload"] = loads(r["payload"]) or {}
        r["activity"] = _activity(r)
    return rows


def to_csv(rows: list[dict], include_users: bool = False) -> str:
    buf = io.StringIO()
    cols = ["case", "session", "activity", "timestamp", "resource", "lifecycle", "type", "phase", "operation",
            "station", "shift", "result", "value"] + (["user"] if include_users else [])
    w = csv.writer(buf)
    w.writerow(cols)
    for e in rows:
        p = e["payload"]
        line = [e["chassis_id"], e["session_id"], e["activity"], e["timestamp"], e["resource"],
                LIFECYCLE.get(e["type"], "complete"), e["type"], e["phase"], e["operation"] or "", e["station"] or "",
                e["shift"] or "", p.get("result") or p.get("recheck") or "", "" if p.get("value") is None else p["value"]]
        if include_users:
            line.append(e["user"] or "")
        w.writerow(line)
    return buf.getvalue()


def to_xes(rows: list[dict], include_users: bool = False) -> Iterator[str]:
    yield '<?xml version="1.0" encoding="UTF-8"?>\n'
    yield '<log xes.version="1849-2016" xes.features="nested-attributes" xmlns="http://www.xes-standard.org/">\n'
    for name, prefix, uri in (("Concept", "concept", "http://www.xes-standard.org/concept.xesext"),
                              ("Time", "time", "http://www.xes-standard.org/time.xesext"),
                              ("Organizational", "org", "http://www.xes-standard.org/org.xesext"),
                              ("Lifecycle", "lifecycle", "http://www.xes-standard.org/lifecycle.xesext")):
        yield f'  <extension name="{name}" prefix="{prefix}" uri="{uri}"/>\n'
    yield '  <global scope="trace"><string key="concept:name" value="UNKNOWN"/></global>\n'
    yield ('  <global scope="event"><string key="concept:name" value="UNKNOWN"/>'
           '<date key="time:timestamp" value="1970-01-01T00:00:00.000+00:00"/>'
           '<string key="lifecycle:transition" value="complete"/></global>\n')
    yield '  <classifier name="Activity" keys="concept:name"/>\n'
    yield '  <string key="concept:name" value="BAKO production build log"/>\n'
    current = None
    for e in rows:
        trace = (e["chassis_id"], e["session_id"])
        if trace != current:
            if current is not None:
                yield "  </trace>\n"
            current = trace
            yield (f'  <trace>\n    <string key="concept:name" value={quoteattr(str(e["chassis_id"]))}/>\n'
                   f'    <int key="session" value="{e["session_id"]}"/>\n')
        attrs = [f'<string key="concept:name" value={quoteattr(e["activity"])}/>',
                 f'<date key="time:timestamp" value="{escape(e["timestamp"])}"/>',
                 f'<string key="lifecycle:transition" value="{LIFECYCLE.get(e["type"], "complete")}"/>',
                 f'<string key="org:resource" value={quoteattr(str(e["resource"] or ""))}/>',
                 f'<string key="type" value={quoteattr(e["type"])}/>']
        if e["phase"] is not None:
            attrs.append(f'<int key="phase" value="{int(e["phase"])}"/>')
        for k in ("station", "shift", "operation"):
            if e.get(k):
                attrs.append(f'<string key="{k}" value={quoteattr(str(e[k]))}/>')
        p = e["payload"]
        if p.get("result"):
            attrs.append(f'<string key="result" value={quoteattr(str(p["result"]))}/>')
        if isinstance(p.get("value"), (int, float)):
            attrs.append(f'<float key="value" value="{float(p["value"])}"/>')
        if include_users and e.get("user"):
            attrs.append(f'<string key="org:role_user" value={quoteattr(e["user"])}/>')
        yield "    <event>" + "".join(attrs) + "</event>\n"
    if current is not None:
        yield "  </trace>\n"
    yield "</log>\n"
