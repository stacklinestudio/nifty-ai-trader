"""Regression coverage for the exact bug reported: run_scheduled_day
supervised an open position by querying the index symbol "NIFTY", not the
actual option contract held. Uses two distinct quote values for two
distinct symbols from one FakeKite, the same approach as last night's
market-data tests -- the index LTP (24080.4) is the literal value captured
live against the real Kite API on 2026-08-31; the option LTP (120.5) is a
realistic, clearly-labeled representative value, not a literally captured
one (no real option quote was successfully fetched before the token
expired -- see V2_BUILD_REPORT.md's honest note on this).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config import IST
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote
from execution.scheduler import resume_open_positions, run_trading_day
from main import build_live_quote_source

INDEX_SYMBOL = "NSE:NIFTY 50"
OPTION_SYMBOL = "NFO:NIFTY2690124200CE"
REAL_CAPTURED_INDEX_LTP = 24080.4  # literal value captured live, 2026-08-31
REPRESENTATIVE_OPTION_LTP = 120.5  # realistic, not literally captured (see module docstring)


class FakeKite:
    """Quote timestamps always use the REAL current wall clock
    (datetime.now(IST) at call time), never a fixed/simulated test time --
    build_live_quote_source's validate_quote check compares a quote's
    timestamp against real wall-clock time (correctly, for production use:
    a live quote's own freshness must be judged against real time, not a
    test's simulated clock). A fixed simulated timestamp here would make
    every quote look permanently stale to that check regardless of what
    run_trading_day/run_supervised's own simulated `clock` says -- exactly
    the mistake in an earlier version of this file, which made
    live_quote() always return None and caused a long real supervision
    loop (with real Discord sends on every stale tick) instead of an
    immediate, correct exit.
    """

    def quote(self, symbols: list[str]) -> dict:
        symbol = symbols[0]
        ltp = REAL_CAPTURED_INDEX_LTP if symbol == INDEX_SYMBOL else REPRESENTATIVE_OPTION_LTP
        now = datetime.now(IST)
        return {
            symbol: {
                "last_price": ltp,
                "volume": 0,
                "timestamp": now.replace(tzinfo=None),
                "depth": {"buy": [{"price": ltp - 0.5}], "sell": [{"price": ltp + 0.5}]},
            }
        }


def test_build_live_quote_source_returns_the_requested_symbols_own_value():
    kite = FakeKite()

    index_source = build_live_quote_source(_dummy_settings(), INDEX_SYMBOL, kite)
    option_source = build_live_quote_source(_dummy_settings(), OPTION_SYMBOL, kite)

    assert index_source() == REAL_CAPTURED_INDEX_LTP
    assert option_source() == REPRESENTATIVE_OPTION_LTP
    assert index_source() != option_source()


def test_quote_source_factory_pattern_supervises_the_option_not_the_index(tmp_path):
    """End-to-end through the actual factory pattern run_scheduled_day
    uses: quote_source_factory(symbol) must resolve to the held option's
    own quote source, never the index's, once a position is open.
    """
    from agents.orchestrator import Orchestrator
    from config import Settings
    from data.calendar import NseCalendar

    # max_trades_per_day=1: _filled_cycle_context is a static
    # always-fillable candidate, so with Brief 6's real periodic
    # re-scanning and the default cap of 3, the loop would otherwise keep
    # taking the same trade again after this one closes.
    settings = Settings(database_path=tmp_path / "paper.db", max_trades_per_day=1)
    orchestrator = Orchestrator(settings)
    # A fixed, guaranteed-real-trading-day datetime (the same Monday
    # tests/test_scheduler.py::market_open_time() uses) -- run_trading_day
    # derives "today" from clock() (seeded from `now` below) when `today`
    # isn't passed explicitly, so a real wall-clock `now()` here made this
    # test's pass/fail depend on which real weekday it happened to run on
    # (it failed outright on a real Saturday). FakeKite.quote() below
    # deliberately keeps its own real datetime.now(IST) for the quote
    # timestamp -- that one must stay real wall-clock (see its docstring).
    now = datetime(2026, 8, 24, 10, 0, tzinfo=IST)
    kite = FakeKite()

    def quote_source_factory(symbol: str):
        return build_live_quote_source(settings, f"NFO:{symbol}", kite)

    context = _filled_cycle_context(now)
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return now + timedelta(seconds=ticks["n"])

    result = run_trading_day(
        orchestrator,
        NseCalendar(),
        context_provider=lambda: context,
        quote_source_factory=quote_source_factory,
        clock=clock,
        sleeper=lambda _s: None,
    )

    # The fixture's option symbol is NIFTY24CE, entry 10, target 13-14 --
    # REPRESENTATIVE_OPTION_LTP (120.5) is comfortably past any target,
    # forcing an immediate real TAKE_PROFIT close using the OPTION's own
    # value. If this were still querying the index (24080.4) instead, the
    # same thing would happen for the wrong reason (a nonsensical price for
    # an option), which is exactly the bug: the number driving stop/target
    # decisions was never the real premium of the actual contract held.
    assert result.ran and result.reason == "daily_limit_reached"
    assert result.supervision is not None and result.supervision.reason == "TAKE_PROFIT"
    assert result.supervision.exit_price == REPRESENTATIVE_OPTION_LTP


def test_resume_open_positions_also_uses_the_options_symbol_not_the_index(tmp_path):
    from agents.orchestrator import Orchestrator
    from config import Settings

    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path)
    first_run = Orchestrator(settings)
    # Fixed for consistency with the test above -- this test doesn't call
    # run_trading_day (resume_open_positions has no calendar check), so it
    # wasn't actually broken by a real wall-clock date, but pinning it
    # removes the same class of latent risk and matches the one real
    # pattern this file should use throughout.
    now = datetime(2026, 8, 24, 10, 0, tzinfo=IST)
    cycle = first_run.run_cycle(_filled_cycle_context(now))
    assert cycle.order is not None
    first_run.open_position(cycle, now=now)

    restarted = Orchestrator(Settings(database_path=db_path))
    kite = FakeKite()

    def quote_source_factory(symbol: str):
        return build_live_quote_source(settings, f"NFO:{symbol}", kite)

    results = resume_open_positions(
        restarted, quote_source_factory, clock=lambda: now, sleeper=lambda _s: None
    )

    assert len(results) == 1
    assert results[0].reason == "TAKE_PROFIT"
    assert results[0].exit_price == REPRESENTATIVE_OPTION_LTP


def _dummy_settings():
    from config import Settings

    return Settings()


def _filled_cycle_context(now: datetime) -> dict:
    # Expiry deliberately NOT derived from the simulated `now` above --
    # strategy/option_selector.py filters candidates on
    # `instrument.expiry >= datetime.now(IST).date()`, the REAL wall
    # clock, not any simulated test time. When `now` is a fixed past date
    # (as it is in this file, pinned to a guaranteed real trading day),
    # `now.date() + timedelta(days=3)` can be long expired by the time this
    # runs, silently filtering the option out of `ranked` and producing
    # "no complete trade thesis" instead of a fill -- which then made
    # run_trading_day's entry-scan loop run for the rest of the simulated
    # day (up to ~18000 iterations) instead of filling on the first scan.
    # Real current date + a few days, same safe pattern as
    # tests/test_scheduler.py::filled_cycle_context, keeps this valid
    # regardless of which simulated `now` the calendar/clock use.
    expiry = datetime.now(IST).date() + timedelta(days=3)
    instrument = OptionInstrument("NIFTY24CE", 22000, expiry, "CE", 25)
    quote = OptionQuote(instrument, 10, now, 9.75, 10.25, 1000)
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
