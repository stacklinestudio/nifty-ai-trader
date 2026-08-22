from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from data.instruments import OptionInstrument


@dataclass(frozen=True)
class OptionQuote:
    instrument: OptionInstrument
    ltp: float
    timestamp: datetime
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None

    @property
    def spread(self) -> float | None:
        return None if self.bid is None or self.ask is None else self.ask - self.bid
