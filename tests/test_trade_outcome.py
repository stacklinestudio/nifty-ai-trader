"""Phase 2 Piece 5, Requirement 2 (+ part of Requirement 8): the
canonical closed-trade outcome record.

No real trade has ever closed in this project (confirmed repeatedly by
this session's own audits) -- these tests exercise the real, deterministic
build_trade_outcome_record function against real-shaped, hand-built
review-context facts (the exact same convention every other test in this
suite already uses for a trade that hasn't happened yet, e.g. tests/
test_v2_system.py's candidate_context helpers), never a fabricated claim
that a specific trade actually occurred.
"""

from __future__ import annotations

from learning.trade_outcome import TradeOutcomeRecord, build_trade_outcome_record


def _real_shaped_review_context(pnl: float = 650.0, outcome: str = "WIN") -> dict:
    return {
        "decision_ledger_entry": {"candidate_id": "CAND-20260910-093000-001", "regime": "TREND_UP"},
        "candidate": {
            "candidate_id": "real-uuid-1",
            "decision_ledger_candidate_id": "CAND-20260910-093000-001",
            "direction": "CALL",
            "setup_type": "OPENING_RANGE_BREAKOUT",
            "confidence": 80.0,
            "evidence": ["e1"],
            "invalidations": [],
            "entry_zone": [100, 101],
            "stop_zone": [95, 95],
            "target_zone": [110, 112],
        },
        "execution": {
            "order_id": "o1",
            "entry_timestamp": "2026-09-10T09:20:00+05:30",
            "entry_price": 100.0,
            "exit_timestamp": "2026-09-10T10:00:00+05:30",
            "exit_price": 110.0,
            "quantity": 65,
            "estimated_costs": 5.0,
            "symbol": "NIFTY24200CE",
        },
        "outcome": {
            "pnl": pnl,
            "outcome": outcome,
            "exit_reason": "TAKE_PROFIT" if outcome == "WIN" else "STOP_LOSS",
            "mae": 2.0,
            "mfe": 12.0,
            "hold_seconds": 2400,
            "stop_was_trailed": True,
            "entry_regime": "TREND_UP",
            "entry_volatility_regime": "NORMAL",
            "entry_consensus": "BULLISH",
            "agent_agreement": {"technical": "BULLISH"},
        },
        "prior_pattern_stats": {
            "setup_type": "OPENING_RANGE_BREAKOUT",
            "regime": "TREND_UP",
            "sample_size": 0,
            "win_rate": None,
            "expectancy": None,
            "low_confidence": True,
        },
        "score_attribution": {"setup_type": "OPENING_RANGE_BREAKOUT", "regime": "TREND_UP", "confidence": 80.0},
    }


def test_build_trade_outcome_record_carries_every_real_required_field():
    record = build_trade_outcome_record(_real_shaped_review_context())

    assert isinstance(record, TradeOutcomeRecord)
    assert record.candidate_id == "real-uuid-1"
    assert record.decision_ledger_candidate_id == "CAND-20260910-093000-001"
    assert record.strategy_version == "v2"
    assert record.setup_type == "OPENING_RANGE_BREAKOUT"
    assert record.direction == "CALL"
    assert record.entry_timestamp == "2026-09-10T09:20:00+05:30"
    assert record.entry_price == 100.0
    assert record.exit_timestamp == "2026-09-10T10:00:00+05:30"
    assert record.exit_price == 110.0
    assert record.realized_pnl == 650.0
    assert record.outcome == "WIN"
    assert record.holding_duration_seconds == 2400
    assert record.mfe == 12.0
    assert record.mae == 2.0
    assert record.entry_regime == "TREND_UP"
    assert record.score_attribution == {"setup_type": "OPENING_RANGE_BREAKOUT", "regime": "TREND_UP", "confidence": 80.0}
    assert record.market_state_snapshot == {"candidate_id": "CAND-20260910-093000-001", "regime": "TREND_UP"}
    assert record.ai_hypothesis_reference is None  # not yet set at construction time -- real, honest default


def test_outcome_id_is_unique_per_real_record():
    record_a = build_trade_outcome_record(_real_shaped_review_context())
    record_b = build_trade_outcome_record(_real_shaped_review_context())

    assert record_a.outcome_id != record_b.outcome_id


def test_to_dict_keeps_the_real_keys_pattern_memory_stats_for_already_depends_on():
    """learning/pattern_memory.py::stats_for reads entry["payload"]["pnl"]/
    ["setup_type"]/["entry_regime"] directly -- this canonical record
    must still carry those exact keys, not just a semantically-equivalent
    renamed field, or the already-tested aggregation silently breaks."""
    record = build_trade_outcome_record(_real_shaped_review_context())
    payload = record.to_dict()

    assert payload["pnl"] == 650.0
    assert payload["setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert payload["entry_regime"] == "TREND_UP"
    assert payload["hold_seconds"] == 2400


def test_a_losing_trade_gets_the_same_complete_fields_as_a_winning_one():
    winning = build_trade_outcome_record(_real_shaped_review_context(pnl=650.0, outcome="WIN"))
    losing = build_trade_outcome_record(_real_shaped_review_context(pnl=-325.0, outcome="LOSS"))

    win_keys = set(winning.to_dict().keys()) - {"outcome_id"}
    loss_keys = set(losing.to_dict().keys()) - {"outcome_id"}
    assert win_keys == loss_keys  # identical field set, only values differ

    assert losing.outcome == "LOSS"
    assert losing.realized_pnl == -325.0
    assert losing.mfe == 12.0 and losing.mae == 2.0  # a loss still carries real MFE/MAE, not stripped


def test_missing_partial_context_fails_honestly_not_silently_with_a_fabricated_value():
    """A genuinely incomplete review-context (e.g. the decision-ledger
    lookup failed, or an entry never went through the live-context
    pipeline) must produce explicit None fields, never a guessed
    placeholder standing in for real data."""
    incomplete = {
        "decision_ledger_entry": None,
        "candidate": {"candidate_id": "real-uuid-2", "setup_type": "VWAP_BREAKOUT", "direction": "PUT"},
        "execution": {},
        "outcome": {"pnl": -50.0, "outcome": "LOSS"},
        "prior_pattern_stats": {},
        "score_attribution": None,
    }

    record = build_trade_outcome_record(incomplete)

    assert record.decision_ledger_candidate_id is None
    assert record.entry_timestamp is None
    assert record.entry_price is None
    assert record.exit_timestamp is None
    assert record.exit_price is None
    assert record.confidence is None
    assert record.market_state_snapshot is None
    assert record.score_attribution is None
    # What genuinely IS present must still come through correctly.
    assert record.setup_type == "VWAP_BREAKOUT"
    assert record.direction == "PUT"
    assert record.realized_pnl == -50.0
    assert record.outcome == "LOSS"
