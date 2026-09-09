"""Phase 1: Decision Ledger + Market State Snapshot.

Proves: (1) every real evaluated cycle gets a unique, correctly-formatted
candidate_id; (2) the market-state snapshot records every real setup
detector evaluated that cycle -- not just the one execution/live_context.py
::_select_setup already, independently, chose; (3) this feature is pure
data/persistence -- the real candidate-formation decision
(context["candidate_direction"]/["candidate_confidence"]/["setup_type"])
is byte-identical to what tests/test_live_context.py already proves it is
without this feature; (4) real persistence round-trips.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from config import IST, Settings
from execution.decision_ledger import (
    build_market_state_snapshot,
    evaluate_all_setups,
    generate_candidate_id,
)
from execution.live_context import assemble_context
from intelligence.market_regime import Regime, classify
from intelligence.technicals import feature_frame
from storage.database import Database
from tests.test_live_context import full_prior_day, minute_bars


def test_generate_candidate_id_matches_the_real_required_format():
    now = datetime(2026, 9, 10, 9, 34, 5, tzinfo=IST)
    assert generate_candidate_id(now, 1) == "CAND-20260910-093405-001"
    assert generate_candidate_id(now, 42) == "CAND-20260910-093405-042"


def test_generate_candidate_id_is_unique_across_a_real_sequence():
    now = datetime(2026, 9, 10, 9, 34, 5, tzinfo=IST)
    ids = {generate_candidate_id(now, seq) for seq in range(1, 51)}
    assert len(ids) == 50


def _trend_up_candles() -> tuple:
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    prior_rows = full_prior_day(prior_day, 24080.4)
    opening_flat = minute_bars(today, 9, 15, 5, 24080.0, 0.0)
    breakout_up = minute_bars(today, 9, 20, 10, 24080.0, 5.0)
    import pandas as pd

    candles = pd.DataFrame(prior_rows + opening_flat + breakout_up).set_index("date")
    return candles, today


def test_assemble_context_produces_decision_ledger_components_on_every_real_evaluated_cycle():
    """Even a cycle whose SignalEngine confidence lands below threshold
    (no context["candidate_direction"]) must still get a real decision-
    ledger snapshot -- the whole point of "every real candidate gets a
    unique ID... not just the winner." """
    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)
    settings = Settings()  # real default signal_threshold=75, this fixture lands ~61 (see test_live_context.py)

    context = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings)

    assert "candidate_direction" not in context  # confirms this cycle really is below threshold
    assert "decision_ledger_components" in context
    components = context["decision_ledger_components"]
    assert components["winning_setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert len(components["setups_evaluated"]) >= 1


def test_decision_ledger_never_changes_the_real_candidate_formation_decision():
    """Pure data/persistence, proven not just asserted: the exact same
    real inputs that tests/test_live_context.py::
    test_build_live_context_produces_the_right_candidate_once_threshold_is_reachable
    already proves produces CALL/OPENING_RANGE_BREAKOUT still produce the
    identical result with decision_ledger_components also present."""
    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)
    settings = Settings(signal_threshold=50.0)

    context = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings)

    assert context["candidate_direction"] == "CALL"
    assert context["setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert "decision_ledger_components" in context
    assert context["decision_ledger_components"]["winning_setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert context["decision_ledger_components"]["winning_direction"] == "CALL"


def test_evaluate_all_setups_records_every_real_trend_favored_detector_not_just_the_winner():
    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 50, tzinfo=IST)  # 35 min after 9:15 open, past the 30-min ORB window
    todays = candles[candles.index.date == today]
    features_df = feature_frame(candles)
    latest = features_df.iloc[-1]
    features = {
        "ema_fast": float(latest.ema_fast), "ema_slow": float(latest.ema_slow),
        "close": float(latest.close), "vwap": float(latest.vwap),
        "atr": float(latest.atr), "momentum": float(latest.momentum),
    }
    regime = classify(features, 0.0)
    session_open = todays.index[0].to_pydatetime()

    results = evaluate_all_setups(
        candles, todays, features, today, now, session_open, regime, "CALL", winning_setup_type="TREND_CONTINUATION"
    )

    setup_types = {r.setup_type for r in results}
    # All 4 real trend-favored detectors evaluated -- not just the winner.
    assert setup_types == {"OPENING_RANGE_BREAKOUT", "TREND_CONTINUATION", "MOMENTUM_CONTINUATION", "VWAP_BREAKOUT"}
    orb = next(r for r in results if r.setup_type == "OPENING_RANGE_BREAKOUT")
    assert orb.eligible is False  # correctly outside its own 30-minute open window
    assert orb.is_winner is False
    winners = [r for r in results if r.is_winner]
    assert len(winners) == 1
    assert winners[0].setup_type == "TREND_CONTINUATION"


def test_evaluate_all_setups_returns_empty_for_high_volatility_regime_matching_the_real_documented_gap():
    """execution/live_context.py::_select_setup's own docstring: no setup
    family is tried at all in HIGH_VOLATILITY/LOW_VOLATILITY -- this
    function must honestly mirror that real gap, not fabricate a result."""
    candles, today = _trend_up_candles()
    todays = candles[candles.index.date == today]
    now = datetime(today.year, today.month, today.day, 9, 45, tzinfo=IST)
    session_open = todays.index[0].to_pydatetime()
    features = {"ema_fast": 1.0, "ema_slow": 1.0, "close": 100.0, "vwap": 100.0, "atr": 5.0, "momentum": 0.0}

    results = evaluate_all_setups(
        candles, todays, features, today, now, session_open, Regime.HIGH_VOLATILITY, None, winning_setup_type=None
    )

    assert results == ()


def test_build_market_state_snapshot_round_trips_through_json():
    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)
    features_df = feature_frame(candles)
    latest = features_df.iloc[-1]
    features = {
        "ema_fast": float(latest.ema_fast), "ema_slow": float(latest.ema_slow),
        "close": float(latest.close), "vwap": float(latest.vwap),
        "atr": float(latest.atr), "momentum": float(latest.momentum),
    }
    regime = classify(features, 0.0)

    snapshot = build_market_state_snapshot(
        candidate_id="CAND-20260901-093000-001",
        now=now,
        spot=features["close"],
        features=features,
        regime=regime,
        trend_direction="CALL",
        gap_pct=0.0,
        option_quotes=[],
        previous_option_quotes=[],
        global_context=[],
        news_items=[],
        setups_evaluated=(),
        winning_setup_type="OPENING_RANGE_BREAKOUT",
        winning_direction="CALL",
    )

    import json

    payload = json.dumps(snapshot.to_dict(), default=str)
    restored = json.loads(payload)
    assert restored["candidate_id"] == "CAND-20260901-093000-001"
    assert restored["winning_setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert restored["regime"] == regime.value


@pytest.fixture
def db(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    return database


def test_next_decision_ledger_sequence_starts_at_one_and_increments_with_real_persisted_rows(db):
    assert db.next_decision_ledger_sequence("20260910") == 1

    snapshot = build_market_state_snapshot(
        candidate_id=generate_candidate_id(datetime(2026, 9, 10, 9, 30, tzinfo=IST), 1),
        now=datetime(2026, 9, 10, 9, 30, tzinfo=IST),
        spot=24080.0,
        features={"ema_fast": 1, "ema_slow": 1, "close": 24080.0, "vwap": 24080.0, "atr": 5.0, "momentum": 0.0},
        regime=Regime.TREND_UP,
        trend_direction="CALL",
        gap_pct=0.0,
        option_quotes=[],
        previous_option_quotes=[],
        global_context=[],
        news_items=[],
        setups_evaluated=(),
        winning_setup_type="OPENING_RANGE_BREAKOUT",
        winning_direction="CALL",
    )
    db.save_decision_ledger_entry(snapshot)

    assert db.next_decision_ledger_sequence("20260910") == 2
    assert db.next_decision_ledger_sequence("20260911") == 1  # a different real day starts its own sequence


def test_save_and_read_back_a_real_decision_ledger_entry(db):
    now = datetime(2026, 9, 10, 9, 30, tzinfo=IST)
    snapshot = build_market_state_snapshot(
        candidate_id="CAND-20260910-093000-001",
        now=now,
        spot=24080.0,
        features={"ema_fast": 1, "ema_slow": 1, "close": 24080.0, "vwap": 24080.0, "atr": 5.0, "momentum": 0.0},
        regime=Regime.TREND_UP,
        trend_direction="CALL",
        gap_pct=0.0,
        option_quotes=[],
        previous_option_quotes=[],
        global_context=[],
        news_items=[],
        setups_evaluated=(),
        winning_setup_type="OPENING_RANGE_BREAKOUT",
        winning_direction="CALL",
    )

    db.save_decision_ledger_entry(snapshot)

    fetched = db.decision_ledger_entry("CAND-20260910-093000-001")
    assert fetched is not None
    assert fetched["winning_setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert len(db.recent_decision_ledger_entries()) == 1


def test_a_duplicate_candidate_id_raises_rather_than_silently_overwriting(db):
    now = datetime(2026, 9, 10, 9, 30, tzinfo=IST)
    kwargs = {
        "now": now, "spot": 24080.0,
        "features": {"ema_fast": 1, "ema_slow": 1, "close": 24080.0, "vwap": 24080.0, "atr": 5.0, "momentum": 0.0},
        "regime": Regime.TREND_UP, "trend_direction": "CALL", "gap_pct": 0.0,
        "option_quotes": [], "previous_option_quotes": [], "global_context": [], "news_items": [],
        "setups_evaluated": (), "winning_setup_type": "OPENING_RANGE_BREAKOUT", "winning_direction": "CALL",
    }
    db.save_decision_ledger_entry(build_market_state_snapshot(candidate_id="CAND-DUP-001", **kwargs))

    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.save_decision_ledger_entry(build_market_state_snapshot(candidate_id="CAND-DUP-001", **kwargs))


def test_orchestrator_run_cycle_persists_a_real_decision_ledger_entry(db):
    from agents.orchestrator import Orchestrator

    candles, today = _trend_up_candles()
    now = datetime(today.year, today.month, today.day, 9, 30, tzinfo=IST)
    settings = Settings(database_path=db.path, signal_threshold=50.0)
    context = assemble_context(candles, [], candles.iloc[-1].close, now, True, settings)

    orchestrator = Orchestrator(settings, db, dry_run=True)
    result = orchestrator.run_cycle(context)

    assert result.decision_ledger_candidate_id is not None
    assert result.decision_ledger_candidate_id.startswith(f"CAND-{today:%Y%m%d}-")
    stored = db.decision_ledger_entry(result.decision_ledger_candidate_id)
    assert stored is not None
    assert stored["winning_setup_type"] == "OPENING_RANGE_BREAKOUT"
