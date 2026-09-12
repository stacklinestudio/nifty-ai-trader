"""Phase 2 Piece 10, Requirements 5, 6, 10: strategy/registry.py."""

from __future__ import annotations

from datetime import datetime

from config import IST
from learning.memory import MemoryStore
from learning.promotion_evidence_types import PROMOTION_EVALUATION_MEMORY_TYPE
from strategy.registry import (
    DEFAULT_REGISTRY,
    STATUS_DRAFT,
    STATUS_PROMOTED,
    STATUS_REJECTED,
    STATUS_VALIDATED,
    derive_strategy_status,
    latest_promotion_evaluation,
)

NOW = datetime(2026, 9, 10, tzinfo=IST)


def _seed_evaluation(
    store: MemoryStore,
    strategy_id: str,
    regime: str,
    promote: bool,
    reasons: list[str],
    historical_candidates: int = 5,
    now: datetime = NOW,
) -> str:
    return store.append(
        PROMOTION_EVALUATION_MEMORY_TYPE,
        {
            "condition": {
                "metric": "win_rate", "setup_type": strategy_id, "regime": regime,
                "operator": ">=", "threshold": 0.5, "min_samples": 3, "rationale": "test",
            },
            "structural_evidence": {
                "historical_candidates": historical_candidates, "train_candidates": 2,
                "validation_candidates": 1, "out_of_sample_candidates": 1,
                "has_historical": True, "has_walk_forward": True, "has_out_of_sample": True,
            },
            "outcome_evidence": {
                "condition": {}, "passed": True, "actual_value": 1.0, "sample_size": 5,
                "evaluated_at": now.isoformat(),
            },
            "decision": {"promote": promote, "reasons": reasons},
            "source_experiment_memory_id": "exp-1",
        },
        now,
    )


# --- registry catalog ---


def test_default_registry_catalogs_the_six_real_already_implemented_setup_detectors():
    ids = {s.strategy_id for s in DEFAULT_REGISTRY}
    assert ids == {
        "OPENING_RANGE_BREAKOUT", "TREND_CONTINUATION", "MOMENTUM_CONTINUATION",
        "VWAP_BREAKOUT", "VWAP_REJECTION", "SUPPORT_RESISTANCE_REACTION",
    }


def test_every_default_strategy_has_a_real_version_and_supported_regimes():
    for strategy in DEFAULT_REGISTRY:
        assert strategy.version == "v1"
        assert strategy.supported_regimes  # never empty
        assert strategy.created_at


def test_trend_setups_support_trend_and_gap_regimes_matching_real_live_context_gating():
    trend_setup = next(s for s in DEFAULT_REGISTRY if s.strategy_id == "OPENING_RANGE_BREAKOUT")
    assert set(trend_setup.supported_regimes) == {"TREND_UP", "TREND_DOWN", "GAP_UP", "GAP_DOWN"}


def test_range_setups_support_range_and_uncertain_regimes_matching_real_live_context_gating():
    range_setup = next(s for s in DEFAULT_REGISTRY if s.strategy_id == "VWAP_REJECTION")
    assert set(range_setup.supported_regimes) == {"RANGE", "UNCERTAIN"}


# --- status derivation: never a second promotion mechanism, always read from real evidence ---


def test_a_strategy_with_no_promotion_evidence_is_draft(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")

    assert derive_strategy_status(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", NOW) == STATUS_DRAFT


def test_a_strategy_missing_only_human_approval_is_validated_not_promoted(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=False, reasons=["human approval"])

    assert derive_strategy_status(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", NOW) == STATUS_VALIDATED


def test_a_strategy_with_a_real_promote_true_decision_is_promoted(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(
        store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True,
        reasons=["Candidate may be promoted by explicit process."],
    )

    assert derive_strategy_status(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", NOW) == STATUS_PROMOTED


def test_a_strategy_that_genuinely_failed_a_deterministic_gate_is_rejected(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(
        store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=False,
        reasons=["out-of-sample validation", "human approval"],
    )

    assert derive_strategy_status(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", NOW) == STATUS_REJECTED


def test_status_is_scoped_to_the_exact_strategy_and_regime_pair(tmp_path):
    """A strategy promoted for one regime must not appear promoted for a
    different regime it was never evaluated against."""
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(
        store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True,
        reasons=["Candidate may be promoted by explicit process."],
    )

    assert derive_strategy_status(store, "OPENING_RANGE_BREAKOUT", "TREND_DOWN", NOW) == STATUS_DRAFT
    assert derive_strategy_status(store, "VWAP_REJECTION", "TREND_UP", NOW) == STATUS_DRAFT


def test_status_only_considers_evidence_at_or_before_as_of_no_look_ahead(tmp_path):
    """A real promotion_evaluation recorded AFTER `as_of` must not make
    a strategy appear promoted at an earlier point in time."""
    store = MemoryStore(tmp_path / "memory.db")
    later = datetime(2026, 9, 15, tzinfo=IST)
    _seed_evaluation(
        store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True,
        reasons=["Candidate may be promoted by explicit process."], now=later,
    )

    assert derive_strategy_status(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", NOW) == STATUS_DRAFT
    assert derive_strategy_status(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", later) == STATUS_PROMOTED


def test_status_uses_the_most_recent_real_evaluation_when_several_exist(tmp_path):
    """A real strategy can be re-evaluated over time (e.g. after more
    real evidence accumulates) -- status must reflect the latest real
    decision, not an earlier, superseded one."""
    store = MemoryStore(tmp_path / "memory.db")
    earlier = datetime(2026, 9, 5, tzinfo=IST)
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=False, reasons=["out-of-sample validation"], now=earlier)
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=False, reasons=["human approval"], now=NOW)

    assert derive_strategy_status(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", NOW) == STATUS_VALIDATED


def test_latest_promotion_evaluation_returns_none_when_nothing_matches(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")

    assert latest_promotion_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", NOW) is None


def test_latest_promotion_evaluation_returns_the_real_payload(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    _seed_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", promote=True, reasons=[], historical_candidates=42)

    payload = latest_promotion_evaluation(store, "OPENING_RANGE_BREAKOUT", "TREND_UP", NOW)

    assert payload is not None
    assert payload["structural_evidence"]["historical_candidates"] == 42
