"""Gap detection (backend module Gap detection, KPI 'Data gaps flagged').

Live: every second, flag
  - can_missing            an expected BMS message is absent for longer than gap_factor x period
  - gateway_offline        the bound gateway sends nothing for gateway_offline_s
  - operation_unconfirmed  an operation ended without a confirmation for operation_unconfirmed_min
and at completion
  - operation_missing      a route operation was never performed / confirmed.

Audit: after the fact, `detect_session_gaps` re-scans the stored data for the
same gap kinds. The KPI 'data gaps flagged' compares the two."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from . import clock
from .records import get_session, log_event

if TYPE_CHECKING:
    from .ingest import GatewayState
    from .runtime import Runtime

CONFIRM_TYPES = ("confirmation", "manual_check")


@dataclass
class Gap:
    kind: str
    key: str | None
    start: str
    end: str | None


class GapDetector:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self._can_start_cache: dict[int, str | None] = {}

    # ---- helpers ------------------------------------------------------------------
    def can_expected_since(self, session: dict) -> str | None:
        """Time from which BMS frames are expected for this session, or None."""
        cfg = self.rt.cfg
        if session["phase"] not in cfg.can_phases:
            return None
        sid = session["session_id"]
        if sid in self._can_start_cache and self._can_start_cache[sid]:
            return self._can_start_cache[sid]
        start = None
        if cfg.can_start_operation:
            row = self.rt.db.one(
                "SELECT MIN(timestamp) t FROM events WHERE session_id = ? AND operation = ? "
                "AND type IN ('operation_end','confirmation','manual_check')",
                (sid, cfg.can_start_operation))
            start = row["t"] if row else None
        else:
            row = self.rt.db.one(
                f"SELECT MIN(timestamp) t FROM events WHERE session_id = ? AND type = 'phase_change' "
                f"AND phase IN ({','.join('?' * len(cfg.can_phases))})", (sid, *cfg.can_phases))
            start = (row and row["t"]) or session["opened_at"]
        self._can_start_cache[sid] = start
        return start

    def _open_gap(self, session_id: int, kind: str, key: str | None) -> dict | None:
        return self.rt.db.one("SELECT * FROM gaps WHERE session_id = ? AND kind = ? AND COALESCE(key,'') = COALESCE(?, '') "
                              "AND ended_at IS NULL", (session_id, kind, key))

    def _flag(self, session_id: int, gateway: str | None, kind: str, key: str | None,
              started_at: str, detail: str, ended_at: str | None = None) -> None:
        if ended_at is None and self._open_gap(session_id, kind, key):
            return
        gid = self.rt.db.execute(
            "INSERT INTO gaps (session_id, gateway_id, kind, key, started_at, ended_at, detail) VALUES (?,?,?,?,?,?,?)",
            (session_id, gateway, kind, key, started_at, ended_at, detail))
        session = get_session(self.rt, session_id)
        if session:
            log_event(self.rt, session, "gap", resource=gateway or "backend", touch=False,
                      payload={"gap_id": gid, "kind": kind, "key": key, "detail": detail})
        self.rt.hub.publish("gap", {"id": gid, "session_id": session_id, "kind": kind, "key": key, "detail": detail})

    def _close(self, session_id: int | None, kind: str, key: str | None = None) -> None:
        if session_id is None:
            return
        if key is None:
            self.rt.db.execute("UPDATE gaps SET ended_at = ? WHERE session_id = ? AND kind = ? AND ended_at IS NULL",
                               (clock.iso(), session_id, kind))
        else:
            self.rt.db.execute("UPDATE gaps SET ended_at = ? WHERE session_id = ? AND kind = ? AND key = ? "
                               "AND ended_at IS NULL", (clock.iso(), session_id, kind, key))

    # ---- live hooks -------------------------------------------------------------------
    def on_frame(self, st: "GatewayState", can_id: int) -> None:
        if st.session_id is not None:
            self._close(st.session_id, "can_missing", f"0x{can_id:08X}")

    def on_gateway_seen(self, gateway_id: str) -> None:
        st = self.rt.live.get(gateway_id)
        if st and st.session_id is not None:
            self._close(st.session_id, "gateway_offline")

    def on_confirmed(self, session_id: int, operation: str) -> None:
        self._close(session_id, "operation_unconfirmed", operation)

    def on_binding_changed(self, session_ids: list[int]) -> None:
        for sid in session_ids:
            self._can_start_cache.pop(sid, None)

    def on_completion(self, session_id: int) -> None:
        now = clock.iso()
        self.rt.db.execute("UPDATE gaps SET ended_at = ? WHERE session_id = ? AND ended_at IS NULL", (now, session_id))
        session = get_session(self.rt, session_id)
        for code in missing_operations(self.rt, session):
            self._flag(session_id, None, "operation_missing", code, now, f"{code} not confirmed at completion", now)
        self._can_start_cache.pop(session_id, None)

    def tick(self) -> None:
        now_dt = clock.now()
        now = clock.iso(now_dt)
        t = self.rt.cfg.t
        offline_s = t("gateway_offline_s", 10)
        unconfirmed_s = t("operation_unconfirmed_min", 15) * 60
        for session in self.rt.db.all("SELECT * FROM sessions WHERE status = 'open'"):
            sid = session["session_id"]
            bind = self.rt.db.one("SELECT * FROM gateway_bindings WHERE session_id = ? AND to_ts IS NULL "
                                  "ORDER BY from_ts DESC LIMIT 1", (sid,))
            if bind:
                gw = bind["gateway_id"]
                st = self.rt.live.get(gw)
                last = st.last_line_at if st and st.last_line_at and st.last_line_at > bind["from_ts"] else None
                since = last or bind["from_ts"]
                if (clock.seconds_between(since, now) or 0) > offline_s:
                    self._flag(sid, gw, "gateway_offline", None, since,
                               f"no data from {gw} since {since}")
                else:
                    self._check_can(session, gw, st, bind["from_ts"], now)
            # operations ended but never confirmed
            for row in self.rt.db.all(
                    "SELECT e.operation, MAX(e.timestamp) t FROM events e WHERE e.session_id = ? "
                    "AND e.type = 'operation_end' AND e.payload NOT LIKE '%\"result\":\"%' GROUP BY e.operation",
                    (sid,)):
                if confirmed(self.rt, sid, row["operation"]):
                    continue
                if (clock.seconds_between(row["t"], now) or 0) > unconfirmed_s:
                    self._flag(sid, None, "operation_unconfirmed", row["operation"], row["t"],
                               f"{row['operation']} ended without confirmation")

    def _check_can(self, session: dict, gw: str, st: "GatewayState | None", bound_from: str, now: str) -> None:
        since = self.can_expected_since(session)
        if since is None:
            return
        since = max(since, bound_from)
        alive_at = st.last_line_at if st else None
        for can_id in self.rt.cfg.can_messages:
            last = st.last_by_id.get(can_id) if st else None
            ref = max(last, since) if last else since
            # Flag only when the gateway sent something after the message was due: that proves the
            # message (not the whole gateway) is missing. A full outage becomes one gateway_offline gap.
            if (clock.seconds_between(ref, alive_at) or 0) > self.rt.cfg.gap_threshold_s(can_id):
                name = self.rt.cfg.can_messages[can_id]["name"]
                self._flag(session["session_id"], gw, "can_missing", f"0x{can_id:08X}", ref,
                           f"{name} (0x{can_id:08X}) missing")


# ---- shared queries ---------------------------------------------------------------------

def confirmed(rt: "Runtime", session_id: int, operation: str) -> bool:
    row = rt.db.one(
        "SELECT 1 FROM events WHERE session_id = ? AND operation = ? AND ("
        "type IN ('confirmation','manual_check') OR (type = 'operation_end' AND payload LIKE '%\"result\":\"%')) "
        "LIMIT 1", (session_id, operation))
    return row is not None


def missing_operations(rt: "Runtime", session: dict | None) -> list[str]:
    if not session:
        return []
    return [op["code"] for op in rt.cfg.operations(session["model"])
            if not confirmed(rt, session["session_id"], op["code"])]


def can_windows(rt: "Runtime", session: dict) -> list[tuple[str, str]]:
    """Intervals in which BMS frames were expected: after CAN start, while a gateway was bound."""
    det = rt.gaps
    since = None
    cfg = rt.cfg
    if cfg.can_start_operation:
        row = rt.db.one("SELECT MIN(timestamp) t FROM events WHERE session_id = ? AND operation = ? "
                        "AND type IN ('operation_end','confirmation','manual_check')",
                        (session["session_id"], cfg.can_start_operation))
        since = row["t"] if row else None
    else:
        since = det.can_expected_since({**session, "phase": min(cfg.can_phases or {0})})
    if not since:
        return []
    end_all = session["closed_at"] or clock.iso()
    out = []
    for b in rt.db.all("SELECT from_ts, to_ts FROM gateway_bindings WHERE session_id = ? ORDER BY from_ts",
                       (session["session_id"],)):
        s = max(b["from_ts"], since)
        e = min(b["to_ts"] or end_all, end_all)
        if s < e:
            out.append((s, e))
    return out


def capture_rate(rt: "Runtime", session: dict) -> dict:
    """KPI CAN capture rate: BMS frames received / frames expected."""
    expected = received = 0.0
    by_id: dict[str, dict] = {}
    for s, e in can_windows(rt, session):
        dur = clock.seconds_between(s, e) or 0
        for can_id, m in rt.cfg.can_messages.items():
            exp = dur * 1000.0 / m["period_ms"]
            got = rt.db.scalar("SELECT COUNT(*) FROM can_frames WHERE session_id = ? AND can_id = ? "
                               "AND server_time >= ? AND server_time < ?", (session["session_id"], can_id, s, e)) or 0
            k = f"0x{can_id:08X}"
            d = by_id.setdefault(k, {"expected": 0.0, "received": 0})
            d["expected"] += exp
            d["received"] += got
            expected += exp
            received += min(got, exp)
    rate = (received / expected) if expected else None
    return {"expected": round(expected), "received": int(received), "rate": rate,
            "by_id": {k: {"expected": round(v["expected"]), "received": v["received"],
                          "rate": (min(v["received"], v["expected"]) / v["expected"]) if v["expected"] else None}
                      for k, v in by_id.items()}}


def detect_session_gaps(rt: "Runtime", session: dict) -> list[Gap]:
    """Audit scan of stored data for the gap kinds that the live detector flags."""
    out: list[Gap] = []
    cfg = rt.cfg
    sid = session["session_id"]
    offline_s = cfg.t("gateway_offline_s", 10)
    # gateway silence: sensor JSON arrives every 2 s whenever the gateway is up
    end_all = session["closed_at"] or clock.iso()
    for b in rt.db.all("SELECT gateway_id, from_ts, to_ts FROM gateway_bindings WHERE session_id = ?", (sid,)):
        e = min(b["to_ts"] or end_all, end_all)
        times = [r["server_time"] for r in rt.db.all(
            "SELECT DISTINCT server_time FROM measurements WHERE session_id = ? AND gateway_id = ? "
            "AND server_time >= ? AND server_time < ? ORDER BY server_time", (sid, b["gateway_id"], b["from_ts"], e))]
        times += [r["server_time"] for r in rt.db.all(
            "SELECT server_time FROM can_frames WHERE session_id = ? AND gateway_id = ? "
            "AND server_time >= ? AND server_time < ?", (sid, b["gateway_id"], b["from_ts"], e))]
        times.sort()
        out += _intervals("gateway_offline", None, [b["from_ts"], *times, e], offline_s)
    # expected CAN messages (a silence inside a gateway outage is the outage, not a separate gap)
    offline = [g for g in out if g.kind == "gateway_offline"]
    for s, e in can_windows(rt, session):
        for can_id in cfg.can_messages:
            times = [r["server_time"] for r in rt.db.all(
                "SELECT server_time FROM can_frames WHERE session_id = ? AND can_id = ? AND server_time >= ? "
                "AND server_time < ? ORDER BY server_time", (sid, can_id, s, e))]
            thr = cfg.gap_threshold_s(can_id)
            for g in _intervals("can_missing", f"0x{can_id:08X}", [s, *times, e], thr):
                covered = any((clock.seconds_between(o.start, g.start) or 0) >= -thr and
                              (clock.seconds_between(g.end, o.end) or 0) >= -thr for o in offline)
                if not covered:
                    out.append(g)
    # operations ended without confirmation, or never done
    for op in cfg.operations(session["model"]):
        if confirmed(rt, sid, op["code"]):
            continue
        ended = rt.db.one("SELECT MAX(timestamp) t FROM events WHERE session_id = ? AND operation = ? "
                          "AND type = 'operation_end'", (sid, op["code"]))
        if ended and ended["t"]:
            due = clock.iso(clock.parse(ended["t"]) + timedelta(minutes=cfg.t("operation_unconfirmed_min", 15)))
            if due <= end_all:
                out.append(Gap("operation_unconfirmed", op["code"], ended["t"], None))
        elif session["status"] == "complete":
            out.append(Gap("operation_missing", op["code"], session["closed_at"], session["closed_at"]))
    return out


def _intervals(kind: str, key: str | None, times: list[str], threshold_s: float) -> list[Gap]:
    gaps = []
    for a, b in zip(times, times[1:]):
        if (clock.seconds_between(a, b) or 0) > threshold_s:
            gaps.append(Gap(kind, key, a, b))
    return gaps


def flagged_ratio(rt: "Runtime", session: dict) -> dict:
    """KPI data gaps flagged: detected gaps that the live detector also flagged."""
    detected = detect_session_gaps(rt, session)
    flagged_rows = rt.db.all("SELECT kind, key, started_at, ended_at FROM gaps WHERE session_id = ?",
                             (session["session_id"],))
    hit = 0
    for g in detected:
        g_end = g.end or clock.iso()
        for f in flagged_rows:
            if f["kind"] != g.kind or (f["key"] or None) != g.key:
                continue
            if f["started_at"] <= g_end and (f["ended_at"] is None or f["ended_at"] >= g.start):
                hit += 1
                break
    # operation_unconfirmed flagged at the time counts even if it was later confirmed
    return {"detected": len(detected), "flagged": hit,
            "rate": (hit / len(detected)) if detected else None,
            "flagged_total": len(flagged_rows)}
