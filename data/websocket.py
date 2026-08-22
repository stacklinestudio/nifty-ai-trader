"""Reconnect-safe state machine; SDK callbacks can feed `on_tick`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class WebsocketHealth:
    connected: bool = False
    last_tick_at: datetime | None = None
    reconnects: int = 0

    def on_connect(self) -> None:
        self.connected = True

    def on_disconnect(self) -> None:
        self.connected = False
        self.reconnects += 1

    def on_tick(self, timestamp: datetime) -> None:
        self.last_tick_at = timestamp

    def safe_for_trading(self, now: datetime, stale_seconds: int) -> bool:
        return (
            self.connected
            and self.last_tick_at is not None
            and (now - self.last_tick_at).total_seconds() <= stale_seconds
        )
