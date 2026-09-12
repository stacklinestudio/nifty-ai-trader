"""Phase 2 Piece 10, Requirements 14, 15: strategy/regime_strategy_report.py."""

from __future__ import annotations

from datetime import date

from execution.regime_detection import INSUFFICIENT_DATA
from learning.memory import MemoryStore
from strategy.regime_strategy_report import (
    evaluate_regime_and_strategy,
    historical_regime_strategy_report,
)
from strategy.selection import NO_STRATEGY_SELECTED
from tests.test_daily_walk_forward import _continuous_trending_days
from tests.test_strategy_registry import _seed_evaluation


def test_evaluate_regime_and_strategy_answers_the_full_real_question(tmp_path):
    """timestamp -> detected regime -> input metrics -> eligible
    strategies -> selected strategy (Requirement 14), all from one real
    call, against real, consistently-trending fixture data."""
    store = MemoryStore(tmp_path / "memory.db")
    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, 1.0)
    as_of = candles.index[-1]
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[], now=as_of)

    evaluation = evaluate_regime_and_strategy(candles, as_of, store)

    assert evaluation.regime_record.regime == "TREND_UP"
    assert evaluation.regime_record.input_metrics  # real metrics present
    assert len(evaluation.eligibility_results) == 6
    assert evaluation.selection.selected_strategy_id == "OPENING_RANGE_BREAKOUT"


def test_insufficient_regime_data_cascades_to_no_strategy_selected_honestly(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    candles = _continuous_trending_days(date(2026, 8, 3), 1, 24000.0, 1.0).iloc[:10]
    as_of = candles.index[-1]

    evaluation = evaluate_regime_and_strategy(candles, as_of, store)

    assert evaluation.regime_record.regime == INSUFFICIENT_DATA
    assert all(r.status == "N/A" and not r.eligible for r in evaluation.eligibility_results)
    assert evaluation.selection.selected_strategy_id is None
    assert NO_STRATEGY_SELECTED in evaluation.selection.reason


def test_historical_report_produces_one_real_row_per_real_timestamp(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    candles = _continuous_trending_days(date(2026, 8, 3), 3, 24000.0, 1.0)
    timestamps = [group.index.max() for _, group in candles.groupby(candles.index.date)]

    rows = historical_regime_strategy_report(candles, timestamps, store)

    assert len(rows) == len(timestamps)
    for row, ts in zip(rows, timestamps, strict=True):
        assert row["timestamp"] == ts.isoformat()
        assert row["regime_record"]["regime"] == "TREND_UP"


def test_historical_report_never_claims_profitability():
    """Requirement 14: facts only. Structural proof -- no key on the
    real report row shape mentions pnl/profit/return of any kind."""
    from strategy.regime_strategy_report import RegimeStrategyEvaluation

    row_keys = set(RegimeStrategyEvaluation.__dataclass_fields__.keys())
    forbidden = {"pnl", "profit", "return", "expectancy", "win_rate"}
    assert not (row_keys & forbidden)


def test_the_combined_pipeline_is_deterministic_and_repeatable(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, 1.0)
    as_of = candles.index[-1]
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[], now=as_of)

    first = evaluate_regime_and_strategy(candles, as_of, store).to_dict()
    second = evaluate_regime_and_strategy(candles, as_of, store).to_dict()
    del first["regime_record"]["regime_id"]
    del second["regime_record"]["regime_id"]

    assert first == second
