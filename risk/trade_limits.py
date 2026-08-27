from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DailyLimits:
    max_trades: int
    max_daily_loss: float
    daily_profit_target: float | None = None
    trades: int = 0
    realized_pnl: float = 0.0

    def can_open(self) -> bool:
        if self.daily_profit_target is not None and self.realized_pnl >= self.daily_profit_target:
            # Blocks new entries only -- an already-open position is
            # supervised by run_supervised independent of this check, and
            # closes through its own normal target/stop/trail/forced-exit
            # path regardless of what the day's realized P&L is doing.
            return False
        return self.trades < self.max_trades and self.realized_pnl > -self.max_daily_loss

    def register_open(self) -> None:
        if not self.can_open():
            raise ValueError("Daily trade/loss limit reached")
        self.trades += 1

    def register_close(self, pnl: float) -> None:
        self.realized_pnl += pnl
