from __future__ import annotations

from datetime import date, datetime, time, timedelta

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
    """Brief 6: run_trading_day now periodically re-scans, so the clock
    must actually advance (a real deployment's real clock does) -- a
    constant clock would never reach entry_scan_cutoff_time and loop
    forever. A tight explicit cutoff keeps this test fast and
    deterministic while still exercising a couple of real scans first."""
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)
    calendar = NseCalendar()
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return market_open_time() + timedelta(seconds=30 * ticks["n"])

    result = run_trading_day(
        orchestrator,
        calendar,
        context_provider=dict,
        quote_source_factory=unavailable_quote_source_factory,
        clock=clock,
        sleeper=lambda _s: None,
        entry_scan_cutoff_time=time(10, 2),
    )
    assert result.ran and result.reason == "scan_cutoff_reached"
    assert result.cycle is not None and result.cycle.order is None


def test_scheduler_fills_and_supervises_to_a_real_close(tmp_path):
    """max_trades_per_day=1 keeps this the same single-trade-day scenario
    it always was -- filled_cycle_context is a static always-fillable
    candidate, so with Brief 6's real re-scanning and the default cap of
    3, the loop would otherwise take up to 2 more trades against the same
    candidate before stopping. Real multi-trade pause/resume across
    several distinct rounds is covered separately below."""
    settings = Settings(database_path=tmp_path / "paper.db", max_trades_per_day=1)
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

    assert result.ran and result.reason == "daily_limit_reached"
    assert result.cycle.order is not None
    assert result.supervision is not None and result.supervision.should_exit
    assert orchestrator.paper_broker.get_positions() == []
    # The factory must be called with the actual held instrument's symbol
    # (NIFTY24CE, per filled_cycle_context's fixture), not a hardcoded index
    # symbol like "NIFTY" -- this is the exact bug this signature exists to
    # prevent.
    assert factory_calls == ["NIFTY24CE"]


def test_scan_loop_stops_immediately_when_daily_cap_is_hit_mid_day(tmp_path):
    """Brief 6 Part B.3: can_open() is checked first, every iteration --
    once the cap is hit, scanning stops entirely for the rest of the day,
    not just skips one iteration. max_trades_per_day=2 with a static
    always-fillable candidate and an instant-TAKE_PROFIT quote proves this
    by asserting context_provider was called EXACTLY twice -- a 3rd call
    would mean the loop tried again after the cap was already hit."""
    settings = Settings(database_path=tmp_path / "paper.db", max_trades_per_day=2)
    orchestrator = Orchestrator(settings)
    calendar = NseCalendar()
    ticks = {"n": 0}
    calls = {"context": 0}

    def clock():
        ticks["n"] += 1
        return market_open_time() + timedelta(seconds=ticks["n"])

    def context_provider():
        calls["context"] += 1
        return filled_cycle_context()

    def quote_source_factory(symbol: str):
        return lambda: 20.0  # comfortably past target -- closes on the first supervision tick

    result = run_trading_day(
        orchestrator,
        calendar,
        context_provider=context_provider,
        quote_source_factory=quote_source_factory,
        clock=clock,
        sleeper=lambda _s: None,
    )

    assert result.reason == "daily_limit_reached"
    assert calls["context"] == 2  # not 3 -- stopped immediately once the cap was hit
    assert len(result.rounds) == 2
    assert all(r.cycle.order is not None and r.supervision.should_exit for r in result.rounds)


def test_position_closing_with_remaining_capacity_resumes_scanning(tmp_path):
    """Brief 6 Part B.4/5: a fill correctly pauses scanning (run_supervised
    blocks until close); once it closes with daily capacity remaining,
    scanning correctly resumes. Proven by a context_provider that is only
    ever fillable on its FIRST call -- a second real round only happens if
    scanning genuinely resumed, not if the loop had quietly stopped after
    the trade closed."""
    settings = Settings(database_path=tmp_path / "paper.db", max_trades_per_day=3)
    orchestrator = Orchestrator(settings)
    calendar = NseCalendar()
    ticks = {"n": 0}
    calls = {"context": 0}

    def clock():
        ticks["n"] += 1
        return market_open_time() + timedelta(seconds=30 * ticks["n"])

    def context_provider():
        calls["context"] += 1
        return filled_cycle_context() if calls["context"] == 1 else {}

    def quote_source_factory(symbol: str):
        return lambda: 20.0

    result = run_trading_day(
        orchestrator,
        calendar,
        context_provider=context_provider,
        quote_source_factory=quote_source_factory,
        clock=clock,
        sleeper=lambda _s: None,
        entry_scan_cutoff_time=time(10, 5),
    )

    assert result.reason == "scan_cutoff_reached"
    assert calls["context"] >= 2  # scanning genuinely resumed after the close
    assert len(result.rounds) >= 2
    assert result.rounds[0].cycle.order is not None
    assert result.rounds[0].supervision is not None and result.rounds[0].supervision.should_exit
    assert all(r.cycle.order is None for r in result.rounds[1:])


def test_no_new_entry_at_or_after_cutoff_but_open_position_still_reaches_forced_exit(tmp_path):
    """Brief 6 Part B.6: entry_scan_cutoff_time only gates STARTING a new
    scan -- an already-open position still reaches the existing 15:15
    forced exit unaffected. A quote held between stop (8.0-8.5) and target
    (13.0-14.0) never exits on price, so the only way this position closes
    is the real forced-exit path in execution/position_supervisor.py::tick;
    a large per-tick clock step carries the position's own supervision
    loop across both entry_scan_cutoff_time (15:00 default) and
    forced_exit_time (15:15 default) to prove neither the position's own
    close nor the subsequent stop-scanning is a coincidence."""
    settings = Settings(database_path=tmp_path / "paper.db", max_trades_per_day=3)
    orchestrator = Orchestrator(settings)
    calendar = NseCalendar()
    ticks = {"n": 0}
    calls = {"context": 0}

    def clock():
        ticks["n"] += 1
        return market_open_time() + timedelta(minutes=20 * ticks["n"])

    def context_provider():
        calls["context"] += 1
        # Only ever fillable on the very first scan -- if scanning
        # incorrectly resumed/started after cutoff, a second call
        # returning this same candidate would open an illegitimate
        # second position, caught by the assertion on calls below.
        return filled_cycle_context() if calls["context"] == 1 else {}

    def quote_source_factory(symbol: str):
        return lambda: 11.0  # between stop and target -- never exits on price alone

    result = run_trading_day(
        orchestrator,
        calendar,
        context_provider=context_provider,
        quote_source_factory=quote_source_factory,
        clock=clock,
        sleeper=lambda _s: None,
    )

    assert len(result.rounds) == 1
    assert result.rounds[0].cycle.order is not None
    assert result.rounds[0].supervision is not None
    assert result.rounds[0].supervision.should_exit
    assert result.rounds[0].supervision.reason == "FORCED_EXIT"
    assert result.reason == "scan_cutoff_reached"
    assert calls["context"] == 1  # no second scan was ever attempted


def test_repeated_scan_failure_is_handled_with_bounded_retry_not_a_crash(tmp_path):
    """Brief 6 Part D's last requirement: a simulated repeated API failure
    during entry scanning is handled the same bounded-retry way already
    built for run_supervised (agents/orchestrator.py), reusing the same
    Settings.max_consecutive_tick_failures knob -- not a crash, not a
    silent infinite loop, and not a new/different failure mode."""
    settings = Settings(database_path=tmp_path / "paper.db", max_consecutive_tick_failures=3)
    orchestrator = Orchestrator(settings)
    calendar = NseCalendar()
    ticks = {"n": 0}
    calls = {"n": 0}

    def clock():
        ticks["n"] += 1
        return market_open_time() + timedelta(seconds=ticks["n"])

    def failing_context_provider():
        calls["n"] += 1
        raise ConnectionError("simulated feed outage")

    result = run_trading_day(
        orchestrator,
        calendar,
        context_provider=failing_context_provider,
        quote_source_factory=unavailable_quote_source_factory,
        clock=clock,
        sleeper=lambda _s: None,
    )

    assert result.reason == "scan_repeated_failure"
    assert calls["n"] == settings.max_consecutive_tick_failures  # bounded, not infinite
    assert result.rounds == []  # never produced a real cycle to record


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
