"""Phase 2 Piece 7, Requirement 7: ties the reconstruction layer (data/
option_price_reconstruction.py), the execution record (execution/
option_execution.py), and the P&L/excursion functions (learning/
option_pnl.py) into one real evidence bundle attachable to an existing
learning/trade_outcome.py::TradeOutcomeRecord -- feeding, unmodified in
shape, into the existing LearningEvent -> Learning Aggregation pipeline
(TradeOutcomeRecord.to_dict() already includes option_price_evidence;
learning/learning_event.py::build_learning_event already wraps
TradeOutcomeRecord.to_dict() wholesale, so no change to learning_event.py
or learning_aggregation.py was needed or made).

Deliberately NOT auto-wired into agents/orchestrator.py's live
_close_position path -- this is an opt-in, callable research/
verification step, invoked separately (a script, a test, an operator)
against a trade's real entry/exit timestamps and a real capture file,
never automatically on every real close. Two real reasons: (1) live
trading behavior must not change for this piece (explicit requirement),
and (2) scanning a real multi-hundred-MB raw capture file is real,
non-trivial I/O that has no place in the live per-cycle/per-close path.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Any

from data.instruments import OptionInstrument
from data.option_price_reconstruction import (
    ExcursionResult,
    RawOptionTick,
    compute_excursion,
    extract_ticks_for_instrument,
)
from execution.option_execution import build_option_execution_record
from learning.option_pnl import compute_option_pnl
from learning.trade_outcome import TradeOutcomeRecord


def build_option_price_evidence(
    instrument_token: int,
    quantity: int,
    entry_timestamp: datetime,
    exit_timestamp: datetime,
    ticks: list[RawOptionTick],
    instruments: list[OptionInstrument] | None = None,
    costs: float = 0.0,
) -> dict[str, Any]:
    """Pure, real, deterministic assembly -- no I/O of its own (ticks
    are supplied by the caller, typically via extract_ticks_for_
    instrument, kept as a separate step so the same real, already-
    extracted tick list can be reused for both the execution record and
    the excursion computation without re-scanning the real capture
    file twice)."""
    execution_record = build_option_execution_record(
        instrument_token, quantity, entry_timestamp, exit_timestamp, ticks, instruments
    )
    pnl_result = compute_option_pnl(execution_record, costs=costs)
    entry_price_for_excursion = execution_record.entry_price.price if execution_record.entry_price else None
    if entry_price_for_excursion is not None:
        excursion_result = compute_excursion(ticks, entry_timestamp, exit_timestamp, entry_price_for_excursion)
    else:
        excursion_result = ExcursionResult("INSUFFICIENT_DATA", None, None, 0)

    return {
        "execution_record": execution_record.to_dict(),
        "pnl_result": pnl_result.to_dict(),
        "excursion_result": excursion_result.to_dict(),
    }


def attach_option_price_evidence(
    outcome_record: TradeOutcomeRecord, evidence: dict[str, Any]
) -> TradeOutcomeRecord:
    """Returns a NEW TradeOutcomeRecord (frozen dataclass -- dataclasses.
    replace, never in-place mutation) with option_price_evidence
    attached. Every other real field (realized_pnl, mfe, mae, etc.) is
    completely unchanged -- this never overwrites the live-authoritative
    figures, only adds the supplementary evidence alongside them."""
    return dataclasses.replace(outcome_record, option_price_evidence=evidence)


def reconstruct_and_attach_from_capture_file(
    outcome_record: TradeOutcomeRecord,
    instrument_token: int,
    quantity: int,
    entry_timestamp: datetime,
    exit_timestamp: datetime,
    capture_file_path: Path,
    instruments: list[OptionInstrument] | None = None,
    costs: float = 0.0,
) -> TradeOutcomeRecord:
    """The real, end-to-end convenience entry point: reads the real raw
    capture file for `instrument_token` ONCE (extract_ticks_for_
    instrument -- read-only, RAW-immutable, see its own docstring),
    builds the real evidence bundle, and attaches it. Raises
    FileNotFoundError (never silently returns an empty/fabricated
    result) if `capture_file_path` genuinely does not exist -- callers
    that expect "no real capture data for this day" to be a normal,
    honest outcome should check Path.exists() themselves first and skip
    calling this, exactly as this project's own real, historical
    finding already establishes (real option-tick capture only exists
    for 2026-09-07 and 2026-09-08)."""
    ticks = extract_ticks_for_instrument(capture_file_path, instrument_token)
    evidence = build_option_price_evidence(
        instrument_token, quantity, entry_timestamp, exit_timestamp, ticks, instruments, costs
    )
    return attach_option_price_evidence(outcome_record, evidence)
