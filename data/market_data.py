from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Quote:
    symbol: str
    ltp: float
    timestamp: datetime
    source: str
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None


class MarketDataProvider(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...


class KiteMarketData:
    """Official SDK quote adapter. Errors intentionally propagate to fail closed."""

    def __init__(self, kite: object) -> None:
        self.kite = kite

    def get_quote(self, symbol: str) -> Quote:
        payload = self.kite.quote([symbol])[symbol]
        timestamp = payload.get("timestamp") or payload.get("last_trade_time")
        if timestamp is None:
            raise ValueError("Kite quote did not include a timestamp")
        if timestamp.tzinfo is None:
            raise ValueError("Kite quote timestamp must be timezone aware")
        depth = payload.get("depth", {})
        buys, sells = depth.get("buy", []), depth.get("sell", [])
        return Quote(
            symbol=symbol,
            ltp=float(payload["last_price"]),
            timestamp=timestamp,
            source="kite",
            bid=float(buys[0]["price"]) if buys else None,
            ask=float(sells[0]["price"]) if sells else None,
            volume=payload.get("volume"),
        )


def validate_quote(quote: Quote, now: datetime, max_age_seconds: int) -> None:
    if quote.timestamp.tzinfo is None or now.tzinfo is None:
        raise ValueError("Market timestamps must be timezone aware")
    if (now - quote.timestamp).total_seconds() > max_age_seconds:
        raise ValueError("Stale market data: refusing new trade")
    if quote.ltp <= 0:
        raise ValueError("Invalid quote price")
