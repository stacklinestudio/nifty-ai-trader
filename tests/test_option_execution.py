"""Phase 2 Piece 7: execution/option_execution.py -- option instrument
identification and execution-record assembly.
"""

from __future__ import annotations

from datetime import date, datetime

from config import IST
from data.instruments import OptionInstrument
from data.option_price_reconstruction import RawOptionTick
from execution.option_execution import build_option_execution_record

INSTRUMENT_TOKEN = 10914562


def _tick(hms: str, price: float) -> RawOptionTick:
    ts = datetime.strptime(f"2026-09-07 {hms}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    return RawOptionTick(INSTRUMENT_TOKEN, ts, ts, price, price - 0.05, price + 0.05, 1000, 500, {})


def test_option_instrument_identification_resolves_the_real_contract():
    instruments = [
        OptionInstrument("NIFTY2690823900CE", 23900.0, date(2026, 9, 8), "CE", 65, INSTRUMENT_TOKEN)
    ]
    entry = datetime(2026, 9, 7, 9, 15, tzinfo=IST)
    exit_ = datetime(2026, 9, 7, 10, 0, tzinfo=IST)
    ticks = [_tick("09:15:00", 40.0), _tick("10:00:00", 45.0)]

    record = build_option_execution_record(INSTRUMENT_TOKEN, 65, entry, exit_, ticks, instruments)

    assert record.symbol == "NIFTY2690823900CE"
    assert record.strike == 23900.0
    assert record.expiry == "2026-09-08"
    assert record.option_type == "CE"
    assert record.lot_size == 65
    assert record.instrument_token == INSTRUMENT_TOKEN


def test_execution_record_carries_real_reconstructed_entry_and_exit_prices():
    entry = datetime(2026, 9, 7, 9, 15, tzinfo=IST)
    exit_ = datetime(2026, 9, 7, 10, 0, tzinfo=IST)
    ticks = [_tick("09:15:00", 40.0), _tick("10:00:00", 45.0)]

    record = build_option_execution_record(INSTRUMENT_TOKEN, 65, entry, exit_, ticks)

    assert record.entry_price.price == 40.0
    assert record.exit_price.price == 45.0
    assert record.quantity == 65


def test_no_instrument_archive_supplied_leaves_identity_fields_honestly_none():
    entry = datetime(2026, 9, 7, 9, 15, tzinfo=IST)
    exit_ = datetime(2026, 9, 7, 10, 0, tzinfo=IST)
    ticks = [_tick("09:15:00", 40.0)]

    record = build_option_execution_record(INSTRUMENT_TOKEN, 65, entry, exit_, ticks, instruments=None)

    assert record.symbol is None
    assert record.strike is None
    assert record.expiry is None
    assert record.option_type is None
    # The instrument_token itself is always real and known -- only the
    # DERIVED identity fields (symbol/strike/expiry/option_type) are
    # honestly None without a real archive to resolve them against.
    assert record.instrument_token == INSTRUMENT_TOKEN


def test_missing_entry_or_exit_price_is_honestly_none_not_fabricated():
    entry = datetime(2026, 9, 7, 9, 0, tzinfo=IST)  # before any real tick
    exit_ = datetime(2026, 9, 7, 10, 0, tzinfo=IST)
    ticks = [_tick("09:15:00", 40.0)]

    record = build_option_execution_record(INSTRUMENT_TOKEN, 65, entry, exit_, ticks)

    assert record.entry_price is None
    assert record.exit_price.price == 40.0


def test_to_dict_round_trips_through_json():
    import json

    entry = datetime(2026, 9, 7, 9, 15, tzinfo=IST)
    exit_ = datetime(2026, 9, 7, 10, 0, tzinfo=IST)
    ticks = [_tick("09:15:00", 40.0), _tick("10:00:00", 45.0)]

    record = build_option_execution_record(INSTRUMENT_TOKEN, 65, entry, exit_, ticks)
    restored = json.loads(json.dumps(record.to_dict(), default=str))

    assert restored["entry_price"]["price"] == 40.0
    assert restored["exit_price"]["price"] == 45.0
    assert restored["quantity"] == 65
