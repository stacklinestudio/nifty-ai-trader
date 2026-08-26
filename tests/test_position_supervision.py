from __future__ import annotations

from agents.contracts import TradeCandidate, TradeThesis
from agents.trading_agents import TradeSupervisorAgent


def thesis(entry=100.0, stop=90.0, target=115.0) -> TradeThesis:
    candidate = TradeCandidate(
        "CALL",
        "OPENING_STRUCTURE",
        "NIFTY",
        88,
        ("evidence",),
        ("Loss of entry zone",),
        (entry, entry + 0.5),
        (stop, stop + 0.5),
        (target - 1, target),
    )
    return TradeThesis(candidate, "NIFTY24CE", entry, stop, target, 75, 750, 88, ("evidence",), ())


def test_supervisor_holds_without_position_or_ltp():
    review = TradeSupervisorAgent().run({})
    assert review.data["recommendation"] == "HOLD" and review.data["thesis_state"] == "UNKNOWN"


def test_supervisor_recommends_exit_target_at_target_price():
    review = TradeSupervisorAgent().run(
        {"thesis": thesis(), "ltp": 115.0, "current_stop": 90.0}
    )
    assert review.data["recommendation"] == "EXIT_TARGET"


def test_supervisor_recommends_exit_stop_at_current_not_original_stop():
    # Original stop is 90, but the trailed current stop has moved to 105 —
    # a price of 100 must trigger EXIT_STOP against the current stop, even
    # though it's well above the original stop.
    review = TradeSupervisorAgent().run(
        {"thesis": thesis(), "ltp": 100.0, "current_stop": 105.0}
    )
    assert review.data["recommendation"] == "EXIT_STOP"


def test_supervisor_holds_between_stop_and_target():
    review = TradeSupervisorAgent().run(
        {"thesis": thesis(), "ltp": 105.0, "current_stop": 90.0}
    )
    assert review.data["recommendation"] == "HOLD"
    assert review.data["thesis_state"] == "STRENGTHENING"


def test_supervisor_flags_thesis_invalidated_on_regime_flip_separately_from_stop():
    # Price is comfortably between stop and target -- a plain price check
    # would say HOLD -- but the regime flipped, which must still surface as
    # a distinct EXIT_INVALIDATED, not EXIT_STOP.
    review = TradeSupervisorAgent().run(
        {
            "thesis": thesis(),
            "ltp": 105.0,
            "current_stop": 90.0,
            "entry_regime": "TREND_UP",
            "current_regime": "TREND_DOWN",
        }
    )
    assert review.data["recommendation"] == "EXIT_INVALIDATED"
    assert review.data["thesis_state"] == "INVALIDATED"


def test_supervisor_flags_thesis_invalidated_on_volatility_spike():
    review = TradeSupervisorAgent().run(
        {
            "thesis": thesis(),
            "ltp": 105.0,
            "current_stop": 90.0,
            "entry_volatility_regime": "NORMAL",
            "current_volatility_regime": "HIGH",
        }
    )
    assert review.data["recommendation"] == "EXIT_INVALIDATED"
