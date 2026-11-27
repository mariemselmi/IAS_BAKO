"""WebSocket fan-out for live dashboard updates (spec: WS /ws).

publish() is safe to call from API worker threads and from the event loop."""

from __future__ import annotations
import asyncio
import json
from typing import Any


class Hub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    def publish(self, kind: str, data: Any) -> None:
        if self.loop is None or not self.clients:
            return
        msg = json.dumps({"type": kind, "data": data}, default=str)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._fan_out(msg)
        elif not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._fan_out, msg)

    def _fan_out(self, msg: str) -> None:
        for q in list(self.clients):
            if q.full():  # slow client: drop its oldest message
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)
