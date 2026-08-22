from datetime import datetime

import pandas as pd

from intelligence.market_regime import Regime
from intelligence.signal_engine import SignalEngine
from strategy.orb import breakout_direction


def candles():
    index = pd.date_range("2025-01-01 09:15", periods=6, freq="min", tz="Asia/Kolkata")
    return pd.DataFrame(
        {
            "open": [100] * 6,
            "high": [101, 102, 103, 102, 102, 106],
            "low": [99] * 6,
            "close": [100, 101, 102, 101, 102, 105],
            "volume": [1] * 6,
        },
        index=index,
    )


def test_orb_breakout_call():
    assert breakout_direction(candles(), 5) == "CALL"


def test_signal_no_trade_under_threshold():
    signal = SignalEngine(75).evaluate(datetime.now().astimezone(), Regime.TREND_UP, 40, 40)
    assert (
        signal.direction == "NO_TRADE" and "below configured confidence threshold" in signal.risks
    )


def test_signal_call_when_quality_is_high():
    signal = SignalEngine(75).evaluate(
        datetime.now().astimezone(), Regime.TREND_UP, 100, 100, 100, 100, 100, 100
    )
    assert signal.direction == "CALL" and signal.confidence >= 75
