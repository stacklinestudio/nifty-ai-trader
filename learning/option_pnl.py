"""Phase 2 Piece 7, Requirement 5: deterministic option P&L from
reconstructed entry/exit prices.

Does NOT replace or compete with the real, live-authoritative P&L
execution/paper_broker.py + agents/orchestrator.py::_close_position
already compute for every real paper trade (`(fill_price - entry) *
quantity - estimated_costs`, the exact figure that feeds risk/trade_
limits.py::DailyLimits.realized_pnl and, through Phase 2 Piece 5,
learning/trade_outcome.py::TradeOutcomeRecord.realized_pnl) -- that
figure remains the ONLY one ever used for real trading decisions or
persisted as `realized_pnl`. This module computes a SEPARATE, clearly-
labeled cross-check from the real reconstructed tick-level prices
(data/option_price_reconstruction.py), for research/verification only.
Never wired into the live trading path; see execution/option_execution.py
's own module docstring for the same real boundary.

Audited, explicit scope, per this project's own real paper-execution
model (agents/trading_agents.py::ExecutionAgent always places "BUY";
agents/orchestrator.py::_close_position always places "SELL" for the
thesis's full real quantity -- confirmed by direct code read, not
assumed):
  - Long calls/puts: SUPPORTED -- the only real position shape this
    project's paper execution model has ever produced.
  - Short positions: NOT SUPPORTED. Not a Piece 7 gap -- the existing
    paper execution model has no "sell to open" path anywhere in this
    codebase, so there is no real short position this function could
    ever be asked to price.
  - Partial exits: NOT SUPPORTED. Not a Piece 7 gap -- _close_position
    always exits the thesis's full real quantity; no partial-exit code
    path exists anywhere in this codebase.
  - Transaction costs: only ever a real, already-computed value handed
    in by the caller (e.g. a real PaperBroker order's own
    `estimated_costs`) -- this module never invents or independently
    re-derives a cost model. Defaults to 0.0, explicitly, when no real
    cost figure is supplied, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from execution.option_execution import OptionExecutionRecord

LONG_CALL = "CALL"
LONG_PUT = "PUT"

STATUS_COMPUTED = "COMPUTED"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class OptionPnLResult:
    status: str  # COMPUTED | INSUFFICIENT_DATA
    realized_pnl: float | None
    gross_pnl: float | None
    entry_price: float | None
    exit_price: float | None
    quantity: int
    costs: float
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "realized_pnl": self.realized_pnl,
            "gross_pnl": self.gross_pnl,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "costs": self.costs,
        }


def compute_option_pnl(
    execution_record: OptionExecutionRecord, costs: float = 0.0
) -> OptionPnLResult:
    """Real, deterministic long-option P&L: (exit_price - entry_price) *
    quantity - costs -- the same real formula agents/orchestrator.py::
    _close_position already uses for its own live-authoritative figure,
    applied here to the real RECONSTRUCTED tick-level prices instead of
    the real PaperBroker fill. Direction (CALL vs PUT) does not change
    this formula -- a long call and a long put are priced identically
    from their own real option premium; direction only matters for
    picking WHICH contract (CE vs PE) was actually held, already
    resolved onto `execution_record.option_type` by execution/
    option_execution.py, not re-decided here.

    Returns INSUFFICIENT_DATA (every price field None, never a
    fabricated number) when either the real entry or real exit price
    could not be reconstructed (data/option_price_reconstruction.py::
    reconstruct_option_price returned None -- no real tick existed at
    or before that real timestamp)."""
    if execution_record.entry_price is None or execution_record.exit_price is None:
        missing = []
        if execution_record.entry_price is None:
            missing.append("entry")
        if execution_record.exit_price is None:
            missing.append("exit")
        return OptionPnLResult(
            status=STATUS_INSUFFICIENT_DATA,
            realized_pnl=None,
            gross_pnl=None,
            entry_price=None,
            exit_price=None,
            quantity=execution_record.quantity,
            costs=costs,
            reason=f"no real reconstructed price available for: {', '.join(missing)}",
        )

    entry = execution_record.entry_price.price
    exit_ = execution_record.exit_price.price
    gross_pnl = (exit_ - entry) * execution_record.quantity
    realized_pnl = gross_pnl - costs

    return OptionPnLResult(
        status=STATUS_COMPUTED,
        realized_pnl=realized_pnl,
        gross_pnl=gross_pnl,
        entry_price=entry,
        exit_price=exit_,
        quantity=execution_record.quantity,
        costs=costs,
    )
