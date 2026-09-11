"""Phase 2 Piece 8, Requirement 4: evidence/ai_evidence.py."""

from __future__ import annotations

from datetime import datetime

from ai.provider import UnavailableProvider
from ai.router import AIRouter
from ai.schemas import AIAnalysis
from config import IST
from evidence.ai_evidence import AI_EVIDENCE_MEMORY_TYPE, build_ai_evidence, record_ai_evidence
from learning.memory import MemoryStore

NOW = datetime(2026, 9, 10, 10, 5, tzinfo=IST)


class _RealShapedProvider:
    """A fake provider standing in for a real, configured Anthropic call
    -- shaped exactly like ai/provider.py::AnthropicProvider's real
    output (summary/confidence/structured), with a real `model`
    attribute, the same one build_ai_evidence reads off any provider."""

    model = "claude-haiku-4-5-20251001"

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        return AIAnalysis(
            "real synthesized summary",
            62.0,
            source_facts={"task": task, "structured": {"metric": "win_rate", "threshold": 55.0}},
        )


class _RaisingProvider:
    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        raise ConnectionError("simulated real AI outage")


def test_a_real_configured_provider_call_is_recorded_as_available_with_provider_and_model():
    router = AIRouter(_RealShapedProvider())

    analysis, evidence = build_ai_evidence(router, "TASK", {"fact": 1}, NOW, correlation_id="CAND-1")

    assert analysis is not None
    assert analysis.summary == "real synthesized summary"
    assert evidence.provider == "_RealShapedProvider"
    assert evidence.model == "claude-haiku-4-5-20251001"
    assert evidence.correlation_id == "CAND-1"
    assert evidence.timestamp == NOW.isoformat()
    assert evidence.available is True
    assert evidence.error is None
    assert evidence.advisory is True
    assert evidence.structured_output == {"metric": "win_rate", "threshold": 55.0}
    assert evidence.summary == "real synthesized summary"
    assert evidence.confidence == 62.0


def test_unavailable_provider_is_recorded_as_genuinely_unavailable_not_a_real_answer():
    """Requirement 11: AI unavailable -> record provider unavailable,
    never treat the safe canned fallback text as a real answer."""
    router = AIRouter(UnavailableProvider())

    analysis, evidence = build_ai_evidence(router, "TASK", {"fact": 1}, NOW)

    assert analysis is not None  # UnavailableProvider.analyze never raises
    assert evidence.provider == "UnavailableProvider"
    assert evidence.model is None
    assert evidence.available is False
    assert evidence.error is not None and "not configured" in evidence.error


def test_a_real_provider_failure_is_recorded_never_fabricated_as_a_success():
    router = AIRouter(_RaisingProvider())

    analysis, evidence = build_ai_evidence(router, "TASK", {"fact": 1}, NOW)

    assert analysis is None
    assert evidence.available is False
    assert evidence.error == "ConnectionError: simulated real AI outage"
    assert evidence.summary is None
    assert evidence.structured_output is None


def test_advisory_is_always_true_regardless_of_outcome():
    ok_router = AIRouter(_RealShapedProvider())
    fail_router = AIRouter(_RaisingProvider())
    unavailable_router = AIRouter(UnavailableProvider())

    _, ok_evidence = build_ai_evidence(ok_router, "T", {}, NOW)
    _, fail_evidence = build_ai_evidence(fail_router, "T", {}, NOW)
    _, unavailable_evidence = build_ai_evidence(unavailable_router, "T", {}, NOW)

    assert ok_evidence.advisory is True
    assert fail_evidence.advisory is True
    assert unavailable_evidence.advisory is True


def test_hypothesis_and_prediction_correlation_fields_are_carried_through_untouched():
    router = AIRouter(_RealShapedProvider())

    _, evidence = build_ai_evidence(
        router, "TASK", {}, NOW, correlation_id="CAND-1", hypothesis_id="HYP-1", prediction={"direction": "CALL"}
    )

    assert evidence.hypothesis_id == "HYP-1"
    assert evidence.prediction == {"direction": "CALL"}


def test_record_ai_evidence_round_trips_through_memory_store(tmp_path):
    store = MemoryStore(tmp_path / "learning.db")
    router = AIRouter(_RealShapedProvider())
    _, evidence = build_ai_evidence(router, "TASK", {}, NOW, correlation_id="CAND-1")

    memory_id = record_ai_evidence(store, evidence, NOW)

    stored = store.recent(memory_type=AI_EVIDENCE_MEMORY_TYPE, limit=5)
    assert len(stored) == 1
    assert stored[0]["memory_id"] == memory_id
    assert stored[0]["payload"]["ai_evidence_id"] == evidence.ai_evidence_id
    assert stored[0]["payload"]["correlation_id"] == "CAND-1"


def test_a_non_dict_structured_field_is_dropped_not_fabricated_into_a_dict():
    class _WeirdStructuredProvider:
        model = "m"

        def analyze(self, task, facts):
            return AIAnalysis("s", 10.0, source_facts={"task": task, "structured": "not a dict"})

    router = AIRouter(_WeirdStructuredProvider())

    _, evidence = build_ai_evidence(router, "TASK", {}, NOW)

    assert evidence.structured_output is None
