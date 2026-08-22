"""Live order adapter is deliberately hard-gated and unused by default."""

from __future__ import annotations

from config import Settings


class KiteExecutor:
    def __init__(self, settings: Settings, kite: object) -> None:
        self.settings = settings
        self.kite = kite

    def place_order(self, **kwargs: object) -> object:
        if not self.settings.live_execution_allowed:
            raise PermissionError("Live execution disabled: use PAPER mode")
        return self.kite.place_order(**kwargs)
