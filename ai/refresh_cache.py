"""Brief 10: decouples AI-synthesis refresh cadence from the 60s entry-
scan interval. Global market conditions and news don't meaningfully
change minute to minute, unlike price-based setup detection
(execution/live_context.py's setup dispatcher), which correctly does
re-run every real scan.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ai.provider import AIProvider
from ai.schemas import AIAnalysis
from config import IST


class RefreshingAIRouter:
    """Wraps a real AIProvider (NOT an AIRouter -- deliberately: AIRouter's
    own cache is an exact task+facts match that never expires, which would
    silently defeat this class's timer whenever two scans' facts happen to
    be byte-identical, making "one real call per refresh window" quietly
    become "one real call ever" for that task. Bypassing AIRouter and
    calling analyze()/validate() directly here avoids stacking two
    different, conflicting cache semantics) so a real API call only
    happens once per `refresh_seconds`, per real `task` -- every call
    within that window reuses the last real result, even when this scan's
    `facts` differ from last time's (that's the whole point: this is a
    time-based throttle on real environmental/informational synthesis, not
    an exact-match dedupe).

    Keyed by `task` alone, not `task, facts` -- one cache slot per kind of
    synthesis (global-market commentary, news classification), matching
    exactly the two real per-scan call sites this throttles
    (agents/research_agents.py::GlobalResearchAgent,
    data/rss_news.py::_classify_headlines_with_ai). Deliberately NOT used
    for agents/trading_agents.py::PostTradeAgent's post-trade explanation
    -- each closed trade's own real facts (pnl, outcome, exit reason) are
    genuinely different from the last trade's, so reusing a cached
    explanation across trades within the refresh window would be wrong,
    not just wasteful; PostTradeAgent keeps using the raw, unwrapped
    AIRouter it always has.

    `clock` defaults to the real wall clock (this is a real elapsed-time
    throttle, like an HTTP cache TTL) -- injectable so a test can advance
    through a simulated multi-hour trading day without a real sleep.
    """

    def __init__(
        self,
        provider: AIProvider,
        refresh_seconds: float,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._refresh_seconds = refresh_seconds
        self._clock = clock or (lambda: datetime.now(IST))
        self._cache: dict[str, tuple[datetime, AIAnalysis]] = {}

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        now = self._clock()
        cached = self._cache.get(task)
        if cached is not None:
            last_call, analysis = cached
            if (now - last_call).total_seconds() < self._refresh_seconds:
                return analysis
        # Same real call + validation AIRouter.analyze() itself would do --
        # deliberately not routed through AIRouter (see class docstring).
        analysis = self._provider.analyze(task, dict(facts))
        analysis.validate()
        self._cache[task] = (now, analysis)
        return analysis
