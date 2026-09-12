"""Phase 2 Piece 10, Requirements 2, 3, 4, 12: execution/regime_detection.py."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from execution.regime_detection import (
    DETECTOR_VERSION,
    INSUFFICIENT_DATA,
    MIN_BARS_FOR_REGIME,
    detect_regime,
)
from tests.test_daily_walk_forward import _continuous_trending_days


def _flat_days(start_day: date, num_days: int, price: float) -> pd.DataFrame:
    """Constant real price, negligible movement -- no real trend, no real
    gap, no real volatility spike: the honest UNCERTAIN case."""
    rows: list[dict] = []
    day = start_day
    days_built = 0
    while days_built < num_days:
        if day.weekday() < 5:
            for i in range(60):
                ts = pd.Timestamp(
                    year=day.year, month=day.month, day=day.day, hour=9, minute=15, tz="Asia/Kolkata"
                ) + pd.Timedelta(minutes=i)
                rows.append(
                    {"date": ts, "open": price, "high": price + 0.05, "low": price - 0.05, "close": price, "volume": 1000}
                )
            days_built += 1
        day = day + pd.Timedelta(days=1)
    return pd.DataFrame(rows).set_index("date")


def _choppy_high_volatility_days(start_day: date, num_days: int, price: float) -> pd.DataFrame:
    """Wide real high-low range every bar, deliberately non-trending
    (price returns to the same level each bar) -- HIGH_VOLATILITY is
    classify()'s first non-gap branch checked, so a large real
    atr/close ratio dominates regardless of the flat closes."""
    rows: list[dict] = []
    day = start_day
    days_built = 0
    while days_built < num_days:
        if day.weekday() < 5:
            for i in range(60):
                ts = pd.Timestamp(
                    year=day.year, month=day.month, day=day.day, hour=9, minute=15, tz="Asia/Kolkata"
                ) + pd.Timedelta(minutes=i)
                rows.append(
                    {"date": ts, "open": price, "high": price + 400.0, "low": price - 400.0, "close": price, "volume": 1000}
                )
            days_built += 1
        day = day + pd.Timedelta(days=1)
    return pd.DataFrame(rows).set_index("date")


def _gap_up_days(start_day: date, prior_close: float, gap_pct: float) -> pd.DataFrame:
    """A real prior day at `prior_close`, then a real next-day open
    genuinely `gap_pct` above it -- the honest GAP_UP case."""
    rows: list[dict] = []
    day1 = start_day
    for i in range(60):
        ts = pd.Timestamp(year=day1.year, month=day1.month, day=day1.day, hour=9, minute=15, tz="Asia/Kolkata") + pd.Timedelta(minutes=i)
        rows.append({"date": ts, "open": prior_close, "high": prior_close + 1, "low": prior_close - 1, "close": prior_close, "volume": 1000})
    day2 = day1 + timedelta(days=1)
    while day2.weekday() >= 5:
        day2 += timedelta(days=1)
    gapped_open = prior_close * (1 + gap_pct)
    for i in range(60):
        ts = pd.Timestamp(year=day2.year, month=day2.month, day=day2.day, hour=9, minute=15, tz="Asia/Kolkata") + pd.Timedelta(minutes=i)
        rows.append({"date": ts, "open": gapped_open, "high": gapped_open + 1, "low": gapped_open - 1, "close": gapped_open, "volume": 1000})
    return pd.DataFrame(rows).set_index("date")


def _as_of_from(candles: pd.DataFrame, offset_from_end: int = 0) -> pd.Timestamp:
    return candles.index[-1 - offset_from_end]


def test_trend_up_regime_is_detected_from_real_consistently_trending_data():
    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, 1.0)
    as_of = _as_of_from(candles)

    record = detect_regime(candles, as_of)

    assert record.regime == "TREND_UP"
    assert record.detector_version == DETECTOR_VERSION
    assert "ema_fast" in record.reason and "ema_slow" in record.reason


def test_trend_down_regime_is_detected_from_real_consistently_declining_data():
    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, -1.0)
    as_of = _as_of_from(candles)

    record = detect_regime(candles, as_of)

    assert record.regime == "TREND_DOWN"


def test_uncertain_regime_is_detected_from_real_flat_data():
    candles = _flat_days(date(2026, 8, 3), 3, 24000.0)
    as_of = _as_of_from(candles)

    record = detect_regime(candles, as_of)

    assert record.regime == "UNCERTAIN"


def test_high_volatility_regime_is_detected_from_a_real_wide_range():
    candles = _choppy_high_volatility_days(date(2026, 8, 3), 3, 24000.0)
    as_of = _as_of_from(candles)

    record = detect_regime(candles, as_of)

    assert record.regime == "HIGH_VOLATILITY"
    assert "atr/close" in record.reason


def test_gap_up_regime_is_detected_from_a_real_overnight_gap():
    candles = _gap_up_days(date(2026, 8, 3), 24000.0, 0.01)
    as_of = _as_of_from(candles)

    record = detect_regime(candles, as_of)

    assert record.regime == "GAP_UP"


def test_gap_down_regime_is_detected_from_a_real_overnight_gap():
    candles = _gap_up_days(date(2026, 8, 3), 24000.0, -0.01)
    as_of = _as_of_from(candles)

    record = detect_regime(candles, as_of)

    assert record.regime == "GAP_DOWN"


def test_insufficient_data_is_honestly_reported_never_guessed():
    candles = _flat_days(date(2026, 8, 3), 1, 24000.0).iloc[:10]  # only 10 real bars, need MIN_BARS_FOR_REGIME
    as_of = candles.index[-1]

    record = detect_regime(candles, as_of)

    assert record.regime == INSUFFICIENT_DATA
    assert record.confidence is None
    assert record.stable is False
    assert str(MIN_BARS_FOR_REGIME) in record.reason


def test_an_empty_real_candle_frame_is_insufficient_data_not_a_crash():
    candles = _flat_days(date(2026, 8, 3), 1, 24000.0).iloc[:0]
    as_of = pd.Timestamp("2026-08-03 09:15:00", tz="Asia/Kolkata")

    record = detect_regime(candles, as_of)

    assert record.regime == INSUFFICIENT_DATA
    assert record.lookback_window_bars == 0
    assert record.data_start_timestamp is None
    assert record.data_end_timestamp is None


def test_regime_record_carries_the_real_input_metrics_and_window_metadata():
    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, 1.0)
    as_of = _as_of_from(candles)

    record = detect_regime(candles, as_of)

    assert set(record.input_metrics.keys()) >= {"ema_fast", "ema_slow", "close", "atr", "momentum", "gap_pct"}
    assert record.lookback_window_days == 10
    assert record.lookback_window_bars > 0
    assert record.data_end_timestamp == as_of.isoformat()
    assert record.timestamp == as_of.isoformat()


def test_a_regime_that_has_held_for_a_real_long_run_is_reported_stable():
    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, 1.0)
    as_of = _as_of_from(candles)

    record = detect_regime(candles, as_of)

    assert record.stable is True
    assert record.confidence == 100.0  # every real trailing bar agrees -- a consistent real trend


def test_a_regime_that_just_flipped_is_reported_unstable_not_fabricated_stable():
    """Requirement 4: rapid regime changes must not be silently reported
    as stable. A real up-trend immediately reversed into a real
    down-trend -- ema_fast/ema_slow/momentum lag the real reversal by
    several bars (a real, EMA-crossover-driven delay, not a test
    artifact), so right at the bar where classify() first flips to
    TREND_DOWN, the trailing stability window is still overwhelmingly
    real TREND_UP/UNCERTAIN bars -- honestly unstable."""
    up = _continuous_trending_days(date(2026, 8, 3), 3, 24000.0, 1.0)
    down_start = up.index[-1].date() + timedelta(days=1)
    while down_start.weekday() >= 5:
        down_start += timedelta(days=1)
    down = _continuous_trending_days(down_start, 2, float(up.iloc[-1].close), -1.0)
    candles = pd.concat([up, down])
    # The first real bar that flips to TREND_DOWN (found empirically
    # against this exact real fixture, not guessed) -- the trailing 20
    # real bars are still almost entirely TREND_UP/UNCERTAIN.
    as_of = down.index[11]
    assert detect_regime(candles, as_of).regime == "TREND_DOWN"  # confirms the real flip point used

    record = detect_regime(candles, as_of)

    assert record.stable is False
    assert record.confidence is not None and record.confidence < 60.0


def test_repeated_classification_of_identical_real_data_is_deterministic():
    candles = _continuous_trending_days(date(2026, 8, 3), 5, 24000.0, 1.0)
    as_of = _as_of_from(candles)

    first = detect_regime(candles, as_of).to_dict()
    second = detect_regime(candles, as_of).to_dict()
    del first["regime_id"]
    del second["regime_id"]

    assert first == second


# --- no-look-ahead (Requirement 12) ---


def test_no_look_ahead_truncation_produces_an_identical_regime_result():
    """The dedicated regression test Requirement 12 requires: run over
    the complete dataset, then truncate exactly at T, and confirm the
    result for T is identical."""
    candles = _continuous_trending_days(date(2026, 8, 3), 10, 24000.0, 1.0)
    mid_index = len(candles) // 2
    as_of = candles.index[mid_index]

    full_result = detect_regime(candles, as_of).to_dict()
    truncated = candles[candles.index <= as_of]
    truncated_result = detect_regime(truncated, as_of).to_dict()
    del full_result["regime_id"]
    del truncated_result["regime_id"]

    assert full_result == truncated_result


def test_an_extreme_future_bar_cannot_alter_an_earlier_classification():
    """Belt-and-suspenders proof: inject a wildly extreme bar AFTER T
    (a huge gap-shaped spike that would flip the classification if it
    ever leaked in) and confirm detect_regime(candles, T) is completely
    unaffected."""
    candles = _continuous_trending_days(date(2026, 8, 3), 10, 24000.0, 1.0)
    mid_index = len(candles) // 2
    as_of = candles.index[mid_index]
    baseline = detect_regime(candles, as_of).to_dict()

    evil_future_row = pd.DataFrame(
        [{"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1000}],
        index=pd.DatetimeIndex([candles.index[-1] + pd.Timedelta(minutes=1)], name="date"),
    )
    poisoned = pd.concat([candles, evil_future_row])
    poisoned_result = detect_regime(poisoned, as_of).to_dict()

    del baseline["regime_id"]
    del poisoned_result["regime_id"]
    assert baseline == poisoned_result
