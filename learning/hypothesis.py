"""Phase 2 Piece 2: AI hypothesis -> Experiment Lab.

The real Anthropic provider proposes a hypothesis; this module is the
ONLY place that hypothesis's real success/failure is ever decided --
deterministically, by re-running learning/pattern_memory.py::stats_for
(the same real, already-tested aggregation over real recorded trades)
and comparing the real measured value against the AI-proposed threshold
with a real, fixed-set comparison operator. The AI proposes the test; it
never grades its own exam -- there is no code path anywhere that reads
an AI-claimed pass/fail and trusts it.

parse_hypothesis_condition is deliberately strict: every field is
validated against a real, fixed allowlist (real setup_type strings from
execution/live_context.py's own detectors, real Regime values, a small
real operator set) and any malformed/out-of-range field returns None --
never a partially-fabricated condition built from whatever the AI
happened to send.
"""

from __future__ import annotations

import operator as _operator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from intelligence.market_regime import Regime
from learning.memory import MemoryStore
from learning.pattern_memory import stats_for

ALLOWED_METRICS = ("win_rate", "expectancy")

# Real, fixed comparison set -- never eval()'d or otherwise dynamically
# resolved from AI-supplied text.
ALLOWED_OPERATORS: dict[str, Any] = {
    ">=": _operator.ge,
    "<=": _operator.le,
    ">": _operator.gt,
    "<": _operator.lt,
}

# The real setup_type strings execution/live_context.py's own detectors
# produce (see its _select_setup docstring) -- not re-derived here to
# avoid a real circular import (execution.live_context transitively
# imports agents.orchestrator, which imports this module for
# PostTradeAgent); kept as a literal, explicitly cross-referenced list so
# a future new setup type is a visible two-file change, not a silent gap.
ALLOWED_SETUP_TYPES = (
    "OPENING_RANGE_BREAKOUT",
    "TREND_CONTINUATION",
    "MOMENTUM_CONTINUATION",
    "VWAP_BREAKOUT",
    "VWAP_REJECTION",
    "SUPPORT_RESISTANCE_REACTION",
)

ALLOWED_REGIMES = tuple(regime.value for regime in Regime)


@dataclass(frozen=True)
class HypothesisCondition:
    metric: str
    setup_type: str
    regime: str
    operator: str
    threshold: float
    min_samples: int
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "setup_type": self.setup_type,
            "regime": self.regime,
            "operator": self.operator,
            "threshold": self.threshold,
            "min_samples": self.min_samples,
            "rationale": self.rationale,
        }


def parse_hypothesis_condition(structured: dict[str, Any] | None) -> HypothesisCondition | None:
    """Strictly validates the AI's raw `structured` response (see
    ai/prompts.py::POST_TRADE_HYPOTHESIS) -- returns None, never a
    partially-fabricated object, on any missing/malformed/out-of-
    allowlist field, or when the AI genuinely proposed nothing."""
    if not isinstance(structured, dict) or not structured:
        return None
    try:
        metric = str(structured["metric"])
        setup_type = str(structured["setup_type"])
        regime = str(structured["regime"])
        op = str(structured["operator"])
        threshold = float(structured["threshold"])
        min_samples = int(structured["min_samples"])
        rationale = str(structured.get("rationale", ""))[:1000]
    except (KeyError, TypeError, ValueError):
        return None
    if (
        metric not in ALLOWED_METRICS
        or setup_type not in ALLOWED_SETUP_TYPES
        or regime not in ALLOWED_REGIMES
        or op not in ALLOWED_OPERATORS
        or min_samples <= 0
    ):
        return None
    return HypothesisCondition(metric, setup_type, regime, op, threshold, min_samples, rationale)


@dataclass(frozen=True)
class HypothesisEvaluation:
    condition: HypothesisCondition
    passed: bool | None
    actual_value: float | None
    sample_size: int
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition.to_dict(),
            "passed": self.passed,
            "actual_value": self.actual_value,
            "sample_size": self.sample_size,
            "evaluated_at": self.evaluated_at,
        }


def evaluate_hypothesis(
    condition: HypothesisCondition, memory: MemoryStore, now: datetime
) -> HypothesisEvaluation:
    """The ONLY place a hypothesis's pass/fail is decided. Purely
    deterministic: re-runs the same real learning/pattern_memory.py::
    stats_for aggregation this project already uses elsewhere, never the
    AI's own claim about its hypothesis. passed=None (never a guessed
    True/False) whenever the real accumulated sample size hasn't reached
    the hypothesis's own min_samples yet -- honestly undetermined, not a
    fabricated verdict."""
    stats = stats_for(memory, condition.setup_type, condition.regime)
    actual_value = stats.win_rate if condition.metric == "win_rate" else stats.expectancy
    if stats.sample_size < condition.min_samples or actual_value is None:
        return HypothesisEvaluation(condition, None, actual_value, stats.sample_size, now.isoformat())
    passed = bool(ALLOWED_OPERATORS[condition.operator](actual_value, condition.threshold))
    return HypothesisEvaluation(condition, passed, actual_value, stats.sample_size, now.isoformat())
