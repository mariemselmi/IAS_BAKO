"""Alert rules and dispatcher (spec O2, Table 1; backend module Alert dispatcher).

The gateway evaluates the SMS rules locally and sends the SMS itself (it keeps
working if the backend is down). The backend evaluates the same rules on the
data it receives, so every alert shows on the dashboard, and escalates alerts
nobody acknowledges. When the gateway reports `ALERT:{"rule":..,"sms":true}`
the matching alert is marked sms_sent.

If `contacts.sms_mode` is "backend", the backend asks the gateway to send the
SMS instead (same 15-minute rate limit)."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

from . import clock
from .records import EventError, get_session, log_event

if TYPE_CHECKING:
    from .ingest import GatewayState
    from .runtime import Runtime

RULES: dict[str, dict[str, Any]] = {
    "bms_fault":         {"severity": "critical", "sms": True,  "label": "BMS fault"},
    "bms_silent":        {"severity": "critical", "sms": True,  "label": "BMS silent"},
    "dcdc_out_of_range": {"severity": "critical", "sms": True,  "label": "DC-DC out of range"},
    "temp_critical":     {"severity": "critical", "sms": True,  "label": "Temperature critical"},
    "temp_warning":      {"severity": "warning",  "sms": False, "label": "Temperature warning"},
    "cell_imbalance":    {"severity": "warning",  "sms": False, "label": "Cell imbalance"},
    "session_idle":      {"severity": "warning",  "sms": False, "label": "Session idle"},
}

SENSOR_TEMPS = ("temp_mppt", "temp_dcdc", "temp_motor")


def sms_text(text: str) -> str:
    """GSM SMS: 160 characters, plain ASCII."""
    t = text.encode("ascii", "replace").decode()
    return t if len(t) <= 160 else t[:157] + "..."


class AlertEngine:
    def __init__(self, rt: "Runtime"):
        self.rt = rt

    # ---- condition tracking ------------------------------------------------------
    def _open_alert(self, rule: str, gateway: str | None, session_id: int | None) -> dict | None:
        return self.rt.db.one(
            "SELECT * FROM alerts WHERE rule = ? AND cleared_at IS NULL AND "
            "COALESCE(gateway_id,'') = COALESCE(?, '') AND COALESCE(session_id,-1) = COALESCE(?, -1) "
            "ORDER BY alert_id DESC LIMIT 1", (rule, gateway, session_id))

    def set_condition(self, rule: str, active: bool, *, gateway: str | None, session_id: int | None,
                      message: str = "", value: Any = None) -> dict | None:
        existing = self._open_alert(rule, gateway, session_id)
        if not active:
            if existing:
                self.rt.db.execute("UPDATE alerts SET cleared_at = ? WHERE alert_id = ?",
                                   (clock.iso(), existing["alert_id"]))
                self.rt.hub.publish("alert", self.get(existing["alert_id"]))
            return None
        if existing:
            return existing
        # Hysteresis: do not re-raise a condition that cleared a moment ago.
        last = self.rt.db.one(
            "SELECT cleared_at FROM alerts WHERE rule = ? AND COALESCE(gateway_id,'') = COALESCE(?, '') "
            "AND COALESCE(session_id,-1) = COALESCE(?, -1) ORDER BY alert_id DESC LIMIT 1",
            (rule, gateway, session_id))
        if last and last["cleared_at"]:
            age = clock.seconds_between(last["cleared_at"], clock.iso())
            if age is not None and age < self.rt.cfg.t("alert_rearm_s", 30):
                return None
        return self.raise_alert(rule, gateway=gateway, session_id=session_id, message=message, value=value)

    def raise_alert(self, rule: str, *, gateway: str | None, session_id: int | None,
                    message: str, value: Any = None, sms_sent: bool = False) -> dict:
        spec = RULES.get(rule, {"severity": "warning", "sms": False, "label": rule})
        now = clock.iso()
        aid = self.rt.db.execute(
            "INSERT INTO alerts (session_id, gateway_id, rule, severity, message, value, raised_at, sms_sent) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (session_id, gateway, rule, spec["severity"], message, None if value is None else str(value),
             now, int(sms_sent)))
        session = get_session(self.rt, session_id) if session_id else None
        if session:
            log_event(self.rt, session, "alert", resource=gateway or "backend", touch=False,
                      payload={"alert_id": aid, "rule": rule, "severity": spec["severity"], "message": message})
        alert = self.get(aid)
        self.rt.hub.publish("alert", alert)
        if spec["sms"] and self.rt.cfg.data.get("contacts", {}).get("sms_mode", "gateway") == "backend":
            self._sms_for(alert)
        return alert

    def get(self, alert_id: int) -> dict | None:
        a = self.rt.db.one(
            "SELECT a.*, s.chassis_id FROM alerts a LEFT JOIN sessions s ON s.session_id = a.session_id "
            "WHERE a.alert_id = ?", (alert_id,))
        if a:
            a["label"] = RULES.get(a["rule"], {}).get("label", a["rule"])
        return a

    def _sms_for(self, alert: dict) -> None:
        if self._rate_limited(alert["rule"], alert["gateway_id"]):
            return
        text = self.format_sms(alert)
        for phone in self.rt.cfg.phones("supervisors"):
            self.rt.queue_sms(phone, text, reason=f"alert:{alert['alert_id']}", gateway=alert["gateway_id"])
        self.rt.db.execute("UPDATE alerts SET sms_sent = 1 WHERE alert_id = ?", (alert["alert_id"],))

    def _rate_limited(self, rule: str, gateway: str | None) -> bool:
        window = self.rt.cfg.t("sms_rate_limit_min", 15) * 60
        last = self.rt.db.one(
            "SELECT raised_at FROM alerts WHERE rule = ? AND COALESCE(gateway_id,'') = COALESCE(?, '') "
            "AND sms_sent = 1 ORDER BY alert_id DESC LIMIT 1", (rule, gateway))
        if not last:
            return False
        age = clock.seconds_between(last["raised_at"], clock.iso())
        return age is not None and age < window

    def format_sms(self, alert: dict) -> str:
        local = self.rt.cfg.local(clock.parse(alert["raised_at"])).strftime("%H:%M")
        where = alert.get("chassis_id") or alert.get("gateway_id") or "line"
        return sms_text(f"BAKO {where}: {alert['message']} [{local}]")

    # ---- rule evaluation -----------------------------------------------------------
    def evaluate_battery(self, st: "GatewayState") -> None:
        b, t = st.battery, self.rt.cfg.t
        gw, sid = st.gateway_id, st.session_id
        if "fault_level" in b:
            fl, ec = b.get("fault_level", 0), b.get("error_code", 0)
            self.set_condition("bms_fault", bool(fl or ec), gateway=gw, session_id=sid,
                               message=f"BMS fault level {fl} code {ec}", value=f"{fl}/{ec}")
        if "cell_spread_mv" in b:
            spread = b["cell_spread_mv"]
            self.set_condition("cell_imbalance", spread > t("cell_spread_mv", 50), gateway=gw, session_id=sid,
                               message=f"Cell spread {spread} mV", value=spread)
        self._temps(st)

    def evaluate_sensors(self, st: "GatewayState") -> None:
        s, t = st.sensors, self.rt.cfg.t
        v12, v72 = s.get("v12_dc_out"), s.get("v72_dc_in")
        if v12 is not None:
            # "while present": the converter has its high-voltage input, or shows some output.
            present = (v72 is not None and v72 >= t("dcdc_input_present_v", 40)) or v12 >= t("dcdc_present_v", 3)
            bad = present and not (t("dcdc_min_v", 11.0) <= v12 <= t("dcdc_max_v", 14.5))
            self.set_condition("dcdc_out_of_range", bad, gateway=st.gateway_id, session_id=st.session_id,
                               message=f"DC-DC 12V output {v12:.2f} V", value=v12)
        self._temps(st)

    def _temps(self, st: "GatewayState") -> None:
        probes: dict[str, float] = {}
        for k in SENSOR_TEMPS:
            v = st.sensors.get(k)
            if isinstance(v, (int, float)) and v > -100:  # DS18B20 error is -127
                probes[k] = v
        for k, v in st.battery.items():
            if k.startswith("battery_temp_") or k == "temp_max_c":
                probes[k] = v
        if not probes:
            return
        name, hottest = max(probes.items(), key=lambda kv: kv[1])
        t = self.rt.cfg.t
        crit = hottest >= t("temp_critical_c", 85)
        warn = hottest >= t("temp_warning_c", 70) and not crit
        msg = f"{name} {hottest:.0f} C"
        self.set_condition("temp_critical", crit, gateway=st.gateway_id, session_id=st.session_id,
                           message=f"Temperature critical: {msg}", value=hottest)
        self.set_condition("temp_warning", warn, gateway=st.gateway_id, session_id=st.session_id,
                           message=f"Temperature warning: {msg}", value=hottest)

    def tick(self) -> None:
        """Time-based rules, run every second."""
        now = clock.iso()
        silent_s = self.rt.cfg.t("bms_silent_s", 5)
        for st in list(self.rt.live.values()):
            if st.first_bms_at and st.last_bms_at:
                age = clock.seconds_between(st.last_bms_at, now) or 0
                self.set_condition("bms_silent", age > silent_s, gateway=st.gateway_id, session_id=st.session_id,
                                   message=f"BMS silent for {age:.0f} s", value=round(age, 1))
        idle_s = self.rt.cfg.t("session_idle_min", 60) * 60
        for s in self.rt.db.all("SELECT session_id, chassis_id, last_event_at FROM sessions WHERE status='open'"):
            age = clock.seconds_between(s["last_event_at"], now) or 0
            self.set_condition("session_idle", age > idle_s, gateway=None, session_id=s["session_id"],
                               message=f"No event for {age / 60:.0f} min", value=round(age / 60))
        self._escalate(now)

    def _escalate(self, now: str) -> None:
        after = self.rt.cfg.t("escalate_after_min", 10) * 60
        rows = self.rt.db.all("SELECT alert_id, raised_at FROM alerts WHERE acknowledged_at IS NULL "
                              "AND escalated_at IS NULL AND severity = 'critical'")
        for r in rows:
            if (clock.seconds_between(r["raised_at"], now) or 0) < after:
                continue
            self.rt.db.execute("UPDATE alerts SET escalated_at = ? WHERE alert_id = ?", (now, r["alert_id"]))
            alert = self.get(r["alert_id"])
            text = sms_text("UNACKNOWLEDGED " + self.format_sms(alert))
            for phone in self.rt.cfg.phones("escalation"):
                self.rt.queue_sms(phone, text, reason=f"escalation:{r['alert_id']}", gateway=alert["gateway_id"])
            self.rt.hub.publish("alert", self.get(r["alert_id"]))

    # ---- gateway reports and acknowledgement ------------------------------------------
    def on_gateway_alert(self, st: "GatewayState", obj: dict) -> None:
        rule = str(obj.get("rule", "")).strip() or "gateway_alert"
        sms = bool(obj.get("sms"))
        recent = self.rt.db.one(
            "SELECT alert_id, raised_at FROM alerts WHERE rule = ? AND gateway_id = ? "
            "ORDER BY alert_id DESC LIMIT 1", (rule, st.gateway_id))
        if recent and (clock.seconds_between(recent["raised_at"], clock.iso()) or 0) < 60:
            if sms:
                self.rt.db.execute("UPDATE alerts SET sms_sent = 1 WHERE alert_id = ?", (recent["alert_id"],))
                self.rt.hub.publish("alert", self.get(recent["alert_id"]))
            return
        self.raise_alert(rule, gateway=st.gateway_id, session_id=st.session_id,
                         message=str(obj.get("msg") or RULES.get(rule, {}).get("label", rule))[:160],
                         value=obj.get("value"), sms_sent=sms)

    def acknowledge(self, alert_id: int, user: str | None) -> dict:
        if not user:
            raise EventError("user is required to acknowledge an alert")
        a = self.get(alert_id)
        if a is None:
            raise EventError(f"alert {alert_id} not found", 404)
        if a["acknowledged_at"]:
            return a
        now = clock.iso()
        self.rt.db.execute("UPDATE alerts SET acknowledged_at = ?, acknowledged_by = ? WHERE alert_id = ?",
                           (now, user, alert_id))
        session = get_session(self.rt, a["session_id"]) if a["session_id"] else None
        if session:
            log_event(self.rt, session, "alert_ack", user=user,
                      payload={"alert_id": alert_id, "rule": a["rule"]})
        a = self.get(alert_id)
        self.rt.hub.publish("alert", a)
        return a

    def on_session_closed(self, session_id: int) -> None:
        self.set_condition("session_idle", False, gateway=None, session_id=session_id)

    def list(self, status: str = "open", limit: int = 200) -> list[dict]:
        where = {"open": "a.acknowledged_at IS NULL", "active": "a.cleared_at IS NULL",
                 "all": "1=1"}.get(status, "a.acknowledged_at IS NULL")
        rows = self.rt.db.all(
            f"SELECT a.*, s.chassis_id FROM alerts a LEFT JOIN sessions s ON s.session_id = a.session_id "
            f"WHERE {where} ORDER BY a.alert_id DESC LIMIT ?", (limit,))
        for r in rows:
            r["label"] = RULES.get(r["rule"], {}).get("label", r["rule"])
        return rows
