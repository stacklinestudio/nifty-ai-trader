"""Phase 2 Piece 10, Requirement 7: strategy/eligibility.py."""

from __future__ import annotations

from datetime import datetime

from config import IST
from learning.memory import MemoryStore
from strategy.eligibility import evaluate_registry_eligibility, evaluate_strategy_eligibility
from strategy.registry import DEFAULT_REGISTRY, STATUS_DRAFT, STATUS_PROMOTED, STATUS_VALIDATED
from tests.test_strategy_registry import _seed_evaluation

NOW = datetime(2026, 9, 10, tzinfo=IST)


def test_an_incompatible_regime_is_ineligible_regardless_of_evidence(tmp_path):
    """VWAP_REJECTION only supports RANGE/UNCERTAIN -- even a real,
    promoted evaluation for a regime it was never gated to run under
    must not make it eligible."""
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "VWAP_REJECTION", "TREND_UP", promote=True, reasons=[])
    strategy = next(s for s in DEFAULT_REGISTRY if s.strategy_id == "VWAP_REJECTION")

    result = evaluate_strategy_eligibility(store, strategy, "TREND_UP", NOW)

    assert result.eligible is False
    assert "not among" in result.reason


def test_an_unvalidated_draft_strategy_is_rejected_as_ineligible(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    strategy = next(s for s in DEFAULT_REGISTRY if s.strategy_id == "OPENING_RANGE_BREAKOUT")

    result = evaluate_strategy_eligibility(store, strategy, "TREND_UP", NOW)

    assert result.eligible is False
    assert result.status == STATUS_DRAFT


def test_a_validated_but_not_yet_human_approved_strategy_is_still_ineligible(tmp_path):
    """The absolute line, at the eligibility layer: every deterministic
    gate genuinely passed is still not enough -- only PROMOTED (which
    itself required a real human_approved=True) is eligible."""
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=False, reasons=["human approval"])
    strategy = next(s for s in DEFAULT_REGISTRY if s.strategy_id == "OPENING_RANGE_BREAKOUT")

    result = evaluate_strategy_eligibility(store, strategy, "TREND_UP", NOW)

    assert result.status == STATUS_VALIDATED
    assert result.eligible is False


def test_a_genuinely_promoted_strategy_in_a_compatible_regime_is_eligible(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[])
    strategy = next(s for s in DEFAULT_REGISTRY if s.strategy_id == "OPENING_RANGE_BREAKOUT")

    result = evaluate_strategy_eligibility(store, strategy, "TREND_UP", NOW)

    assert result.status == STATUS_PROMOTED
    assert result.eligible is True


def test_evaluate_registry_eligibility_covers_every_real_registered_strategy(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")

    results = evaluate_registry_eligibility(store, DEFAULT_REGISTRY, "TREND_UP", NOW)

    assert len(results) == len(DEFAULT_REGISTRY)
    assert {r.strategy_id for r in results} == {s.strategy_id for s in DEFAULT_REGISTRY}


def test_with_zero_real_promotion_evidence_every_registered_strategy_is_ineligible(tmp_path):
    """The honest, expected real-data result: while zero strategies
    have ever cleared the real promotion bar, eligibility is uniformly
    False for every real regime."""
    store = MemoryStore(tmp_path / "memory.db")

    for regime in ("TREND_UP", "TREND_DOWN", "RANGE", "UNCERTAIN", "HIGH_VOLATILITY", "GAP_UP", "GAP_DOWN"):
        results = evaluate_registry_eligibility(store, DEFAULT_REGISTRY, regime, NOW)
        assert all(not r.eligible for r in results)
