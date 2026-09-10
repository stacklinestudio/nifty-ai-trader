"""Phase 2 Piece 4: learning/promotion_pipeline.py.

Real integration coverage for the actual bridge this phase exists to
build -- real backtest-derived evidence flowing into the existing,
unmodified learning.promotion_engine.decide(), not hand-supplied test
booleans standing in for it.
"""

from __future__ import annotations

from datetime import date, datetime

import learning.promotion_pipeline as promotion_pipeline_module
from config import IST, Settings
from learning.hypothesis import HypothesisCondition
from learning.memory import MemoryStore
from learning.promotion_pipeline import (
    evaluate_experiment_for_promotion,
    gather_structural_backtest_evidence,
)
from learning.trade_memory import record_trade
from tests.test_daily_walk_forward import _continuous_trending_days


def _matching_condition(threshold: float = 0.5, min_samples: int = 5) -> HypothesisCondition:
    # OPENING_RANGE_BREAKOUT/TREND_UP is exactly what _continuous_trending_days
    # reliably produces every real day (ORB always forms a candidate in the
    # trend direction when time-eligible -- execution/live_context.py::
    # _select_setup's own docstring).
    return HypothesisCondition(
        metric="win_rate",
        setup_type="OPENING_RANGE_BREAKOUT",
        regime="TREND_UP",
        operator=">=",
        threshold=threshold,
        min_samples=min_samples,
        rationale="test",
    )


def _non_matching_condition() -> HypothesisCondition:
    # Never fires against _continuous_trending_days's real, consistently
    # trending data -- VWAP_REJECTION only ever fires in a RANGE/UNCERTAIN
    # regime, which this fixture never produces.
    return HypothesisCondition(
        metric="win_rate",
        setup_type="VWAP_REJECTION",
        regime="RANGE",
        operator=">=",
        threshold=0.5,
        min_samples=5,
        rationale="test",
    )


def test_gather_structural_backtest_evidence_reflects_a_real_backtest_run(tmp_path):
    """Real proof, not a description: the evidence counts here are
    exactly what an independent real run_daily_backtest call over the
    same real data produces -- not invented numbers."""
    from backtest.daily_backtest import run_daily_backtest

    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")
    condition = _matching_condition()

    evidence = gather_structural_backtest_evidence(settings, candles, condition)

    independent_full = run_daily_backtest(
        Settings(signal_threshold=50.0, database_path=tmp_path / "independent_full.db"), candles
    )
    expected_historical = sum(
        1
        for d in independent_full.days
        if d.candidate_formed
        and d.cycle
        and d.cycle.score_attribution
        and d.cycle.score_attribution.get("setup_type") == "OPENING_RANGE_BREAKOUT"
        and d.cycle.score_attribution.get("regime") == "TREND_UP"
    )

    assert evidence.historical_candidates == expected_historical
    assert evidence.historical_candidates >= 14  # 15 real evaluated days, all real TREND_UP/ORB
    assert evidence.has_historical is True
    assert evidence.has_walk_forward is True
    assert evidence.has_out_of_sample is True


def test_a_non_matching_setup_produces_zero_real_evidence_and_fails_structurally(tmp_path):
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")
    condition = _non_matching_condition()

    evidence = gather_structural_backtest_evidence(settings, candles, condition)

    assert evidence.historical_candidates == 0
    assert evidence.has_historical is False
    assert evidence.has_walk_forward is False
    assert evidence.has_out_of_sample is False


def test_promotion_engine_decide_receives_the_real_computed_booleans_not_hand_supplied_ones(tmp_path, monkeypatch):
    """The real integration proof this phase exists to build: spies on
    the actual learning.promotion_engine.decide() call promotion_pipeline
    makes, and asserts the booleans it receives are EXACTLY the real ones
    independently computed by gather_structural_backtest_evidence/
    evaluate_hypothesis for this same real data -- not test-authored
    stand-ins."""
    calls = []
    real_decide = promotion_pipeline_module.decide

    def spy(has_historical, has_walk_forward, has_out_of_sample, human_approved):
        calls.append((has_historical, has_walk_forward, has_out_of_sample, human_approved))
        return real_decide(has_historical, has_walk_forward, has_out_of_sample, human_approved)

    monkeypatch.setattr(promotion_pipeline_module, "decide", spy)

    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition(threshold=0.5, min_samples=3)
    now = datetime(2026, 9, 10, tzinfo=IST)

    # Seed real, deterministic winning trade facts -- enough real samples
    # to clear the hypothesis's own min_samples, all real wins so the
    # real win_rate genuinely clears threshold=0.5.
    for i in range(5):
        record_trade(
            memory,
            {
                "outcome": "WIN", "pnl": 100.0, "setup_type": "OPENING_RANGE_BREAKOUT",
                "entry_regime": "TREND_UP", "exit_reason": "TAKE_PROFIT",
            },
            now,
        )

    result = evaluate_experiment_for_promotion(settings, candles, memory, condition, human_approved=True, now=now)

    assert len(calls) == 1
    real_has_historical, real_has_walk_forward, real_has_out_of_sample, real_human_approved = calls[0]
    assert real_has_historical == result.structural_evidence.has_historical is True
    assert real_has_walk_forward == result.structural_evidence.has_walk_forward is True
    assert real_human_approved is True
    # has_out_of_sample fed to decide() is structural AND real-outcome
    # combined (see evaluate_experiment_for_promotion's own docstring) --
    # both real conditions hold here (structural OOS candidates exist,
    # and the real seeded win_rate is 1.0 >= 0.5 threshold).
    assert real_has_out_of_sample is True
    assert result.outcome_evidence.passed is True
    assert result.outcome_evidence.actual_value == 1.0
    assert result.decision.promote is True


def test_weak_structural_evidence_fails_closed_even_with_human_approved(tmp_path):
    """The gate must fail closed on WEAK evidence, not just missing
    evidence -- human_approved=True alone must never be enough."""
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _non_matching_condition()  # never fires against this real data
    now = datetime(2026, 9, 10, tzinfo=IST)

    result = evaluate_experiment_for_promotion(settings, candles, memory, condition, human_approved=True, now=now)

    assert result.structural_evidence.has_historical is False
    assert result.decision.promote is False
    assert "historical validation" in " ".join(result.decision.reasons)


def test_a_real_losing_track_record_fails_closed_even_with_strong_structural_evidence_and_human_approved(tmp_path):
    """Weak REAL-OUTCOME evidence must independently fail the gate too --
    strong structural evidence (real candidates repeatably form) is not
    enough on its own if the real accumulated trades for this exact
    setup+regime are genuinely losing."""
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition(threshold=0.5, min_samples=3)
    now = datetime(2026, 9, 10, tzinfo=IST)

    # Real, deterministic LOSING trade facts -- enough real samples to
    # clear min_samples, but a real win_rate of 0.0, well below threshold.
    for i in range(5):
        record_trade(
            memory,
            {
                "outcome": "LOSS", "pnl": -50.0, "setup_type": "OPENING_RANGE_BREAKOUT",
                "entry_regime": "TREND_UP", "exit_reason": "STOP_LOSS",
            },
            now,
        )

    result = evaluate_experiment_for_promotion(settings, candles, memory, condition, human_approved=True, now=now)

    assert result.structural_evidence.has_historical is True  # real candidates genuinely do form
    assert result.outcome_evidence.passed is False  # but the real accumulated track record is losing
    assert result.decision.promote is False


def test_missing_human_approval_fails_closed_even_with_strong_real_evidence(tmp_path):
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "settings.db")
    memory = MemoryStore(tmp_path / "memory.db")
    condition = _matching_condition(threshold=0.5, min_samples=3)
    now = datetime(2026, 9, 10, tzinfo=IST)

    for i in range(5):
        record_trade(
            memory,
            {
                "outcome": "WIN", "pnl": 100.0, "setup_type": "OPENING_RANGE_BREAKOUT",
                "entry_regime": "TREND_UP", "exit_reason": "TAKE_PROFIT",
            },
            now,
        )

    result = evaluate_experiment_for_promotion(settings, candles, memory, condition, human_approved=False, now=now)

    assert result.structural_evidence.has_historical is True
    assert result.outcome_evidence.passed is True
    assert result.decision.promote is False
    assert "human approval" in " ".join(result.decision.reasons)


def test_promotion_pipeline_never_writes_to_the_callers_own_database_path(tmp_path):
    import sqlite3

    from storage.database import Database

    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    database = Database(real_db_path)
    database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=real_db_path)

    gather_structural_backtest_evidence(settings, candles, _matching_condition())

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0


def test_promotion_pipeline_never_sends_a_real_notification_even_with_real_credentials_configured(tmp_path, monkeypatch):
    import requests

    calls: list[str] = []

    class _FakeResponse:
        ok = True

    def tracking_transport(url, *args, **kwargs):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(requests, "post", tracking_transport)

    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(
        signal_threshold=50.0,
        database_path=tmp_path / "settings.db",
        telegram_bot_token="real-looking-token",
        telegram_chat_id="real-looking-chat-id",
        discord_webhook_url="https://discord.com/api/webhooks/real/looking",
    )

    gather_structural_backtest_evidence(settings, candles, _matching_condition())

    assert calls == []
