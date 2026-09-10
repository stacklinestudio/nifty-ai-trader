"""Phase 2 Piece 4: backtest/daily_walk_forward.py.

The single most important test in this file: proves the real 60/20/20
date-partitioned walk-forward produces NO look-ahead leakage -- a
decision made on day N is identical whether or not later (validation/
out-of-sample) days exist in the input at all. Financial time-series
backtests are well known to be vulnerable to exactly this class of bug
(reusing the whole dataset's statistics/computation for an "earlier"
window), so this is verified directly, not just asserted from reading
the code.

Every Settings() here explicitly sets database_path under tmp_path --
run_daily_backtest/run_daily_backtest_walk_forward both construct a real
Orchestrator/Database internally and genuinely persist to whatever real
path they're given (run_daily_backtest_walk_forward now isolates its own
internal run_daily_backtest call regardless, per its own fix, but this
file is explicit about it anyway rather than relying on that alone --
the same real incident this session already hit once with the default
live nifty_ai_trader.db).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.daily_backtest import run_daily_backtest
from backtest.daily_walk_forward import run_daily_backtest_walk_forward
from config import Settings


def _continuous_trending_days(start_day: date, num_days: int, start_price: float, trend: float) -> pd.DataFrame:
    """Real trading days chained together -- each day's own price series
    continues from the PRIOR day's real closing price (no artificial gap
    between days), with a small, consistent intraday trend -- reliably
    classifies as a real TREND_UP regime (intelligence/market_regime.py
    ::classify) every day, so OPENING_RANGE_BREAKOUT fires consistently
    (ORB always forms a candidate in the trend direction when time-
    eligible, per execution/live_context.py::_select_setup's own
    docstring)."""
    rows: list[dict] = []
    price = start_price
    day = start_day
    days_built = 0
    while days_built < num_days:
        # Skip real weekends -- these are trading days, not calendar days.
        if day.weekday() < 5:
            for i in range(375):
                ts = pd.Timestamp(
                    year=day.year, month=day.month, day=day.day, hour=9, minute=15, tz="Asia/Kolkata"
                ) + pd.Timedelta(minutes=i)
                price += trend
                rows.append(
                    {"date": ts, "open": price - 0.5, "high": price + 1.0, "low": price - 1.0, "close": price, "volume": 1000}
                )
            days_built += 1
        day = day + pd.Timedelta(days=1)
    return pd.DataFrame(rows).set_index("date")


def test_a_train_split_days_real_decision_is_unaffected_by_later_validation_and_out_of_sample_data(tmp_path):
    """Real proof of no look-ahead: run the full walk-forward (train +
    validation + out-of-sample all present in the input), take one real
    TRAIN-split day's resulting decision, then independently re-run
    run_daily_backtest against the SAME data truncated to end exactly at
    that day -- no validation/out-of-sample data at all. The two real
    decisions must be byte-identical; if they weren't, later data would
    have leaked backward into an earlier day's real decision.
    """
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "walk.db")

    walk = run_daily_backtest_walk_forward(settings, candles)
    assert len(walk.train.days) >= 2  # real train-split days exist to test against
    train_day = walk.train.days[-1]  # the LAST real train day -- closest to the split boundary, the real worst case for leakage
    assert train_day.cycle is not None

    truncated = candles[candles.index.date <= train_day.trading_day]
    truncated_settings = Settings(signal_threshold=50.0, database_path=tmp_path / "truncated_train.db")
    truncated_report = run_daily_backtest(truncated_settings, truncated)
    truncated_day = next(d for d in truncated_report.days if d.trading_day == train_day.trading_day)
    assert truncated_day.cycle is not None

    # Real, deterministic fields a leak would change if later data (EMA/
    # ATR computed over more history, or a different regime read) had
    # somehow reached this earlier day's decision. score_attribution is
    # unconditionally set on every real evaluated cycle (see execution/
    # live_context.py::_add_candidate) -- no option quotes are supplied
    # here, so cycle.thesis itself is correctly None on both sides
    # (OptionsAgent finds nothing tradeable), not a fact worth asserting.
    assert train_day.candidate_formed and truncated_day.candidate_formed
    assert train_day.cycle.score_attribution == truncated_day.cycle.score_attribution
    assert train_day.cycle.consensus == truncated_day.cycle.consensus


def test_a_validation_split_days_real_decision_is_unaffected_by_later_out_of_sample_data(tmp_path):
    """Same real proof, one split later: a VALIDATION-split day must not
    see out-of-sample data either."""
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "walk.db")

    walk = run_daily_backtest_walk_forward(settings, candles)
    assert len(walk.validation.days) >= 1
    validation_day = walk.validation.days[-1]
    assert validation_day.cycle is not None

    truncated = candles[candles.index.date <= validation_day.trading_day]
    truncated_settings = Settings(signal_threshold=50.0, database_path=tmp_path / "truncated_validation.db")
    truncated_report = run_daily_backtest(truncated_settings, truncated)
    truncated_day = next(d for d in truncated_report.days if d.trading_day == validation_day.trading_day)
    assert truncated_day.cycle is not None

    assert validation_day.candidate_formed and truncated_day.candidate_formed
    assert validation_day.cycle.score_attribution == truncated_day.cycle.score_attribution


def test_train_validation_out_of_sample_partition_the_real_trading_days_with_no_overlap_and_no_gap(tmp_path):
    candles = _continuous_trending_days(date(2026, 8, 3), 16, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "walk.db")

    walk = run_daily_backtest_walk_forward(settings, candles)

    train_days = {d.trading_day for d in walk.train.days}
    validation_days = {d.trading_day for d in walk.validation.days}
    out_of_sample_days = {d.trading_day for d in walk.out_of_sample.days}

    assert train_days & validation_days == set()
    assert validation_days & out_of_sample_days == set()
    assert train_days & out_of_sample_days == set()

    all_real_days = {ts.date() for ts in candles.index}
    assert train_days | validation_days | out_of_sample_days == all_real_days

    # Real chronological ordering -- train is strictly earlier than
    # validation, which is strictly earlier than out-of-sample.
    assert max(train_days) < min(validation_days)
    assert max(validation_days) < min(out_of_sample_days)


def test_requires_at_least_three_real_trading_days(tmp_path):
    candles = _continuous_trending_days(date(2026, 8, 3), 2, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=tmp_path / "walk.db")

    with pytest.raises(ValueError):
        run_daily_backtest_walk_forward(settings, candles)


def test_daily_walk_forward_never_sends_a_real_notification_even_with_real_credentials_configured(tmp_path, monkeypatch):
    """dry_run=True discipline (see backtest/daily_backtest.py's own fix
    and its regression test) must hold here too -- run_daily_backtest_
    walk_forward calls run_daily_backtest under the hood, which already
    carries the fix, but this proves it end-to-end through this specific
    entry point rather than assuming the fix propagates."""
    import requests

    calls: list[str] = []

    class _FakeResponse:
        ok = True

    def tracking_transport(url, *args, **kwargs):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(requests, "post", tracking_transport)

    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, 1.0)
    settings = Settings(
        signal_threshold=50.0,
        database_path=tmp_path / "walk.db",
        telegram_bot_token="real-looking-token",
        telegram_chat_id="real-looking-chat-id",
        discord_webhook_url="https://discord.com/api/webhooks/real/looking",
    )

    run_daily_backtest_walk_forward(settings, candles)

    assert calls == []


def test_daily_walk_forward_never_writes_to_the_callers_own_database_path(tmp_path):
    """Real proof of the isolated-scratch-database fix: pass a real,
    existing settings.database_path (the same real db test_a_fresh_
    orchestrator_is_used_each_day_but_learning_memory_persists in
    tests/test_daily_backtest.py writes real rows to) and confirm
    run_daily_backtest_walk_forward never touches it at all."""
    import sqlite3

    from storage.database import Database

    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    database = Database(real_db_path)
    database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, 1.0)
    settings = Settings(signal_threshold=50.0, database_path=real_db_path)

    run_daily_backtest_walk_forward(settings, candles)

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0
