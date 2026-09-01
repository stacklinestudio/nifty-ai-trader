"""Brief 5 Part B: real option-chain persistence across cycles.

storage/database.py's `snapshots` table existed in the schema (id,
timestamp, source, payload) but had no reader/writer anywhere -- this
tests the new save/latest pair that reuses it, and the JSON round-trip
that makes it possible, using real field shapes (a currently-listed
NIFTY weekly contract symbol/strike/expiry), not synthetic placeholders.
"""

from __future__ import annotations

from datetime import date, datetime

from config import IST
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote, quotes_from_json, quotes_to_json
from storage.database import Database


def _quote(symbol: str, strike: float, oi: int, volume: int | None = None) -> OptionQuote:
    instrument = OptionInstrument(symbol, strike, date(2026, 9, 1), "CE", 65, instrument_token=12345)
    return OptionQuote(
        instrument,
        ltp=120.5,
        timestamp=datetime(2026, 9, 1, 9, 30, tzinfo=IST),
        bid=120.0,
        ask=121.0,
        volume=volume,
        open_interest=oi,
        implied_volatility=None,
    )


def test_quotes_json_round_trip_preserves_every_field():
    original = [_quote("NIFTY2690124200CE", 24200.0, 45000, volume=5000)]

    restored = quotes_from_json(quotes_to_json(original))

    assert restored == original


def test_quotes_json_round_trip_preserves_a_genuinely_null_volume():
    """Per the codebase's own honest disclosure, real option volume/oi
    field behavior is not yet fully confirmed live -- a None must survive
    the round trip as None, never become a fabricated 0."""
    original = [_quote("NIFTY2690124200CE", 24200.0, 45000, volume=None)]

    restored = quotes_from_json(quotes_to_json(original))

    assert restored[0].volume is None


def test_database_persists_and_retrieves_the_latest_option_chain_snapshot(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    quotes = [_quote("NIFTY2690124200CE", 24200.0, 45000, volume=5000)]

    db.save_option_chain_snapshot(datetime(2026, 9, 1, 9, 30, tzinfo=IST), quotes)

    assert db.latest_option_chain_snapshot() == quotes


def test_database_returns_empty_list_not_fabricated_when_nothing_persisted_yet(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()

    assert db.latest_option_chain_snapshot() == []


def test_database_latest_snapshot_is_the_most_recently_saved_one(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    day1_quotes = [_quote("NIFTY2680324200CE", 24200.0, 10000)]
    day2_quotes = [_quote("NIFTY2690124200CE", 24200.0, 40000)]

    db.save_option_chain_snapshot(datetime(2026, 8, 3, 9, 30, tzinfo=IST), day1_quotes)
    db.save_option_chain_snapshot(datetime(2026, 9, 1, 9, 30, tzinfo=IST), day2_quotes)

    assert db.latest_option_chain_snapshot() == day2_quotes


def test_saving_an_empty_snapshot_is_a_no_op_and_never_poisons_the_next_read(tmp_path):
    """A day where no option chain was fetched (e.g. Kite unavailable)
    must not overwrite a real prior snapshot with an empty one -- that
    would silently turn tomorrow's real "compare against yesterday" into
    a fabricated "compared against nothing and found no buildup," instead
    of correctly still comparing against the last real chain."""
    db = Database(tmp_path / "test.db")
    db.initialize()
    real_quotes = [_quote("NIFTY2680324200CE", 24200.0, 10000)]
    db.save_option_chain_snapshot(datetime(2026, 8, 3, 9, 30, tzinfo=IST), real_quotes)

    db.save_option_chain_snapshot(datetime(2026, 8, 4, 9, 30, tzinfo=IST), [])

    assert db.latest_option_chain_snapshot() == real_quotes
