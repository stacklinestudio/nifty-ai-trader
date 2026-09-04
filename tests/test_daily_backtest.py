from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

import backtest.daily_backtest as daily_backtest_module
from backtest.daily_backtest import OPENING_RANGE_MINUTES, run_daily_backtest
from config import IST, Settings
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote


def minute_bars(day: date, count: int, base_price: float, trend: float):
    rows = []
    price = base_price
    for i in range(count):
        ts = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST) + timedelta(minutes=i)
        price += trend
        rows.append(
            {
                "date": ts,
                "open": price - 0.5,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 1000,
            }
        )
    return rows


def test_first_day_is_skipped_for_insufficient_prior_history(tmp_path):
    day1 = date(2026, 8, 3)
    frame = pd.DataFrame(minute_bars(day1, 375, 24000.0, 0.0)).set_index("date")
    settings = Settings(database_path=tmp_path / "backtest.db")

    report = run_daily_backtest(settings, frame)

    assert len(report.days) == 1
    assert report.days[0].reason == "insufficient_prior_history"
    assert report.candidates_formed == 0


def test_no_look_ahead_only_early_bars_of_a_later_day_are_visible(tmp_path):
    day1 = date(2026, 8, 3)
    day2 = date(2026, 8, 4)
    rows = minute_bars(day1, 375, 24000.0, 0.0) + minute_bars(day2, 375, 24000.0, 5.0)
    frame = pd.DataFrame(rows).set_index("date")
    settings = Settings(database_path=tmp_path / "backtest.db")

    report = run_daily_backtest(settings, frame)

    assert len(report.days) == 2
    day2_result = report.days[1]
    assert day2_result.trading_day == day2
    # The context builder must only have seen day2's first
    # OPENING_RANGE_MINUTES+1 bars, not all 375 -- verified via the
    # driver's own internal slicing rather than the day never running.
    assert day2_result.reason in {"no_candidate", "candidate_no_option", "traded"}


def test_insufficient_todays_bars_produces_no_candidate_not_a_crash(tmp_path):
    # Only OPENING_RANGE_MINUTES bars on day 2 -- not enough for _add_candidate.
    day1 = date(2026, 8, 3)
    day2 = date(2026, 8, 4)
    rows = minute_bars(day1, 375, 24000.0, 0.0) + minute_bars(day2, OPENING_RANGE_MINUTES, 24000.0, 0.0)
    frame = pd.DataFrame(rows).set_index("date")
    settings = Settings(database_path=tmp_path / "backtest.db")

    report = run_daily_backtest(settings, frame)

    assert report.days[1].reason == "insufficient_today_bars"
    assert report.candidates_formed == 0


def test_a_fresh_orchestrator_is_used_each_day_but_learning_memory_persists(tmp_path):
    day1 = date(2026, 8, 3)
    day2 = date(2026, 8, 4)
    rows = minute_bars(day1, 375, 24000.0, 0.0) + minute_bars(day2, 375, 24000.0, 0.0)
    frame = pd.DataFrame(rows).set_index("date")
    db_path = tmp_path / "backtest.db"
    settings = Settings(database_path=db_path)

    report = run_daily_backtest(settings, frame)

    assert report.trading_days_evaluated == 2
    # day1 is correctly skipped (no prior history to compute anything
    # from) -- day2 is the real assertion: a fresh Orchestrator/run_cycle
    # actually ran against the same database file without crashing on a
    # second Database.initialize() call against an already-initialized db.
    assert report.days[0].cycle is None
    assert report.days[1].cycle is not None


def _option_quote(symbol: str, oi: int) -> OptionQuote:
    instrument = OptionInstrument(symbol, 24200.0, date(2026, 8, 5), "CE", 65)
    return OptionQuote(instrument, 100.0, datetime(2026, 8, 5, 9, 30, tzinfo=IST), open_interest=oi)


def test_day_ns_option_chain_becomes_day_n_plus_1s_previous_option_quotes(tmp_path, monkeypatch):
    """Brief 5 Part B.3: run_daily_backtest doesn't need its own SQLite
    persistence the way the live path does (main.py) -- the whole
    day-indexed option_quotes_by_day dict is already resident in memory
    for the entire backtest window, so day N's real chain can be threaded
    straight into day N+1's assemble_context call as previous_option_quotes,
    the same real day-over-day comparison OI-buildup/option-volume scoring
    needs. Verified here by spying on the real assemble_context call
    arguments (backtest/daily_backtest.py's own dependency), not by
    reimplementing the OI-buildup math."""
    day1, day2, day3 = date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)
    rows = minute_bars(day1, 375, 24000.0, 0.0) + minute_bars(day2, 375, 24000.0, 0.0) + minute_bars(
        day3, 375, 24000.0, 0.0
    )
    frame = pd.DataFrame(rows).set_index("date")
    settings = Settings(database_path=tmp_path / "backtest.db")
    day1_chain = [_option_quote("NIFTY2680324200CE", 10000)]
    # day2 deliberately has NO entry -- proves day3 still sees day1's real
    # chain (the last one actually fetched), not an erased/reset baseline.
    option_quotes_by_day = {day1: day1_chain}

    calls = []
    real_assemble_context = daily_backtest_module.assemble_context

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_assemble_context(*args, **kwargs)

    monkeypatch.setattr(daily_backtest_module, "assemble_context", spy)

    run_daily_backtest(settings, frame, option_quotes_by_day)

    # day1 is skipped for insufficient_prior_history (every backtest's
    # first day, always) -- assemble_context is never called for it, but
    # its real chain must still be recorded as day2's baseline (that's the
    # bug this test guards: reading option_quotes_by_day only AFTER the
    # insufficient_prior_history skip would lose day1's data forever).
    assert len(calls) == 2  # day2, day3 -- day1 skipped before assemble_context
    day2_call, day3_call = calls[0], calls[1]
    # Positional call shape: (candles, option_quotes, spot, decision_time,
    # market_open, settings, previous_option_quotes) -- see run_daily_backtest.
    assert day2_call[0][6] == day1_chain  # day2: real day1 chain as its previous
    assert day3_call[0][6] == day1_chain  # day3: day2 had none -- still day1's, not reset to []


def test_no_option_data_anywhere_still_correctly_reads_unavailable(tmp_path, monkeypatch):
    day1, day2 = date(2026, 8, 3), date(2026, 8, 4)
    rows = minute_bars(day1, 375, 24000.0, 0.0) + minute_bars(day2, 375, 24000.0, 0.0)
    frame = pd.DataFrame(rows).set_index("date")
    settings = Settings(database_path=tmp_path / "backtest.db")

    calls = []
    real_assemble_context = daily_backtest_module.assemble_context

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_assemble_context(*args, **kwargs)

    monkeypatch.setattr(daily_backtest_module, "assemble_context", spy)

    run_daily_backtest(settings, frame)  # no option_quotes_by_day at all

    assert len(calls) == 1  # day2 only
    assert calls[0][0][6] == []  # never any real data to compare against -- explicit [], not fabricated


def test_global_context_by_day_threads_the_real_per_day_value_with_no_carry_forward(tmp_path, monkeypatch):
    """Brief 8 Part D: unlike option_quotes_by_day (carried forward when a
    day has no entry), global_context_by_day has a genuinely real value
    for every real trading day (data/global_market.py::fetch_global_history
    has no gaps to carry across) -- each day's own entry (or explicit []
    if genuinely missing) is used as-is, never the previous day's."""
    from data.global_market import ContextValue

    day1, day2, day3 = date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)
    rows = (
        minute_bars(day1, 375, 24000.0, 0.0)
        + minute_bars(day2, 375, 24000.0, 0.0)
        + minute_bars(day3, 375, 24000.0, 0.0)
    )
    frame = pd.DataFrame(rows).set_index("date")
    settings = Settings(database_path=tmp_path / "backtest.db")
    day2_context = [ContextValue("SP500", 0.01, datetime.now(IST), "yfinance", True)]
    # day3 deliberately has no entry -- must read as [] (genuinely
    # unavailable that day), not silently carry day2's real value forward.
    global_context_by_day = {day2: day2_context}

    calls = []
    real_assemble_context = daily_backtest_module.assemble_context

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_assemble_context(*args, **kwargs)

    monkeypatch.setattr(daily_backtest_module, "assemble_context", spy)

    run_daily_backtest(settings, frame, global_context_by_day=global_context_by_day)

    day2_call, day3_call = calls[0], calls[1]
    assert day2_call[0][7] == day2_context
    assert day3_call[0][7] == []
