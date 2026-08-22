"""Human-readable end-of-session paper-trading audit report."""

from __future__ import annotations

from pathlib import Path

from intelligence.signal_engine import Signal
from storage.models import Trade


def write_daily_report(
    output: Path,
    date_label: str,
    market_summary: str,
    signal: Signal | None,
    trade: Trade | None,
    data_quality: str,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    signal_text = (
        "NO SIGNAL"
        if signal is None
        else f"{signal.direction} / {signal.confidence:.1f}\nReasons: {', '.join(signal.reasons)}\nRisks: {', '.join(signal.risks)}"
    )
    trade_text = (
        "NO TRADE"
        if trade is None
        else (
            f"{trade.symbol} {trade.side} x {trade.quantity}\nEntry: {trade.entry_price:.2f}\n"
            f"Stop: {trade.stop_price:.2f}\nTarget: {trade.target_price:.2f}\nExit: {trade.exit_price}\n"
            f"Gross P&L: {trade.gross_pnl:.2f}\nSlippage: {trade.slippage:.2f}\n"
            f"Estimated costs: {trade.estimated_costs:.2f}\nNet P&L: {trade.net_pnl:.2f}\n"
            f"Exit reason: {trade.exit_reason or 'OPEN'}"
        )
    )
    output.write_text(
        f"# Daily Paper-Trading Report — {date_label}\n\n## Market summary\n{market_summary}\n\n"
        f"## Signal\n{signal_text}\n\n## Trade\n{trade_text}\n\n## Data quality\n{data_quality}\n\n"
        "## Review\nWhat went right: recorded from realised execution only.\n\n"
        "What went wrong: review signal, data freshness, liquidity, and simulated execution.\n",
        encoding="utf-8",
    )
    return output
