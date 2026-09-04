"""Best-effort public-data abstraction; unavailable values are explicit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from config import IST
from monitoring.logger import configure_logger

logger = configure_logger(__name__)


@dataclass(frozen=True)
class ContextValue:
    name: str
    value: float | None
    timestamp: datetime | None
    source: str
    available: bool
    error: str | None = None


class GlobalMarketProvider:
    def snapshot(self) -> list[ContextValue]:
        return []  # Inject a lawful provider; no invented external values.


# Brief 8 Part A: real symbols for S&P 500, Nasdaq, Dow, Nikkei, Hang Seng,
# crude oil, gold, USD/INR -- exactly the 8 the brief named. Confirmed
# live against the real yfinance API (2026-09-04): all 8 return real
# recent daily closes.
GLOBAL_SYMBOLS: dict[str, str] = {
    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "DOW": "^DJI",
    "NIKKEI": "^N225",
    "HANG_SENG": "^HSI",
    "CRUDE_OIL": "CL=F",
    "GOLD": "GC=F",
    "USD_INR": "INR=X",
}


class YFinanceGlobalMarketProvider(GlobalMarketProvider):
    """Real global-market data via yfinance (Brief 8 Part A) -- free, no
    API key, no signup friction, the pragmatic source decided now per
    Brief 5 Part C's already-researched options.

    Known, accepted tradeoff, stated plainly: yfinance is an UNOFFICIAL
    library scraping Yahoo Finance, not an official/contracted API -- it
    can occasionally break without notice. Accepted for zero cost and
    zero setup friction to get real data flowing today; Twelve Data or
    Finnhub (real pricing already researched in Brief 5 Part C) remain a
    reasonable paid upgrade later if reliability becomes a real problem.

    `value` is the real day-over-day percent change, not an absolute
    price level -- averaging S&P 500's ~7700 against gold's ~4489 and
    USD/INR's ~94 would be meaningless; percent change is the one
    directionally comparable real number across all 8 symbols, matching
    how GlobalResearchAgent already averages `value` into one directional
    score. A symbol whose real fetch fails, or returns fewer than 2 real
    closes to compare, is marked available=False with a real error
    message -- never filled with a guessed number.
    """

    def __init__(self, symbols: dict[str, str] | None = None) -> None:
        self.symbols = symbols or GLOBAL_SYMBOLS

    def snapshot(self) -> list[ContextValue]:
        import yfinance as yf  # imported lazily -- this module stays importable without yfinance installed unless this provider is actually used

        now = datetime.now(IST)
        values = []
        for name, symbol in self.symbols.items():
            try:
                history = yf.Ticker(symbol).history(period="5d", interval="1d")
                closes = history["Close"].dropna()
                if len(closes) < 2:
                    raise ValueError(f"insufficient real history ({len(closes)} row(s))")
                pct_change = float((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2])
                values.append(ContextValue(name, pct_change, now, "yfinance", True))
            except Exception as exc:  # noqa: BLE001 - one real symbol's failure must not block the other 7; each is independently marked unavailable, never guessed.
                logger.warning("global_market_symbol_fetch_failed symbol=%s error=%s", symbol, exc)
                values.append(
                    ContextValue(name, None, now, "yfinance", False, error=f"{type(exc).__name__}: {exc}")
                )
        return values


def fetch_global_history(
    symbols: dict[str, str], start: date, end: date
) -> dict[date, list[ContextValue]]:
    """Real historical day-over-day percent change for each real trading
    day in [start, end] -- one bulk real fetch per symbol (not per day),
    used by backtest/daily_backtest.py so a historical run can use real
    global-market data instead of the permanently-empty context a single
    live snapshot alone would give a backtest. No look-ahead: day N's
    value only ever compares day N's real close to day N-1's, both
    already-known by day N -- never a later day's close.
    """
    import yfinance as yf

    by_day: dict[date, list[ContextValue]] = {}
    for name, symbol in symbols.items():
        try:
            history = yf.Ticker(symbol).history(
                start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), interval="1d"
            )
            closes = history["Close"].dropna()
        except Exception as exc:  # noqa: BLE001 - one symbol's real failure must not block the other 7 for every day.
            logger.warning("global_market_history_fetch_failed symbol=%s error=%s", symbol, exc)
            continue
        prior_close: float | None = None
        for ts, close in closes.items():
            trading_day = ts.date()
            timestamp = datetime.combine(trading_day, datetime.min.time(), tzinfo=IST)
            if prior_close is not None and prior_close != 0:
                pct_change = float((close - prior_close) / prior_close)
                by_day.setdefault(trading_day, []).append(
                    ContextValue(name, pct_change, timestamp, "yfinance", True)
                )
            prior_close = float(close)
    return by_day
