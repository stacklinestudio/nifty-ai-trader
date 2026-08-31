from __future__ import annotations

from datetime import datetime

import pytest

from config import IST
from data.market_data import KiteMarketData, Quote, validate_quote


class FakeKite:
    def __init__(self, quote_payload: dict) -> None:
        self.quote_payload = quote_payload

    def quote(self, symbols: list[str]) -> dict:
        return {symbols[0]: self.quote_payload}


def real_captured_payload(timestamp) -> dict:
    """Shaped after the actual raw response captured live against Kite on
    2026-08-31 (NSE:NIFTY 50), not a synthetic guess at the schema."""
    return {
        "instrument_token": 256265,
        "last_price": 24080.4,
        "volume": 0,
        "timestamp": timestamp,
        "depth": {"buy": [{"price": 24079.0}], "sell": [{"price": 24081.0}]},
    }


def test_real_captured_naive_timestamp_no_longer_raises():
    """The exact real-world value that broke this on 2026-08-31: Kite
    returned a naive datetime (no tzinfo) for a live NSE:NIFTY 50 quote,
    and get_quote() rejected it outright. Pinned literally so this can't
    quietly regress.
    """
    real_naive_timestamp = datetime(2026, 8, 31, 17, 35, 5)  # noqa: DTZ001 - naive on purpose
    assert real_naive_timestamp.tzinfo is None  # sanity: this really is naive

    kite = FakeKite(real_captured_payload(real_naive_timestamp))
    quote = KiteMarketData(kite).get_quote("NSE:NIFTY 50")

    assert quote.timestamp.tzinfo is not None
    assert quote.timestamp == real_naive_timestamp.replace(tzinfo=IST)
    assert quote.ltp == 24080.4


def test_naive_timestamp_is_interpreted_as_ist_not_utc_or_local():
    kite = FakeKite(real_captured_payload(datetime(2026, 8, 31, 17, 35, 5)))  # noqa: DTZ001
    quote = KiteMarketData(kite).get_quote("NSE:NIFTY 50")
    assert quote.timestamp.utcoffset().total_seconds() == 5.5 * 3600


def test_already_tz_aware_timestamp_is_accepted_unchanged():
    aware = datetime(2026, 8, 31, 17, 35, 5, tzinfo=IST)
    kite = FakeKite(real_captured_payload(aware))
    quote = KiteMarketData(kite).get_quote("NSE:NIFTY 50")
    assert quote.timestamp == aware


def test_missing_timestamp_still_raises():
    payload = real_captured_payload(None)
    payload.pop("timestamp")
    kite = FakeKite(payload)
    with pytest.raises(ValueError, match="valid timestamp"):
        KiteMarketData(kite).get_quote("NSE:NIFTY 50")


def test_genuinely_malformed_timestamp_still_raises_not_silently_accepted():
    """A naive datetime is fine (Kite's real, expected shape) -- but a
    timestamp field that isn't a datetime at all must still be rejected,
    not papered over."""
    payload = real_captured_payload("not-a-real-timestamp")
    kite = FakeKite(payload)
    with pytest.raises(ValueError, match="valid timestamp"):
        KiteMarketData(kite).get_quote("NSE:NIFTY 50")


def test_validate_quote_accepts_the_now_tz_aware_real_quote():
    real_naive_timestamp = datetime(2026, 8, 31, 17, 35, 5)  # noqa: DTZ001 - naive on purpose
    kite = FakeKite(real_captured_payload(real_naive_timestamp))
    quote = KiteMarketData(kite).get_quote("NSE:NIFTY 50")
    # validate_quote itself still independently requires tz-aware inputs --
    # get_quote's fix is what makes that requirement satisfiable from a
    # real Kite response in the first place.
    validate_quote(quote, datetime.now(IST), max_age_seconds=999_999_999)


def test_validate_quote_still_rejects_a_naive_quote_object_directly():
    naive_quote = Quote(
        "NSE:NIFTY 50", 24080.4, datetime(2026, 8, 31, 17, 35, 5), "kite"  # noqa: DTZ001
    )
    with pytest.raises(ValueError, match="timezone aware"):
        validate_quote(naive_quote, datetime.now(IST), max_age_seconds=999_999_999)
