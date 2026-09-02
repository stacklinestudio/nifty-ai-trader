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


def test_uncertain_regime_is_no_trade_without_an_override_regardless_of_confidence():
    """Unchanged pre-Brief-7 behavior: regime alone implies no direction
    for UNCERTAIN/RANGE, so this stays NO_TRADE even at maximum
    confidence -- exactly why range-favored setups (execution/
    live_context.py's VWAP_REJECTION/SUPPORT_RESISTANCE_REACTION) need
    override_direction to ever produce a real candidate."""
    signal = SignalEngine(75).evaluate(
        datetime.now().astimezone(), Regime.UNCERTAIN, 100, 100, 100, 100, 100, 100
    )
    assert signal.direction == "NO_TRADE"
    assert "uncertain regime" in signal.risks


def test_override_direction_lets_a_setup_supplied_direction_through_on_uncertain_regime():
    """Brief 7: a caller (a range-favored setup detector) that already
    found a real direction independent of the regime classifier can
    supply it -- the exact case UNCERTAIN/RANGE previously vetoed by
    construction."""
    signal = SignalEngine(75).evaluate(
        datetime.now().astimezone(),
        Regime.UNCERTAIN,
        100,
        100,
        100,
        100,
        100,
        100,
        override_direction="PUT",
    )
    assert signal.direction == "PUT"
    assert "uncertain regime" not in signal.risks


def test_override_direction_still_respects_the_real_confidence_threshold():
    """The confidence formula and threshold gate are completely
    unchanged by override_direction -- only which direction the
    confidence attaches to."""
    signal = SignalEngine(75).evaluate(
        datetime.now().astimezone(), Regime.UNCERTAIN, 10, 10, override_direction="CALL"
    )
    assert signal.direction == "NO_TRADE"
    assert "below configured confidence threshold" in signal.risks


def test_override_direction_does_not_suppress_the_real_high_volatility_risk_flag():
    """High-volatility whipsaw risk (strategy/regime_selector.py's own
    reasoning) is real regardless of where the direction came from --
    an override must not silently bypass it."""
    signal = SignalEngine(75).evaluate(
        datetime.now().astimezone(),
        Regime.HIGH_VOLATILITY,
        100,
        100,
        100,
        100,
        100,
        100,
        override_direction="CALL",
    )
    assert signal.direction == "NO_TRADE"
    assert "high volatility regime" in signal.risks


def test_invalid_override_direction_is_ignored_falls_back_to_regime():
    signal = SignalEngine(75).evaluate(
        datetime.now().astimezone(),
        Regime.TREND_UP,
        100,
        100,
        100,
        100,
        100,
        100,
        override_direction="NOT_A_REAL_DIRECTION",
    )
    assert signal.direction == "CALL"  # falls back to the real regime-derived direction
