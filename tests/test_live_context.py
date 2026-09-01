from __future__ import annotations

from datetime import date, datetime, timedelta

from config import IST, Settings
from data.calendar import NseCalendar
from data.instruments import OptionInstrument
from execution.live_context import (
    NIFTY_INDEX_TOKEN,
    _select_option_universe,
    build_live_context,
    fetch_option_quotes,
)


class FakeKite:
    """Payload shapes mirror what was literally captured live on
    2026-08-31/09-01 (field names, naive-vs-aware timestamp behavior, real
    instrument fields) -- individual OHLC candle values are representative,
    not the literal captured series (only aggregate row count/date range
    were captured, not every bar)."""

    def __init__(
        self,
        quote_response: dict,
        historical_rows: list[dict],
        instrument_rows: list[dict],
        option_quote_response: dict | None = None,
    ) -> None:
        self.quote_response = quote_response
        self.historical_rows = historical_rows
        self.instrument_rows = instrument_rows
        self.option_quote_response = option_quote_response or {}

    def quote(self, symbols: list[str]) -> dict:
        if symbols and symbols[0].startswith("NFO:"):
            return self.option_quote_response
        return self.quote_response

    def historical_data(self, instrument_token, start, end, interval):
        assert instrument_token == NIFTY_INDEX_TOKEN
        return self.historical_rows

    def instruments(self, segment: str) -> list[dict]:
        return self.instrument_rows


def real_index_quote(ltp: float = 24080.4, minutes_ago: int = 0, now: datetime | None = None) -> dict:
    """Shaped exactly after the real raw response captured live for
    NSE:NIFTY 50 on 2026-08-31: last_price, volume, timestamp (naive,
    implicitly IST), depth.buy/sell."""
    now = now or datetime.now(IST)
    ts = (now - timedelta(minutes=minutes_ago)).replace(tzinfo=None)
    return {
        "NSE:NIFTY 50": {
            "instrument_token": NIFTY_INDEX_TOKEN,
            "last_price": ltp,
            "volume": 0,
            "timestamp": ts,
            "depth": {"buy": [{"price": ltp - 0.5}], "sell": [{"price": ltp + 0.5}]},
        }
    }


def real_instrument_row(symbol: str, strike: float, expiry: date, option_type: str) -> dict:
    """Shaped after a real parsed row: NIFTY2690124200CE, strike 24200.0,
    expiry 2026-09-01, CE, lot_size 65 -- the real lot size captured live,
    superseding the 75 used as a reference figure in earlier sizing work."""
    return {
        "name": "NIFTY",
        "segment": "NFO-OPT",
        "tradingsymbol": symbol,
        "strike": strike,
        "expiry": expiry.isoformat(),
        "instrument_type": option_type,
        "lot_size": 65,
        "instrument_token": hash(symbol) % 1_000_000,
    }


def minute_bars(day: date, start_hour: int, start_minute: int, count: int, base_price: float, trend: float):
    rows = []
    price = base_price
    for i in range(count):
        ts = datetime(day.year, day.month, day.day, start_hour, start_minute, tzinfo=IST) + timedelta(
            minutes=i
        )
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


def full_prior_day(day: date, close_price: float) -> list[dict]:
    return minute_bars(day, 9, 15, 375, close_price - 20, 20 / 375)


def _clear_breakout_fixture(now: datetime):
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    # Prior day: full session ending near 24080 (matches the real captured
    # NIFTY 50 last_price). Today: opening range flat, then a clear upward
    # breakout well past it -- deterministic CALL scenario for both the
    # regime classifier and the ORB read.
    prior_rows = full_prior_day(prior_day, 24080.4)
    opening_flat = minute_bars(today, 9, 15, 5, 24080.0, 0.0)
    breakout_up = minute_bars(today, 9, 20, 10, 24080.0, 5.0)
    historical_rows = prior_rows + opening_flat + breakout_up
    instruments = [
        real_instrument_row("NIFTY2690124200CE", 24200.0, today, "CE"),
        real_instrument_row("NIFTY2690124000CE", 24000.0, today, "CE"),
        real_instrument_row("NIFTY2690124200PE", 24200.0, today, "PE"),
    ]
    option_quotes = {
        "NFO:NIFTY2690124200CE": {
            "last_price": 120.5,
            "volume": 5000,
            "timestamp": now.replace(tzinfo=None),
            "depth": {"buy": [{"price": 120.0}], "sell": [{"price": 121.0}]},
            "oi": 45000,
        },
        "NFO:NIFTY2690124000CE": {
            "last_price": 200.0,
            "volume": 4000,
            "timestamp": now.replace(tzinfo=None),
            "depth": {"buy": [{"price": 199.5}], "sell": [{"price": 200.5}]},
            "oi": 30000,
        },
        "NFO:NIFTY2690124200PE": {
            "last_price": 80.0,
            "volume": 3000,
            "timestamp": now.replace(tzinfo=None),
            "depth": {"buy": [{"price": 79.5}], "sell": [{"price": 80.5}]},
            "oi": 20000,
        },
    }
    return FakeKite(real_index_quote(now=now), historical_rows, instruments, option_quotes)


def test_build_live_context_at_default_threshold_correctly_produces_no_candidate_even_on_a_clear_breakout():
    """The honest, current-state behavior, not a bug: SignalEngine.evaluate()
    is fed only 2 of its 7 inputs from real data (technical, opening) --
    volume/option/global_score/news are all 0, a documented gap (KNOWN_GAPS
    in execution/live_context.py). That structurally caps achievable
    confidence around 54-59, which can never clear the default
    signal_threshold (75) -- so even this clean, textbook upward breakout
    correctly produces no candidate. Market data itself is still fresh and
    real; the gate that blocks this is the same one that would block a
    genuinely bad setup, just currently mis-calibrated for how few real
    sub-signals are wired in yet.
    """
    now = datetime(2026, 9, 1, 9, 30, tzinfo=IST)
    kite = _clear_breakout_fixture(now)
    settings = Settings()  # real default signal_threshold (75)

    context = build_live_context(settings, kite, NseCalendar(), now=now)

    assert context["market_data_fresh"] is True
    assert context["spot"] == 24080.4
    assert context["features"]["close"] > 0
    assert "candidate_direction" not in context
    # Option chain assembly still runs independent of whether a candidate
    # formed -- proves that piece works even when signal generation gates
    # everything else closed.
    assert len(context["option_quotes"]) == 3
    assert all(q.instrument.lot_size == 65 for q in context["option_quotes"])


def test_build_live_context_produces_the_right_candidate_once_threshold_is_reachable():
    """Proves the wiring (regime -> direction, ORB -> opening score, real
    features -> technical score, all the way to a CALL/PUT candidate) is
    correct, using the exact same real inputs as the test above -- only
    signal_threshold is lowered, and only because Settings.signal_threshold
    is already a real, existing, per-environment config knob (not something
    invented for this test). This does not inflate any sub-score; it proves
    the pipeline works once given a bar its current real inputs can clear.
    """
    now = datetime(2026, 9, 1, 9, 30, tzinfo=IST)
    kite = _clear_breakout_fixture(now)
    settings = Settings(signal_threshold=50.0)

    context = build_live_context(settings, kite, NseCalendar(), now=now)

    assert context["candidate_direction"] == "CALL"
    assert context["candidate_confidence"] > 0
    assert context["setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert len(context["option_quotes"]) == 3


def test_build_live_context_fails_closed_when_quote_is_stale():
    now = datetime(2026, 9, 1, 9, 30, tzinfo=IST)
    kite = FakeKite(real_index_quote(minutes_ago=120, now=now), [], [])
    settings = Settings()
    context = build_live_context(settings, kite, NseCalendar(), now=now)

    assert context["market_data_fresh"] is False
    assert "candidate_direction" not in context
    assert context.get("option_quotes", []) == []


def test_build_live_context_fails_closed_when_quote_call_raises():
    class BrokenKite(FakeKite):
        def quote(self, symbols):
            raise ConnectionError("simulated feed outage")

    kite = BrokenKite({}, [], [])
    context = build_live_context(Settings(), kite, NseCalendar())

    assert context["market_data_fresh"] is False
    assert "candidate_direction" not in context


def test_build_live_context_no_candidate_when_todays_session_too_short():
    now = datetime(2026, 9, 1, 9, 18, tzinfo=IST)  # only 3 minutes into the session
    prior_rows = full_prior_day(date(2026, 8, 31), 24080.4)
    only_three_bars = minute_bars(date(2026, 9, 1), 9, 15, 3, 24080.0, 5.0)
    kite = FakeKite(
        real_index_quote(now=now), prior_rows + only_three_bars, [
            real_instrument_row("NIFTY2690124200CE", 24200.0, date(2026, 9, 1), "CE")
        ]
    )

    context = build_live_context(Settings(), kite, NseCalendar(), now=now)

    assert context["market_data_fresh"] is True  # spot data itself is fine...
    assert "candidate_direction" not in context  # ...but not enough of today's session for an ORB read yet


def test_select_option_universe_filters_to_nearest_expiry_and_strike_window():
    # _select_option_universe operates on already-parsed OptionInstrument
    # objects (as produced by download_kite_nifty_options), not raw Kite
    # instrument-row dicts -- those are two different fixture shapes.
    near = date(2026, 9, 1)
    far = date(2026, 9, 8)
    instruments = [
        OptionInstrument("NIFTY2690124200CE", 24200.0, near, "CE", 65),  # in window
        OptionInstrument("NIFTY2690123000CE", 23000.0, near, "CE", 65),  # outside strike window
        OptionInstrument("NIFTY2690824200CE", 24200.0, far, "CE", 65),  # further expiry
    ]
    universe = _select_option_universe(instruments, spot=24080.4, today=near)

    assert len(universe) == 1
    assert universe[0].symbol == "NIFTY2690124200CE"


def test_fetch_option_quotes_parses_real_shaped_response_including_oi():
    instrument = OptionInstrument("NIFTY2690124200CE", 24200.0, date(2026, 9, 1), "CE", 65)
    now = datetime(2026, 9, 1, 9, 30, tzinfo=IST)
    kite = FakeKite(
        {}, [], [], {
            "NFO:NIFTY2690124200CE": {
                "last_price": 120.5,
                "volume": 5000,
                "timestamp": now.replace(tzinfo=None),
                "depth": {"buy": [{"price": 120.0}], "sell": [{"price": 121.0}]},
                "oi": 45000,
            }
        },
    )

    quotes = fetch_option_quotes(kite, [instrument])

    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.ltp == 120.5
    assert quote.open_interest == 45000
    assert quote.timestamp.tzinfo is not None
    assert quote.bid == 120.0 and quote.ask == 121.0
