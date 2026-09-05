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
from dataclasses import dataclass, field
from datetime import date, timedelta
from datetime import time as time_of_day

import pandas as pd

from config import Settings
from data.global_market import ContextValue
from execution.live_context import (
    OPENING_RANGE_MINUTES,
    TECHNICAL_FEATURE_WINDOW_DAYS,
    assemble_context,
)
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
    # Brief 13 Part B: report-only -- does NOT change how a candidate is
    # scored or rejected here or anywhere else.
    mean_data_completeness: float = 0.0
    median_data_completeness: float = 0.0
    data_completeness_distinct_values: Counter[float] = field(default_factory=Counter)
    confidence_completeness_correlation: float | None = None
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
        lines.append(
            f"data_completeness: median={self.median_data_completeness:.1f}% "
            f"mean={self.mean_data_completeness:.1f}%"
        )
        lines.append("data_completeness real distinct values (Brief 13 Part A, report-only):")
        for value, count in sorted(self.data_completeness_distinct_values.items()):
            pct = (count / self.candidates * 100) if self.candidates else 0.0
            lines.append(f"  {value:.1f}%: {count} ({pct:.1f}%)")
        if self.confidence_completeness_correlation is None:
            lines.append(
                "confidence vs. data_completeness correlation: UNDEFINED -- data_completeness has "
                "zero real variance in this dataset (every candidate has the same real completeness), "
                "so no correlation coefficient is computable, not silently reported as 0 or omitted."
            )
        else:
            lines.append(
                f"confidence vs. data_completeness correlation (Pearson r): "
                f"{self.confidence_completeness_correlation:.4f}"
            )
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
    candles: pd.DataFrame,
    settings: Settings,
    scan_interval_bars: int = 5,
    cutoff: time_of_day = time_of_day(15, 0),
    global_context_by_day: dict[date, list[ContextValue]] | None = None,
) -> DiagnosticReport:
    """global_context_by_day (fix, found via a real reported discrepancy
    between this function's own 42.9% data_completeness figure and a
    separate 57.14% figure from backtest/daily_backtest.py::
    run_daily_backtest on the identical real window): matches
    run_daily_backtest's own real parameter exactly -- real historical
    global-market data per real day, e.g. from
    data/global_market.py::fetch_global_history. Defaults to None/{} -- a
    day with no entry reads as [] (unavailable), never fabricated. Before
    this fix, this function had NO parameter for global-market data at
    all, so global_score was structurally unavailable for every single
    candidate regardless of whether real data existed for that day --
    not a live-vs-backtest inconsistency, a real gap in this specific
    report generator, now closed.
    """
    global_context_by_day = global_context_by_day or {}
    trading_days = sorted({ts.date() for ts in candles.index})
    attributions: list[dict] = []
    counterfactual_profitable = 0
    counterfactual_not_profitable = 0
    counterfactual_no_data = 0

    for trading_day in trading_days:
        todays_all = candles[candles.index.date == trading_day]
        if len(todays_all) <= OPENING_RANGE_MINUTES:
            continue
        # Real existence check stays unbounded (is there ANY real prior
        # day at all -- only ever false for the dataset's very first
        # day(s)); the `prior` actually fed into feature computation
        # below is bounded to TECHNICAL_FEATURE_WINDOW_DAYS, the same
        # real window execution/live_context.py::build_live_context
        # fetches for the live path -- see that constant's own docstring
        # for the real empirical proof this doesn't change any computed
        # value, only the cost of computing it.
        if candles[candles.index.date < trading_day].empty:
            continue
        window_start = pd.Timestamp(trading_day, tz=candles.index.tz) - timedelta(days=TECHNICAL_FEATURE_WINDOW_DAYS)
        prior = candles[(candles.index.date < trading_day) & (candles.index >= window_start)]
        decision_times = [ts for ts in todays_all.index if ts.time() <= cutoff][OPENING_RANGE_MINUTES::scan_interval_bars]
        todays_global_context = global_context_by_day.get(trading_day, [])
        for now in decision_times:
            as_of = pd.concat([prior, todays_all[todays_all.index <= now]])
            context = assemble_context(
                as_of,
                [],
                float(as_of.iloc[-1].close),
                now.to_pydatetime(),
                True,
                settings,
                previous_option_quotes=None,
                global_context=todays_global_context,
            )
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

    # Brief 13 Part B: real, report-only -- does not touch scoring or
    # rejection. round()'d to 1 decimal for the distinct-value tally since
    # data_completeness is always n/7 * 100 for a real n in [0, 7], so
    # float rounding noise (e.g. 71.42857142857143) would otherwise
    # fragment what is really one real value into several near-duplicate
    # buckets.
    completeness = [round(a["data_completeness"], 1) for a in attributions]
    completeness_series = pd.Series(completeness) if completeness else pd.Series([0.0])
    completeness_distinct = Counter(completeness)
    correlation = None
    if len(completeness_distinct) > 1:
        raw_correlation = pd.Series(scores).corr(pd.Series(completeness))
        if pd.notna(raw_correlation):
            correlation = float(raw_correlation)

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
        mean_data_completeness=float(completeness_series.mean()),
        median_data_completeness=float(completeness_series.median()),
        data_completeness_distinct_values=completeness_distinct,
        confidence_completeness_correlation=correlation,
    )
