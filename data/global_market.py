"""Best-effort public-data abstraction; unavailable values are explicit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ContextValue:
    name: str
    value: float | None
    timestamp: datetime | None
    source: str
    available: bool
    error: str | None = None


class GlobalMarketProvider:
    def snapshot(self) -> list[ContextValue]:
        return []  # Inject a lawful provider; no invented external values.
