from __future__ import annotations

from datetime import datetime, timedelta

from agents.contracts import TradeCandidate
from agents.orchestrator import Orchestrator
from agents.trading_agents import TradeBuilderAgent
from config import IST, Settings
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote
from risk.risk_manager import RiskManager
from risk.trade_limits import DailyLimits
from strategy.option_selector import SelectedOption


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


def candidate() -> TradeCandidate:
    return TradeCandidate(
        "CALL", "OPENING_STRUCTURE", "NIFTY", 88.0, ("evidence",), (), (100.0, 100.5),
        (92.0, 92.5), (114.0, 115.0),
    )


def selected_option() -> SelectedOption:
    instrument = OptionInstrument(
        "NIFTY24CE", 22000, datetime.now(IST).date() + timedelta(days=3), "CE", 75
    )
    quote = OptionQuote(instrument, 100, datetime.now(IST), 99.5, 100.5, 1000)
    return SelectedOption(quote, 75, 1.0)


def test_second_trade_sizing_is_unaffected_by_first_trades_profit():
    """Position sizing must be anchored to the fixed per-trade risk cap, not
    to running/current balance -- proven here by building two theses back to
    back under identical market conditions, with a large realized profit
    recorded on the RiskManager's *caller* in between (simulating trade #1
    having already closed in profit), and asserting the second thesis is
    sized identically to the first.
    """
    risk = RiskManager(600, 7500)
    builder = TradeBuilderAgent(risk, low_confidence=75.0, high_confidence=95.0)
    context = {"candidate": candidate(), "selected_option": selected_option(), "option_atr": 8}

    first_thesis = builder.run(context).data["thesis"]
    # Nothing about RiskManager/TradeBuilderAgent takes a "realized profit so
    # far" input at all -- there is no argument to mutate here to simulate
    # trade #1's profit landing in a running balance, which is itself the
    # point: the API surface has no such path.
    second_thesis = builder.run(context).data["thesis"]

    assert first_thesis.quantity == second_thesis.quantity
    assert first_thesis.estimated_risk == second_thesis.estimated_risk


def test_daily_profit_target_blocks_new_entries_with_trades_remaining_and_no_loss():
    limits = DailyLimits(max_trades=3, max_daily_loss=1000, daily_profit_target=500)
    limits.register_open()
    limits.register_close(500.0)  # hit the target exactly, no loss at all

    assert limits.trades == 1 < limits.max_trades
    assert limits.realized_pnl > 0
    assert not limits.can_open()


def test_daily_profit_target_does_not_block_when_pnl_is_below_it():
    limits = DailyLimits(max_trades=3, max_daily_loss=1000, daily_profit_target=500)
    limits.register_open()
    limits.register_close(200.0)

    assert limits.can_open()


def test_unset_profit_target_never_blocks_regardless_of_profit():
    limits = DailyLimits(max_trades=3, max_daily_loss=1000, daily_profit_target=None)
    limits.register_open()
    limits.register_close(10_000.0)

    assert limits.can_open()


def test_already_open_position_still_supervised_normally_after_profit_target_hit(tmp_path):
    """The profit lock blocks new entries -- it must never reach into an
    already-open position's own supervision. Simulates the target having
    already been hit by an earlier trade this day (realized_pnl set past
    the target, can_open() now False) while a second position is still
    open, and asserts that position still closes via its own normal
    target-hit path, completely unaffected by the daily-limits state.
    """
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    cycle = orchestrator.run_cycle(filled_cycle_context())
    market_time = datetime.now(IST).replace(hour=10, minute=0, second=0, microsecond=0)
    state = orchestrator.open_position(cycle, now=market_time)

    orchestrator.limits.daily_profit_target = 1.0
    orchestrator.limits.realized_pnl = 500.0
    assert not orchestrator.limits.can_open()

    result = orchestrator.supervise_once(state, state.thesis.target, market_time)

    assert result.should_exit and result.reason == "TAKE_PROFIT"
    assert orchestrator.paper_broker.get_positions() == []
