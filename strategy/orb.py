from __future__ import annotations

import pandas as pd


def opening_range(candles: pd.DataFrame, minutes: int = 5) -> tuple[float, float]:
    first = candles.iloc[:minutes]
    if len(first) < minutes:
        raise ValueError("Insufficient opening candles")
    return float(first.high.max()), float(first.low.min())


def breakout_direction(candles: pd.DataFrame, minutes: int = 5) -> str:
    high, low = opening_range(candles, minutes)
    price = float(candles.iloc[-1].close)
    return "CALL" if price > high else "PUT" if price < low else "NO_TRADE"
