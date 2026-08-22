"""No-look-ahead opening-range strategy simulator."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.metrics import PerformanceMetrics, calculate_metrics
from backtest.simulator import Simulator
from risk.risk_manager import RiskManager
from strategy.orb import breakout_direction


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    metrics: PerformanceMetrics
    config: dict


class BacktestEngine:
    def __init__(
        self, risk: RiskManager, simulator: Simulator, lot_size: int = 1, opening_minutes: int = 5
    ) -> None:
        self.risk = risk
        self.simulator = simulator
        self.lot_size = lot_size
        self.opening_minutes = opening_minutes

    def run(self, candles: pd.DataFrame) -> BacktestResult:
        rows = []
        for day, frame in candles.groupby(candles.index.date):
            frame = frame.sort_index()
            if len(frame) <= self.opening_minutes:
                continue
            observed = frame.iloc[
                : self.opening_minutes + 1
            ]  # Decision only sees the range plus next completed bar.
            direction = breakout_direction(observed, self.opening_minutes)
            if direction == "NO_TRADE":
                continue
            entry_bar = frame.iloc[self.opening_minutes]
            atr = max(
                float((observed.high - observed.low).tail(self.opening_minutes).mean()),
                entry_bar.close * 0.02,
            )
            plan = self.risk.plan_long_option(float(entry_bar.close), atr, self.lot_size)
            if not plan:
                continue
            buy = self.simulator.buy_fill(plan.entry, plan.quantity)
            subsequent = frame.iloc[self.opening_minutes + 1 :]
            if subsequent.empty:
                continue
            raw_exit, reason, exit_time = self.simulator.exit_price(
                subsequent, plan.stop, plan.target
            )
            sell = self.simulator.sell_fill(raw_exit, plan.quantity)
            gross = (sell.price - buy.price) * plan.quantity
            costs = buy.costs + sell.costs
            rows.append(
                {
                    "date": str(day),
                    "direction": direction,
                    "entry_time": entry_bar.name,
                    "exit_time": exit_time,
                    "entry": buy.price,
                    "exit": sell.price,
                    "quantity": plan.quantity,
                    "exit_reason": reason,
                    "gross_pnl": gross,
                    "slippage": buy.slippage + sell.slippage,
                    "estimated_costs": costs,
                    "net_pnl": gross - costs,
                }
            )
        trades = pd.DataFrame(rows)
        return BacktestResult(
            trades,
            calculate_metrics(trades),
            {
                "opening_minutes": self.opening_minutes,
                "lot_size": self.lot_size,
                "strategy": "ORB v0.1",
                "lookahead": "prevented",
            },
        )
