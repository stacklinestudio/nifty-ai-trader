"""Brief 7: real detection logic for the setup types designed in the
original spec but never actually implemented beyond OPENING_RANGE_
BREAKOUT. Each detector is tested standalone here (controlled, real-shaped
feature/candle data, following the established pattern), plus the
dispatcher (_select_setup) that decides which one -- if any -- fires on a
given scan.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from config import IST
from execution.live_context import (
    _momentum_continuation_setup,
    _select_setup,
    _support_resistance_reaction_setup,
    _trend_continuation_setup,
    _vwap_breakout_setup,
    _vwap_rejection_setup,
)


def _features(**overrides: float) -> dict[str, float]:
    base = {"ema_fast": 24100.0, "ema_slow": 24050.0, "close": 24120.0, "vwap": 24080.0, "atr": 20.0, "momentum": 0.002}
    base.update(overrides)
    return base


def _bar(high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"high": high, "low": low, "close": close})


def _minute_bars(day: date, start_hour: int, start_minute: int, count: int, base_price: float, trend: float) -> list[dict]:
    rows = []
    price = base_price
    for i in range(count):
        ts = datetime(day.year, day.month, day.day, start_hour, start_minute, tzinfo=IST) + timedelta(minutes=i)
        price += trend
        rows.append(
            {"date": ts, "open": price - 0.5, "high": price + 1.0, "low": price - 1.0, "close": price, "volume": 1000}
        )
    return rows


# --- _vwap_breakout_setup ---------------------------------------------


def test_vwap_breakout_detects_real_call_break_above_vwap_with_positive_momentum():
    direction, score, evidence = _vwap_breakout_setup(
        _features(close=24120.0, vwap=24080.0, momentum=0.003, atr=20.0)
    )
    assert direction == "CALL"
    assert 20.0 <= score <= 80.0
    assert "above session vwap" in evidence


def test_vwap_breakout_detects_real_put_break_below_vwap_with_negative_momentum():
    direction, _score, evidence = _vwap_breakout_setup(
        _features(close=24040.0, vwap=24080.0, momentum=-0.003, atr=20.0)
    )
    assert direction == "PUT"
    assert "below session vwap" in evidence


def test_vwap_breakout_finds_nothing_when_momentum_contradicts_price_side():
    direction, score, _evidence = _vwap_breakout_setup(
        _features(close=24120.0, vwap=24080.0, momentum=-0.001)
    )
    assert direction is None
    assert score == 0.0


def test_vwap_breakout_is_unavailable_not_fabricated_with_zero_atr():
    direction, _score, evidence = _vwap_breakout_setup(_features(atr=0.0))
    assert direction is None
    assert "insufficient data" in evidence


# --- _vwap_rejection_setup ----------------------------------------------


def test_vwap_rejection_detects_real_put_rejection_from_above():
    direction, _score, evidence = _vwap_rejection_setup(
        _bar(high=24100.0, low=24075.0, close=24070.0), _features(vwap=24080.0, atr=20.0)
    )
    assert direction == "PUT"
    assert "pierced session vwap" in evidence


def test_vwap_rejection_detects_real_call_rejection_from_below():
    direction, _score, _evidence = _vwap_rejection_setup(
        _bar(high=24095.0, low=24060.0, close=24090.0), _features(vwap=24080.0, atr=20.0)
    )
    assert direction == "CALL"


def test_vwap_rejection_finds_nothing_without_a_real_wick_through_vwap():
    # Entirely above vwap, no piercing structure.
    direction, _score, _evidence = _vwap_rejection_setup(
        _bar(high=24110.0, low=24090.0, close=24100.0), _features(vwap=24080.0, atr=20.0)
    )
    assert direction is None


# --- _momentum_continuation_setup ---------------------------------------


def test_momentum_continuation_detects_real_call_when_momentum_exceeds_atr_relative_threshold():
    direction, _score, evidence = _momentum_continuation_setup(
        _features(close=24120.0, momentum=0.01, atr=20.0, ema_fast=24100.0, ema_slow=24050.0)
    )
    assert direction == "CALL"
    assert "5-bar momentum" in evidence


def test_momentum_continuation_detects_real_put_symmetric_case():
    direction, _score, _evidence = _momentum_continuation_setup(
        _features(close=24120.0, momentum=-0.01, atr=20.0, ema_fast=24000.0, ema_slow=24050.0)
    )
    assert direction == "PUT"


def test_momentum_continuation_finds_nothing_below_the_real_volatility_relative_threshold():
    # threshold here is atr/close = 20/24120 ~= 0.00083 -- momentum below it.
    direction, _score, _evidence = _momentum_continuation_setup(
        _features(close=24120.0, momentum=0.0003, atr=20.0, ema_fast=24100.0, ema_slow=24050.0)
    )
    assert direction is None


def test_momentum_continuation_requires_ema_alignment_not_momentum_alone():
    direction, _score, _evidence = _momentum_continuation_setup(
        _features(close=24120.0, momentum=0.01, atr=20.0, ema_fast=24000.0, ema_slow=24050.0)  # EMAs disagree
    )
    assert direction is None


# --- _trend_continuation_setup -------------------------------------------


def test_trend_continuation_detects_a_real_sustained_uptrend():
    rows = _minute_bars(date(2026, 9, 1), 9, 15, 30, 24000.0, 3.0)  # steady real climb
    candles = pd.DataFrame(rows).set_index("date")

    direction, _score, evidence = _trend_continuation_setup(candles, lookback=10)

    assert direction == "CALL"
    assert "held for the last 10 real bars" in evidence


def test_trend_continuation_detects_a_real_sustained_downtrend():
    rows = _minute_bars(date(2026, 9, 1), 9, 15, 30, 24000.0, -3.0)
    candles = pd.DataFrame(rows).set_index("date")

    direction, _score, _evidence = _trend_continuation_setup(candles, lookback=10)

    assert direction == "PUT"


def test_trend_continuation_is_unavailable_not_fabricated_with_insufficient_history():
    rows = _minute_bars(date(2026, 9, 1), 9, 15, 5, 24000.0, 3.0)
    candles = pd.DataFrame(rows).set_index("date")

    direction, _score, evidence = _trend_continuation_setup(candles, lookback=10)

    assert direction is None
    assert "insufficient history" in evidence


def test_trend_continuation_finds_nothing_when_the_trend_flips_mid_window():
    # Climbs then reverses within the lookback -- ema ordering won't hold
    # for the whole window even though the very latest bars look bullish.
    rows = _minute_bars(date(2026, 9, 1), 9, 15, 20, 24000.0, 5.0) + _minute_bars(
        date(2026, 9, 1), 9, 35, 15, 24100.0, -5.0
    )
    candles = pd.DataFrame(rows).set_index("date")

    direction, _score, _evidence = _trend_continuation_setup(candles, lookback=10)

    assert direction is None


# --- _support_resistance_reaction_setup -----------------------------------


def test_support_resistance_reaction_detects_real_rejection_at_prior_day_resistance():
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    prior_rows = _minute_bars(prior_day, 9, 15, 375, 24080.0, 0.0)  # prior day high ~24081
    todays_rows = _minute_bars(today, 9, 15, 5, 24075.0, 0.0)
    # Force today's latest bar to wick up through the prior-day resistance
    # and close back below it.
    todays_rows[-1]["high"] = 24090.0
    todays_rows[-1]["close"] = 24075.0
    candles = pd.DataFrame(prior_rows + todays_rows).set_index("date")
    features = {"atr": 20.0}

    direction, _score, evidence = _support_resistance_reaction_setup(candles, today, features)

    assert direction == "PUT"
    assert "prior-day resistance" in evidence


def test_support_resistance_reaction_detects_real_rejection_at_prior_day_support():
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    prior_rows = _minute_bars(prior_day, 9, 15, 375, 24080.0, 0.0)  # prior day low ~24078
    todays_rows = _minute_bars(today, 9, 15, 5, 24085.0, 0.0)
    todays_rows[-1]["low"] = 24070.0
    todays_rows[-1]["close"] = 24086.0
    candles = pd.DataFrame(prior_rows + todays_rows).set_index("date")
    features = {"atr": 20.0}

    direction, _score, evidence = _support_resistance_reaction_setup(candles, today, features)

    assert direction == "CALL"
    assert "prior-day support" in evidence


def test_support_resistance_reaction_is_unavailable_not_fabricated_without_a_prior_day():
    today = date(2026, 9, 1)
    rows = _minute_bars(today, 9, 15, 10, 24080.0, 0.0)
    candles = pd.DataFrame(rows).set_index("date")

    direction, _score, evidence = _support_resistance_reaction_setup(candles, today, {"atr": 20.0})

    assert direction is None
    assert "no prior real trading day" in evidence


# --- _select_setup dispatcher ---------------------------------------------


def test_select_setup_returns_none_without_a_directional_regime():
    """Range-favored setups (VWAP_REJECTION, SUPPORT_RESISTANCE_REACTION)
    are real and independently tested above, but are deliberately not
    tried by the dispatcher at all -- see _select_setup's own docstring."""
    rows = _minute_bars(date(2026, 9, 1), 9, 15, 10, 24080.0, 0.0)
    candles = pd.DataFrame(rows).set_index("date")
    todays = candles
    now = candles.index[-1].to_pydatetime()
    session_open = candles.index[0].to_pydatetime()

    result = _select_setup(candles, todays, _features(), now, session_open, trend_direction=None)

    assert result is None


def test_select_setup_prefers_opening_range_breakout_within_its_window_regardless_of_agreement():
    """Unchanged pre-Brief-7 behavior: ORB is tried first and always wins
    while time-eligible, whether or not its own read agrees with the
    regime -- SignalEngine's confidence weighting penalizes disagreement,
    this dispatcher doesn't hard-gate on it, for this one setup only."""
    today = date(2026, 9, 1)
    rows = _minute_bars(today, 9, 15, 10, 24080.0, 0.0)  # flat -- ORB reads NO_TRADE
    candles = pd.DataFrame(rows).set_index("date")
    todays = candles
    now = candles.index[-1].to_pydatetime()
    session_open = candles.index[0].to_pydatetime()

    result = _select_setup(candles, todays, _features(), now, session_open, trend_direction="CALL")

    assert result is not None
    setup_type, direction, score, _evidence = result
    assert setup_type == "OPENING_RANGE_BREAKOUT"
    assert direction == "CALL"
    assert score == 50.0  # NO_TRADE ORB read -> neutral score, not penalized or rewarded


def test_select_setup_falls_through_to_trend_continuation_outside_the_orb_window():
    today = date(2026, 9, 1)
    rows = _minute_bars(today, 9, 15, 40, 24000.0, 3.0)  # steady real uptrend
    candles = pd.DataFrame(rows).set_index("date")
    todays = candles
    session_open = candles.index[0].to_pydatetime()
    now = session_open + timedelta(hours=3)  # well outside OPENING_RANGE_BREAKOUT's window

    result = _select_setup(candles, todays, _features(), now, session_open, trend_direction="CALL")

    assert result is not None
    setup_type, direction, _score, _evidence = result
    assert setup_type == "TREND_CONTINUATION"
    assert direction == "CALL"


def test_select_setup_returns_none_when_no_eligible_setup_agrees_with_the_regime():
    today = date(2026, 9, 1)
    rows = _minute_bars(today, 9, 15, 10, 24080.0, 0.0)  # flat -- nothing agrees with a CALL regime
    candles = pd.DataFrame(rows).set_index("date")
    todays = candles
    session_open = candles.index[0].to_pydatetime()
    now = session_open + timedelta(hours=3)

    flat_features = _features(ema_fast=24080.0, ema_slow=24080.0, close=24080.0, vwap=24080.0, momentum=0.0)
    result = _select_setup(candles, todays, flat_features, now, session_open, trend_direction="CALL")

    assert result is None
