"""Server clock. Every gateway line and app event gets its absolute time here
(gateway time is relative, spec section 7). Tests can freeze or move it."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone

_frozen: datetime | None = None


def now() -> datetime:
    return _frozen if _frozen is not None else datetime.now(timezone.utc)


def freeze(dt: datetime | None) -> None:
    global _frozen
    _frozen = dt


def advance(seconds: float) -> None:
    global _frozen
    if _frozen is None:
        raise RuntimeError("clock is not frozen")
    _frozen = _frozen + timedelta(seconds=seconds)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).astimezone(timezone.utc).isoformat(timespec="milliseconds")


def parse(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def seconds_between(a: str | None, b: str | None) -> float | None:
    da, db = parse(a), parse(b)
    if da is None or db is None:
        return None
    return (db - da).total_seconds()
