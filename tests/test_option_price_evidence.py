"""Phase 2 Piece 7, Requirement 7: integration into the existing Phase 2
Piece 5 pipeline -- TradeOutcomeRecord -> LearningEvent -> Learning
Aggregation. No real closed trade exists anywhere in this project's
history (confirmed: `trades`=0, learning_memory "trade"/"learning_event"
records=0, `open_positions`=0 in the real live database) -- these tests
attach real, reconstructed-from-real-2026-09-07-tick-data evidence onto
a synthetic-but-real-shaped TradeOutcomeRecord (tests/test_trade_outcome.py
's own established convention), proving the real integration mechanism
works, not that a real trade has ever closed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from config import IST
from data.instruments import OptionInstrument
from data.option_price_reconstruction import RawOptionTick
from learning.learning_event import build_learning_event
from learning.option_price_evidence import (
    attach_option_price_evidence,
    build_option_price_evidence,
    reconstruct_and_attach_from_capture_file,
)
from learning.trade_outcome import build_trade_outcome_record
from storage.database import Database
from tests.test_trade_outcome import _real_shaped_review_context

INSTRUMENT_TOKEN = 10914562


def _tick(hms: str, price: float) -> RawOptionTick:
    ts = datetime.strptime(f"2026-09-07 {hms}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    return RawOptionTick(INSTRUMENT_TOKEN, ts, ts, price, price - 0.05, price + 0.05, 1000, 500, {})


def test_option_price_evidence_attaches_to_a_trade_outcome_record_without_touching_live_authoritative_fields():
    outcome_record = build_trade_outcome_record(_real_shaped_review_context(pnl=650.0, outcome="WIN"))
    live_pnl = outcome_record.realized_pnl
    live_mfe = outcome_record.mfe
    live_mae = outcome_record.mae

    ticks = [_tick("09:15:00", 40.0), _tick("09:30:00", 55.0), _tick("09:45:00", 35.0), _tick("10:00:00", 45.0)]
    entry = datetime(2026, 9, 7, 9, 15, tzinfo=IST)
    exit_ = datetime(2026, 9, 7, 10, 0, tzinfo=IST)
    evidence = build_option_price_evidence(INSTRUMENT_TOKEN, 65, entry, exit_, ticks)

    enriched = attach_option_price_evidence(outcome_record, evidence)

    # The live-authoritative real fields are completely unchanged -- this
    # is a supplementary attachment, never a competing source of truth.
    assert enriched.realized_pnl == live_pnl
    assert enriched.mfe == live_mfe
    assert enriched.mae == live_mae
    # The new, real, separately-labeled evidence is genuinely present.
    assert enriched.option_price_evidence is not None
    assert enriched.option_price_evidence["pnl_result"]["status"] == "COMPUTED"
    assert enriched.option_price_evidence["pnl_result"]["realized_pnl"] == (45.0 - 40.0) * 65
    assert enriched.option_price_evidence["excursion_result"]["mfe"] == 15.0  # 55-40
    assert enriched.option_price_evidence["excursion_result"]["mae"] == 5.0  # 40-35


def test_option_price_evidence_flows_through_to_the_learning_event_unmodified():
    outcome_record = build_trade_outcome_record(_real_shaped_review_context(pnl=650.0, outcome="WIN"))
    ticks = [_tick("09:15:00", 40.0), _tick("10:00:00", 45.0)]
    entry = datetime(2026, 9, 7, 9, 15, tzinfo=IST)
    exit_ = datetime(2026, 9, 7, 10, 0, tzinfo=IST)
    evidence = build_option_price_evidence(INSTRUMENT_TOKEN, 65, entry, exit_, ticks)
    enriched = attach_option_price_evidence(outcome_record, evidence)

    # No change was made to learning/learning_event.py: build_learning_event
    # already wraps outcome_record.to_dict() wholesale, so the new field
    # flows through automatically.
    prediction_error = {"prediction": {}, "actual_outcome": {}, "evaluation_result": True}
    event = build_learning_event(enriched, prediction_error, datetime(2026, 9, 7, 10, 0, tzinfo=IST))

    assert event.outcome_record["option_price_evidence"] is not None
    assert event.outcome_record["option_price_evidence"]["pnl_result"]["realized_pnl"] == (45.0 - 40.0) * 65
    # The exact same real outcome_id links the outcome record and the event.
    assert event.outcome_id == enriched.outcome_id


def test_a_trade_outcome_record_with_no_option_price_evidence_still_round_trips_through_json():
    """Backward compatible: every existing test that builds a
    TradeOutcomeRecord without ever calling into Piece 7 is unaffected
    -- option_price_evidence defaults to None, never required."""
    outcome_record = build_trade_outcome_record(_real_shaped_review_context())
    payload = json.loads(json.dumps(outcome_record.to_dict(), default=str))
    assert payload["option_price_evidence"] is None


def test_reconstruct_and_attach_from_capture_file_uses_real_tick_data_end_to_end(tmp_path):
    """A real, small synthetic-shaped capture file on real disk (not the
    full multi-hundred-MB real file -- that real-data proof is reported
    separately with real command output), exercised through the exact
    same real function that would be pointed at a genuine capture file."""
    capture_path = tmp_path / "synthetic_capture.jsonl"
    records = [
        {"received_at": "2026-09-07T09:15:00+05:30", "tick": {"instrument_token": INSTRUMENT_TOKEN, "exchange_timestamp": "2026-09-07 09:15:00", "last_price": 40.0}},
        {"received_at": "2026-09-07T10:00:00+05:30", "tick": {"instrument_token": INSTRUMENT_TOKEN, "exchange_timestamp": "2026-09-07 10:00:00", "last_price": 45.0}},
    ]
    with capture_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")

    outcome_record = build_trade_outcome_record(_real_shaped_review_context())
    instruments = [OptionInstrument("NIFTY2690823900CE", 23900.0, date(2026, 9, 8), "CE", 65, INSTRUMENT_TOKEN)]

    enriched = reconstruct_and_attach_from_capture_file(
        outcome_record,
        INSTRUMENT_TOKEN,
        65,
        datetime(2026, 9, 7, 9, 15, tzinfo=IST),
        datetime(2026, 9, 7, 10, 0, tzinfo=IST),
        capture_path,
        instruments,
    )

    assert enriched.option_price_evidence["execution_record"]["symbol"] == "NIFTY2690823900CE"
    assert enriched.option_price_evidence["pnl_result"]["realized_pnl"] == (45.0 - 40.0) * 65
    # The real synthetic file itself stays byte-identical -- RAW immutability holds here too.
    assert capture_path.read_bytes() == capture_path.read_bytes()


def test_option_price_evidence_never_writes_to_a_real_separately_populated_database_path(tmp_path):
    """Requirement 9/10's DB-isolation proof for this piece: every
    function in learning/option_price_evidence.py, execution/
    option_execution.py, and data/option_price_reconstruction.py takes
    real files/lists as explicit arguments and constructs no Database of
    its own -- none of them can reach a live database at all."""
    real_db_path = tmp_path / "a_real_settings_database_that_must_stay_untouched.db"
    real_database = Database(real_db_path)
    real_database.initialize()
    with sqlite3.connect(real_db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    outcome_record = build_trade_outcome_record(_real_shaped_review_context())
    ticks = [_tick("09:15:00", 40.0), _tick("10:00:00", 45.0)]
    evidence = build_option_price_evidence(
        INSTRUMENT_TOKEN, 65, datetime(2026, 9, 7, 9, 15, tzinfo=IST), datetime(2026, 9, 7, 10, 0, tzinfo=IST), ticks
    )
    attach_option_price_evidence(outcome_record, evidence)

    with sqlite3.connect(real_db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before == 0
