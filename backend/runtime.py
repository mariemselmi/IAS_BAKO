"""Wires the backend modules together and runs the background loops:
ingest flush, alert and gap ticks, SMS outbox and the report scheduler."""

from __future__ import annotations
import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Callable

from . import clock, reports
from .alerts import AlertEngine
from .config import ROOT, Config
from .db import Database
from .gaps import GapDetector
from .hub import Hub
from .ingest import GatewayState, Ingestor
from .records import get_session, log_event

log = logging.getLogger("bako")


class Runtime:
    def __init__(self, db_path: str | Path = ROOT / "var" / "bako.db", config_path: str | Path | None = None,
                 data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir or ROOT / "var")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = Config(config_path)
        self.db = Database(db_path)
        self.hub = Hub()
        self.live: dict[str, GatewayState] = {}
        self.writers: dict[str, asyncio.StreamWriter] = {}
        self.alerts = AlertEngine(self)
        self.gaps = GapDetector(self)
        self.ingest = Ingestor(self)
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self.auto_reports = True

    # ---- hooks called by the modules -------------------------------------------------------
    def on_bms_online(self, st: GatewayState) -> None:
        session = get_session(self, st.session_id) if st.session_id else None
        if session:
            log_event(self, session, "bms_online", resource=st.gateway_id, touch=False,
                      payload={"gateway": st.gateway_id})

    def on_binding_changed(self, gateway_id: str) -> None:
        old = self.live.get(gateway_id)
        old_sid = old.session_id if old else None
        # Gateway-scoped alerts belong to the car the gateway was on.
        for a in self.db.all("SELECT alert_id FROM alerts WHERE gateway_id = ? AND cleared_at IS NULL", (gateway_id,)):
            self.db.execute("UPDATE alerts SET cleared_at = ? WHERE alert_id = ?", (clock.iso(), a["alert_id"]))
        self.ingest.refresh_binding(gateway_id)
        new = self.live.get(gateway_id)
        self.gaps.on_binding_changed([x for x in (old_sid, new.session_id if new else None) if x])

    def on_completion(self, session: dict) -> None:
        if not self.auto_reports:
            return
        try:
            path = reports.build_record_pdf(self, session)
            rel = str(path.relative_to(self.data_dir))
            self.db.execute("UPDATE sessions SET record_pdf = ? WHERE session_id = ?", (rel, session["session_id"]))
            reports.store_report(self, "vehicle", f"{session['chassis_id']}#{session['session_id']}", path,
                                 {"chassis": session["chassis_id"], "session": session["session_id"]})
        except Exception:  # the record can be regenerated on demand
            log.exception("build record PDF failed for %s", session["chassis_id"])

    # ---- SMS outbox (O6: messages for the gateway to send) ---------------------------------
    def queue_sms(self, phone: str, text: str, reason: str = "", gateway: str | None = None) -> int:
        sid = self.db.execute("INSERT INTO sms_outbox (phone, text, reason, gateway_id, created_at) VALUES (?,?,?,?,?)",
                              (phone, text[:160], reason, gateway, clock.iso()))
        self._on_loop(self.flush_sms)
        return sid

    def flush_sms(self) -> None:
        """Send queued SMS through a connected gateway. Must run on the event loop."""
        for m in self.db.all("SELECT * FROM sms_outbox WHERE status = 'queued' ORDER BY id LIMIT 20"):
            gw = m["gateway_id"] if m["gateway_id"] in self.writers else next(iter(self.writers), None)
            if gw is None:
                return  # no gateway connected; retried every tick
            if self.ingest.send(gw, f"SMS {m['id']} {m['phone']} {m['text']}"):
                self.db.execute("UPDATE sms_outbox SET status = 'sent', sent_at = ?, gateway_id = ? WHERE id = ?",
                                (clock.iso(), gw, m["id"]))

    def _on_loop(self, fn: Callable[[], None]) -> None:
        if self.loop is None or self.loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            fn()
        else:
            self.loop.call_soon_threadsafe(fn)

    # ---- scheduled reports --------------------------------------------------------------------
    def run_report_schedule(self) -> None:
        now = clock.now()
        # Shift reports: for every shift that ended in the last 2 hours.
        for shift in self.cfg.data.get("shifts", []):
            for days_back in (0, 1):
                day = now - timedelta(days=days_back)
                start, end = self.cfg.shift_window(shift["id"], day)
                if not (end <= now < end + timedelta(hours=2)) or not self.cfg.is_workday(start):
                    continue
                date = self.cfg.local(start).date().isoformat()
                key = f"{date}/{shift['id']}"
                if self.db.one("SELECT 1 FROM reports WHERE type = 'shift' AND period_key = ?", (key,)):
                    continue
                rep = reports.shift_report(self, date, shift["id"])
                folder = self.data_dir / "reports"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"shift-{date}-{shift['id']}.html"
                path.write_text(reports.shift_report_html(self, rep), encoding="utf-8")
                reports.store_report(self, "shift", key, path, {"sms": rep["sms"]})
                for phone in self.cfg.phones("supervisors"):
                    self.queue_sms(phone, rep["sms"], reason=f"shift:{key}")
        # Weekly report: on Monday, for the week that just ended.
        local = self.cfg.local(now)
        if local.weekday() == 0 and local.hour >= 6:
            y, w, _ = (local - timedelta(days=7)).isocalendar()
            key = f"{y}-W{w:02d}"
            if not self.db.one("SELECT 1 FROM reports WHERE type = 'weekly' AND period_key = ?", (key,)):
                path = reports.weekly_report_pdf(self, key)
                reports.store_report(self, "weekly", key, path, {"week": key})

    # ---- background loops ---------------------------------------------------------------------
    async def start(self, tcp_host: str = "0.0.0.0", tcp_port: int = 5000) -> None:
        self.loop = asyncio.get_running_loop()
        self.hub.attach(self.loop)
        await self.ingest.serve(tcp_host, tcp_port)
        log.info("gateway TCP ingest listening on %s:%d", tcp_host, tcp_port)
        self._tasks = [asyncio.create_task(self._every(1.0, self.tick), name="tick"),
                       asyncio.create_task(self._every(60.0, self.run_report_schedule), name="reports")]

    def tick(self) -> None:
        self.ingest.flush()
        self.alerts.tick()
        self.gaps.tick()
        self.flush_sms()

    async def _every(self, period: float, fn: Callable[[], None]) -> None:
        while True:
            try:
                fn()
            except Exception:
                log.exception("background task %s failed", getattr(fn, "__name__", fn))
            await asyncio.sleep(period)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.ingest.server:
            self.ingest.server.close()
            for w in list(self.writers.values()):
                w.close()
            await self.ingest.server.wait_closed()
        self.ingest.flush()
