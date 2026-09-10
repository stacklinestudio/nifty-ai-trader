"""Phase 2 Piece 4: Experiment -> Backtest -> Promotion.

Connects an AI-proposed hypothesis (learning.hypothesis.HypothesisCondition,
Piece 2) to REAL backtest evidence, then to the existing, completely
unmodified learning.promotion_engine.decide() -- never AI opinion.

Honest, explicit scope (per this phase's own brief, and matching what
Phase 1's own audit already found): the real agent/orchestrator-based
backtest (backtest/daily_backtest.py) evaluates whether a real historical
candidate/entry would have FORMED for a given setup_type+regime
combination -- it does not (yet) simulate a full intraday exit/P&L
outcome for that entry; no code anywhere in this repository does that
today. So has_historical/has_walk_forward/has_out_of_sample here measure
real STRUCTURAL robustness -- does this setup+regime combination
genuinely and repeatably produce real candidates across real historical
time, not clustered noise in one narrow window -- using the real,
existing backtest pipeline (backtest/daily_backtest.py, backtest/
daily_walk_forward.py). Real WIN/LOSS evidence (does it actually make
money) continues to come from learning.hypothesis.evaluate_hypothesis
against REAL LIVE PAPER TRADES (learning.pattern_memory.stats_for) -- a
genuinely different, complementary real evidence source, not a backtest
simulation. has_out_of_sample below requires BOTH: real out-of-sample
structural evidence AND the hypothesis having already genuinely passed
against real live outcomes -- promotion cannot proceed on backtest
structure alone, and cannot proceed on a live win rate alone either.
"""

from __future__ import annotations

import dataclasses
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.daily_backtest import DailyBacktestReport, run_daily_backtest
from backtest.daily_walk_forward import run_daily_backtest_walk_forward
from config import Settings
from learning.hypothesis import HypothesisCondition, HypothesisEvaluation, evaluate_hypothesis
from learning.memory import MemoryStore
from learning.promotion_engine import PromotionDecision, decide

# A real, explicit floor -- not zero (which would let a single lucky
# historical candidate satisfy "has_historical"), not large enough to be
# practically unreachable early in this project's real historical window.
MIN_REAL_CANDIDATES_FOR_STRUCTURAL_EVIDENCE = 3


def _matching_candidate_count(report: DailyBacktestReport, condition: HypothesisCondition) -> int:
    """Deliberately keyed on day.candidate_formed + score_attribution
    (always present on every real cycle that formed a candidate -- see
    execution/live_context.py::_add_candidate, set unconditionally before
    the option/thesis-building stage even runs), NOT on day.cycle.thesis.

    A real bug found and fixed while writing this module's own tests:
    the real 42-day/248-day historical datasets (and any real historical
    replay run without a full option-chain archive -- confirmed absent
    for both by Phase 1's own audit) never populate option_quotes_by_day,
    so OptionsAgent never selects a contract and cycle.thesis stays None
    on every real day, regardless of how many real candidates actually
    formed. Requiring thesis is not None here would have made
    has_historical permanently False against the exact real data this
    function exists to evaluate -- an orthogonal concern (real option
    data availability) this structural-candidate-formation gate must not
    depend on.
    """
    return sum(
        1
        for day in report.days
        if day.candidate_formed
        and day.cycle is not None
        and day.cycle.score_attribution is not None
        and day.cycle.score_attribution.get("setup_type") == condition.setup_type
        and day.cycle.score_attribution.get("regime") == condition.regime
    )


@dataclass(frozen=True)
class StructuralBacktestEvidence:
    historical_candidates: int
    train_candidates: int
    validation_candidates: int
    out_of_sample_candidates: int
    has_historical: bool
    has_walk_forward: bool
    has_out_of_sample: bool

    def to_dict(self) -> dict:
        return {
            "historical_candidates": self.historical_candidates,
            "train_candidates": self.train_candidates,
            "validation_candidates": self.validation_candidates,
            "out_of_sample_candidates": self.out_of_sample_candidates,
            "has_historical": self.has_historical,
            "has_walk_forward": self.has_walk_forward,
            "has_out_of_sample": self.has_out_of_sample,
        }


def gather_structural_backtest_evidence(
    settings: Settings, all_candles: pd.DataFrame, condition: HypothesisCondition
) -> StructuralBacktestEvidence:
    """Real, deterministic backtest evidence -- see this module's own
    docstring for exactly what this does and does not measure.

    run_daily_backtest constructs a real Orchestrator/Database per
    simulated day and genuinely persists to whatever settings.
    database_path resolves to -- a real incident this session found the
    hard way testing this exact code path. Structural, not caller
    discipline: this function ALWAYS runs the real backtest replay
    against an isolated, deleted-after-call scratch database, regardless
    of what settings.database_path the caller's own `settings` carries
    (e.g. the live settings.database_path used elsewhere in the same
    process) -- every other real field on `settings` (signal_threshold,
    capital, etc.) is preserved unchanged via dataclasses.replace."""
    with tempfile.TemporaryDirectory() as scratch_dir:
        backtest_settings = dataclasses.replace(
            settings, database_path=Path(scratch_dir) / "promotion_backtest_scratch.db"
        )
        full_report = run_daily_backtest(backtest_settings, all_candles)
        walk = run_daily_backtest_walk_forward(backtest_settings, all_candles)

    historical_candidates = _matching_candidate_count(full_report, condition)
    train_candidates = _matching_candidate_count(walk.train, condition)
    validation_candidates = _matching_candidate_count(walk.validation, condition)
    out_of_sample_candidates = _matching_candidate_count(walk.out_of_sample, condition)

    return StructuralBacktestEvidence(
        historical_candidates=historical_candidates,
        train_candidates=train_candidates,
        validation_candidates=validation_candidates,
        out_of_sample_candidates=out_of_sample_candidates,
        has_historical=historical_candidates >= MIN_REAL_CANDIDATES_FOR_STRUCTURAL_EVIDENCE,
        has_walk_forward=(train_candidates >= 1 and validation_candidates >= 1),
        has_out_of_sample=out_of_sample_candidates >= 1,
    )


@dataclass(frozen=True)
class ExperimentPromotionResult:
    condition: HypothesisCondition
    structural_evidence: StructuralBacktestEvidence
    outcome_evidence: HypothesisEvaluation
    decision: PromotionDecision

    def to_dict(self) -> dict:
        return {
            "condition": self.condition.to_dict(),
            "structural_evidence": self.structural_evidence.to_dict(),
            "outcome_evidence": self.outcome_evidence.to_dict(),
            "decision": {"promote": self.decision.promote, "reasons": list(self.decision.reasons)},
        }


def evaluate_experiment_for_promotion(
    settings: Settings,
    all_candles: pd.DataFrame,
    memory: MemoryStore,
    condition: HypothesisCondition,
    human_approved: bool,
    now: datetime,
) -> ExperimentPromotionResult:
    """The one real bridge from an AI-proposed hypothesis to
    learning.promotion_engine.decide() -- called here completely
    unmodified. Every boolean fed into it is real, deterministic
    evidence: has_historical/has_walk_forward from the real backtest
    pipeline above; has_out_of_sample additionally requires the
    hypothesis to have already genuinely passed against real live
    outcomes (never the AI's own claim). Nothing here writes to any live
    strategy parameter -- this only ever returns a decision for a human
    to act on."""
    structural = gather_structural_backtest_evidence(settings, all_candles, condition)
    outcome = evaluate_hypothesis(condition, memory, now)
    has_out_of_sample = structural.has_out_of_sample and outcome.passed is True
    decision = decide(structural.has_historical, structural.has_walk_forward, has_out_of_sample, human_approved)
    return ExperimentPromotionResult(condition, structural, outcome, decision)
