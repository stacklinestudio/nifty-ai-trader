"""Brief 12 Part C: the score-bucket diagnostic report. Descriptive only
-- reports real numbers, never recommends a specific new
signal_threshold value (Part A/B's own data is what a future threshold
decision would eventually be made from, not this report).

Replays the real, unmodified execution/live_context.py::assemble_context
(the exact function Part A's score_attribution comes from, and that
agents/orchestrator.py::Orchestrator.run_cycle persists via
Database.save_signal) at real intraday decision points across a real
candle dataset -- no look-ahead, no fabricated data. For every real
candidate that structurally fired but did not clear signal_threshold,
research/counterfactual.py::evaluate_counterfactual is run against the
SAME real day's real subsequent price -- COUNTERFACTUAL, INDEX-PRICE
PROXY, NOT REAL OPTION P&L, exactly as labeled everywhere else that
number is used.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import time as time_of_day

import pandas as pd

from config import Settings
from execution.live_context import OPENING_RANGE_MINUTES, assemble_context
from intelligence.technicals import feature_frame
from research.counterfactual import COUNTERFACTUAL_LABEL, evaluate_counterfactual

# This project's own, already-established real per-component ceilings
# (V2_BUILD_REPORT.md's "the real ceiling isn't one number" analysis) --
# not invented here, reused to identify which component most often costs
# the most real points relative to what it could structurally have
# contributed.
REAL_CEILINGS = {
    "technical_score": 75.0,
    "opening_score": 80.0,
    "volume_score": 100.0,
    "option_score": 75.0,
    "global_score": 80.0,
    "news_score": 40.0,
}
WEIGHTS = {
    "technical_score": 0.35,
    "opening_score": 0.25,
    "volume_score": 0.15,
    "option_score": 0.10,
    "global_score": 0.05,
    "news_score": 0.025,
}


def _most_restrictive_component(attribution: dict) -> str:
    """The single component costing the most real confidence points
    relative to its own real achievable ceiling -- risk_penalty handled
    separately (it subtracts, floor is 0, not a ceiling to fall short of).
    """
    if attribution["risk_penalty"] > 0:
        lost = {"risk_penalty": attribution["risk_penalty"] * 0.125}
    else:
        lost = {}
    for key, ceiling in REAL_CEILINGS.items():
        lost[key] = max(0.0, ceiling - attribution[key]) * WEIGHTS[key]
    return max(lost, key=lost.get)


@dataclass
class DiagnosticReport:
    sessions: int
    candidates: int
    actual_trades: int
    rejected_candidates: int
    score_bucket_counts: dict[str, int]
    median_score: float
    mean_score: float
    top_rejection_reasons: Counter
    most_restrictive_component_counts: Counter
    counterfactual_profitable: int
    counterfactual_not_profitable: int
    counterfactual_no_data: int
    counterfactual_label: str = COUNTERFACTUAL_LABEL

    def summary_lines(self) -> list[str]:
        n = self.candidates
        lines = [
            f"sessions (real trading days scanned): {self.sessions}",
            f"candidates (real structural setups scored): {n}",
            f"actual trades (cleared signal_threshold): {self.actual_trades}",
            f"rejected candidates: {self.rejected_candidates}",
            "score distribution by bucket:",
        ]
        for bucket, count in self.score_bucket_counts.items():
            pct = (count / n * 100) if n else 0.0
            lines.append(f"  {bucket}: {count} ({pct:.1f}%)")
        lines.append(f"median score: {self.median_score:.1f}  mean score: {self.mean_score:.1f}")
        lines.append("top rejection reasons:")
        for reason, count in self.top_rejection_reasons.most_common():
            lines.append(f"  {reason}: {count}")
        lines.append("most restrictive component (real points lost vs. its own real ceiling), by frequency:")
        for component, count in self.most_restrictive_component_counts.most_common():
            lines.append(f"  {component}: {count}")
        total_cf = self.counterfactual_profitable + self.counterfactual_not_profitable
        lines.append(
            f"[{self.counterfactual_label}] rejected-but-counterfactually-profitable: "
            f"{self.counterfactual_profitable}/{total_cf} "
            f"(index-proxy only, never real option P&L)"
        )
        lines.append(
            f"[{self.counterfactual_label}] rejected-and-correctly-avoided (index-proxy): "
            f"{self.counterfactual_not_profitable}/{total_cf}"
        )
        if self.counterfactual_no_data:
            lines.append(
                f"[{self.counterfactual_label}] no real subsequent price left to check: "
                f"{self.counterfactual_no_data} (honestly excluded, not fabricated)"
            )
        return lines


def _score_bucket(score: float) -> str:
    if score < 40:
        return "<40"
    if score < 50:
        return "40-49"
    if score < 60:
        return "50-59"
    if score < 70:
        return "60-69"
    if score < 80:
        return "70-79"
    return "80+"


def generate_report(
    candles: pd.DataFrame, settings: Settings, scan_interval_bars: int = 5, cutoff: time_of_day = time_of_day(15, 0)
) -> DiagnosticReport:
    trading_days = sorted({ts.date() for ts in candles.index})
    attributions: list[dict] = []
    counterfactual_profitable = 0
    counterfactual_not_profitable = 0
    counterfactual_no_data = 0

    for trading_day in trading_days:
        todays_all = candles[candles.index.date == trading_day]
        if len(todays_all) <= OPENING_RANGE_MINUTES:
            continue
        prior = candles[candles.index.date < trading_day]
        if prior.empty:
            continue
        decision_times = [ts for ts in todays_all.index if ts.time() <= cutoff][OPENING_RANGE_MINUTES::scan_interval_bars]
        for now in decision_times:
            as_of = pd.concat([prior, todays_all[todays_all.index <= now]])
            context = assemble_context(as_of, [], float(as_of.iloc[-1].close), now.to_pydatetime(), True, settings)
            attribution = context.get("score_attribution")
            if attribution is None:
                continue
            attributions.append(attribution)

            if not attribution["cleared_threshold"]:
                todays = as_of[as_of.index.date == trading_day]
                remaining = todays_all[todays_all.index > now]
                # The same real close/atr SignalEngine's own technical_score
                # was computed from (execution/live_context.py::
                # _technical_features), not re-derived differently here.
                real_features = feature_frame(as_of).iloc[-1]
                features = {"close": float(real_features["close"]), "atr": float(real_features["atr"])}
                record = evaluate_counterfactual(
                    attribution["setup_type"],
                    attribution["direction"],
                    "confidence_gated",
                    now.to_pydatetime(),
                    todays,
                    remaining,
                    features,
                )
                if record is None:
                    counterfactual_no_data += 1
                elif record.profitable:
                    counterfactual_profitable += 1
                else:
                    counterfactual_not_profitable += 1

    scores = [a["confidence"] for a in attributions]
    bucket_counts: dict[str, int] = {b: 0 for b in ("<40", "40-49", "50-59", "60-69", "70-79", "80+")}
    for s in scores:
        bucket_counts[_score_bucket(s)] += 1

    rejection_reasons = Counter(
        "confidence_gated" if not a["cleared_threshold"] else "cleared_threshold" for a in attributions
    )
    most_restrictive = Counter(
        _most_restrictive_component(a) for a in attributions if not a["cleared_threshold"]
    )

    scores_series = pd.Series(scores) if scores else pd.Series([0.0])
    return DiagnosticReport(
        sessions=len(trading_days),
        candidates=len(attributions),
        actual_trades=sum(1 for a in attributions if a["cleared_threshold"]),
        rejected_candidates=sum(1 for a in attributions if not a["cleared_threshold"]),
        score_bucket_counts=bucket_counts,
        median_score=float(scores_series.median()),
        mean_score=float(scores_series.mean()),
        top_rejection_reasons=rejection_reasons,
        most_restrictive_component_counts=most_restrictive,
        counterfactual_profitable=counterfactual_profitable,
        counterfactual_not_profitable=counterfactual_not_profitable,
        counterfactual_no_data=counterfactual_no_data,
    )
