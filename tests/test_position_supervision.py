from __future__ import annotations

from datetime import datetime, time, timedelta

from agents.contracts import TradeCandidate, TradeThesis
from agents.trading_agents import TradeSupervisorAgent
from config import IST
from execution.position_supervisor import PositionState, tick


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


def opened_at(hour=10, minute=0) -> datetime:
    return datetime.now(IST).replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_tick_holds_within_bounds_and_tracks_mae_mfe():
    open_time = opened_at()
    state = PositionState.opening(thesis(), open_time)
    result = tick(state, 95.0, open_time, time(15, 15), 60, TradeSupervisorAgent())
    assert not result.should_exit
    assert state.mae == 5.0 and state.mfe == 0.0
    result = tick(
        state, 108.0, open_time + timedelta(seconds=5), time(15, 15), 60, TradeSupervisorAgent()
    )
    assert not result.should_exit
    assert state.mfe == 8.0 and state.mae == 5.0


def test_tick_exits_on_target_hit():
    open_time = opened_at()
    state = PositionState.opening(thesis(), open_time)
    result = tick(state, 115.0, open_time, time(15, 15), 60, TradeSupervisorAgent())
    assert result.should_exit and result.reason == "TAKE_PROFIT" and result.exit_price == 115.0


def test_tick_exits_on_trailed_stop_not_original_stop():
    open_time = opened_at()
    state = PositionState.opening(thesis(entry=100, stop=90, target=200), open_time)
    # Run the price up so the trailing stop moves well above the original 90.
    tick(state, 160.0, open_time, time(15, 15), 60, TradeSupervisorAgent())
    trailed_stop = state.current_stop
    assert trailed_stop > 90
    # A pullback that is still above the *original* stop but at/below the
    # *trailed* stop must trigger STOP_LOSS.
    pullback_price = trailed_stop - 1
    assert pullback_price > 90
    result = tick(
        state,
        pullback_price,
        open_time + timedelta(seconds=5),
        time(15, 15),
        60,
        TradeSupervisorAgent(),
    )
    assert result.should_exit and result.reason == "STOP_LOSS"


def test_tick_forces_exit_at_1515_regardless_of_pnl_or_strengthening():
    open_time = opened_at(hour=10)
    state = PositionState.opening(thesis(entry=100, stop=90, target=1000), open_time)
    # Deep in profit and clearly STRENGTHENING (no target set within reach).
    tick(state, 150.0, open_time, time(15, 15), 60, TradeSupervisorAgent())
    now = opened_at(hour=15, minute=15)
    result = tick(state, 155.0, now, time(15, 15), 60, TradeSupervisorAgent())
    assert result.should_exit and result.reason == "FORCED_EXIT"


def test_tick_holds_and_notifies_on_stale_data_instead_of_guessing():
    open_time = opened_at()
    state = PositionState.opening(thesis(), open_time)
    # No fresh quote for well beyond the staleness threshold.
    later = open_time + timedelta(seconds=120)
    result = tick(state, None, later, time(15, 15), 60, TradeSupervisorAgent())
    assert not result.should_exit
    assert result.notify_stale is True


def test_tick_forces_exit_at_deadline_even_with_stale_data():
    open_time = opened_at(hour=10)
    state = PositionState.opening(thesis(), open_time)
    now = opened_at(hour=15, minute=15)
    result = tick(state, None, now, time(15, 15), 60, TradeSupervisorAgent())
    assert result.should_exit and result.reason == "FORCED_EXIT"
    # Exits at the last known valid price (entry, since no quote ever arrived)
    # rather than fabricating a current price.
    assert result.exit_price == state.thesis.entry
