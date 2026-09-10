"""Phase 2 Piece 7, Requirement 4: option-level execution pricing.

Pure assembly -- no I/O of its own beyond what data/option_price_
reconstruction.py and data/instruments.py already provide. Does NOT
change live execution behavior: this is a separate, opt-in research/
reconstruction layer, never called from agents/orchestrator.py's real
trading path. A real paper trade's real, authoritative fill price and
P&L continue to come exclusively from execution/paper_broker.py, exactly
as before this piece -- see learning/option_pnl.py's own module
docstring for why this stays a supplementary cross-check, never a
competing source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from data.instruments import OptionInstrument, resolve_instrument_by_token
from data.option_price_reconstruction import (
    RawOptionTick,
    ReconstructedPrice,
    reconstruct_option_price,
)


@dataclass(frozen=True)
class OptionExecutionRecord:
    instrument_token: int
    symbol: str | None
    expiry: str | None
    strike: float | None
    option_type: str | None
    lot_size: int | None
    quantity: int
    entry_timestamp: str
    entry_price: ReconstructedPrice | None
    exit_timestamp: str
    exit_price: ReconstructedPrice | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_token": self.instrument_token,
            "symbol": self.symbol,
            "expiry": self.expiry,
            "strike": self.strike,
            "option_type": self.option_type,
            "lot_size": self.lot_size,
            "quantity": self.quantity,
            "entry_timestamp": self.entry_timestamp,
            "entry_price": self.entry_price.to_dict() if self.entry_price else None,
            "exit_timestamp": self.exit_timestamp,
            "exit_price": self.exit_price.to_dict() if self.exit_price else None,
        }


def build_option_execution_record(
    instrument_token: int,
    quantity: int,
    entry_timestamp: datetime,
    exit_timestamp: datetime,
    ticks: list[RawOptionTick],
    instruments: list[OptionInstrument] | None = None,
) -> OptionExecutionRecord:
    """Real, deterministic assembly: resolves the real contract identity
    (when a real instrument archive is supplied) and the real
    reconstructed entry/exit prices (data/option_price_reconstruction.py
    ::reconstruct_option_price, never fabricated -- entry_price/
    exit_price are None, explicitly, when no real tick exists at or
    before the requested real timestamp). No decision is made here --
    this only assembles what's genuinely knowable from the real supplied
    data."""
    instrument = resolve_instrument_by_token(instruments, instrument_token) if instruments else None
    entry_price = reconstruct_option_price(ticks, entry_timestamp)
    exit_price = reconstruct_option_price(ticks, exit_timestamp)

    return OptionExecutionRecord(
        instrument_token=instrument_token,
        symbol=instrument.symbol if instrument else None,
        expiry=instrument.expiry.isoformat() if instrument else None,
        strike=instrument.strike if instrument else None,
        option_type=instrument.option_type if instrument else None,
        lot_size=instrument.lot_size if instrument else None,
        quantity=quantity,
        entry_timestamp=entry_timestamp.isoformat(),
        entry_price=entry_price,
        exit_timestamp=exit_timestamp.isoformat(),
        exit_price=exit_price,
    )
