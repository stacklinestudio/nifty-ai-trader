"""Brief 10: GlobalResearchAgent's synthesis and data/rss_news.py's news
classification were both real per-scan AI calls -- fired on every real
60s entry scan, not because global market conditions or news actually
change that often. Proves RefreshingAIRouter genuinely throttles to one
real call per refresh window, across a simulated multi-hour trading day
(no real sleeping), and that the real wiring (Orchestrator.
synthesis_ai_router, not the raw ai_router) is what GlobalResearchAgent
actually holds.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from agents.orchestrator import Orchestrator
from agents.research_agents import GlobalResearchAgent
from ai.refresh_cache import RefreshingAIRouter
from ai.schemas import AIAnalysis
from config import IST, Settings


class _CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, task: str, facts: dict) -> AIAnalysis:
        self.calls += 1
        return AIAnalysis(f"real call #{self.calls}", 50, risks=(), source_facts={"task": task})


def test_refreshing_router_calls_the_real_provider_once_per_refresh_window_not_once_per_scan():
    provider = _CountingProvider()
    ticks = {"n": datetime(2026, 8, 24, 9, 15, tzinfo=IST)}

    def clock():
        return ticks["n"]

    router = RefreshingAIRouter(provider, refresh_seconds=900, clock=clock)

    # A full real trading day's worth of 60s scans (09:15 -> 15:00 =
    # 5h45m = 345 real scan intervals), calling .analyze the same way
    # GlobalResearchAgent._synthesize does every single scan.
    scan_count = 0
    while ticks["n"].time() < time(15, 0):
        router.analyze("GLOBAL_SYNTHESIS", {"SP500": 0.4})
        scan_count += 1
        ticks["n"] += timedelta(seconds=60)

    assert scan_count == 345  # confirms this really did simulate a full scanning day, not a shortcut
    # 5h45m / 15min refresh windows = 23 real windows -- 23 real calls to
    # the underlying provider, not 345.
    assert provider.calls == 23


def test_refreshing_router_makes_a_real_new_call_once_the_window_elapses():
    provider = _CountingProvider()
    ticks = {"n": datetime(2026, 8, 24, 9, 15, tzinfo=IST)}
    router = RefreshingAIRouter(provider, refresh_seconds=900, clock=lambda: ticks["n"])

    first = router.analyze("GLOBAL_SYNTHESIS", {"SP500": 0.4})
    ticks["n"] += timedelta(seconds=899)  # one second short of the window
    still_cached = router.analyze("GLOBAL_SYNTHESIS", {"SP500": 0.9})  # different facts -- still cached
    ticks["n"] += timedelta(seconds=2)  # now past the window
    refreshed = router.analyze("GLOBAL_SYNTHESIS", {"SP500": 0.9})

    assert provider.calls == 2
    assert still_cached.summary == first.summary  # reused despite different facts -- the whole point
    assert refreshed.summary != first.summary


def test_two_different_tasks_are_cached_independently():
    """GlobalResearchAgent's GLOBAL_SYNTHESIS and data/rss_news.py's
    NEWS_CLASSIFICATION share one RefreshingAIRouter instance on
    Orchestrator -- must not collide with or reset each other's window."""
    provider = _CountingProvider()
    ticks = {"n": datetime(2026, 8, 24, 9, 15, tzinfo=IST)}
    router = RefreshingAIRouter(provider, refresh_seconds=900, clock=lambda: ticks["n"])

    router.analyze("GLOBAL_SYNTHESIS", {})
    router.analyze("NEWS_CLASSIFICATION", {})
    ticks["n"] += timedelta(seconds=100)
    router.analyze("GLOBAL_SYNTHESIS", {})
    router.analyze("NEWS_CLASSIFICATION", {})

    assert provider.calls == 2  # one real call per task, not per call


def test_orchestrator_wires_global_research_agent_to_the_throttled_router_not_the_raw_one(tmp_path):
    orchestrator = Orchestrator(Settings(database_path=tmp_path / "paper.db"))
    global_research = next(a for a in orchestrator.research_agents if isinstance(a, GlobalResearchAgent))

    assert global_research.ai_router is orchestrator.synthesis_ai_router
    assert global_research.ai_router is not orchestrator.ai_router
    assert isinstance(orchestrator.synthesis_ai_router, RefreshingAIRouter)
    # PostTradeAgent deliberately keeps the raw, un-throttled router -- each
    # closed trade's own real facts are genuinely different from the last
    # one's; reusing a cached explanation across trades would be wrong.
    assert orchestrator.post_trade_agent.ai_router is orchestrator.ai_router
