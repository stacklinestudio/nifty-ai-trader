"""Conservative NSE trading-calendar abstraction.

Maintain the supplied holiday list from the official exchange calendar before
using unattended scheduling. Weekends and explicit holidays always fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from config import IST


@dataclass(frozen=True)
class NseCalendar:
    holidays: set[date] = field(default_factory=set)
    open_time: time = time(9, 15)
    close_time: time = time(15, 30)

    def is_trading_day(self, value: date) -> bool:
        return value.weekday() < 5 and value not in self.holidays

    def is_market_open(self, value: datetime) -> bool:
        local = value.astimezone(IST)
        current = local.timetz().replace(tzinfo=None)
        return self.is_trading_day(local.date()) and self.open_time <= current < self.close_time
