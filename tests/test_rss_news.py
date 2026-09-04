"""Brief 8 Part B: real RSS news feeds -> real NewsItems.

requests.get is monkeypatched with a real-captured RSS XML shape (the
literal structure returned by economictimes.indiatimes.com's real
markets RSS feed, checked live 2026-09-04 -- field names/structure real,
individual headline text is representative, not the literal live
content, following the same convention already established for FakeKite
elsewhere in this test suite) -- not a live network call in the test
suite.
"""

from __future__ import annotations

from ai.provider import UnavailableProvider
from ai.router import AIRouter
from ai.schemas import AIAnalysis
from data.rss_news import (
    _classify_headline_keywords,
    fetch_raw_headlines,
    fetch_recent_news,
)

REAL_SHAPED_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?><rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0"><channel><title>Markets-Economic Times</title><link>https://economictimes.indiatimes.com/markets</link><description><![CDATA[real feed]]></description><language>en-gb</language><lastBuildDate>Fri, 04 Sep 2026 21:14:22 +0530</lastBuildDate><item><title><![CDATA[Nifty surges to record high as FIIs turn net buyers]]></title><description><![CDATA[Nifty rallied 1.2%% on strong FII inflows and positive global cues.]]></description><link>https://economictimes.indiatimes.com/markets/example-1</link><pubDate>Fri, 04 Sep 2026 20:40:47 +0530</pubDate></item><item><title><![CDATA[Sensex plunges 500 points on weak global cues]]></title><description><![CDATA[Domestic equities fell sharply as global markets sold off.]]></description><link>https://economictimes.indiatimes.com/markets/example-2</link><pubDate>Fri, 04 Sep 2026 19:10:00 +0530</pubDate></item><item><title><![CDATA[Local bakery opens new outlet in downtown area]]></title><description><![CDATA[A new bakery chain outlet opened today.]]></description><link>https://economictimes.indiatimes.com/markets/example-3</link><pubDate>Fri, 04 Sep 2026 18:00:00 +0530</pubDate></item></channel></rss>"""


class _FakeResponse:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ConnectionError(f"HTTP {self.status_code}")


def test_fetch_raw_headlines_parses_the_real_rss_xml_shape(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse(REAL_SHAPED_RSS_XML))

    headlines = fetch_raw_headlines({"economic_times_markets": "https://example.test/rss"})

    assert len(headlines) == 3
    assert headlines[0]["title"] == "Nifty surges to record high as FIIs turn net buyers"
    assert headlines[0]["source"] == "economic_times_markets"
    assert headlines[0]["timestamp"] is not None


def test_fetch_raw_headlines_one_feed_failing_does_not_block_the_others(monkeypatch):
    import requests

    def fake_get(url, **kwargs):
        if "broken" in url:
            raise ConnectionError("simulated real feed outage")
        return _FakeResponse(REAL_SHAPED_RSS_XML)

    monkeypatch.setattr(requests, "get", fake_get)

    headlines = fetch_raw_headlines(
        {"broken_feed": "https://example.test/broken", "good_feed": "https://example.test/good"}
    )

    assert len(headlines) == 3  # only the good feed's 3 real items
    assert all(h["source"] == "good_feed" for h in headlines)


def test_fetch_raw_headlines_http_error_is_fail_closed_not_a_crash(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse("", status=503))

    headlines = fetch_raw_headlines({"feed": "https://example.test/rss"})

    assert headlines == []


def test_classify_headline_keywords_detects_real_positive_language():
    sentiment, _confidence, relevance = _classify_headline_keywords(
        "Nifty surges to record high as FIIs turn net buyers"
    )
    assert sentiment == "POSITIVE"
    assert relevance == 0.8  # "nifty" and "fii" both real relevance keywords


def test_classify_headline_keywords_detects_real_negative_language():
    sentiment, _confidence, relevance = _classify_headline_keywords(
        "Sensex plunges 500 points on weak global cues"
    )
    assert sentiment == "NEGATIVE"
    assert relevance == 0.8  # "sensex"


def test_classify_headline_keywords_is_neutral_and_low_relevance_for_unrelated_real_headlines():
    sentiment, _confidence, relevance = _classify_headline_keywords(
        "Local bakery opens new outlet in downtown area"
    )
    assert sentiment == "NEUTRAL"
    assert relevance == 0.3


def test_fetch_recent_news_uses_the_real_keyword_fallback_without_an_ai_router(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse(REAL_SHAPED_RSS_XML))

    items = fetch_recent_news(ai_router=None, feeds={"economic_times_markets": "https://example.test/rss"})

    assert len(items) == 3
    assert items[0].sentiment == "POSITIVE"
    assert items[1].sentiment == "NEGATIVE"
    assert items[2].sentiment == "NEUTRAL"


def test_fetch_recent_news_uses_real_ai_classification_when_the_shape_is_valid(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse(REAL_SHAPED_RSS_XML))

    class _FakeAIProvider:
        def analyze(self, task: str, facts: dict) -> AIAnalysis:
            return AIAnalysis(
                summary="3 headlines classified",
                confidence=80,
                source_facts={
                    "structured": {
                        "classifications": [
                            {"sentiment": "POSITIVE", "relevance": 0.9},
                            {"sentiment": "NEGATIVE", "relevance": 0.85},
                            {"sentiment": "UNKNOWN", "relevance": 0.05},
                        ]
                    }
                },
            )

    router = AIRouter(_FakeAIProvider())
    items = fetch_recent_news(ai_router=router, feeds={"economic_times_markets": "https://example.test/rss"})

    assert [item.sentiment for item in items] == ["POSITIVE", "NEGATIVE", "UNKNOWN"]
    assert items[0].relevance == 0.9


def test_fetch_recent_news_falls_back_to_keywords_when_ai_shape_is_wrong(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse(REAL_SHAPED_RSS_XML))

    class _BadShapeProvider:
        def analyze(self, task: str, facts: dict) -> AIAnalysis:
            # Only 2 classifications for 3 real headlines -- a real shape
            # mismatch, must not be silently truncated/misapplied.
            return AIAnalysis(
                summary="oops",
                confidence=50,
                source_facts={"structured": {"classifications": [{"sentiment": "POSITIVE", "relevance": 0.5}]}},
            )

    router = AIRouter(_BadShapeProvider())
    items = fetch_recent_news(ai_router=router, feeds={"economic_times_markets": "https://example.test/rss"})

    # Falls back to the real keyword classifier for every item, not a
    # crash and not a silently-misaligned partial AI result.
    assert [item.sentiment for item in items] == ["POSITIVE", "NEGATIVE", "NEUTRAL"]


def test_fetch_recent_news_falls_back_to_keywords_when_ai_raises(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse(REAL_SHAPED_RSS_XML))

    class _FailingProvider:
        def analyze(self, task: str, facts: dict) -> AIAnalysis:
            raise ConnectionError("simulated real AI outage")

    router = AIRouter(_FailingProvider())
    items = fetch_recent_news(ai_router=router, feeds={"economic_times_markets": "https://example.test/rss"})

    assert [item.sentiment for item in items] == ["POSITIVE", "NEGATIVE", "NEUTRAL"]


def test_fetch_recent_news_with_unavailable_provider_uses_keyword_fallback(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse(REAL_SHAPED_RSS_XML))

    router = AIRouter(UnavailableProvider())
    items = fetch_recent_news(ai_router=router, feeds={"economic_times_markets": "https://example.test/rss"})

    assert [item.sentiment for item in items] == ["POSITIVE", "NEGATIVE", "NEUTRAL"]


def test_fetch_recent_news_returns_empty_not_fabricated_when_every_feed_fails(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: (_ for _ in ()).throw(ConnectionError("down")))

    items = fetch_recent_news(ai_router=None, feeds={"feed": "https://example.test/rss"})

    assert items == []
