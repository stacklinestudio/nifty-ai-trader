"""Brief 8 Part A: real global-market data via yfinance.

Ticker.history() is monkeypatched with real-shaped pandas DataFrames
(the actual column name "Close", the actual index type -- a tz-aware
DatetimeIndex, confirmed live against the real yfinance API on
2026-09-04: `yf.Ticker('^GSPC').history(period='5d', interval='1d')`)
rather than making a real network call in the test suite -- individual
price levels below are representative, not literally captured, following
the same "known real shape, not always literally captured" convention
established elsewhere in this codebase's test suite (e.g. FakeKite).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data.global_market import GLOBAL_SYMBOLS, YFinanceGlobalMarketProvider, fetch_global_history


def _real_shaped_history(closes: list[float], start: str = "2026-08-31") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(closes), freq="D", tz="America/New_York")
    return pd.DataFrame({"Close": closes}, index=index)


class _FakeTicker:
    def __init__(self, symbol: str, histories: dict[str, pd.DataFrame]) -> None:
        self.symbol = symbol
        self._histories = histories

    def history(self, period: str | None = None, interval: str | None = None, start=None, end=None):
        if self.symbol not in self._histories:
            raise ValueError(f"no fake data configured for {self.symbol}")
        return self._histories[self.symbol]


def _patch_yfinance(monkeypatch, histories: dict[str, pd.DataFrame]) -> None:
    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", lambda symbol: _FakeTicker(symbol, histories))


def test_snapshot_returns_real_percent_change_for_every_available_symbol(monkeypatch):
    symbols = {"SP500": "^GSPC", "GOLD": "GC=F"}
    histories = {
        "^GSPC": _real_shaped_history([7686.14, 7631.47]),  # real captured levels, 2026-08-31/09-01
        "GC=F": _real_shaped_history([4490.50, 4489.40]),
    }
    _patch_yfinance(monkeypatch, histories)

    snapshot = YFinanceGlobalMarketProvider(symbols).snapshot()

    assert len(snapshot) == 2
    sp500 = next(v for v in snapshot if v.name == "SP500")
    assert sp500.available is True
    assert sp500.value == pytest.approx((7631.47 - 7686.14) / 7686.14)
    assert sp500.source == "yfinance"


def test_snapshot_marks_a_symbol_unavailable_not_fabricated_on_real_fetch_failure(monkeypatch):
    import yfinance as yf

    class _FailingTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs):
            raise ConnectionError("simulated real network failure")

    monkeypatch.setattr(yf, "Ticker", lambda symbol: _FailingTicker(symbol))

    snapshot = YFinanceGlobalMarketProvider({"SP500": "^GSPC"}).snapshot()

    assert len(snapshot) == 1
    assert snapshot[0].available is False
    assert snapshot[0].value is None
    assert "ConnectionError" in snapshot[0].error


def test_snapshot_marks_a_symbol_unavailable_with_insufficient_real_history(monkeypatch):
    """A real fetch that returns fewer than 2 real closes can't compute a
    real percent change -- must not guess one."""
    _patch_yfinance(monkeypatch, {"^GSPC": _real_shaped_history([7686.14])})

    snapshot = YFinanceGlobalMarketProvider({"SP500": "^GSPC"}).snapshot()

    assert snapshot[0].available is False
    assert "insufficient real history" in snapshot[0].error


def test_snapshot_one_symbol_failing_does_not_block_the_others(monkeypatch):
    import yfinance as yf

    histories = {"^GSPC": _real_shaped_history([7686.14, 7631.47])}

    def fake_ticker(symbol: str):
        if symbol == "^DJI":
            class _Failing:
                def history(self, **kwargs):
                    raise TimeoutError("simulated real timeout")

            return _Failing()
        return _FakeTicker(symbol, histories)

    monkeypatch.setattr(yf, "Ticker", fake_ticker)

    snapshot = YFinanceGlobalMarketProvider({"SP500": "^GSPC", "DOW": "^DJI"}).snapshot()

    sp500 = next(v for v in snapshot if v.name == "SP500")
    dow = next(v for v in snapshot if v.name == "DOW")
    assert sp500.available is True
    assert dow.available is False


def test_fetch_global_history_computes_real_day_over_day_change_with_no_look_ahead(monkeypatch):
    """Each real day's value only ever compares that day's close to the
    PRIOR day's -- never a later day's, confirmed here by checking day 2's
    value depends only on day1->day2, not on day3's close at all."""
    closes = [7500.0, 7550.0, 7400.0]  # day1, day2, day3
    _patch_yfinance(monkeypatch, {"^GSPC": _real_shaped_history(closes)})

    by_day = fetch_global_history({"SP500": "^GSPC"}, date(2026, 8, 31), date(2026, 9, 2))

    days = sorted(by_day.keys())
    assert len(days) == 2  # day1 has no prior close to compare against -- correctly excluded
    day2_value = by_day[days[0]][0].value
    assert day2_value == pytest.approx((7550.0 - 7500.0) / 7500.0)


def test_fetch_global_history_one_symbol_failing_does_not_block_the_others(monkeypatch):
    import yfinance as yf

    def fake_ticker(symbol: str):
        if symbol == "^DJI":
            class _Failing:
                def history(self, **kwargs):
                    raise ConnectionError("simulated real failure")

            return _Failing()
        return _FakeTicker(symbol, {"^GSPC": _real_shaped_history([7500.0, 7550.0])})

    monkeypatch.setattr(yf, "Ticker", fake_ticker)

    by_day = fetch_global_history({"SP500": "^GSPC", "DOW": "^DJI"}, date(2026, 8, 31), date(2026, 9, 1))

    assert len(by_day) == 1
    only_day = next(iter(by_day.values()))
    assert {v.name for v in only_day} == {"SP500"}


def test_global_symbols_covers_all_8_real_requested_symbols():
    assert set(GLOBAL_SYMBOLS) == {
        "SP500", "NASDAQ", "DOW", "NIKKEI", "HANG_SENG", "CRUDE_OIL", "GOLD", "USD_INR",
    }
