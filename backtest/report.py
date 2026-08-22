from __future__ import annotations

import json
from pathlib import Path

from backtest.engine import BacktestResult
from backtest.walk_forward import WalkForwardResult


def write_backtest_report(
    result: BacktestResult, output: Path, walk: WalkForwardResult | None = None
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "configuration": result.config,
        "metrics": result.metrics.to_dict(),
        "trades": result.trades.to_dict(orient="records"),
    }
    if walk:
        payload["walk_forward"] = {
            "train": walk.train.metrics.to_dict(),
            "validation": walk.validation.metrics.to_dict(),
            "out_of_sample": walk.out_of_sample.metrics.to_dict(),
            "survives_out_of_sample": walk.survives_out_of_sample,
        }
    output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output
