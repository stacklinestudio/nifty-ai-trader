"""Only validated, structured facts can leave an AI provider boundary."""

from __future__ import annotations

import json
from typing import Any, Protocol

import requests

from ai.prompts import SYSTEM_PROMPT
from ai.schemas import AIAnalysis
from config import Settings
from monitoring.logger import configure_logger

logger = configure_logger(__name__)


class AIProvider(Protocol):
    def analyze(self, task: str, facts: dict) -> AIAnalysis: ...


class UnavailableProvider:
    """Safe default that makes unavailable AI explicit rather than fabricating insight."""

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        return AIAnalysis(
            "AI provider is not configured; deterministic workflow only.",
            0,
            risks=("AI unavailable",),
            source_facts={"task": task, "fact_count": len(facts)},
        )


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
# A real, hard per-call bound -- this is the actual protection against a
# hung/slow network call blocking the trading pipeline; agents/base.py::
# BaseAgent's own timeout_seconds is only a post-hoc check *after* a call
# returns, so it alone cannot stop a call that's still in flight. Callers
# outside BaseAgent's wrapping (data/rss_news.py's classification step)
# have no other timeout protection at all, so this must hold regardless
# of caller.
ANTHROPIC_REQUEST_TIMEOUT_SECONDS = 15


class AnthropicProvider:
    """Real AI provider (Brief 8 Part C) -- calls the Anthropic Messages
    API directly via `requests` (already a dependency; no new SDK added).

    Enrichment only, by construction: `analyze()` returns a validated
    AIAnalysis (summary/confidence/risks/structured facts) and nothing
    else -- there is no method here, or anywhere this class is used, that
    can set a position size, approve a trade, or place an order. See each
    calling agent's own docstring (agents/research_agents.py::
    GlobalResearchAgent, agents/trading_agents.py::PostTradeAgent,
    data/rss_news.py) for exactly where this output is and is not read.

    Fails by raising -- callers (ai/router.py::AIRouter callers) are
    expected to catch and fall back to UnavailableProvider's behavior;
    this class never silently returns a fabricated/placeholder analysis
    to paper over a real failure.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": f"Task: {task}\n\nFacts (JSON):\n{json.dumps(facts, default=str)}",
                }
            ],
        }
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            json=payload,
            timeout=ANTHROPIC_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        text = "".join(
            block.get("text", "") for block in body.get("content", []) if block.get("type") == "text"
        )
        parsed = _parse_json_response(text)
        summary = str(parsed.get("summary", ""))
        confidence = float(parsed.get("confidence", 0) or 0)
        risks = tuple(str(r) for r in parsed.get("risks", []) if isinstance(r, (str, int, float)))
        structured = parsed.get("structured", {})
        if not isinstance(structured, dict):
            structured = {}
        return AIAnalysis(
            summary=summary,
            confidence=max(0.0, min(100.0, confidence)),
            risks=risks,
            source_facts={"task": task, "fact_count": len(facts), "structured": structured},
        )


def _parse_json_response(text: str) -> dict[str, Any]:
    """Claude sometimes wraps JSON in a markdown code fence even when
    told not to -- stripped here defensively. Raises (not fabricates) on
    genuinely unparseable output, so the caller's own fallback path runs
    instead of a fake/empty analysis silently passing as real.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.removeprefix("json")
        stripped = stripped.strip()
    return json.loads(stripped)


def build_ai_provider(settings: Settings) -> AIProvider:
    """Settings.ai_provider stays "unavailable" (the real default, see
    config.py) until explicitly flipped by hand in .env.local -- never
    flipped programmatically anywhere in this codebase. Missing
    Settings.anthropic_api_key with ai_provider="anthropic" also falls
    back to UnavailableProvider (fail closed, not a crash at startup).
    """
    if settings.ai_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key, settings.ai_model)
    return UnavailableProvider()
