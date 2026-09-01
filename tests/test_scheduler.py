from __future__ import annotations

from datetime import date, datetime, timedelta

from agents.orchestrator import Orchestrator
from config import IST, Settings
from data.calendar import NseCalendar
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote
from execution.scheduler import resume_open_positions, run_trading_day


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


def market_open_time() -> datetime:
    # A Monday, comfortably inside market hours.
    return datetime(2026, 8, 24, 10, 0, tzinfo=IST)


def unavailable_quote_source_factory(symbol: str):
    return lambda: None


def test_scheduler_skips_weekend():
    calendar = NseCalendar()
    saturday = date(2026, 8, 29)
    result = run_trading_day(
        orchestrator=None,
        calendar=calendar,
        context_provider=dict,
        quote_source_factory=unavailable_quote_source_factory,
        clock=lambda: datetime(2026, 8, 29, 10, tzinfo=IST),
        sleeper=lambda _s: None,
        today=saturday,
    )
    assert not result.ran and result.reason == "not_a_trading_day"


def test_scheduler_skips_configured_holiday():
    holiday = date(2026, 8, 26)
    calendar = NseCalendar(holidays={holiday})
    result = run_trading_day(
        orchestrator=None,
        calendar=calendar,
        context_provider=dict,
        quote_source_factory=unavailable_quote_source_factory,
        clock=lambda: datetime(2026, 8, 26, 10, tzinfo=IST),
        sleeper=lambda _s: None,
        today=holiday,
    )
    assert not result.ran and result.reason == "not_a_trading_day"


def test_scheduler_no_entry_on_trading_day_with_no_market_data(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    calendar = NseCalendar()
    result = run_trading_day(
        orchestrator,
        calendar,
        context_provider=dict,
        quote_source_factory=unavailable_quote_source_factory,
        clock=market_open_time,
        sleeper=lambda _s: None,
    )
    assert result.ran and result.reason == "no_entry"
    assert result.cycle is not None and result.cycle.order is None


def test_scheduler_fills_and_supervises_to_a_real_close(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    calendar = NseCalendar()
    ticks = {"n": 0}
    factory_calls: list[str] = []

    def clock():
        ticks["n"] += 1
        return market_open_time() + timedelta(seconds=ticks["n"])

    def quote_source_factory(symbol: str):
        factory_calls.append(symbol)

        def quote_source():
            return 20.0  # comfortably past any realistic target for this fixture

        return quote_source

    result = run_trading_day(
        orchestrator,
        calendar,
        context_provider=filled_cycle_context,
        quote_source_factory=quote_source_factory,
        clock=clock,
        sleeper=lambda _s: None,
    )

    assert result.ran and result.reason == "closed"
    assert result.cycle.order is not None
    assert result.supervision is not None and result.supervision.should_exit
    assert orchestrator.paper_broker.get_positions() == []
    # The factory must be called with the actual held instrument's symbol
    # (NIFTY24CE, per filled_cycle_context's fixture), not a hardcoded index
    # symbol like "NIFTY" -- this is the exact bug this signature exists to
    # prevent.
    assert factory_calls == ["NIFTY24CE"]


def test_resume_open_positions_resumes_before_any_new_entry(tmp_path):
    db_path = tmp_path / "paper.db"
    settings = Settings(database_path=db_path)
    first_run = Orchestrator(settings)
    cycle = first_run.run_cycle(filled_cycle_context())
    assert cycle.order is not None
    first_run.open_position(cycle, now=market_open_time())

    restarted = Orchestrator(Settings(database_path=db_path))
    factory_calls: list[str] = []

    def quote_source_factory(symbol: str):
        factory_calls.append(symbol)
        return lambda: 20.0

    results = resume_open_positions(
        restarted,
        quote_source_factory=quote_source_factory,
        clock=market_open_time,
        sleeper=lambda _s: None,
    )

    assert len(results) == 1 and results[0].should_exit
    assert restarted.database.open_positions() == []
    # Same requirement as run_trading_day: a recovered position must be
    # supervised using its own real instrument symbol, not a hardcoded one.
    assert factory_calls == ["NIFTY24CE"]


def test_resume_open_positions_does_nothing_when_none_exist(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    assert (
        resume_open_positions(
            orchestrator,
            quote_source_factory=unavailable_quote_source_factory,
            clock=market_open_time,
            sleeper=lambda _s: None,
        )
        == []
    )
