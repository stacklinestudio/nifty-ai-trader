from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class PerformanceMetrics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_pnl: float
    net_pnl: float
    average_win: float
    average_loss: float
    expectancy: float
    profit_factor: float
    max_drawdown: float
    sharpe: float | None
    sortino: float | None
    largest_win: float
    largest_loss: float
    consecutive_wins: int
    consecutive_losses: int

    def to_dict(self) -> dict:
        return asdict(self)


def _streak(values: list[float], positive: bool) -> int:
    best = run = 0
    for value in values:
        if (value > 0) == positive:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def calculate_metrics(trades: pd.DataFrame) -> PerformanceMetrics:
    if trades.empty:
        return PerformanceMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, None, None, 0, 0, 0, 0)
    pnl = trades["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax()
    std = pnl.std(ddof=1)
    downside = losses.std(ddof=1)
    return PerformanceMetrics(
        len(pnl),
        len(wins),
        len(losses),
        len(wins) / len(pnl) * 100,
        float(trades.get("gross_pnl", pnl).sum()),
        float(pnl.sum()),
        float(wins.mean()) if len(wins) else 0.0,
        float(losses.mean()) if len(losses) else 0.0,
        float(pnl.mean()),
        float(wins.sum() / -losses.sum())
        if len(losses) and losses.sum()
        else math.inf
        if len(wins)
        else 0.0,
        float(drawdown.min()),
        float(pnl.mean() / std * math.sqrt(len(pnl))) if std and not pd.isna(std) else None,
        float(pnl.mean() / downside * math.sqrt(len(pnl)))
        if downside and not pd.isna(downside)
        else None,
        float(pnl.max()),
        float(pnl.min()),
        _streak(pnl.tolist(), True),
        _streak(pnl.tolist(), False),
    )
