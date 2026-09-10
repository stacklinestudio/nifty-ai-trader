"""Phase 2 Piece 7: learning/option_pnl.py -- deterministic option P&L
from reconstructed prices.
"""

from __future__ import annotations

from datetime import datetime

from config import IST
from data.option_price_reconstruction import ReconstructedPrice
from execution.option_execution import OptionExecutionRecord
from learning.option_pnl import compute_option_pnl

INSTRUMENT_TOKEN = 10914562


def _price(value: float, ts: str = "09:15:00") -> ReconstructedPrice:
    timestamp = datetime.strptime(f"2026-09-07 {ts}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    return ReconstructedPrice(value, "LAST_PRICE", INSTRUMENT_TOKEN, timestamp, timestamp, 0.0, value - 0.05, value + 0.05)


def _execution_record(option_type: str, entry: float, exit_: float, quantity: int = 65) -> OptionExecutionRecord:
    return OptionExecutionRecord(
        instrument_token=INSTRUMENT_TOKEN,
        symbol="NIFTY2690823900CE" if option_type == "CE" else "NIFTY2690823900PE",
        expiry="2026-09-08",
        strike=23900.0,
        option_type=option_type,
        lot_size=65,
        quantity=quantity,
        entry_timestamp="2026-09-07T09:15:00+05:30",
        entry_price=_price(entry, "09:15:00"),
        exit_timestamp="2026-09-07T10:00:00+05:30",
        exit_price=_price(exit_, "10:00:00"),
    )


def test_long_call_pnl_is_real_price_difference_times_quantity():
    record = _execution_record("CE", entry=40.0, exit_=55.0, quantity=65)

    result = compute_option_pnl(record)

    assert result.status == "COMPUTED"
    assert result.gross_pnl == (55.0 - 40.0) * 65
    assert result.realized_pnl == result.gross_pnl  # zero costs supplied


def test_long_put_pnl_uses_the_same_real_formula_as_a_long_call():
    """Direction (CALL vs PUT) does not change the P&L formula -- a long
    put is priced identically from its own real premium path, only the
    CONTRACT identity differs (already resolved separately by execution/
    option_execution.py)."""
    call_record = _execution_record("CE", entry=40.0, exit_=30.0, quantity=65)
    put_record = _execution_record("PE", entry=40.0, exit_=30.0, quantity=65)

    call_result = compute_option_pnl(call_record)
    put_result = compute_option_pnl(put_record)

    assert call_result.gross_pnl == put_result.gross_pnl == (30.0 - 40.0) * 65


def test_a_losing_long_position_produces_a_real_negative_pnl():
    record = _execution_record("CE", entry=40.0, exit_=25.0, quantity=65)

    result = compute_option_pnl(record)

    assert result.gross_pnl == (25.0 - 40.0) * 65
    assert result.gross_pnl < 0


def test_lot_size_aware_quantity_scales_pnl_linearly():
    one_lot = _execution_record("CE", entry=40.0, exit_=50.0, quantity=65)
    two_lots = _execution_record("CE", entry=40.0, exit_=50.0, quantity=130)

    assert compute_option_pnl(two_lots).gross_pnl == compute_option_pnl(one_lot).gross_pnl * 2


def test_real_supplied_transaction_costs_are_subtracted_never_invented():
    record = _execution_record("CE", entry=40.0, exit_=55.0, quantity=65)

    without_costs = compute_option_pnl(record, costs=0.0)
    with_real_costs = compute_option_pnl(record, costs=12.35)  # a real, caller-supplied figure

    assert with_real_costs.costs == 12.35
    assert with_real_costs.realized_pnl == without_costs.gross_pnl - 12.35
    assert with_real_costs.gross_pnl == without_costs.gross_pnl  # gross is cost-independent by definition


def test_insufficient_data_when_entry_price_could_not_be_reconstructed():
    record = OptionExecutionRecord(
        instrument_token=INSTRUMENT_TOKEN, symbol="X", expiry="2026-09-08", strike=23900.0,
        option_type="CE", lot_size=65, quantity=65,
        entry_timestamp="2026-09-07T09:15:00+05:30", entry_price=None,
        exit_timestamp="2026-09-07T10:00:00+05:30", exit_price=_price(45.0, "10:00:00"),
    )

    result = compute_option_pnl(record)

    assert result.status == "INSUFFICIENT_DATA"
    assert result.realized_pnl is None
    assert result.gross_pnl is None
    assert "entry" in result.reason


def test_insufficient_data_when_exit_price_could_not_be_reconstructed():
    record = OptionExecutionRecord(
        instrument_token=INSTRUMENT_TOKEN, symbol="X", expiry="2026-09-08", strike=23900.0,
        option_type="CE", lot_size=65, quantity=65,
        entry_timestamp="2026-09-07T09:15:00+05:30", entry_price=_price(40.0, "09:15:00"),
        exit_timestamp="2026-09-07T10:00:00+05:30", exit_price=None,
    )

    result = compute_option_pnl(record)

    assert result.status == "INSUFFICIENT_DATA"
    assert "exit" in result.reason
