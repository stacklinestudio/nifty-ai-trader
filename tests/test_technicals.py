"""Regression coverage for a real bug found while building Brief 7's new
setup types: intelligence/technicals.py::feature_frame's vwap was a
multi-day cumulative price*volume/volume with no session reset, which
silently produced NaN (0/0) for the entire series once discovered against
real NIFTY 50 index data -- Kite's real index volume is structurally
always 0 (Brief 5's finding) -- and _technical_features' own NaN->0.0
fallback quietly turned that into a permanent vwap=0.0, making every
"close > vwap" bullish read (execution/live_context.py and
agents/research_agents.py::TechnicalAgent) vacuously true regardless of
real price action.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from config import IST
from intelligence.technicals import feature_frame


def _bars(day: date, count: int, base_price: float, trend: float, volume: int) -> list[dict]:
    rows = []
    price = base_price
    for i in range(count):
        ts = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST) + timedelta(minutes=i)
        price += trend
        rows.append(
            {
                "date": ts,
                "open": price - 0.5,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": volume,
            }
        )
    return rows


def test_session_vwap_never_produces_nan_when_real_volume_is_genuinely_zero():
    """The exact real-world case: NIFTY index volume from Kite is always
    literally 0 -- confirmed against the real 42-day captured dataset.
    price*0/0 must not silently become NaN (and therefore a vacuous 0.0
    downstream)."""
    rows = _bars(date(2026, 9, 1), 30, 24080.0, 1.0, volume=0)
    frame = pd.DataFrame(rows).set_index("date")

    feats = feature_frame(frame)

    assert feats["vwap"].notna().all()
    assert (feats["vwap"] != 0).all()


def test_session_vwap_degrades_to_the_real_unweighted_typical_price_average_at_zero_volume():
    rows = _bars(date(2026, 9, 1), 5, 100.0, 1.0, volume=0)
    frame = pd.DataFrame(rows).set_index("date")

    feats = feature_frame(frame)

    typical = (frame.high + frame.low + frame.close) / 3
    expected = typical.expanding().mean()
    pd.testing.assert_series_equal(feats["vwap"], expected, check_names=False)


def test_session_vwap_is_the_real_volume_weighted_average_when_volume_is_real():
    rows = _bars(date(2026, 9, 1), 5, 100.0, 1.0, volume=1000)
    # Give one bar materially more real volume than the rest so a
    # volume-weighted result would differ measurably from a plain average.
    rows[2]["volume"] = 9000
    frame = pd.DataFrame(rows).set_index("date")

    feats = feature_frame(frame)

    typical = (frame.high + frame.low + frame.close) / 3
    expected = (typical * frame.volume).expanding().sum() / frame.volume.expanding().sum()
    pd.testing.assert_series_equal(feats["vwap"], expected, check_names=False)
    # Sanity: the weighted and plain averages genuinely differ here --
    # proves this is really testing the weighting, not a coincidence.
    assert not feats["vwap"].equals(typical.expanding().mean())


def test_session_vwap_resets_each_real_trading_day_not_cumulative_across_days():
    """VWAP is a single-session concept -- a second day's early bars must
    reflect that day's own price action, not get pulled toward a prior
    day's average level."""
    day1 = date(2026, 8, 31)
    day2 = date(2026, 9, 1)
    # Day 1 sits far below day 2 -- if vwap incorrectly carried across
    # days, day 2's early vwap would be dragged down toward day 1's level.
    rows = _bars(day1, 375, 20000.0, 0.0, volume=0) + _bars(day2, 5, 25000.0, 0.0, volume=0)
    frame = pd.DataFrame(rows).set_index("date")

    feats = feature_frame(frame)
    day2_vwap = feats[feats.index.date == day2]["vwap"]

    assert (day2_vwap > 24000).all()  # anchored to day 2's own real level, not dragged toward ~20000
