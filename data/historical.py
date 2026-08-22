"""Historical candle interfaces; requires a configured broker/data source."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

REQUIRED_COLUMNS = {"open", "high", "low", "close"}


def validate_candles(candles: pd.DataFrame) -> pd.DataFrame:
    if not REQUIRED_COLUMNS.issubset(candles.columns):
        raise ValueError(f"Candles require {REQUIRED_COLUMNS}")
    if not isinstance(candles.index, pd.DatetimeIndex) or candles.index.tz is None:
        raise ValueError("Historical candle index must be timezone-aware")
    return candles.sort_index().copy()


class KiteHistoricalData:
    def __init__(self, kite: object) -> None:
        self.kite = kite

    def candles(
        self, instrument_token: int, start: datetime, end: datetime, interval: str = "minute"
    ) -> pd.DataFrame:
        rows = self.kite.historical_data(instrument_token, start, end, interval)
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.tz_convert("Asia/Kolkata")
        return validate_candles(frame.set_index("date"))
