import pandas as pd

from backtest.engine import BacktestEngine
from backtest.metrics import calculate_metrics
from backtest.simulator import Simulator
from backtest.walk_forward import walk_forward
from risk.risk_manager import RiskManager


def data(days=5):
    frames = []
    for day in range(days):
        index = pd.date_range(
            f"2025-01-{day + 1:02} 09:15", periods=10, freq="min", tz="Asia/Kolkata"
        )
        close = [100, 101, 102, 101, 102, 105, 108, 110, 109, 108]
        frames.append(
            pd.DataFrame(
                {
                    "open": close,
                    "high": [x + 1 for x in close],
                    "low": [x - 1 for x in close],
                    "close": close,
                    "volume": [100] * 10,
                },
                index=index,
            )
        )
    return pd.concat(frames)


def engine():
    return BacktestEngine(RiskManager(200, 5000), Simulator(), 1)


def test_backtest_reports_net_and_gross_pnl():
    result = engine().run(data())
    assert result.metrics.total_trades == 5
    assert {"gross_pnl", "net_pnl", "slippage"}.issubset(result.trades.columns)


def test_metrics_drawdown_and_streaks():
    result = calculate_metrics(pd.DataFrame({"gross_pnl": [1, -2, 3], "net_pnl": [1, -2, 3]}))
    assert result.max_drawdown == -2 and result.consecutive_wins == 1


def test_walk_forward_separates_periods():
    result = walk_forward(engine(), data(6))
    assert (
        result.train.metrics.total_trades == 3
        and result.validation.metrics.total_trades == 1
        and result.out_of_sample.metrics.total_trades == 2
    )
