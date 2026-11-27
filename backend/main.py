"""REST API + WebSocket (spec 6.4) and the operator app / dashboard pages."""

from __future__ import annotations
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

from . import clock, export, kpi, records, reports
from .config import ROOT
from .records import EventError
from .runtime import Runtime

STATIC = Path(__file__).resolve().parent / "static"
NO_CACHE = {"Cache-Control": "no-cache"}  # pages revalidate, so plant devices pick up updates


def create_app(rt: Runtime | None = None, start_background: bool = True) -> FastAPI:
    runtime = rt or Runtime(os.environ.get("BAKO_DB", str(ROOT / "var" / "bako.db")),
                            os.environ.get("BAKO_CONFIG"), os.environ.get("BAKO_DATA"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            await runtime.start(os.environ.get("BAKO_TCP_HOST", "0.0.0.0"), int(os.environ.get("BAKO_TCP_PORT", "5000")))
        else:
            runtime.loop = asyncio.get_running_loop()
            runtime.hub.attach(runtime.loop)
        yield
        if start_background:
            await runtime.stop()

    app = FastAPI(title="BAKO production monitoring", version="0.1", lifespan=lifespan)
    app.state.rt = runtime

    @app.exception_handler(EventError)
    async def _event_error(_: Request, exc: EventError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # ---- pages ----------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/dashboard")

    @app.get("/app", include_in_schema=False)
    def operator_app():
        return FileResponse(STATIC / "app.html", media_type="text/html", headers=NO_CACHE)

    @app.get("/dashboard", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC / "dashboard.html", media_type="text/html", headers=NO_CACHE)

    @app.get("/static/{name:path}", include_in_schema=False)
    def static(name: str):
        p = (STATIC / name).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"} if "vendor/" in name else NO_CACHE)

    @app.get("/api/photos/{name}", include_in_schema=False)
    def photo(name: str):
        folder = (runtime.data_dir / "photos").resolve()
        p = (folder / name).resolve()
        if p.parent != folder or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    # ---- reference data (I4) -----------------------------------------------------------------
    @app.get("/api/config")
    def get_config():
        return runtime.cfg.data

    @app.put("/api/config")
    def put_config(body: dict = Body(...)):
        runtime.cfg.update(body)
        return {"ok": True}

    # ---- sessions and events (I3) -------------------------------------------------------------
    @app.post("/api/sessions", status_code=201)
    def open_session(body: dict = Body(...)):
        return records.open_session(runtime, body.get("chassis", ""), body.get("model", ""),
                                    body.get("manager_id") or body.get("user"), body.get("gateway"),
                                    body.get("client_time"))

    @app.get("/api/sessions")
    def list_sessions(status: str | None = None, limit: int = 100):
        if status:
            return runtime.db.all("SELECT * FROM sessions WHERE status = ? ORDER BY opened_at DESC LIMIT ?", (status, limit))
        return runtime.db.all("SELECT * FROM sessions ORDER BY opened_at DESC LIMIT ?", (limit,))

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: int):
        s = records.get_session(runtime, session_id)
        if not s:
            raise HTTPException(404, "session not found")
        return reports.session_record(runtime, s)

    @app.post("/api/sessions/{session_id}/complete")
    def complete(session_id: int, body: dict = Body(default={})):
        s = records.get_session(runtime, session_id)
        if not s:
            raise HTTPException(404, "session not found")
        return records.complete_session(runtime, s, body.get("user") or body.get("manager_id"),
                                        body.get("client_time"), body.get("note"))

    @app.post("/api/events", status_code=201)
    def post_event(body: dict = Body(...)):
        return records.record_event(runtime, body)

    @app.get("/api/anomalies")
    def anomalies(status: str = "open", session: int | None = None):
        q = "SELECT a.*, s.chassis_id FROM anomalies a JOIN sessions s USING(session_id) WHERE 1=1"
        p: list[Any] = []
        if status == "open":
            q += " AND a.closed_at IS NULL"
        if session is not None:
            q += " AND a.session_id = ?"
            p.append(session)
        return runtime.db.all(q + " ORDER BY a.opened_at DESC LIMIT 200", p)

    # ---- vehicles and build record (O3) --------------------------------------------------------
    @app.get("/api/vehicles")
    def vehicles(q: str | None = None, limit: int = 100):
        sql = ("SELECT c.chassis_id, c.model, s.session_id, s.status, s.phase, s.station, s.opened_at, s.closed_at "
               "FROM chassis c JOIN sessions s ON s.session_id = (SELECT MAX(session_id) FROM sessions WHERE chassis_id = c.chassis_id)")
        if q:
            return runtime.db.all(sql + " WHERE c.chassis_id LIKE ? ORDER BY s.opened_at DESC LIMIT ?", (f"%{q}%", limit))
        return runtime.db.all(sql + " ORDER BY s.opened_at DESC LIMIT ?", (limit,))

    @app.get("/api/vehicles/{chassis}/history")
    def history(chassis: str):
        h = reports.vehicle_history(runtime, chassis)
        if h is None:
            raise HTTPException(404, "chassis not found")
        return h

    @app.get("/api/vehicles/{chassis}/record.pdf")
    def record_pdf(chassis: str, session: int | None = None):
        s = (records.get_session(runtime, session) if session else
             runtime.db.one("SELECT * FROM sessions WHERE chassis_id = ? ORDER BY session_id DESC LIMIT 1", (chassis,)))
        if not s or s["chassis_id"] != chassis:
            raise HTTPException(404, "chassis not found")
        if s["record_pdf"] and (runtime.data_dir / s["record_pdf"]).is_file():
            path = runtime.data_dir / s["record_pdf"]
        else:
            path = reports.build_record_pdf(runtime, s)
        return FileResponse(path, media_type="application/pdf", filename=path.name)

    # ---- KPIs and analytics (O4) -----------------------------------------------------------------
    @app.get("/api/kpi")
    def get_kpi(from_: str | None = Query(None, alias="from"), to: str | None = None, group_by: str | None = None):
        return kpi.compute(runtime, from_, to, group_by)

    @app.get("/api/analytics/{view}")
    def get_analytics(view: str, from_: str | None = Query(None, alias="from"), to: str | None = None):
        return kpi.analytics(runtime, view, from_, to)

    # ---- alerts (O2) -------------------------------------------------------------------------------
    @app.get("/api/alerts")
    def list_alerts(status: str = "open", limit: int = 200):
        return runtime.alerts.list(status, limit)

    @app.post("/api/alerts/{alert_id}/ack")
    def ack(alert_id: int, body: dict = Body(default={})):
        return runtime.alerts.acknowledge(alert_id, body.get("user"))

    # ---- reports (O5) and exports ----------------------------------------------------------------------
    @app.get("/api/reports")
    def list_reports(type: str | None = None):
        return reports.list_reports(runtime, type)

    @app.get("/api/reports/{type_}")
    def get_report(type_: str, chassis: str | None = None, date: str | None = None, shift: str | None = None,
                   week: str | None = None, format: str = "json"):
        if type_ == "vehicle":
            if not chassis:
                raise HTTPException(422, "chassis is required")
            if format == "pdf":
                return record_pdf(chassis)
            h = reports.vehicle_history(runtime, chassis)
            if h is None:
                raise HTTPException(404, "chassis not found")
            return h
        if type_ == "shift":
            now = clock.now()
            shift = shift or runtime.cfg.shift_for(now)
            date = date or runtime.cfg.local(now).date().isoformat()
            rep = reports.shift_report(runtime, date, shift)
            if format == "html":
                return HTMLResponse(reports.shift_report_html(runtime, rep))
            return rep
        if type_ == "weekly":
            if not week:
                y, w, _ = runtime.cfg.local(clock.now()).isocalendar()
                week = f"{y}-W{w:02d}"
            if format == "pdf":
                path = reports.weekly_report_pdf(runtime, week)
                return FileResponse(path, media_type="application/pdf", filename=path.name)
            return reports.weekly_report(runtime, week)
        raise HTTPException(404, "report type must be vehicle, shift or weekly")

    @app.get("/api/export/csv")
    def export_csv(from_: str | None = Query(None, alias="from"), to: str | None = None, chassis: str | None = None,
                   include_users: bool = False):
        s, e = _opt_range(from_, to)
        rows = export.events(runtime, s, e, chassis)
        return Response(export.to_csv(rows, include_users), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="bako-events.csv"'})

    @app.get("/api/export/xes")
    def export_xes(from_: str | None = Query(None, alias="from"), to: str | None = None, chassis: str | None = None,
                   include_users: bool = False):
        s, e = _opt_range(from_, to)
        rows = export.events(runtime, s, e, chassis)
        return StreamingResponse(export.to_xes(rows, include_users), media_type="application/xml",
                                 headers={"Content-Disposition": 'attachment; filename="bako-events.xes"'})

    @app.get("/api/export/frames.csv")
    def export_frames(session: int):
        rows = runtime.db.all("SELECT server_time, gateway_id, can_id, dlc, data FROM can_frames WHERE session_id = ? "
                              "ORDER BY server_time, id", (session,))
        lines = ["server_time,gateway_id,can_id,dlc,data"] + [
            f"{r['server_time']},{r['gateway_id']},0x{r['can_id']:08X},{r['dlc']},{r['data']}" for r in rows]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv")

    def _opt_range(f: str | None, t: str | None) -> tuple[str | None, str | None]:
        if not f and not t:
            return None, None
        return kpi.parse_range(runtime, f, t)

    # ---- live view (O1) and gateway downlink (O6) ----------------------------------------------------
    @app.get("/api/live")
    def live():
        sessions = records.open_sessions(runtime)
        gw_by_session = {st.session_id: st.view() for st in runtime.live.values() if st.session_id}
        out = []
        for s in sessions:
            gw = records.gateway_for_session(runtime, s["session_id"])
            out.append({**s, "phase_name": runtime.cfg.phase_name(s["model"], s["phase"]), "gateway_id": gw,
                        "phases": reports.phase_states(runtime, s),
                        "gateway": gw_by_session.get(s["session_id"]),
                        "open_anomalies": runtime.db.scalar("SELECT COUNT(*) FROM anomalies WHERE session_id = ? "
                                                            "AND closed_at IS NULL", (s["session_id"],)),
                        "open_gaps": runtime.db.scalar("SELECT COUNT(*) FROM gaps WHERE session_id = ? AND ended_at IS NULL",
                                                       (s["session_id"],))})
        return {"now": clock.iso(), "sessions": out, "gateways": gateways(), "alerts": runtime.alerts.list("open", 50)}

    @app.get("/api/gateways")
    def gateways():
        inv = {g["id"]: g for g in runtime.cfg.data.get("gateways", [])}
        out = []
        for gid in sorted(set(inv) | set(runtime.live)):
            st = runtime.live.get(gid)
            v = st.view() if st else {"gateway_id": gid, "connected": False}
            v["note"] = inv.get(gid, {}).get("note")
            v["bound_session"] = records.session_for_gateway(runtime, gid)
            out.append(v)
        return out

    @app.post("/api/gateways/{gateway_id}/config")
    async def push_gateway_config(gateway_id: str):
        t = runtime.cfg.thresholds
        cfg = {"phones": runtime.cfg.phones("supervisors"), "bms_silent_s": t.get("bms_silent_s"),
               "dcdc_min_v": t.get("dcdc_min_v"), "dcdc_max_v": t.get("dcdc_max_v"),
               "temp_warning_c": t.get("temp_warning_c"), "temp_critical_c": t.get("temp_critical_c"),
               "sms_rate_limit_min": t.get("sms_rate_limit_min")}
        import json
        ok = runtime.ingest.send(gateway_id, "CONFIG " + json.dumps(cfg, separators=(",", ":")))
        if not ok:
            raise HTTPException(409, "gateway is not connected")
        return {"sent": cfg}

    @app.get("/api/sms")
    def sms(limit: int = 100):
        return runtime.db.all("SELECT * FROM sms_outbox ORDER BY id DESC LIMIT ?", (limit,))

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        q = runtime.hub.subscribe()
        try:
            await socket.send_json({"type": "hello", "data": {"now": clock.iso()}})
            while True:
                msg = await q.get()
                await socket.send_text(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            runtime.hub.unsubscribe(q)

    return app
