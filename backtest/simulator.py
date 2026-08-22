from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SimulatedFill:
    price: float
    slippage: float
    costs: float


class Simulator:
    def __init__(
        self, tick_size: float = 0.05, slippage_ticks: int = 1, cost_rate: float = 0.0005
    ) -> None:
        self.tick_size = tick_size
        self.slippage_ticks = slippage_ticks
        self.cost_rate = cost_rate

    def buy_fill(self, price: float, quantity: int) -> SimulatedFill:
        fill = price + self.tick_size * self.slippage_ticks
        return SimulatedFill(fill, fill - price, fill * quantity * self.cost_rate)

    def sell_fill(self, price: float, quantity: int) -> SimulatedFill:
        fill = price - self.tick_size * self.slippage_ticks
        return SimulatedFill(fill, price - fill, fill * quantity * self.cost_rate)

    def exit_price(
        self, candles: pd.DataFrame, stop: float, target: float
    ) -> tuple[float, str, pd.Timestamp]:
        for timestamp, row in candles.iterrows():
            # Conservative ordering when both levels fall inside the same candle.
            if row.low <= stop:
                return stop, "STOP_LOSS", timestamp
            if row.high >= target:
                return target, "TAKE_PROFIT", timestamp
        return float(candles.iloc[-1].close), "SESSION_END", candles.index[-1]
