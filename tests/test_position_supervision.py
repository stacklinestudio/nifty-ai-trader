from __future__ import annotations

from datetime import datetime, time, timedelta

from agents.contracts import TradeCandidate, TradeThesis
from agents.orchestrator import Orchestrator
from agents.trading_agents import TradeSupervisorAgent
from config import IST, Settings
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote
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


def filled_cycle_context() -> dict:
    instrument = OptionInstrument(
        "NIFTY24CE", 22000, datetime.now(IST).date() + timedelta(days=3), "CE", 25
    )
    quote = OptionQuote(instrument, 10, datetime.now(IST), 9.75, 10.25, 1000)
    return {
        "candidate_direction": "CALL",
        "candidate_confidence": 88,
        "entry_zone": (10.0, 10.5),
        "stop_zone": (8.0, 8.5),
        "target_zone": (13.0, 14.0),
        "option_quotes": [quote],
        "spot": 22000,
        "option_atr": 1,
        "market_data_fresh": True,
        "market_open": True,
        "features": {"ema_fast": 2, "ema_slow": 1, "close": 2, "vwap": 1, "atr": 10},
    }


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


def test_full_cycle_supervise_exit_review_trade_path_end_to_end(tmp_path):
    """run_cycle fills an entry -> open_position -> supervise_once hits
    target -> a real paper SELL closes the position -> review_trade records
    real P&L/MAE/MFE into learning memory, not just that the call happened.
    """
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    cycle = orchestrator.run_cycle(filled_cycle_context())
    assert cycle.order and cycle.order["status"] == "FILLED"

    state = orchestrator.open_position(cycle)
    assert state is not None and state.thesis.entry == cycle.thesis.entry

    open_time = opened_at(hour=10)
    state.opened_at = open_time
    state.last_quote_at = open_time
    # Run the price up through the target so the tick both records MFE along
    # the way and then triggers an actual exit.
    orchestrator.supervise_once(state, state.thesis.entry + 0.5, open_time)
    result = orchestrator.supervise_once(
        state, state.thesis.target, open_time + timedelta(seconds=5)
    )
    assert result.should_exit and result.reason == "TAKE_PROFIT"

    # The paper broker actually closed the position, not just recorded intent.
    assert orchestrator.paper_broker.get_positions() == []

    trades = orchestrator.memory.recent(memory_type="trade", limit=5)
    assert trades, "expected a closed-trade record in learning memory"
    payload = trades[0]["payload"]
    assert payload["outcome"] == "WIN"
    assert payload["exit_reason"] == "TAKE_PROFIT"
    # Real P&L reflects the paper broker's exit slippage and costs, so it is
    # positive but strictly less than the naive (exit - entry) * qty gain --
    # this is what proves it came from a real fill, not a fabricated number.
    raw_gain = (result.exit_price - state.thesis.entry) * state.thesis.quantity
    assert 0 < payload["pnl"] < raw_gain
    assert payload["mfe"] > 0
    assert payload["mae"] == 0.0


def test_supervise_once_closes_real_position_on_stop_loss(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    cycle = orchestrator.run_cycle(filled_cycle_context())
    state = orchestrator.open_position(cycle)
    open_time = opened_at(hour=10)
    state.opened_at = open_time
    state.last_quote_at = open_time

    result = orchestrator.supervise_once(state, state.thesis.stop - 0.5, open_time)

    assert result.should_exit and result.reason == "STOP_LOSS"
    assert orchestrator.paper_broker.get_positions() == []
    payload = orchestrator.memory.recent(memory_type="trade", limit=5)[0]["payload"]
    assert payload["outcome"] == "LOSS" and payload["pnl"] < 0


def test_supervise_once_forces_exit_at_1515_via_real_broker_regardless_of_state(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    cycle = orchestrator.run_cycle(filled_cycle_context())
    state = orchestrator.open_position(cycle)
    open_time = opened_at(hour=10)
    state.opened_at = open_time
    state.last_quote_at = open_time
    # Comfortably between stop and target -- nothing about price alone would
    # trigger an exit -- but it is past the forced square-off deadline.
    result = orchestrator.supervise_once(
        state, state.thesis.entry + 0.1, opened_at(hour=15, minute=15)
    )

    assert result.should_exit and result.reason == "FORCED_EXIT"
    assert orchestrator.paper_broker.get_positions() == []
    events = {event["event_type"] for event in orchestrator.database.events()}
    assert "FORCED_EXIT" in events


def test_run_supervised_retries_transient_quote_failures_without_crashing(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    cycle = orchestrator.run_cycle(filled_cycle_context())
    state = orchestrator.open_position(cycle)
    open_time = opened_at(hour=10)
    state.opened_at = open_time
    state.last_quote_at = open_time

    clock_calls = {"n": 0}
    sleeps: list[float] = []

    def clock():
        clock_calls["n"] += 1
        return open_time + timedelta(seconds=clock_calls["n"])

    def quote_source():
        # Fails twice, then succeeds by returning the target price.
        if clock_calls["n"] <= 2:
            raise ConnectionError("simulated feed drop")
        return state.thesis.target

    result = orchestrator.run_supervised(
        state,
        quote_source,
        poll_seconds=0,
        clock=clock,
        sleeper=sleeps.append,
        max_consecutive_failures=5,
    )

    assert result.should_exit and result.reason == "TAKE_PROFIT"
    # Two failures were retried (slept through), not raised.
    assert len(sleeps) >= 2


def test_run_supervised_force_exits_after_persistent_quote_failures(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    cycle = orchestrator.run_cycle(filled_cycle_context())
    state = orchestrator.open_position(cycle)
    open_time = opened_at(hour=10)
    state.opened_at = open_time
    state.last_quote_at = open_time

    def clock():
        return open_time

    def always_fails():
        raise TimeoutError("simulated permanent feed outage")

    result = orchestrator.run_supervised(
        state,
        always_fails,
        poll_seconds=0,
        clock=clock,
        sleeper=lambda _seconds: None,
        max_consecutive_failures=3,
    )

    assert result.should_exit and result.reason == "FORCED_EXIT_DATA_FAILURE"
    # A real close happened, not just a returned decision -- the position
    # is not left open and unmonitored.
    assert orchestrator.paper_broker.get_positions() == []
    events = {event["event_type"] for event in orchestrator.database.events()}
    assert "SYSTEM_ERROR" in events
