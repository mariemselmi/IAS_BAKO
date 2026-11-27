"""Reference and configuration data (spec I4): route, expected CAN messages,
thresholds, contacts, stations, shifts, calendar, users, gateways."""

from __future__ import annotations
import json
import os
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "reference.json"


class Config:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.environ.get("BAKO_CONFIG", DEFAULT_PATH))
        self._lock = threading.Lock()
        self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        self._index()

    # ---- editing -------------------------------------------------------------
    def update(self, new: dict[str, Any], save: bool = True) -> None:
        validate(new)
        with self._lock:
            self.data = new
            self._index()
            if save:
                self.path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")

    def _index(self) -> None:
        d = self.data
        tzname = d.get("timezone", "Africa/Tunis")
        try:
            self.tz = ZoneInfo(tzname) if ZoneInfo else timezone(timedelta(hours=1))
        except Exception:
            self.tz = timezone(timedelta(hours=1))
        self.thresholds: dict[str, float] = d.get("thresholds", {})
        can = d.get("can", {})
        self.can_messages = {int(m["can_id"], 16): m for m in can.get("messages", [])}
        self.can_phases = set(can.get("phases", []))
        self.can_start_operation = can.get("start_operation")
        self.gap_factor = float(can.get("gap_factor", 3.0))
        self.gap_min_s = float(can.get("gap_min_s", 2.0))
        self._ops: dict[tuple[str, str], dict] = {}
        for model, m in d.get("models", {}).items():
            for ph in m["phases"]:
                for op in ph["operations"]:
                    self._ops[(model, op["code"])] = {**op, "phase": ph["phase"]}

    # ---- lookups -------------------------------------------------------------
    def t(self, key: str, default: float = 0.0) -> float:
        return float(self.thresholds.get(key, default))

    def models(self) -> list[str]:
        return list(self.data.get("models", {}))

    def phases(self, model: str) -> list[dict]:
        m = self.data.get("models", {}).get(model)
        return m["phases"] if m else []

    def phase_name(self, model: str, phase: int | None) -> str:
        for ph in self.phases(model):
            if ph["phase"] == phase:
                return ph["name"]
        return f"Phase {phase}"

    def operations(self, model: str) -> list[dict]:
        return [{**op, "phase": ph["phase"]} for ph in self.phases(model) for op in ph["operations"]]

    def operation(self, model: str, code: str | None) -> dict | None:
        if not code:
            return None
        return self._ops.get((model, code))

    def target_min(self, code: str) -> float | None:
        for (_, c), op in self._ops.items():
            if c == code:
                return op.get("target_min")
        return None

    def gap_threshold_s(self, can_id: int) -> float:
        m = self.can_messages.get(can_id)
        if not m:
            return self.gap_min_s
        return max(self.gap_min_s, self.gap_factor * m["period_ms"] / 1000.0)

    def phones(self, group: str) -> list[str]:
        return [c["phone"] for c in self.data.get("contacts", {}).get(group, []) if c.get("phone")]

    def user_role(self, user_id: str | None) -> str | None:
        for u in self.data.get("users", []):
            if u["id"] == user_id:
                return u["role"]
        return None

    # ---- time and shifts -------------------------------------------------------
    def local(self, dt: datetime) -> datetime:
        return dt.astimezone(self.tz)

    def shift_for(self, dt: datetime) -> str | None:
        t = self.local(dt).time()
        for s in self.data.get("shifts", []):
            start, end = _hm(s["start"]), _hm(s["end"])
            if start <= end:
                if start <= t < end:
                    return s["id"]
            elif t >= start or t < end:  # crosses midnight
                return s["id"]
        return None

    def shift_window(self, shift_id: str, day: datetime) -> tuple[datetime, datetime]:
        """UTC window of a shift that starts on the local calendar date of `day`."""
        s = next(x for x in self.data["shifts"] if x["id"] == shift_id)
        d = self.local(day).date()
        start = datetime.combine(d, _hm(s["start"]), tzinfo=self.tz)
        end = datetime.combine(d, _hm(s["end"]), tzinfo=self.tz)
        if end <= start:
            end += timedelta(days=1)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    def is_workday(self, dt: datetime) -> bool:
        local = self.local(dt)
        cal = self.data.get("calendar", {})
        if local.date().isoformat() in cal.get("holidays", []):
            return False
        return local.weekday() in cal.get("workdays", [0, 1, 2, 3, 4, 5])


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def validate(d: dict[str, Any]) -> None:
    """Reject config edits that would break the backend."""
    if not isinstance(d.get("models"), dict) or not d["models"]:
        raise ValueError("models: at least one vehicle model is required")
    for name, m in d["models"].items():
        seen: set[str] = set()
        for ph in m.get("phases", []):
            if not isinstance(ph.get("phase"), int) or not 0 <= ph["phase"] <= 6:
                raise ValueError(f"{name}: phase numbers must be 0..6")
            for op in ph.get("operations", []):
                if not op.get("code"):
                    raise ValueError(f"{name}: operation without code")
                if op["code"] in seen:
                    raise ValueError(f"{name}: duplicate operation {op['code']}")
                seen.add(op["code"])
    for m in d.get("can", {}).get("messages", []):
        int(m["can_id"], 16)
        if float(m["period_ms"]) <= 0:
            raise ValueError("can.messages: period_ms must be positive")
    for s in d.get("shifts", []):
        _hm(s["start"]), _hm(s["end"])
