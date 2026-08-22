"""Optional dependency-free local dashboard from an exported trades CSV."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def build_dashboard(trades_csv: Path, output: Path) -> Path:
    trades = pd.read_csv(trades_csv)
    if "net_pnl" not in trades:
        raise ValueError("trades CSV requires net_pnl")
    trades["equity"] = trades.net_pnl.cumsum()
    trades["drawdown"] = trades.equity - trades.equity.cummax()
    metrics = {
        "Trades": len(trades),
        "Net P&L": round(trades.net_pnl.sum(), 2),
        "Win rate": round((trades.net_pnl.gt(0).mean() * 100) if len(trades) else 0, 2),
        "Maximum drawdown": round(trades.drawdown.min() if len(trades) else 0, 2),
    }
    table = trades.to_html(index=False, classes="trades")
    cards = "".join(f"<li><strong>{key}</strong>: {value}</li>" for key, value in metrics.items())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"<!doctype html><title>NIFTY AI Trader Results</title><style>body{{font-family:system-ui;margin:2rem}}li{{margin:.5rem}}.trades{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.4rem}}</style><h1>NIFTY AI Trader Results</h1><p>Research results only; no profitability guarantee.</p><ul>{cards}</ul><h2>Trade distribution and equity inputs</h2>{table}",
        encoding="utf-8",
    )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("trades_csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/generated/dashboard.html"))
    args = parser.parse_args()
    print(build_dashboard(args.trades_csv, args.output))
