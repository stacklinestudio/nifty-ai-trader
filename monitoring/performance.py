from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.metrics import calculate_metrics


def export_performance(trades: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv = output_dir / "trades.csv"
    json = output_dir / "performance.json"
    trades.to_csv(csv, index=False)
    json.write_text(
        pd.Series(calculate_metrics(trades).to_dict()).to_json(indent=2), encoding="utf-8"
    )
    return csv, json
