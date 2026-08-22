from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.engine import BacktestEngine, BacktestResult


@dataclass
class WalkForwardResult:
    train: BacktestResult
    validation: BacktestResult
    out_of_sample: BacktestResult

    @property
    def survives_out_of_sample(self) -> bool:
        return (
            self.out_of_sample.metrics.total_trades > 0 and self.out_of_sample.metrics.net_pnl > 0
        )


def walk_forward(
    engine: BacktestEngine,
    candles: pd.DataFrame,
    train_ratio: float = 0.6,
    validation_ratio: float = 0.2,
) -> WalkForwardResult:
    dates = sorted(candles.index.normalize().unique())
    if len(dates) < 3:
        raise ValueError("Walk-forward requires at least three trading days")
    first = max(1, int(len(dates) * train_ratio))
    second = max(first + 1, int(len(dates) * (train_ratio + validation_ratio)))

    def part(values: list[pd.Timestamp]):
        return candles[candles.index.normalize().isin(values)]

    return WalkForwardResult(
        engine.run(part(dates[:first])),
        engine.run(part(dates[first:second])),
        engine.run(part(dates[second:])),
    )
