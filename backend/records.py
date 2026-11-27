"""Sessions, gateway binding and the operator/manager event log (spec I3,
backend modules Binding and Event log)."""

from __future__ import annotations
import base64
import re
from typing import TYPE_CHECKING, Any

from . import clock
from .db import dumps, loads

if TYPE_CHECKING:
    from .runtime import Runtime

APP_EVENT_TYPES = {
    "bind_gateway", "phase_change", "operation_start", "operation_end",
    "confirmation", "manual_check", "anomaly", "intervention", "alert_ack", "completion",
}
RESULTS = {"ok", "not_ok"}


class EventError(ValueError):
    """Rejected event; status is the HTTP code to return."""

    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


# ---- sessions ----------------------------------------------------------------

def get_session(rt: "Runtime", session_id: int) -> dict | None:
    return rt.db.one("SELECT * FROM sessions WHERE session_id = ?", (session_id,))


def open_session_for(rt: "Runtime", chassis_id: str) -> dict | None:
    return rt.db.one("SELECT * FROM sessions WHERE chassis_id = ? AND status = 'open'",
                     (chassis_id,))


def open_sessions(rt: "Runtime") -> list[dict]:
    return rt.db.all("SELECT * FROM sessions WHERE status = 'open' ORDER BY opened_at")


def _require_manager(rt: "Runtime", user: str | None) -> None:
    if not user:
        raise EventError("manager_id is required")
    role = rt.cfg.user_role(user)
    if role is None:
        raise EventError(f"unknown user {user}", 403)
    if role != "manager":
        raise EventError(f"{user} is a {role}; only a manager can do this", 403)


def open_session(rt: "Runtime", chassis: str, model: str, manager_id: str,
                 gateway_id: str | None = None, client_time: str | None = None) -> dict:
    chassis = (chassis or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", chassis):
        raise EventError("chassis id must be 3-40 letters, digits, '.', '_' or '-'")
    if model not in rt.cfg.models():
        raise EventError(f"unknown vehicle model {model}")
    _require_manager(rt, manager_id)
    if open_session_for(rt, chassis):
        raise EventError(f"chassis {chassis} already has an open session", 409)
    now = clock.iso()
    with rt.db.lock:
        rt.db.execute("INSERT INTO chassis (chassis_id, model, created_at) VALUES (?,?,?) "
                      "ON CONFLICT(chassis_id) DO UPDATE SET model = excluded.model",
                      (chassis, model, now))
        sid = rt.db.execute(
            "INSERT INTO sessions (chassis_id, model, manager_id, opened_at, status, phase, last_event_at) "
            "VALUES (?,?,?,?, 'open', 0, ?)", (chassis, model, manager_id, now, now))
    session = get_session(rt, sid)
    log_event(rt, session, "open_session", user=manager_id, client_time=client_time,
              payload={"model": model, "gateway": gateway_id})
    if gateway_id:
        bind_gateway(rt, session, gateway_id, user=manager_id)
    session = get_session(rt, sid)
    rt.hub.publish("session", session)
    return session


def complete_session(rt: "Runtime", session: dict, user: str | None,
                     client_time: str | None = None, note: str | None = None) -> dict:
    _require_manager(rt, user)
    if session["status"] != "open":
        raise EventError("session is already complete", 409)
    now = clock.iso()
    log_event(rt, session, "completion", user=user, client_time=client_time,
              payload={"declaration": "production complete - ready for verification", "note": note})
    rt.db.execute("UPDATE sessions SET status = 'complete', closed_at = ? WHERE session_id = ?",
                  (now, session["session_id"]))
    rt.db.execute("UPDATE gateway_bindings SET to_ts = ? WHERE session_id = ? AND to_ts IS NULL",
                  (now, session["session_id"]))
    rt.gaps.on_completion(session["session_id"])
    rt.alerts.on_session_closed(session["session_id"])
    session = get_session(rt, session["session_id"])
    rt.on_completion(session)
    rt.hub.publish("session", get_session(rt, session["session_id"]))
    return get_session(rt, session["session_id"])


# ---- gateway binding ---------------------------------------------------------

def bind_gateway(rt: "Runtime", session: dict, gateway_id: str, user: str | None = None,
                 from_ts: str | None = None, client_time: str | None = None) -> None:
    gateway_id = gateway_id.strip()
    if not gateway_id:
        raise EventError("gateway id is required")
    known = {g["id"] for g in rt.cfg.data.get("gateways", [])}
    if known and gateway_id not in known:
        raise EventError(f"gateway {gateway_id} is not in the gateway inventory")
    t0 = clock.iso(clock.parse(from_ts)) if from_ts else clock.iso()
    with rt.db.lock:
        # A gateway is on one car at a time, and a car carries one gateway.
        rt.db.execute("UPDATE gateway_bindings SET to_ts = ? WHERE (gateway_id = ? OR session_id = ?) "
                      "AND to_ts IS NULL", (t0, gateway_id, session["session_id"]))
        rt.db.execute("INSERT INTO gateway_bindings (gateway_id, session_id, from_ts) VALUES (?,?,?)",
                      (gateway_id, session["session_id"], t0))
    log_event(rt, session, "bind_gateway", user=user, client_time=client_time,
              payload={"gateway": gateway_id, "from": t0})
    rt.on_binding_changed(gateway_id)


def session_for_gateway(rt: "Runtime", gateway_id: str, at: str | None = None) -> int | None:
    """Session the gateway is bound to at time `at` (default: now)."""
    at = at or clock.iso()
    row = rt.db.one(
        "SELECT session_id FROM gateway_bindings WHERE gateway_id = ? AND from_ts <= ? "
        "AND (to_ts IS NULL OR to_ts > ?) ORDER BY from_ts DESC LIMIT 1",
        (gateway_id, at, at))
    return row["session_id"] if row else None


def gateway_for_session(rt: "Runtime", session_id: int) -> str | None:
    row = rt.db.one("SELECT gateway_id FROM gateway_bindings WHERE session_id = ? AND to_ts IS NULL "
                    "ORDER BY from_ts DESC LIMIT 1", (session_id,))
    return row["gateway_id"] if row else None


# ---- event log ---------------------------------------------------------------

def log_event(rt: "Runtime", session: dict | None, type_: str, *, phase: int | None = None,
              operation: str | None = None, station: str | None = None, shift: str | None = None,
              user: str | None = None, resource: str | None = None, client_time: str | None = None,
              payload: Any = None, touch: bool = True) -> dict:
    now = clock.now()
    ts = clock.iso(now)
    sid = session["session_id"] if session else None
    if phase is None and session:
        phase = session["phase"]
    if shift is None:
        shift = rt.cfg.shift_for(now)
    eid = rt.db.execute(
        "INSERT INTO events (session_id, chassis_id, type, phase, operation, station, shift, user, "
        "resource, timestamp, client_time, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, session["chassis_id"] if session else None, type_, phase, operation, station, shift,
         user, resource or station or ("app" if user else "system"), ts, client_time, dumps(payload)))
    if session and touch:
        rt.db.execute("UPDATE sessions SET last_event_at = ? WHERE session_id = ?", (ts, sid))
    ev = rt.db.one("SELECT * FROM events WHERE event_id = ?", (eid,))
    ev["payload"] = loads(ev["payload"])
    rt.hub.publish("event", ev)
    return ev


def _resolve_session(rt: "Runtime", body: dict) -> dict:
    session = None
    if body.get("session") is not None:
        try:
            session = get_session(rt, int(body["session"]))
        except (TypeError, ValueError):
            raise EventError("session must be a number")
        if session and body.get("chassis") and session["chassis_id"] != body["chassis"]:
            raise EventError("session and chassis do not match")
    elif body.get("chassis"):
        session = open_session_for(rt, body["chassis"])
    if session is None:
        raise EventError("no matching session (give session or chassis of an open session)", 404)
    return session


def record_event(rt: "Runtime", body: dict) -> dict:
    """POST /api/events. Validates and stores one app event (spec 6.2)."""
    type_ = body.get("type")
    if type_ == "open_session":
        s = open_session(rt, body.get("chassis", ""), body.get("model", ""),
                         body.get("manager_id") or body.get("user"), body.get("gateway"),
                         body.get("client_time"))
        return {"session": s}
    if type_ not in APP_EVENT_TYPES:
        raise EventError(f"unknown event type {type_!r}")
    session = _resolve_session(rt, body)
    if type_ == "alert_ack":
        return {"alert": rt.alerts.acknowledge(int(body["alert"]), body.get("user"))}
    if session["status"] != "open":
        raise EventError("session is complete; no further events are accepted", 409)
    user = body.get("confirmed_by") or body.get("user")
    ct = body.get("client_time")
    model = session["model"]

    if type_ == "completion":
        return {"session": complete_session(rt, session, user, ct, body.get("note"))}

    if type_ == "bind_gateway":
        bind_gateway(rt, session, body.get("gateway", ""), user, body.get("from"), ct)
        return {"session": get_session(rt, session["session_id"])}

    if type_ == "phase_change":
        phase = body.get("phase")
        if not isinstance(phase, int) or not 0 <= phase <= 6:
            raise EventError("phase must be an integer 0..6")
        ev = log_event(rt, session, "phase_change", phase=phase, user=user, client_time=ct,
                       payload={"from": session["phase"], "to": phase})
        rt.db.execute("UPDATE sessions SET phase = ? WHERE session_id = ?", (phase, session["session_id"]))
        rt.hub.publish("session", get_session(rt, session["session_id"]))
        return {"event": ev}

    # Remaining types are tied to a route operation and/or station.
    code = body.get("operation")
    op = rt.cfg.operation(model, code)
    if type_ in {"operation_start", "operation_end", "confirmation", "manual_check"}:
        if op is None:
            raise EventError(f"operation {code!r} is not in the route of model {model}")
    station = body.get("station") or (op or {}).get("station") or session["station"]
    phase = body.get("phase", (op or {}).get("phase", session["phase"]))
    shift = body.get("shift")

    result = body.get("result")
    if result is not None and result not in RESULTS:
        raise EventError("result must be 'ok' or 'not_ok'")
    value = body.get("value")
    if value is not None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise EventError("value must be a number")

    if type_ == "operation_start":
        ev = log_event(rt, session, type_, phase=phase, operation=code, station=station, shift=shift,
                       user=user, client_time=ct)
        rt.db.execute("UPDATE sessions SET station = ? WHERE session_id = ?", (station, session["session_id"]))
        rt.hub.publish("session", get_session(rt, session["session_id"]))
        return {"event": ev}

    if type_ in {"operation_end", "confirmation", "manual_check"}:
        if type_ != "operation_end" and result is None:
            raise EventError("result is required for a confirmation")
        if type_ != "operation_end" and not user:
            raise EventError("confirmed_by is required for a confirmation")
        check = (op or {}).get("check")
        payload: dict[str, Any] = {"result": result, "value": value, "confirmed_by": body.get("confirmed_by")}
        if check and value is not None:
            payload["unit"] = check.get("unit")
            payload["in_spec"] = check.get("min", float("-inf")) <= value <= check.get("max", float("inf"))
        ev = log_event(rt, session, type_, phase=phase, operation=code, station=station, shift=shift,
                       user=user, client_time=ct, payload=payload)
        if result is not None:
            rt.gaps.on_confirmed(session["session_id"], code)
        return {"event": ev}

    if type_ == "anomaly":
        atype = (body.get("anomaly_type") or body.get("kind") or "").strip()
        if not atype:
            raise EventError("anomaly_type is required")
        photo = save_photo(rt, body.get("photo"), session["chassis_id"])
        now = clock.iso()
        aid = rt.db.execute(
            "INSERT INTO anomalies (session_id, type, phase, station, description, photo, opened_by, opened_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (session["session_id"], atype, phase, station, body.get("description"), photo, user, now))
        ev = log_event(rt, session, "anomaly", phase=phase, operation=code, station=station, shift=shift,
                       user=user, client_time=ct,
                       payload={"anomaly_id": aid, "anomaly_type": atype,
                                "description": body.get("description"), "photo": photo})
        return {"event": ev, "anomaly_id": aid}

    if type_ == "intervention":
        try:
            aid = int(body.get("anomaly"))
        except (TypeError, ValueError):
            raise EventError("anomaly (id) is required")
        an = rt.db.one("SELECT * FROM anomalies WHERE id = ? AND session_id = ?", (aid, session["session_id"]))
        if an is None:
            raise EventError(f"anomaly {aid} not found on this session", 404)
        if an["closed_at"]:
            raise EventError(f"anomaly {aid} is already closed", 409)
        action = (body.get("action") or "").strip()
        if not action:
            raise EventError("action is required")
        recheck = body.get("recheck") or result
        if recheck not in RESULTS:
            raise EventError("recheck must be 'ok' or 'not_ok'")
        now = clock.iso()
        rt.db.execute("INSERT INTO interventions (anomaly_id, session_id, action, recheck_result, user, created_at) "
                      "VALUES (?,?,?,?,?,?)", (aid, session["session_id"], action, recheck, user, now))
        if recheck == "ok":
            rt.db.execute("UPDATE anomalies SET closed_at = ?, recheck_result = 'ok' WHERE id = ?", (now, aid))
        else:
            rt.db.execute("UPDATE anomalies SET recheck_result = 'not_ok' WHERE id = ?", (aid,))
        ev = log_event(rt, session, "intervention", phase=an["phase"], operation=code, station=an["station"],
                       shift=shift, user=user, client_time=ct,
                       payload={"anomaly_id": aid, "action": action, "recheck": recheck})
        return {"event": ev}

    raise EventError(f"unhandled event type {type_}")  # pragma: no cover


_DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", re.S)


def save_photo(rt: "Runtime", data_url: str | None, chassis: str) -> str | None:
    if not data_url:
        return None
    m = _DATA_URL.match(data_url)
    if not m:
        raise EventError("photo must be a base64 PNG, JPEG or WebP data URL")
    raw = base64.b64decode(m.group(2), validate=False)
    if len(raw) > 5_000_000:
        raise EventError("photo is larger than 5 MB")
    ext = "jpg" if m.group(1) == "jpeg" else m.group(1)
    folder = rt.data_dir / "photos"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{chassis}-{clock.now().strftime('%Y%m%dT%H%M%S%f')}.{ext}"
    (folder / name).write_bytes(raw)
    return f"photos/{name}"


def session_events(rt: "Runtime", session_id: int) -> list[dict]:
    rows = rt.db.all("SELECT * FROM events WHERE session_id = ? ORDER BY timestamp, event_id", (session_id,))
    for r in rows:
        r["payload"] = loads(r["payload"])
    return rows
