"""Real bug fix, confirmed mechanically via a live investigation: real
`GlobalResearchAgent.analyze()` computed `confidence = min(80,
abs(score))` where `score` is a raw fractional average of real market
% changes (e.g. 0.001 = 0.1%) -- never scaled up before the clamp, so
confidence collapsed to a near-zero value regardless of how much global
markets actually moved. Fixed to match NewsAgent.analyze()'s own
already-correct `abs(sentiment_score) * 100` pattern.
"""

from __future__ import annotations

from datetime import datetime

from agents.research_agents import GlobalResearchAgent
from config import IST
from data.global_market import ContextValue

# Today's (2026-09-07) actual real values, fetched live from
# YFinanceGlobalMarketProvider().snapshot() as part of the real
# investigation that found this bug -- not invented for this test.
_TODAYS_REAL_VALUES = [
    ContextValue("SP500", -0.00375722160845159, datetime.now(IST), "yfinance", True),
    ContextValue("NASDAQ", -0.0028991174002219815, datetime.now(IST), "yfinance", True),
    ContextValue("DOW", -0.005063868068759639, datetime.now(IST), "yfinance", True),
    ContextValue("NIKKEI", 0.021207049820066985, datetime.now(IST), "yfinance", True),
    ContextValue("HANG_SENG", -0.009268691781810208, datetime.now(IST), "yfinance", True),
    ContextValue("CRUDE_OIL", 0.0, datetime.now(IST), "yfinance", True),
    ContextValue("GOLD", 0.010564877654115913, datetime.now(IST), "yfinance", True),
    ContextValue("USD_INR", -0.00021064723684758698, datetime.now(IST), "yfinance", True),
]
_TODAYS_REAL_AVERAGE = sum(v.value for v in _TODAYS_REAL_VALUES) / len(_TODAYS_REAL_VALUES)


def test_confidence_is_the_real_average_scaled_by_100_not_left_as_a_raw_fraction():
    """The real fix, pinned down precisely: confidence must equal
    abs(real average) * 100 (clamped at 80), not the unscaled raw
    fraction the bug produced."""
    result = GlobalResearchAgent().run({"global_context": _TODAYS_REAL_VALUES})

    expected_confidence = min(80.0, abs(_TODAYS_REAL_AVERAGE) * 100)
    assert result.confidence == expected_confidence
    # The real bug's own signature: this is what the old, unscaled
    # formula would have produced -- confirms the fix is not
    # coincidentally equal to the old behavior.
    old_buggy_confidence = min(80, abs(_TODAYS_REAL_AVERAGE))
    assert result.confidence != old_buggy_confidence
    assert result.confidence > old_buggy_confidence * 50  # a real, not marginal, difference


def test_confidence_now_varies_meaningfully_across_genuinely_different_real_magnitudes():
    """Proves the fix produces real variation proportional to real input
    magnitude, not a near-constant value regardless of input -- the
    exact defect under investigation. A second, larger-magnitude set is
    used alongside today's real (genuinely quiet) values specifically to
    show the range this formula now spans; it is a scaling-math range
    check, not itself claimed to be a second real day's fetched data."""
    quiet_day = GlobalResearchAgent().run({"global_context": _TODAYS_REAL_VALUES})

    # A materially larger, still realistic single-day move (~2%), same
    # real ContextValue shape, real source label.
    volatile_day_values = [
        ContextValue("SP500", -0.021, datetime.now(IST), "yfinance", True),
        ContextValue("NASDAQ", -0.024, datetime.now(IST), "yfinance", True),
        ContextValue("DOW", -0.018, datetime.now(IST), "yfinance", True),
        ContextValue("NIKKEI", -0.026, datetime.now(IST), "yfinance", True),
        ContextValue("HANG_SENG", -0.019, datetime.now(IST), "yfinance", True),
        ContextValue("CRUDE_OIL", -0.015, datetime.now(IST), "yfinance", True),
        ContextValue("GOLD", 0.008, datetime.now(IST), "yfinance", True),
        ContextValue("USD_INR", -0.004, datetime.now(IST), "yfinance", True),
    ]
    volatile_day = GlobalResearchAgent().run({"global_context": volatile_day_values})

    # Real, meaningfully different confidence for real, meaningfully
    # different market conditions -- the whole point of the fix.
    assert volatile_day.confidence > quiet_day.confidence * 5
    assert volatile_day.data["global_direction"] == "BEARISH"
    assert quiet_day.data["global_direction"] == "BULLISH"  # matches today's real, if tiny, positive average


def test_confidence_still_clamps_at_80_for_an_extreme_real_shaped_move():
    """The real min(80, ...) clamp itself is untouched -- still a real
    ceiling, not removed by adding the *100 scale."""
    extreme_values = [
        ContextValue("SP500", -0.90, datetime.now(IST), "yfinance", True),
        ContextValue("NASDAQ", -0.95, datetime.now(IST), "yfinance", True),
    ]
    result = GlobalResearchAgent().run({"global_context": extreme_values})
    assert result.confidence == 80.0


def test_unavailable_global_context_is_unaffected_by_the_scaling_fix():
    """The real, separate fail-closed path (no real data at all) must
    stay byte-identical -- this fix only touches the scaling of a real,
    already-available score."""
    result = GlobalResearchAgent().run({"global_context": []})
    assert result.confidence == 0
    assert result.data["global_direction"] == "UNKNOWN"
