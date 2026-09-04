"""Brief 12 Part A: previously, a candidate's real 7-component score
breakdown existed only as a log line (or, for a cleared-threshold
candidate, free-text strings inside candidate_evidence) -- neither
queryable nor aggregable afterward. Proves execution/live_context.py::
_add_candidate now sets context["score_attribution"] with the SAME real
values SignalEngine.evaluate() itself used (not recomputed/approximated),
for both a below-threshold and a cleared-threshold real evaluation, and
that Orchestrator.run_cycle persists it to the real database.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from agents.orchestrator import Orchestrator
from config import IST, Settings
from execution.live_context import assemble_context
from intelligence.market_regime import Regime
from intelligence.signal_engine import SignalEngine


def _candles() -> pd.DataFrame:
    """A flat prior day (anchors EMA/VWAP/ATR with real history) followed
    by a flat 5-minute opening range, then a clean, sustained breakout --
    the same deterministic-CALL shape used throughout this project's
    tests (e.g. tests/test_live_context.py::_clear_breakout_fixture)."""
    rows = []
    t0 = datetime(2026, 8, 31, 9, 15, tzinfo=IST)
    for i in range(375):  # a full real session, 09:15-15:29
        ts = t0 + timedelta(minutes=i)
        rows.append({"date": ts, "open": 24080.0, "high": 24080.5, "low": 24079.5, "close": 24080.0, "volume": 1000})
    t1 = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    for i in range(5):  # flat opening range
        ts = t1 + timedelta(minutes=i)
        rows.append({"date": ts, "open": 24080.0, "high": 24080.5, "low": 24079.5, "close": 24080.0, "volume": 1000})
    for i in range(10):  # clean, sustained breakout
        ts = t1 + timedelta(minutes=5 + i)
        price = 24080.0 + 5.0 * (i + 1)
        rows.append({"date": ts, "open": price - 1, "high": price + 1, "low": price - 2, "close": price, "volume": 1200})
    frame = pd.DataFrame(rows).set_index("date")
    return frame[["open", "high", "low", "close", "volume"]].astype(float)


def test_score_attribution_matches_signal_engines_own_output_below_threshold():
    candles = _candles()
    now = datetime(2026, 9, 1, 9, 25, tzinfo=IST)  # right after the breakout starts
    settings = Settings()  # real default signal_threshold=75

    context = assemble_context(candles, [], float(candles.iloc[-1].close), now, True, settings)

    attribution = context["score_attribution"]
    assert attribution["setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert attribution["direction"] == "CALL"
    assert attribution["cleared_threshold"] is False
    assert attribution["threshold"] == settings.signal_threshold
    for key in (
        "technical_score", "opening_score", "volume_score", "volume_reason",
        "option_score", "option_reason", "global_score", "global_direction",
        "news_score", "news_direction", "risk_penalty", "regime", "setup_evidence", "now",
    ):
        assert key in attribution, f"missing real component: {key}"

    # Independently reconstruct SignalEngine's own confidence from the
    # attribution's own captured component values -- must match exactly,
    # proving this is the SAME real computation, not a re-derived guess.
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
    assert "candidate_direction" not in context  # real, below-threshold -- no candidate formed


def test_score_attribution_matches_signal_engines_own_output_cleared_threshold():
    """Same real fixture, only signal_threshold lowered -- an existing,
    per-environment config knob, not a production change (matches the
    pattern tests/test_live_context.py already uses for this)."""
    candles = _candles()
    now = datetime(2026, 9, 1, 9, 25, tzinfo=IST)
    settings = Settings(signal_threshold=50.0)

    context = assemble_context(candles, [], float(candles.iloc[-1].close), now, True, settings)

    attribution = context["score_attribution"]
    assert attribution["cleared_threshold"] is True
    assert context["candidate_direction"] == "CALL"
    assert attribution["confidence"] == context["candidate_confidence"]


def test_orchestrator_persists_score_attribution_to_the_real_database(tmp_path):
    candles = _candles()
    now = datetime(2026, 9, 1, 9, 25, tzinfo=IST)
    settings = Settings(database_path=tmp_path / "paper.db")
    context = assemble_context(candles, [], float(candles.iloc[-1].close), now, True, settings)
    assert "score_attribution" in context  # sanity: the fixture really produces one

    orchestrator = Orchestrator(settings)
    orchestrator.run_cycle(context)

    signals = orchestrator.database.recent_signals()
    assert len(signals) == 1
    assert signals[0]["setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert signals[0]["direction"] == "CALL"
    assert signals[0]["cleared_threshold"] is False
    assert signals[0]["confidence"] == context["score_attribution"]["confidence"]


def test_run_cycle_without_score_attribution_persists_nothing_not_an_error(tmp_path):
    """A hand-built context dict (most existing tests) never went through
    the live-context pipeline -- must not raise, must simply persist
    nothing, exactly the same as every other real gap in this codebase."""
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)

    orchestrator.run_cycle({"market_data_fresh": False, "market_open": False})

    assert orchestrator.database.recent_signals() == []
