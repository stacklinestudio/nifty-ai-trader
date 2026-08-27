from __future__ import annotations

from datetime import datetime, timedelta

from agents.contracts import TradeCandidate, TradeThesis
from config import IST
from execution.position_persistence import position_state_from_dict, position_state_to_dict
from execution.position_supervisor import PositionState


def sample_state() -> PositionState:
    candidate = TradeCandidate(
        "CALL",
        "OPENING_STRUCTURE",
        "NIFTY",
        88.0,
        ("deterministic setup",),
        ("Loss of entry zone",),
        (100.0, 100.5),
        (90.0, 90.5),
        (114.0, 115.0),
    )
    thesis = TradeThesis(
        candidate, "NIFTY24CE", 100.0, 90.0, 115.0, 75, 750.0, 88.0, ("evidence",), ()
    )
    opened_at = datetime.now(IST).replace(microsecond=0)
    state = PositionState.opening(
        thesis, opened_at, "TREND_UP", "NORMAL", "BULLISH",
        {"india_market": "BULLISH"}, "PAPER-abc123",
    )
    state.observe(108.0, opened_at + timedelta(seconds=30), 0.15)
    return state


def test_position_state_round_trips_through_dict():
    original = sample_state()
    restored = position_state_from_dict(position_state_to_dict(original))

    assert restored.thesis.candidate.candidate_id == original.thesis.candidate.candidate_id
    assert restored.thesis.entry == original.thesis.entry
    assert restored.thesis.stop == original.thesis.stop
    assert restored.thesis.target == original.thesis.target
    assert restored.thesis.quantity == original.thesis.quantity
    assert restored.current_stop == original.current_stop
    assert restored.mae == original.mae and restored.mfe == original.mfe
    assert restored.opened_at == original.opened_at
    assert restored.last_quote_at == original.last_quote_at
    assert restored.entry_regime == "TREND_UP"
    assert restored.entry_agent_directions == {"india_market": "BULLISH"}
    assert restored.entry_order_id == "PAPER-abc123"
