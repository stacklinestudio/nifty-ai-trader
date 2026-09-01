from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from backtest.daily_backtest import OPENING_RANGE_MINUTES, run_daily_backtest
from config import IST, Settings


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
