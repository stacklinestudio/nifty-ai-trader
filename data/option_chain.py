from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

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


def quotes_to_json(quotes: list[OptionQuote]) -> str:
    """Round-trips through storage/database.py's `snapshots` table -- an
    already-existing, previously-unused table (source/timestamp/payload),
    reused here rather than adding a new one."""
    return json.dumps(
        [
            {
                "instrument": {
                    "symbol": q.instrument.symbol,
                    "strike": q.instrument.strike,
                    "expiry": q.instrument.expiry.isoformat(),
                    "option_type": q.instrument.option_type,
                    "lot_size": q.instrument.lot_size,
                    "instrument_token": q.instrument.instrument_token,
                },
                "ltp": q.ltp,
                "timestamp": q.timestamp.isoformat(),
                "bid": q.bid,
                "ask": q.ask,
                "volume": q.volume,
                "open_interest": q.open_interest,
                "implied_volatility": q.implied_volatility,
            }
            for q in quotes
        ]
    )


def quotes_from_json(payload: str) -> list[OptionQuote]:
    rows = json.loads(payload)
    quotes = []
    for row in rows:
        inst = row["instrument"]
        instrument = OptionInstrument(
            symbol=inst["symbol"],
            strike=inst["strike"],
            expiry=date.fromisoformat(inst["expiry"]),
            option_type=inst["option_type"],
            lot_size=inst["lot_size"],
            instrument_token=inst["instrument_token"],
        )
        quotes.append(
            OptionQuote(
                instrument=instrument,
                ltp=row["ltp"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                bid=row["bid"],
                ask=row["ask"],
                volume=row["volume"],
                open_interest=row["open_interest"],
                implied_volatility=row["implied_volatility"],
            )
        )
    return quotes
