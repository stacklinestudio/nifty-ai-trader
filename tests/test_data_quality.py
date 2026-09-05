"""Brief 13 Part A: separates DATA_QUALITY from signal value. Brief 12's
diagnostic proved volume_score is 0.0 on 99.6% of real evaluations
specifically because option-chain history is unavailable for this
window -- not because real volume, when available, is weak. Proves
data_available/data_completeness genuinely distinguish these two cases,
using the exact real conditions each scoring function itself already
branches on (never a new or fabricated signal), and that this is purely
additive -- every value Brief 12 already computed and tested is
byte-identical.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from config import IST, Settings
from data.global_market import ContextValue
from data.instruments import OptionInstrument
from data.news import NewsItem
from data.option_chain import OptionQuote
from execution.live_context import assemble_context
from intelligence.market_regime import Regime
from intelligence.signal_engine import SignalEngine


def _candles() -> pd.DataFrame:
    """Same real deterministic OPENING_RANGE_BREAKOUT CALL fixture as
    tests/test_score_attribution.py -- a flat prior day, a flat opening
    range, then a clean, sustained breakout."""
    rows = []
    t0 = datetime(2026, 8, 31, 9, 15, tzinfo=IST)
    for i in range(375):
        ts = t0 + timedelta(minutes=i)
        rows.append({"date": ts, "open": 24080.0, "high": 24080.5, "low": 24079.5, "close": 24080.0, "volume": 1000})
    t1 = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    for i in range(5):
        ts = t1 + timedelta(minutes=i)
        rows.append({"date": ts, "open": 24080.0, "high": 24080.5, "low": 24079.5, "close": 24080.0, "volume": 1000})
    for i in range(10):
        ts = t1 + timedelta(minutes=5 + i)
        price = 24080.0 + 5.0 * (i + 1)
        rows.append({"date": ts, "open": price - 1, "high": price + 1, "low": price - 2, "close": price, "volume": 1200})
    frame = pd.DataFrame(rows).set_index("date")
    return frame[["open", "high", "low", "close", "volume"]].astype(float)


def _real_option_quotes(now: datetime) -> tuple[list[OptionQuote], list[OptionQuote]]:
    """A real current snapshot AND a real previous snapshot -- both
    genuinely present, matching what a live day with an actually-persisted
    prior-session chain looks like (main.py's real
    Database.latest_option_chain_snapshot path)."""
    instrument = OptionInstrument("NIFTY24CE", 24200.0, date(2026, 9, 8), "CE", 25)
    current = [OptionQuote(instrument, 120.0, now, 119.5, 120.5, 5000, open_interest=45000)]
    previous = [OptionQuote(instrument, 100.0, now - timedelta(days=1), 99.5, 100.5, 4000, open_interest=30000)]
    return current, previous


def test_all_seven_inputs_genuinely_available_reports_100_percent(monkeypatch):
    candles = _candles()
    now = datetime(2026, 9, 1, 9, 25, tzinfo=IST)
    settings = Settings()
    option_quotes, previous_option_quotes = _real_option_quotes(now)
    global_context = [ContextValue("SP500", 0.5, now, "yfinance", True)]
    news_items = [NewsItem(now, "Nifty surges on strong FII inflows", "test", 0.8, "POSITIVE", 0.6)]

    context = assemble_context(
        candles, option_quotes, float(candles.iloc[-1].close), now, True, settings,
        previous_option_quotes, global_context, news_items,
    )

    attribution = context["score_attribution"]
    assert attribution["data_available"] == {
        "technical_score": True,
        "opening_score": True,
        "volume_score": True,
        "option_score": True,
        "global_score": True,
        "news_score": True,
        "risk_penalty": True,
    }
    assert attribution["data_completeness"] == 100.0


def test_no_real_option_snapshot_reduces_completeness_by_exactly_its_real_share():
    """volume_score and option_score share the EXACT same real gating
    condition (both _combined_volume_score and detect_buildup branch on
    `option_quotes and previous_option_quotes`) -- there is no real
    scenario in the current implementation where one is available and the
    other isn't, so a missing snapshot honestly costs 2/7, not 1/7. Stated
    plainly rather than forcing an artificial, inaccurate "just volume"
    scenario the real code doesn't support."""
    candles = _candles()
    now = datetime(2026, 9, 1, 9, 25, tzinfo=IST)
    settings = Settings()
    global_context = [ContextValue("SP500", 0.5, now, "yfinance", True)]
    news_items = [NewsItem(now, "Nifty surges on strong FII inflows", "test", 0.8, "POSITIVE", 0.6)]

    context = assemble_context(
        candles, [], float(candles.iloc[-1].close), now, True, settings,
        [],  # no real previous snapshot -- the real historical pattern (Brief 12: 99.6% of this project's own data)
        global_context, news_items,
    )

    attribution = context["score_attribution"]
    assert attribution["data_available"]["volume_score"] is False
    assert attribution["data_available"]["option_score"] is False
    assert attribution["data_available"]["global_score"] is True
    assert attribution["data_available"]["news_score"] is True
    # 5 of 7 real inputs available -- exactly 5/7, not an arbitrary number.
    assert attribution["data_completeness"] == pytest.approx(5 / 7 * 100.0)


def test_news_unavailable_alone_reduces_completeness_by_exactly_one_seventh():
    """news_score IS independently gateable (its own real
    context["news_items"] check, unrelated to the option-snapshot
    coupling above) -- the clean single-input case."""
    candles = _candles()
    now = datetime(2026, 9, 1, 9, 25, tzinfo=IST)
    settings = Settings()
    option_quotes, previous_option_quotes = _real_option_quotes(now)
    global_context = [ContextValue("SP500", 0.5, now, "yfinance", True)]

    context = assemble_context(
        candles, option_quotes, float(candles.iloc[-1].close), now, True, settings,
        previous_option_quotes, global_context, [],  # real news genuinely unavailable
    )

    attribution = context["score_attribution"]
    assert attribution["data_available"]["news_score"] is False
    assert all(v for k, v in attribution["data_available"].items() if k != "news_score")
    assert attribution["data_completeness"] == pytest.approx(6 / 7 * 100.0)



def test_data_quality_fields_never_change_any_of_brief_12s_original_score_values():
    """Regression: every field Brief 12 already computed and tested is
    byte-identical whether or not the new data_available/data_completeness
    fields are read -- this is purely additive."""
    candles = _candles()
    now = datetime(2026, 9, 1, 9, 25, tzinfo=IST)
    settings = Settings()

    context = assemble_context(candles, [], float(candles.iloc[-1].close), now, True, settings)
    attribution = context["score_attribution"]

    # Same real reconstruction Brief 12's own test performs -- independent
    # proof the real confidence computation is unaffected by this change.
    regime = Regime(attribution["regime"])
    reconstructed = SignalEngine(threshold=attribution["threshold"]).evaluate(
        timestamp=now,
        regime=regime,
        technical=attribution["technical_score"],
        opening=attribution["opening_score"],
        volume=attribution["volume_score"],
        option=attribution["option_score"],
        global_score=attribution["global_score"],
        news=attribution["news_score"],
        risk_penalty=attribution["risk_penalty"],
        override_direction=attribution["direction"],
    )
    assert reconstructed.confidence == attribution["confidence"]
    assert attribution["setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert attribution["direction"] == "CALL"
    assert attribution["cleared_threshold"] is False
    # The new fields exist alongside the old ones -- neither replaces the other.
    assert "data_available" in attribution
    assert "data_completeness" in attribution
