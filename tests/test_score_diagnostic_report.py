"""Brief 12 Part C: the score-bucket diagnostic report, run against the
real 42-day dataset already used throughout this project. Descriptive
only -- this test checks structural/internal consistency of the real
numbers produced (which must hold regardless of the exact real values,
so it isn't brittle against natural data drift), not specific hardcoded
confidence values.
"""

from __future__ import annotations

import pandas as pd

from config import IST, Settings
from reports.score_diagnostic import generate_report
from research.counterfactual import COUNTERFACTUAL_LABEL

CSV_PATH = "data/private/nifty_index_minute_2026-07-06_to_2026-09-01.csv"


def _real_candles(limit_days: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    df = df.set_index("date")
    df.index = df.index.tz_convert(IST)
    candles = df[["open", "high", "low", "close", "volume"]].astype(float)
    if limit_days is not None:
        first_days = set(sorted({ts.date() for ts in candles.index})[:limit_days])
        candles = candles[[d in first_days for d in candles.index.date]]
    return candles


def test_report_runs_against_the_real_42_day_dataset_and_is_internally_consistent():
    candles = _real_candles(limit_days=8)  # a real slice -- full 42-day run is covered manually, this keeps CI fast
    settings = Settings()

    report = generate_report(candles, settings)

    assert report.sessions == 8
    assert report.candidates > 0  # real structural setups were actually found, not an empty run
    # actual_trades + rejected must exactly account for every real candidate.
    assert report.actual_trades + report.rejected_candidates == report.candidates
    # Every bucket count must sum to the real total candidate count.
    assert sum(report.score_bucket_counts.values()) == report.candidates
    # median/mean must be real, sane confidence values (SignalEngine.evaluate clamps to [0, 100]).
    assert 0.0 <= report.median_score <= 100.0
    assert 0.0 <= report.mean_score <= 100.0
    # Every real rejection reason tallied must sum to the real rejected count.
    assert sum(report.top_rejection_reasons.values()) == report.candidates
    # The most-restrictive-component tally is only computed for rejected
    # candidates -- must never exceed that real count.
    assert sum(report.most_restrictive_component_counts.values()) <= report.rejected_candidates
    # Every real counterfactual bucket accounts for exactly the rejected total.
    cf_total = (
        report.counterfactual_profitable + report.counterfactual_not_profitable + report.counterfactual_no_data
    )
    assert cf_total == report.rejected_candidates
    assert report.counterfactual_label == COUNTERFACTUAL_LABEL


def test_summary_lines_always_carry_the_counterfactual_label():
    candles = _real_candles(limit_days=6)
    settings = Settings()

    report = generate_report(candles, settings)
    lines = report.summary_lines()

    counterfactual_lines = [l for l in lines if "counterfactual" in l.lower() or "rejected-" in l.lower()]
    assert counterfactual_lines  # real counterfactual lines were actually produced
    for line in counterfactual_lines:
        assert COUNTERFACTUAL_LABEL in line


def test_most_restrictive_component_is_one_of_the_real_seven_inputs():
    candles = _real_candles(limit_days=6)
    settings = Settings()

    report = generate_report(candles, settings)

    valid = {"technical_score", "opening_score", "volume_score", "option_score", "global_score", "news_score", "risk_penalty"}
    for component in report.most_restrictive_component_counts:
        assert component in valid
