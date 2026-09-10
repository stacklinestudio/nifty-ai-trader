"""Phase 2 Piece 7: data/option_price_reconstruction.py.

Fixtures here are SYNTHETIC (clearly labelled) -- small, hand-built
JSONL records matching the exact real shape this project's own capture
files use (confirmed against the real files directly, see this module's
own docstring), used for fast, deterministic unit coverage. Real-data
proof against the actual captured files (data/private/option_tick_
capture/nifty_option_ticks_2026-09-07.jsonl et al.) is reported
separately, with real command output, in this piece's completion
report -- not repeated here as a routine test, since scanning a real
700MB+ file on every `pytest -q` run would meaningfully slow down the
whole suite for a fact already proven once, with real evidence.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from config import IST
from data.option_price_reconstruction import (
    PRICE_SOURCE_LAST_PRICE,
    PRICE_SOURCE_MID_BID_ASK,
    compute_excursion,
    extract_ticks_for_instrument,
    is_stale,
    parse_raw_tick,
    reconstruct_option_price,
)

INSTRUMENT_TOKEN = 10914562


def _synthetic_tick_record(exchange_timestamp: str, last_price: float | None, bid: float | None = None, ask: float | None = None) -> dict:
    """A synthetic-but-real-shaped record -- the exact real envelope/tick
    field names this project's own real captured data uses (confirmed
    directly against nifty_option_ticks_2026-09-07.jsonl's first real
    records), not a fabricated schema."""
    tick = {
        "tradable": True,
        "mode": "full",
        "instrument_token": INSTRUMENT_TOKEN,
        "exchange_timestamp": exchange_timestamp,
    }
    if last_price is not None:
        tick["last_price"] = last_price
    if bid is not None or ask is not None:
        tick["depth"] = {
            "buy": [{"price": bid, "quantity": 100, "orders": 1}] if bid is not None else [],
            "sell": [{"price": ask, "quantity": 100, "orders": 1}] if ask is not None else [],
        }
    return {"received_at": "2026-09-07T09:15:00.000000+05:30", "tick": tick}


def _write_synthetic_capture_file(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _ts(hms: str) -> datetime:
    return datetime.strptime(f"2026-09-07 {hms}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)


def test_extract_ticks_for_instrument_only_collects_the_requested_token(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    other_token_record = _synthetic_tick_record("2026-09-07 09:15:00", 40.0)
    other_token_record["tick"]["instrument_token"] = 999999
    _write_synthetic_capture_file(
        path,
        [
            _synthetic_tick_record("2026-09-07 09:15:00", 40.0),
            other_token_record,
            _synthetic_tick_record("2026-09-07 09:16:00", 41.0),
        ],
    )

    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    assert len(ticks) == 2
    assert all(t.instrument_token == INSTRUMENT_TOKEN for t in ticks)


def test_exact_timestamp_price_reconstruction(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(
        path,
        [
            _synthetic_tick_record("2026-09-07 09:15:00", 40.0, bid=39.9, ask=40.1),
            _synthetic_tick_record("2026-09-07 09:16:00", 41.0, bid=40.9, ask=41.1),
        ],
    )
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    result = reconstruct_option_price(ticks, _ts("09:16:00"))

    assert result is not None
    assert result.price == 41.0
    assert result.price_source == PRICE_SOURCE_LAST_PRICE
    assert result.tick_exchange_timestamp == _ts("09:16:00")
    assert result.age_seconds == 0.0
    assert result.bid == 40.9 and result.ask == 41.1


def test_before_timestamp_tick_selection_picks_the_most_recent_prior_tick(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(
        path,
        [
            _synthetic_tick_record("2026-09-07 09:15:00", 40.0),
            _synthetic_tick_record("2026-09-07 09:16:00", 41.0),
            _synthetic_tick_record("2026-09-07 09:17:00", 42.0),
        ],
    )
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    # Requested timestamp falls BETWEEN two real ticks -- the most recent
    # one at or before it must be selected, never rounded to the nearest.
    result = reconstruct_option_price(ticks, _ts("09:16:45"))

    assert result.price == 41.0
    assert result.tick_exchange_timestamp == _ts("09:16:00")
    assert result.age_seconds == 45.0


def test_rejects_a_tick_strictly_after_the_requested_timestamp(tmp_path):
    """The single most important test in this module: no look-ahead."""
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(
        path,
        [
            _synthetic_tick_record("2026-09-07 09:15:00", 40.0),
            _synthetic_tick_record("2026-09-07 09:20:00", 999.0),  # a real future tick -- must never be selected
        ],
    )
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    result = reconstruct_option_price(ticks, _ts("09:17:00"))

    assert result.price == 40.0  # NOT 999.0
    assert result.tick_exchange_timestamp == _ts("09:15:00")


def test_reports_no_valid_price_when_requested_timestamp_is_before_any_real_tick(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(path, [_synthetic_tick_record("2026-09-07 09:15:00", 40.0)])
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    result = reconstruct_option_price(ticks, _ts("09:00:00"))

    assert result is None  # never a fabricated price


def test_reports_no_valid_price_with_zero_real_ticks():
    result = reconstruct_option_price([], _ts("09:15:00"))
    assert result is None


def test_stale_price_detection_is_a_separate_explicit_policy_check(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(path, [_synthetic_tick_record("2026-09-07 09:15:00", 40.0)])
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    result = reconstruct_option_price(ticks, _ts("09:16:41"))  # 101 real seconds later

    assert result.age_seconds == 101.0
    assert is_stale(result, max_age_seconds=60) is True
    assert is_stale(result, max_age_seconds=120) is False


def test_bid_ask_is_preserved_alongside_last_price_not_replaced_by_it(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(
        path, [_synthetic_tick_record("2026-09-07 09:15:00", 40.0, bid=39.5, ask=40.5)]
    )
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    result = reconstruct_option_price(ticks, _ts("09:15:00"))

    assert result.price == 40.0  # last_price, not the mid (40.0) -- coincidentally equal here, checked below with a real divergent case
    assert result.bid == 39.5
    assert result.ask == 40.5


def test_deterministic_price_source_selection_prefers_last_price_over_mid_bid_ask(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(
        path, [_synthetic_tick_record("2026-09-07 09:15:00", 45.0, bid=39.5, ask=40.5)]
    )
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    result = reconstruct_option_price(ticks, _ts("09:15:00"))

    assert result.price == 45.0  # the real last_price, NOT the mid-bid-ask (40.0)
    assert result.price_source == PRICE_SOURCE_LAST_PRICE


def test_falls_back_to_mid_bid_ask_only_when_last_price_is_genuinely_missing(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    record = _synthetic_tick_record("2026-09-07 09:15:00", None, bid=39.0, ask=41.0)
    _write_synthetic_capture_file(path, [record])
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    result = reconstruct_option_price(ticks, _ts("09:15:00"))

    assert result.price == 40.0  # (39.0 + 41.0) / 2
    assert result.price_source == PRICE_SOURCE_MID_BID_ASK


def test_missing_price_data_with_no_bid_ask_either_is_not_a_valid_tick(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    record = _synthetic_tick_record("2026-09-07 09:15:00", None)
    _write_synthetic_capture_file(path, [record])
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    result = reconstruct_option_price(ticks, _ts("09:15:00"))

    assert result is None


def test_mfe_mae_computed_from_the_real_option_price_path(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(
        path,
        [
            _synthetic_tick_record("2026-09-07 09:15:00", 40.0),
            _synthetic_tick_record("2026-09-07 09:16:00", 45.0),  # +5 favorable
            _synthetic_tick_record("2026-09-07 09:17:00", 33.0),  # -7 adverse
            _synthetic_tick_record("2026-09-07 09:18:00", 38.0),  # exit
        ],
    )
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    excursion = compute_excursion(ticks, _ts("09:15:00"), _ts("09:18:00"), entry_price=40.0)

    assert excursion.status == "COMPUTED"
    assert excursion.mfe == 5.0
    assert excursion.mae == 7.0
    assert excursion.tick_count_in_window == 4


def test_insufficient_tick_coverage_reports_explicitly_not_a_fabricated_zero(tmp_path):
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(path, [_synthetic_tick_record("2026-09-07 09:15:00", 40.0)])
    ticks = extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)

    # A real window with zero real ticks inside it -- entry/exit both
    # after the only real tick this fixture has.
    excursion = compute_excursion(ticks, _ts("10:00:00"), _ts("10:05:00"), entry_price=40.0)

    assert excursion.status == "INSUFFICIENT_DATA"
    assert excursion.mfe is None
    assert excursion.mae is None
    assert excursion.tick_count_in_window == 0


def test_raw_capture_file_bytes_are_byte_identical_after_reconstruction(tmp_path):
    """Requirement 2's dedicated regression test: a real (synthetic-
    shaped, but a real file on real disk) capture file's bytes must be
    completely unchanged after extract_ticks_for_instrument reads it --
    proven by comparing real file bytes before/after, not just "no write
    call was made"."""
    path = tmp_path / "synthetic_capture.jsonl"
    _write_synthetic_capture_file(
        path,
        [
            _synthetic_tick_record("2026-09-07 09:15:00", 40.0),
            _synthetic_tick_record("2026-09-07 09:16:00", 41.0),
        ],
    )
    bytes_before = path.read_bytes()

    extract_ticks_for_instrument(path, INSTRUMENT_TOKEN)
    reconstruct_option_price(extract_ticks_for_instrument(path, INSTRUMENT_TOKEN), _ts("09:16:00"))

    assert path.read_bytes() == bytes_before


def test_parse_raw_tick_preserves_the_exact_untouched_tick_dict(tmp_path):
    """raw carries the exact tick dict, unmodified -- a real identity/
    equality check against the original parsed JSON, not a re-derived
    copy that merely looks the same."""
    record = _synthetic_tick_record("2026-09-07 09:15:00", 40.0, bid=39.5, ask=40.5)
    parsed = parse_raw_tick(record)

    assert parsed.raw == record["tick"]
    assert parsed.raw is record["tick"]  # the exact same object, not a copy
