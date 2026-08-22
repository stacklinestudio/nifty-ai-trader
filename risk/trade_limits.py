from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DailyLimits:
    max_trades: int
    max_daily_loss: float
    trades: int = 0
    realized_pnl: float = 0.0

    def can_open(self) -> bool:
        return self.trades < self.max_trades and self.realized_pnl > -self.max_daily_loss

    def register_open(self) -> None:
        if not self.can_open():
            raise ValueError("Daily trade/loss limit reached")
        self.trades += 1

    def register_close(self, pnl: float) -> None:
        self.realized_pnl += pnl
