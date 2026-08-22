from __future__ import annotations

from datetime import datetime, time

from storage.models import Trade


def exit_reason(trade: Trade, ltp: float, now: datetime, forced_exit: time) -> str | None:
    if ltp <= trade.stop_price:
        return "STOP_LOSS"
    if ltp >= trade.target_price:
        return "TAKE_PROFIT"
    if now.timetz().replace(tzinfo=None) >= forced_exit:
        return "FORCED_SQUARE_OFF"
    return None
