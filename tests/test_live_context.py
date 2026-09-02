from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from config import IST, Settings
from data.calendar import NseCalendar
from data.global_market import ContextValue
from data.instruments import OptionInstrument
from data.news import NewsItem
from data.option_chain import OptionQuote
from execution.live_context import (
    NIFTY_INDEX_TOKEN,
    OPENING_RANGE_MINUTES,
    _combined_volume_score,
    _global_score,
    _news_score,
    _option_score,
    _option_volume_score,
    _select_option_universe,
    _volume_score,
    assemble_context,
    build_live_context,
    fetch_option_quotes,
)
from storage.database import Database


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
    """The honest, current-state behavior, not a bug: as of Brief 4, all 7
    of SignalEngine.evaluate()'s inputs are real computations (technical,
    opening, volume, option, global_score, news, risk_penalty) -- but
    option/global_score/news still read as 0.0/neutral on THIS fixture
    because their underlying real data genuinely isn't available here (no
    previous option-chain snapshot, no live global/news source wired --
    KNOWN_GAPS in execution/live_context.py), and volume (~50, "about
    average") doesn't add enough by itself. Confidence lands around 61,
    still short of the default signal_threshold (75) -- so even this
    clean, textbook upward breakout correctly produces no candidate.
    Market data itself is still fresh and real; the gate that blocks this
    is the same one that would block a genuinely bad setup, just fed
    partial real inputs on this particular fixture, not fabricated ones.
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


def _candles_with_volume(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).set_index("date")


def test_volume_score_reflects_real_above_average_opening_volume():
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    # Prior day's opening 5 minutes: 1000/min (average baseline). Today's
    # opening 5 minutes: 3000/min -- 3x the real prior baseline, not a
    # guessed "high" label.
    prior_rows = minute_bars(prior_day, 9, 15, 375, 24080.0, 0.0)
    todays_rows = minute_bars(today, 9, 15, OPENING_RANGE_MINUTES + 1, 24080.0, 1.0)
    for row in todays_rows:
        row["volume"] = 3000
    candles = _candles_with_volume(prior_rows + todays_rows)

    score = _volume_score(candles, today, OPENING_RANGE_MINUTES)

    assert score == 100.0  # capped: 3x average maps to max, not fabricated headroom


def test_volume_score_reflects_real_below_average_opening_volume():
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    prior_rows = minute_bars(prior_day, 9, 15, 375, 24080.0, 0.0)
    todays_rows = minute_bars(today, 9, 15, OPENING_RANGE_MINUTES + 1, 24080.0, 1.0)
    for row in todays_rows:
        row["volume"] = 200  # 1/5th of the real prior baseline
    candles = _candles_with_volume(prior_rows + todays_rows)

    score = _volume_score(candles, today, OPENING_RANGE_MINUTES)

    assert 0.0 < score < 50.0


def test_volume_score_is_zero_not_fabricated_when_no_prior_day_exists():
    today = date(2026, 9, 1)
    todays_rows = minute_bars(today, 9, 15, OPENING_RANGE_MINUTES + 1, 24080.0, 1.0)
    candles = _candles_with_volume(todays_rows)

    assert _volume_score(candles, today, OPENING_RANGE_MINUTES) == 0.0


def _option_quote(
    symbol: str, strike: float, option_type: str, open_interest: int, volume: int | None = None
) -> OptionQuote:
    instrument = OptionInstrument(symbol, strike, date(2026, 9, 1), option_type, 65)
    return OptionQuote(
        instrument,
        100.0,
        datetime(2026, 9, 1, 9, 30, tzinfo=IST),
        open_interest=open_interest,
        volume=volume,
    )


def test_option_score_rewards_real_oi_buildup_aligned_with_the_candidate_direction():
    previous = [
        _option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000),
        _option_quote("NIFTY2690124200PE", 24200.0, "PE", 10000),
    ]
    current = [
        _option_quote("NIFTY2690124200CE", 24200.0, "CE", 40000),  # +30000 call OI
        _option_quote("NIFTY2690124200PE", 24200.0, "PE", 10500),  # +500 put OI
    ]

    score, reason = _option_score(current, previous, regime_direction="CALL")

    assert score == 75.0  # call buildup aligned with a CALL candidate
    assert "Call Buildup" in reason


def test_option_score_penalizes_real_oi_buildup_contradicting_the_candidate_direction():
    previous = [
        _option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000),
        _option_quote("NIFTY2690124200PE", 24200.0, "PE", 10000),
    ]
    current = [
        _option_quote("NIFTY2690124200CE", 24200.0, "CE", 40000),  # call buildup...
        _option_quote("NIFTY2690124200PE", 24200.0, "PE", 10500),
    ]

    score, reason = _option_score(current, previous, regime_direction="PUT")  # ...but candidate is PUT

    assert score == 20.0
    assert "Call Buildup" in reason


def test_option_score_is_unavailable_not_fabricated_without_a_previous_snapshot():
    current = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 40000)]

    score, reason = _option_score(current, previous_option_quotes=[], regime_direction="CALL")

    assert score == 0.0
    assert reason == "No prior snapshot to compare OI change against."


def test_global_score_reflects_real_bullish_context_aligned_with_the_candidate_direction():
    context = {
        "global_context": [
            ContextValue("SGX_NIFTY", 40.0, datetime(2026, 9, 1, 8, 30, tzinfo=IST), "sgx", True),
            ContextValue("SP500", 20.0, datetime(2026, 9, 1, 8, 30, tzinfo=IST), "sp500", True),
        ]
    }

    score, direction = _global_score(context, regime_direction="CALL")

    assert direction == "BULLISH"
    assert score > 0  # aligned with the CALL candidate -- positive per SignalEngine's +100 baseline


def test_global_score_is_zero_not_fabricated_when_context_unavailable():
    score, direction = _global_score({"global_context": []}, regime_direction="CALL")

    assert score == 0.0
    assert direction == "UNKNOWN"


def test_news_score_reflects_real_positive_sentiment_aligned_with_the_candidate_direction():
    context = {
        "news_items": [
            NewsItem(datetime(2026, 9, 1, 8, 0, tzinfo=IST), "Strong GDP print", "reuters", 1.0, "POSITIVE", 35.0),
        ]
    }

    score, direction = _news_score(context, regime_direction="CALL")

    assert direction == "BULLISH"
    assert score > 0


def test_news_score_is_zero_not_fabricated_when_no_verified_items_available():
    score, direction = _news_score({"news_items": []}, regime_direction="CALL")

    assert score == 0.0
    assert direction == "UNKNOWN"


def test_assemble_context_option_score_becomes_real_once_a_previous_snapshot_is_passed_in():
    """End-to-end proof through the real public entry point (not just the
    private helper): the same clear-breakout scenario as the module-level
    tests above, but this time a real previous option-chain snapshot with
    genuine call-side OI buildup AND genuine higher option-contract volume
    is supplied -- confidence should move up over the no-snapshot case,
    though not necessarily enough to clear the default threshold by itself
    (global/news remain genuinely unavailable on this fixture)."""
    now = datetime(2026, 9, 1, 9, 30, tzinfo=IST)
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    prior_rows = full_prior_day(prior_day, 24080.4)
    opening_flat = minute_bars(today, 9, 15, 5, 24080.0, 0.0)
    breakout_up = minute_bars(today, 9, 20, 10, 24080.0, 5.0)
    candles = _candles_with_volume(prior_rows + opening_flat + breakout_up)

    current_quotes = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 40000, volume=20000)]
    previous_quotes = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 5000, volume=5000)]
    # Low enough threshold that a candidate forms either way -- this test
    # is about whether the real option score moves confidence, not about
    # threshold behavior (already covered above).
    settings = Settings(signal_threshold=40.0)

    without_snapshot = assemble_context(candles, current_quotes, 24080.4, now, True, settings)
    with_snapshot = assemble_context(
        candles, current_quotes, 24080.4, now, True, settings, previous_option_quotes=previous_quotes
    )

    assert without_snapshot["previous_option_quotes"] == []
    assert with_snapshot["previous_option_quotes"] == previous_quotes
    assert "candidate_direction" in without_snapshot and "candidate_direction" in with_snapshot
    # Real call-side OI buildup aligned with the CALL candidate should push
    # confidence above the no-snapshot (option=0.0/UNAVAILABLE) case.
    assert with_snapshot["candidate_confidence"] > without_snapshot["candidate_confidence"]
    without_evidence = " ".join(without_snapshot["candidate_evidence"])
    with_evidence = " ".join(with_snapshot["candidate_evidence"])
    assert "option=0.0" in without_evidence and "No prior snapshot" in without_evidence
    assert "option=75.0" in with_evidence and "Call Buildup" in with_evidence
    # volume itself also switches source: no previous snapshot means the
    # (structurally-zero-for-NIFTY) index candle fallback; a real previous
    # snapshot means real option-contract volume, here a real 4x increase.
    assert "index_candle_volume" in without_evidence
    assert "volume=100.0 (option_contract_volume)" in with_evidence


def test_option_volume_score_reflects_real_above_average_option_contract_volume():
    previous = [
        _option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000, volume=4000),
        _option_quote("NIFTY2690124000CE", 24000.0, "CE", 10000, volume=2000),
    ]
    current = [
        _option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000, volume=16000),
        _option_quote("NIFTY2690124000CE", 24000.0, "CE", 10000, volume=8000),
    ]  # real total 24000 vs prior real total 6000 -- 4x

    score = _option_volume_score(current, previous)

    assert score == 100.0  # capped, same shape as _volume_score


def test_option_volume_score_reflects_real_below_average_option_contract_volume():
    previous = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000, volume=10000)]
    current = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000, volume=1000)]

    score = _option_volume_score(current, previous)

    assert 0.0 < score < 50.0


def test_option_volume_score_is_zero_not_fabricated_without_a_previous_snapshot():
    current = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000, volume=16000)]

    assert _option_volume_score(current, previous_option_quotes=[]) == 0.0


def test_option_volume_score_is_zero_not_fabricated_when_volume_field_is_genuinely_null():
    """Per last week's honest disclosure, the real live option-quote
    `volume` field is not yet confirmed against a real Kite response --
    this proves a quote with volume=None (the OptionQuote default) is
    read as "unavailable," never silently treated as zero participation
    that would otherwise still average into a real-looking ratio."""
    previous = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000)]  # volume=None
    current = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000)]  # volume=None

    assert _option_volume_score(current, previous) == 0.0


def test_combined_volume_score_prefers_real_option_volume_when_a_previous_snapshot_exists():
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    prior_rows = minute_bars(prior_day, 9, 15, 375, 24080.0, 0.0)  # 1000/min baseline
    todays_rows = minute_bars(today, 9, 15, OPENING_RANGE_MINUTES + 1, 24080.0, 1.0)
    candles = _candles_with_volume(prior_rows + todays_rows)  # index score would read 50.0 here
    previous = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000, volume=5000)]
    current = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000, volume=20000)]  # real 4x

    score, reason = _combined_volume_score(candles, today, OPENING_RANGE_MINUTES, current, previous)

    assert score == 100.0  # the real option-volume read, not the index fallback's 50.0
    assert reason == "option_contract_volume"


def test_combined_volume_score_falls_back_to_index_candle_volume_without_a_previous_snapshot():
    prior_day = date(2026, 8, 31)
    today = date(2026, 9, 1)
    prior_rows = minute_bars(prior_day, 9, 15, 375, 24080.0, 0.0)
    todays_rows = minute_bars(today, 9, 15, OPENING_RANGE_MINUTES + 1, 24080.0, 1.0)
    candles = _candles_with_volume(prior_rows + todays_rows)
    current = [_option_quote("NIFTY2690124200CE", 24200.0, "CE", 10000, volume=20000)]

    score, reason = _combined_volume_score(candles, today, OPENING_RANGE_MINUTES, current, [])

    assert score == _volume_score(candles, today, OPENING_RANGE_MINUTES)
    assert "index_candle_volume" in reason


def test_two_real_cycles_through_the_database_reproduces_main_pys_context_provider_wiring(tmp_path):
    """Exercises the exact sequence main.py's run_scheduled_day::
    context_provider now runs (Brief 5 Part B): read the latest persisted
    snapshot, build this cycle's context with it as previous_option_quotes,
    then persist this cycle's own chain for the NEXT cycle. Doesn't call
    run_scheduled_day itself (that blocks on the real wall clock waiting
    for real market hours) -- this reproduces its actual database
    read/build/write sequence directly against two real captured
    option-chain shapes from _clear_breakout_fixture, one session apart."""
    db = Database(tmp_path / "two_cycle.db")
    db.initialize()
    settings = Settings(signal_threshold=40.0)
    calendar = NseCalendar()

    # Cycle 1: nothing persisted yet -- correctly unavailable.
    now1 = datetime(2026, 8, 31, 9, 30, tzinfo=IST)
    kite1 = _clear_breakout_fixture(now1)
    previous = db.latest_option_chain_snapshot()
    assert previous == []
    context1 = build_live_context(settings, kite1, calendar, now1, previous_option_quotes=previous)
    assert "option=0.0" in " ".join(context1.get("candidate_evidence", []))
    db.save_option_chain_snapshot(now1, context1["option_quotes"])

    # Cycle 2 (next real session): the database now hands back cycle
    # 1's real chain as this cycle's previous_option_quotes.
    now2 = datetime(2026, 9, 1, 9, 30, tzinfo=IST)
    kite2 = _clear_breakout_fixture(now2)
    previous = db.latest_option_chain_snapshot()
    assert previous == context1["option_quotes"]
    context2 = build_live_context(settings, kite2, calendar, now2, previous_option_quotes=previous)

    assert context2["previous_option_quotes"] == context1["option_quotes"]
    # Same OI/volume both cycles here (identical fixture) -- real
    # buildup detection correctly reads BALANCED (no net change), not
    # UNAVAILABLE anymore, proving cycle 2 really did receive cycle 1's
    # real data rather than an empty default.
    assert "option=" in " ".join(context2.get("candidate_evidence", []))
    assert "No prior snapshot" not in " ".join(context2.get("candidate_evidence", []))


def _extended_breakout_historical_rows(hold_minutes: int) -> list[dict]:
    """Same real breakout shape as _clear_breakout_fixture, but continues
    flat (holding the breakout level) for `hold_minutes` more bars past
    9:30 -- lets a test observe the exact same underlying technical/regime
    structure at a much later real scan time."""
    prior_rows = full_prior_day(date(2026, 8, 31), 24080.4)
    opening_flat = minute_bars(date(2026, 9, 1), 9, 15, 5, 24080.0, 0.0)
    breakout_up = minute_bars(date(2026, 9, 1), 9, 20, 10, 24080.0, 5.0)
    holding = minute_bars(date(2026, 9, 1), 9, 30, hold_minutes, 24130.0, 0.0)
    return prior_rows + opening_flat + breakout_up + holding


def test_opening_range_breakout_correctly_excluded_hours_after_the_real_open():
    """Brief 6 Part A: exactly the scenario a periodic re-scan (Part B)
    would otherwise hit every single interval for the rest of the day.
    OPENING_RANGE_BREAKOUT is an open-window setup (OPEN_WINDOW_SETUPS);
    it must not fire outside OPEN_WINDOW_MINUTES of the real session open,
    even with a threshold low enough that only the time gate could be
    blocking it."""
    scan_time = datetime(2026, 9, 1, 13, 0, tzinfo=IST)  # ~3h45m after the 9:15 open
    historical_rows = _extended_breakout_historical_rows(hold_minutes=210)  # 9:30 -> 13:00
    instruments = [real_instrument_row("NIFTY2690124200CE", 24200.0, date(2026, 9, 1), "CE")]
    kite = FakeKite(real_index_quote(now=scan_time), historical_rows, instruments)
    settings = Settings(signal_threshold=1.0)  # trivially low -- only the time gate can block this

    context = build_live_context(settings, kite, NseCalendar(), now=scan_time)

    assert "candidate_direction" not in context


def test_opening_range_breakout_still_fires_within_the_real_open_window():
    """Same real breakout structure and same low threshold as the test
    above -- only the scan time differs (still within OPEN_WINDOW_MINUTES
    of the real 9:15 open) -- proving the exclusion above is really about
    time, not some other difference between the fixtures."""
    scan_time = datetime(2026, 9, 1, 9, 30, tzinfo=IST)  # 15 minutes after open
    historical_rows = _extended_breakout_historical_rows(hold_minutes=0)
    instruments = [real_instrument_row("NIFTY2690124200CE", 24200.0, date(2026, 9, 1), "CE")]
    kite = FakeKite(real_index_quote(now=scan_time), historical_rows, instruments)
    settings = Settings(signal_threshold=1.0)

    context = build_live_context(settings, kite, NseCalendar(), now=scan_time)

    assert context["candidate_direction"] == "CALL"


def test_setup_eligible_now_gates_open_window_setups_by_real_elapsed_time():
    from execution.live_context import OPEN_WINDOW_MINUTES, _setup_eligible_now

    session_open = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    just_inside = session_open + timedelta(minutes=OPEN_WINDOW_MINUTES)
    just_outside = session_open + timedelta(minutes=OPEN_WINDOW_MINUTES, seconds=1)

    assert _setup_eligible_now("OPENING_RANGE_BREAKOUT", just_inside, session_open) is True
    assert _setup_eligible_now("OPENING_RANGE_BREAKOUT", just_outside, session_open) is False


def test_setup_eligible_now_never_gates_all_day_or_unrecognized_setups():
    from execution.live_context import _setup_eligible_now

    session_open = datetime(2026, 9, 1, 9, 15, tzinfo=IST)
    hours_later = session_open + timedelta(hours=5)

    assert _setup_eligible_now("VWAP_BREAKOUT", hours_later, session_open) is True
    # An unrecognized setup_type defaults to eligible -- the safer failure
    # mode is not silently blocking a name this gate doesn't know about.
    assert _setup_eligible_now("SOME_FUTURE_SETUP_TYPE", hours_later, session_open) is True
