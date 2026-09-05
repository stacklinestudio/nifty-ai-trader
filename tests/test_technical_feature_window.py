"""Brief 13 follow-up: confirms execution/live_context.py::
TECHNICAL_FEATURE_WINDOW_DAYS is a safe bound, not just a fast one --
the same real window build_live_context fetches for the live path
produces the same real technical-feature values (EMA/ATR, the two
components with any memory beyond a fixed rolling window) as computing
over the FULL accumulated real history, within a tight numerical
tolerance. This is the real evidence backing backtest/daily_backtest.py
and reports/score_diagnostic.py both now bounding `prior` the same way,
instead of feeding an ever-growing, purely-wasted amount of history into
feature_frame on every real decision-point check.
"""

from __future__ import annotations

import pandas as pd
import pytest

from config import IST
from execution.live_context import TECHNICAL_FEATURE_WINDOW_DAYS, _technical_features
from intelligence.market_regime import classify

CSV_PATH = "data/private/nifty_index_minute_2026-07-06_to_2026-09-01.csv"


def _real_candles() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    df = df.set_index("date")
    df.index = df.index.tz_convert(IST)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


@pytest.mark.parametrize("day_index,bar_index", [(20, 10), (25, 100), (30, 200), (38, 50)])
def test_bounded_window_matches_full_history_within_tight_tolerance(day_index, bar_index):
    candles = _real_candles()
    trading_days = sorted({ts.date() for ts in candles.index})
    day = trading_days[day_index]
    todays = candles[candles.index.date == day]
    if bar_index >= len(todays):
        pytest.skip("not enough real bars this real day for this sample point")
    decision_time = todays.index[bar_index]

    full_history = candles[candles.index <= decision_time]
    bounded = full_history[full_history.index >= decision_time - pd.Timedelta(days=TECHNICAL_FEATURE_WINDOW_DAYS)]
    # Sanity: this real sample point must actually have LESS real history
    # in the bounded window than the full one, or the test proves nothing.
    assert len(bounded) < len(full_history)

    full_features = _technical_features(full_history)
    bounded_features = _technical_features(bounded)

    for key in ("ema_fast", "ema_slow", "atr"):
        full_value = full_features[key]
        bounded_value = bounded_features[key]
        # A real, tight relative tolerance (0.01%) -- not exact equality,
        # since a real dataset could in principle land exactly at the
        # convergence boundary; still far tighter than anything that could
        # change a real technical_score's 75.0/45.0 binary read.
        if full_value:
            assert bounded_value == pytest.approx(full_value, rel=1e-4), (
                f"{key}: full={full_value} bounded={bounded_value} on {day} bar {bar_index}"
            )

    # The real downstream effect that actually matters: the bullish/
    # bearish read technical_score is keyed on must agree.
    full_bullish = full_features["ema_fast"] > full_features["ema_slow"] and full_features["close"] > full_features["vwap"]
    bounded_bullish = bounded_features["ema_fast"] > bounded_features["ema_slow"] and bounded_features["close"] > bounded_features["vwap"]
    assert full_bullish == bounded_bullish

    full_regime = classify(full_features, 0.0)
    bounded_regime = classify(bounded_features, 0.0)
    assert full_regime == bounded_regime
