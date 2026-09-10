"""Phase 2 Piece 5, Requirement 6: the first bounded AI-learning context
interface for the real Anthropic provider.

Reuses the existing ai/router.py::AIRouter / ai/provider.py::
AnthropicProvider / ai/schemas.py::AIAnalysis machinery completely
unmodified -- only a new prompt (ai/prompts.py::AI_LEARNING_CONTEXT_
ANALYSIS) and a new, bounded fact payload. The AI never grades anything
here: every statistic and every per-trade evaluation_result in the
supplied facts was already computed deterministically (learning/
learning_aggregation.py, learning/prediction_review.py) before this
call -- the AI only writes a narrative synthesis over already-true,
already-graded numbers.

Bounded, not arbitrary raw history: recent_learning_events is capped at
MAX_RECENT_LEARNING_EVENTS real records, not the full unbounded
"learning_event" history -- a real, fixed limit, so the size of the
real context sent to the AI does not grow without bound as more trades
close.

Advisory only, by construction: this module has no write path of any
kind into Settings, SignalEngine, RiskAgent, TradeBuilderAgent,
ExecutionAgent, or promotion_engine -- it returns an AIAnalysis (a plain,
read-only dataclass) and nothing calls it automatically from the live
trading path; it exists to be invoked deliberately (a CLI command, a
notebook, a human review), the same way learning/ai_learning_context.py
::analyze_learning_context's caller chooses to.
"""

from __future__ import annotations

from typing import Any

from ai.prompts import AI_LEARNING_CONTEXT_ANALYSIS
from ai.router import AIRouter
from ai.schemas import AIAnalysis
from learning.learning_aggregation import aggregate_all_learning_evidence
from learning.memory import MemoryStore

MAX_RECENT_LEARNING_EVENTS = 20


def build_bounded_ai_learning_context(memory: MemoryStore) -> dict[str, Any]:
    """Pure, deterministic assembly -- no AI call here. Real, structured
    statistics (learning/learning_aggregation.py, already grouped/
    averaged) plus a real, bounded sample of the most recent individual
    learning events, never the full unbounded history."""
    aggregates = aggregate_all_learning_evidence(memory)
    recent_events = [
        e["payload"] for e in memory.recent(memory_type="learning_event", limit=MAX_RECENT_LEARNING_EVENTS)
    ]
    return {
        "aggregated_statistics": [a.to_dict() for a in aggregates],
        "recent_learning_events": recent_events,
        "recent_learning_events_count": len(recent_events),
        "recent_learning_events_bound": MAX_RECENT_LEARNING_EVENTS,
    }


def analyze_learning_context(ai_router: AIRouter, memory: MemoryStore) -> AIAnalysis:
    """The one real AI call this module makes -- advisory only. Returns
    whatever ai_router.analyze() returns (UnavailableProvider's honest
    "not configured" analysis when no real AI is wired, exactly like
    every other real AI call site in this codebase); never raises past
    this point for a normal AI failure, since ai_router.analyze() already
    validates its own result."""
    facts = build_bounded_ai_learning_context(memory)
    return ai_router.analyze(AI_LEARNING_CONTEXT_ANALYSIS, facts)
