from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from config import IST


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
        if not isinstance(timestamp, datetime):
            # ValueError (not TypeError) to match the timezone check right
            # below and validate_quote's convention -- both are "this Kite
            # quote payload is malformed," the same failure category.
            raise ValueError("Kite quote did not include a valid timestamp")  # noqa: TRY004
        if timestamp.tzinfo is None:
            # Confirmed against a real Kite response (2026-08-31): Kite
            # Connect returns naive datetimes for this field that are
            # implicitly IST -- the API never returns any other timezone
            # here, so attaching IST is recovering known information, not
            # guessing. A genuinely malformed value (not a datetime at all)
            # is still rejected above before reaching this point.
            timestamp = timestamp.replace(tzinfo=IST)
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
