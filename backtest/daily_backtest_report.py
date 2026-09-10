"""Phase 2 Piece 4: a real, JSON-safe summary of a DailyBacktestReport
(backtest/daily_backtest.py's own agent/orchestrator-based replay) --
mirrors backtest/report.py::write_backtest_report's pattern for the
older ORB-only engine, but for the real per-day CycleResult objects this
report actually carries (nested AgentResult/TradeThesis/Validation
objects that aren't trivially JSON-serializable on their own).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backtest.daily_backtest import BacktestDay, DailyBacktestReport


def _summarize_day(day: BacktestDay) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "trading_day": day.trading_day.isoformat(),
        "reason": day.reason,
        "candidate_formed": day.candidate_formed,
    }
    if day.cycle is not None:
        summary["consensus"] = day.cycle.consensus
        summary["conflicting_evidence"] = day.cycle.conflicting_evidence
        summary["risk_approved"] = day.cycle.risk_approved
        summary["order_filled"] = day.cycle.order is not None
        if day.cycle.thesis is not None:
            summary["setup_type"] = day.cycle.thesis.candidate.setup_type
            summary["direction"] = day.cycle.thesis.candidate.direction
        if day.cycle.score_attribution is not None:
            summary["regime"] = day.cycle.score_attribution.get("regime")
            summary["confidence"] = day.cycle.score_attribution.get("confidence")
    return summary


def summarize_daily_backtest_report(report: DailyBacktestReport) -> dict[str, Any]:
    return {
        "trading_days_evaluated": report.trading_days_evaluated,
        "candidates_formed": report.candidates_formed,
        "trades_filled": report.trades_filled,
        "days": [_summarize_day(d) for d in report.days],
    }


def write_daily_backtest_report(report: DailyBacktestReport, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = summarize_daily_backtest_report(report)
    output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output
