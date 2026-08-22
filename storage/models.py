from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SignalRecord:
    timestamp: datetime
    direction: str
    confidence: float
    features: dict[str, Any]
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def serializable(self) -> dict[str, Any]:
        item = asdict(self)
        item["timestamp"] = self.timestamp.isoformat()
        return item


@dataclass
class Trade:
    symbol: str
    side: str
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float
    opened_at: datetime
    order_id: str = ""
    exit_price: float | None = None
    closed_at: datetime | None = None
    exit_reason: str | None = None
    slippage: float = 0.0
    estimated_costs: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def gross_pnl(self) -> float:
        return (
            0.0 if self.exit_price is None else (self.exit_price - self.entry_price) * self.quantity
        )

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.estimated_costs
