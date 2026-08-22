from __future__ import annotations

from enum import Enum

import pandas as pd


class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    GAP_UP = "GAP_UP"
    GAP_DOWN = "GAP_DOWN"
    UNCERTAIN = "UNCERTAIN"


def classify(features: pd.Series, gap_pct: float = 0.0) -> Regime:
    if gap_pct >= 0.006:
        return Regime.GAP_UP
    if gap_pct <= -0.006:
        return Regime.GAP_DOWN
    if float(features.get("atr", 0)) / max(float(features.get("close", 1)), 1) > 0.008:
        return Regime.HIGH_VOLATILITY
    if (
        features.get("ema_fast", 0) > features.get("ema_slow", 0)
        and features.get("momentum", 0) > 0
    ):
        return Regime.TREND_UP
    if (
        features.get("ema_fast", 0) < features.get("ema_slow", 0)
        and features.get("momentum", 0) < 0
    ):
        return Regime.TREND_DOWN
    return Regime.UNCERTAIN
