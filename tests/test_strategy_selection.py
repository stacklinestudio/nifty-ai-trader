"""Phase 2 Piece 10, Requirement 8: strategy/selection.py."""

from __future__ import annotations

from datetime import datetime

from config import IST
from learning.memory import MemoryStore
from strategy.eligibility import evaluate_registry_eligibility
from strategy.registry import DEFAULT_REGISTRY
from strategy.selection import NO_STRATEGY_SELECTED, select_strategy
from tests.test_strategy_registry import _seed_evaluation

NOW = datetime(2026, 9, 10, tzinfo=IST)


def test_no_eligible_strategy_produces_no_strategy_selected(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    eligibility = evaluate_registry_eligibility(store, DEFAULT_REGISTRY, "TREND_UP", NOW)

    result = select_strategy(store, "TREND_UP", eligibility, NOW)

    assert result.selected_strategy_id is None
    assert result.selected_strategy_version is None
    assert NO_STRATEGY_SELECTED in result.reason
    assert result.eligible_count == 0
    assert result.evaluated_count == len(DEFAULT_REGISTRY)


def test_a_single_eligible_strategy_is_selected(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[])
    eligibility = evaluate_registry_eligibility(store, DEFAULT_REGISTRY, "TREND_UP", NOW)

    result = select_strategy(store, "TREND_UP", eligibility, NOW)

    assert result.selected_strategy_id == "OPENING_RANGE_BREAKOUT"
    assert result.selected_strategy_version == "v1"
    assert result.eligible_count == 1


def test_deterministic_selection_prefers_more_real_historical_evidence(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[], historical_candidates=3)
    _seed_evaluation(store, "TREND_CONTINUATION", "TREND_UP", promote=True, reasons=[], historical_candidates=50)
    eligibility = evaluate_registry_eligibility(store, DEFAULT_REGISTRY, "TREND_UP", NOW)

    result = select_strategy(store, "TREND_UP", eligibility, NOW)

    assert result.selected_strategy_id == "TREND_CONTINUATION"
    assert result.eligible_count == 2


def test_deterministic_tie_break_is_alphabetical_strategy_id(tmp_path):
    """Equal real historical_candidates -- the final, total-ordering
    tiebreak must always pick the same winner, run after run."""
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "VWAP_BREAKOUT", "TREND_UP", promote=True, reasons=[], historical_candidates=10)
    _seed_evaluation(store, "MOMENTUM_CONTINUATION", "TREND_UP", promote=True, reasons=[], historical_candidates=10)
    eligibility = evaluate_registry_eligibility(store, DEFAULT_REGISTRY, "TREND_UP", NOW)

    first = select_strategy(store, "TREND_UP", eligibility, NOW)
    second = select_strategy(store, "TREND_UP", eligibility, NOW)

    assert first.selected_strategy_id == second.selected_strategy_id == "MOMENTUM_CONTINUATION"  # alphabetically first


def test_selection_never_reads_real_evidence_timestamped_after_as_of(tmp_path):
    """A real, later promotion_evaluation for a competing strategy must
    not change an earlier selection."""
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[], historical_candidates=3)
    later = datetime(2026, 9, 20, tzinfo=IST)
    _seed_evaluation(
        store, "TREND_CONTINUATION", "TREND_UP", promote=True, reasons=[], historical_candidates=100, now=later
    )
    eligibility_at_now = evaluate_registry_eligibility(store, DEFAULT_REGISTRY, "TREND_UP", NOW)

    result = select_strategy(store, "TREND_UP", eligibility_at_now, NOW)

    assert result.selected_strategy_id == "OPENING_RANGE_BREAKOUT"  # TREND_CONTINUATION wasn't PROMOTED yet as of NOW
