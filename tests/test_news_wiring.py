from __future__ import annotations

from datetime import datetime

from agents.research_agents import NewsAgent, SignalHunterAgent
from config import IST
from data.news import NewsItem


def item(sentiment: str, confidence: float = 0.8, relevance: float = 0.9) -> NewsItem:
    return NewsItem(datetime.now(IST), "headline", "test-source", relevance, sentiment, confidence)


def test_news_agent_reports_unknown_direction_without_items():
    review = NewsAgent().run({})
    assert review.data["direction"] == "UNKNOWN" and review.confidence == 0


def test_news_agent_classifies_real_bullish_sentiment():
    review = NewsAgent().run({"news_items": [item("POSITIVE"), item("POSITIVE")]})
    assert review.data["direction"] == "BULLISH"
    assert review.data["classification"] == "BULLISH"
    assert review.confidence > 0


def test_news_agent_classifies_real_bearish_sentiment():
    review = NewsAgent().run({"news_items": [item("NEGATIVE"), item("NEGATIVE")]})
    assert review.data["direction"] == "BEARISH"


def test_news_agent_confidence_is_capped_below_other_agents():
    review = NewsAgent().run({"news_items": [item("POSITIVE", confidence=1.0, relevance=1.0)]})
    assert review.confidence <= 40.0


def test_signal_hunter_news_nudge_is_bounded_and_cannot_flip_low_confidence_to_high():
    base_context = {
        "candidate_direction": "CALL",
        "candidate_confidence": 10,  # deliberately low
        "setup_type": "OPENING_STRUCTURE",
    }
    unnudged = SignalHunterAgent().run(base_context).data["candidates"][0].confidence
    nudged = SignalHunterAgent().run(
        {**base_context, "news_direction": "BULLISH", "news_confidence": 40}
    ).data["candidates"][0].confidence

    assert nudged > unnudged  # aligned news does shade confidence up...
    assert nudged <= unnudged * 1.05 + 1e-9  # ...but by at most 5%, never enough to "flip" it
    assert nudged < 50  # nowhere close to turning a weak candidate strong


def test_signal_hunter_contradicting_news_shades_confidence_down_not_to_zero():
    base_context = {
        "candidate_direction": "CALL",
        "candidate_confidence": 80,
        "setup_type": "OPENING_STRUCTURE",
    }
    unnudged = SignalHunterAgent().run(base_context).data["candidates"][0].confidence
    nudged = SignalHunterAgent().run(
        {**base_context, "news_direction": "BEARISH", "news_confidence": 40}
    ).data["candidates"][0].confidence

    assert 0 < nudged < unnudged
