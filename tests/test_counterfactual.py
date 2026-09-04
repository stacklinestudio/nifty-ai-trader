"""Brief 12 Part B: COUNTERFACTUAL -- INDEX-PRICE PROXY, NOT REAL OPTION
P&L. Proves research/counterfactual.py uses real index price (the real
42-day dataset already used throughout this project, and small
synthetic-but-real-shaped price paths for deterministic outcome checks),
never fabricates a price, and that the required label is structurally
present in every output path -- describe(), to_dict(), and the database
row -- not just prose in a docstring.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from config import IST, Settings
from research.counterfactual import (
    COUNTERFACTUAL_LABEL,
    CounterfactualRecord,
    evaluate_counterfactual,
)
from storage.database import Database

CSV_PATH = "data/private/nifty_index_minute_2026-07-06_to_2026-09-01.csv"


def _real_candles() -> pd.DataFrame:
    """The same real, captured 42-day NIFTY minute dataset used throughout
    this project (Brief 8-10's backtests) -- not fabricated price data."""
    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    df = df.set_index("date")
    df.index = df.index.tz_convert(IST)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def _flat_then_move(direction_up: bool) -> pd.DataFrame:
    """Small, real-shaped (not fabricated as real market data -- a
    constructed deterministic path, used only to prove the walk-forward
    outcome logic itself is correct) index-price series: flat, then a
    clean, sustained move in one direction."""
    rows = []
    t0 = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    for i in range(5):
        ts = t0 + timedelta(minutes=i)
        rows.append({"date": ts, "open": 24080.0, "high": 24080.5, "low": 24079.5, "close": 24080.0, "volume": 1000})
    for i in range(20):
        ts = t0 + timedelta(minutes=5 + i)
        step = (i + 1) * (10.0 if direction_up else -10.0)
        price = 24080.0 + step
        rows.append(
            {
                "date": ts,
                "open": price,
                "high": price + (15 if direction_up else 2),
                "low": price - (2 if direction_up else 15),
                "close": price,
                "volume": 1200,
            }
        )
    frame = pd.DataFrame(rows).set_index("date")
    return frame[["open", "high", "low", "close", "volume"]].astype(float)


def test_call_that_hits_target_is_counterfactually_profitable():
    candles = _flat_then_move(direction_up=True)
    todays = candles[candles.index.date == date(2026, 9, 1)]
    features = {"close": 24080.0, "atr": 5.0}
    decision_time = todays.index[5]
    remaining = todays[todays.index > decision_time]

    record = evaluate_counterfactual(
        "TREND_CONTINUATION", "CALL", "confidence_gated", decision_time, todays, remaining, features
    )

    assert record is not None
    assert record.exit_reason == "TAKE_PROFIT"
    assert record.profitable is True
    assert record.direction == "CALL"


def test_put_that_hits_stop_is_not_counterfactually_profitable():
    """direction-aware: for a PUT, real price rising (not falling) hits
    the stop (above entry), not the target (below entry) -- the mirrored
    check research/counterfactual.py adds beyond Simulator.exit_price,
    which only handles a CALL-shaped trade."""
    candles = _flat_then_move(direction_up=True)  # price rises -- bad for a PUT
    todays = candles[candles.index.date == date(2026, 9, 1)]
    features = {"close": 24080.0, "atr": 5.0}
    decision_time = todays.index[5]
    remaining = todays[todays.index > decision_time]

    record = evaluate_counterfactual(
        "TREND_CONTINUATION", "PUT", "confidence_gated", decision_time, todays, remaining, features
    )

    assert record is not None
    assert record.exit_reason == "STOP_LOSS"
    assert record.profitable is False


def test_no_remaining_price_returns_none_not_fabricated():
    candles = _flat_then_move(direction_up=True)
    todays = candles[candles.index.date == date(2026, 9, 1)]
    features = {"close": 24080.0, "atr": 5.0}
    last_bar = todays.index[-1]

    record = evaluate_counterfactual(
        "TREND_CONTINUATION", "CALL", "confidence_gated", last_bar, todays, todays[todays.index > last_bar], features
    )

    assert record is None


def test_every_output_path_carries_the_required_label():
    """Greps for the exact required label in every real output surface:
    the label property itself, describe()'s text, to_dict()'s payload,
    and the real database row after a round-trip."""
    candles = _flat_then_move(direction_up=True)
    todays = candles[candles.index.date == date(2026, 9, 1)]
    decision_time = todays.index[5]
    remaining = todays[todays.index > decision_time]
    features = {"close": 24080.0, "atr": 5.0}

    records = [
        evaluate_counterfactual(setup, direction, "confidence_gated", decision_time, todays, remaining, features)
        for setup in ("TREND_CONTINUATION", "OPENING_RANGE_BREAKOUT", "VWAP_REJECTION")
        for direction in ("CALL", "PUT")
    ]
    records = [r for r in records if r is not None]
    assert len(records) >= 4  # real records were actually produced, not an empty list

    for record in records:
        assert record.label == COUNTERFACTUAL_LABEL
        assert COUNTERFACTUAL_LABEL in record.describe()
        assert record.to_dict()["label"] == COUNTERFACTUAL_LABEL
        assert "INDEX-PRICE PROXY" in record.describe()
        assert "INDEX-PRICE PROXY" in record.to_dict()["label"]


def test_label_cannot_be_overridden_by_a_caller():
    """label is a read-only property, not a constructor field -- proves
    this structurally, not just by convention."""
    with pytest.raises(TypeError):
        CounterfactualRecord(
            timestamp=datetime.now(IST),
            setup_type="X",
            direction="CALL",
            rejection_reason="x",
            entry=1.0,
            stop=1.0,
            target=1.0,
            exit_price=1.0,
            exit_reason="TAKE_PROFIT",
            exit_time=datetime.now(IST),
            profitable=True,
            label="SOMETHING ELSE",  # not a real field -- must be rejected, not silently accepted
        )


def test_database_round_trip_preserves_the_real_label(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    candles = _flat_then_move(direction_up=True)
    todays = candles[candles.index.date == date(2026, 9, 1)]
    decision_time = todays.index[5]
    remaining = todays[todays.index > decision_time]
    features = {"close": 24080.0, "atr": 5.0}
    record = evaluate_counterfactual(
        "TREND_CONTINUATION", "CALL", "confidence_gated", decision_time, todays, remaining, features
    )

    database.save_counterfactual(record)
    stored = database.recent_counterfactuals()

    assert len(stored) == 1
    assert stored[0]["label"] == COUNTERFACTUAL_LABEL
    assert "INDEX-PRICE PROXY" in stored[0]["label"]
    assert stored[0]["profitable"] is True


def test_uses_the_real_42_day_dataset_not_fabricated_price():
    """The real, captured NIFTY minute data already used throughout this
    project -- confirms the engine runs against it end to end and
    produces a real, label-carrying record from real price."""
    candles = _real_candles()
    trading_days = sorted({ts.date() for ts in candles.index})
    a_real_day = trading_days[20]
    todays = candles[candles.index.date == a_real_day]
    decision_time = todays.index[10]
    remaining = todays[todays.index > decision_time]
    features = {"close": float(todays.iloc[10].close), "atr": 15.0}

    record = evaluate_counterfactual(
        "TREND_CONTINUATION", "CALL", "confidence_gated", decision_time, todays, remaining, features
    )

    assert record is not None
    assert record.label == COUNTERFACTUAL_LABEL
    assert record.exit_reason in {"TAKE_PROFIT", "STOP_LOSS", "SESSION_END"}
    # The exit price is a real value drawn from the real candle data, not invented.
    assert remaining["low"].min() <= record.exit_price <= remaining["high"].max() or record.exit_reason == "SESSION_END"
