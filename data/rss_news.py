"""Real RSS news feeds -- free, no API key, no signup friction (Brief 8
Part B), matching the free-news-source comparison already researched
with real pricing in Brief 5 Part C.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from ai.prompts import NEWS_CLASSIFICATION
from ai.router import AIRouter
from config import IST
from data.news import NewsItem, Sentiment
from monitoring.logger import configure_logger

logger = configure_logger(__name__)

# Real, confirmed-reachable RSS feeds (checked live, 2026-09-04, HTTP 200
# with real XML content). Reuters' public RSS feeds are discontinued
# (feeds.reuters.com no longer resolves) -- Business Standard substituted,
# matching the brief's own "or similar" latitude.
RSS_FEEDS: dict[str, str] = {
    "economic_times_markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "moneycontrol_business": "https://www.moneycontrol.com/rss/business.xml",
    "business_standard_markets": "https://www.business-standard.com/rss/markets-106.rss",
}

RSS_TIMEOUT_SECONDS = 10
MAX_ITEMS_PER_FEED = 5  # keeps a real batched AI classification call (one request, all headlines) small
_USER_AGENT = "Mozilla/5.0 (compatible; NiftyAITraderResearch/1.0)"

_POSITIVE_KEYWORDS = (
    "surge", "rally", "gain", "gains", "jump", "jumps", "soar", "soars", "record high",
    "beat estimates", "beats estimates", "upgrade", "upgraded", "outperform", "bullish",
    "rise", "rises", "rising", "profit up", "profit rises", "growth", "buy rating",
)
_NEGATIVE_KEYWORDS = (
    "crash", "crashes", "plunge", "plunges", "fall", "falls", "falling", "drop", "drops",
    "decline", "declines", "loss", "losses", "downgrade", "downgraded", "underperform",
    "bearish", "slump", "slumps", "miss estimates", "misses estimates", "recession",
    "selloff", "sell-off", "sell off",
)
_RELEVANCE_KEYWORDS = (
    "nifty", "sensex", "bse", "nse", "rbi", "rupee", "indian market", "india market",
    "dalal street", "fii", "dii",
)


def _parse_feed(xml_text: str, source: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        description = (item.findtext("description") or "").strip()
        pub_date_raw = item.findtext("pubDate")
        timestamp = None
        if pub_date_raw:
            try:
                timestamp = parsedate_to_datetime(pub_date_raw)
            except (TypeError, ValueError, IndexError) as exc:
                logger.warning("rss_pubdate_unparseable source=%s value=%s error=%s", source, pub_date_raw, exc)
        items.append({"title": title, "description": description, "timestamp": timestamp, "source": source})
    return items[:MAX_ITEMS_PER_FEED]


def fetch_raw_headlines(feeds: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Real headlines from each real RSS feed, independently fail-closed:
    one feed's real failure (network error, malformed XML, HTTP error)
    does not block the others -- logged, never silently dropped, never
    fabricated.
    """
    feeds = feeds if feeds is not None else RSS_FEEDS
    headlines: list[dict[str, Any]] = []
    for source, url in feeds.items():
        try:
            response = requests.get(url, timeout=RSS_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
            headlines.extend(_parse_feed(response.text, source))
        except Exception as exc:  # noqa: BLE001 - one real feed's failure must not block the others.
            logger.warning("rss_feed_fetch_failed source=%s error=%s", source, exc)
    return headlines


def _classify_headline_keywords(headline: str) -> tuple[Sentiment, float, float]:
    """Real, deterministic fallback classifier -- used when no AI
    provider is configured/available, or when the AI classification call
    fails for any reason. Simple keyword matching, honestly documented as
    such (not claiming sophistication it doesn't have); this is the "same
    system trades, less rich synthesis" floor Brief 8 Part C.6 requires.
    """
    text = headline.lower()
    positive_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
    negative_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)
    relevant = any(kw in text for kw in _RELEVANCE_KEYWORDS)
    relevance = 0.8 if relevant else 0.3
    if positive_hits > negative_hits:
        return "POSITIVE", min(1.0, 0.3 + 0.15 * positive_hits), relevance
    if negative_hits > positive_hits:
        return "NEGATIVE", min(1.0, 0.3 + 0.15 * negative_hits), relevance
    return "NEUTRAL", 0.2, relevance


def _classify_headlines_with_ai(
    headlines: list[dict[str, Any]], ai_router: AIRouter
) -> list[tuple[Sentiment, float, float]] | None:
    """One real, batched AI call classifying every headline at once
    (not one call per headline -- avoids N sequential API calls for N
    headlines). Returns None (not a fabricated classification) if the
    call fails or the response doesn't have exactly one classification
    per input headline -- the caller falls back to the real deterministic
    keyword classifier per-headline in that case.
    """
    facts = {
        "headlines": [
            {"headline": h["title"], "source": h["source"], "description": h["description"][:300]}
            for h in headlines
        ]
    }
    analysis = ai_router.analyze(NEWS_CLASSIFICATION, facts)
    classifications = analysis.source_facts.get("structured", {}).get("classifications")
    if not isinstance(classifications, list) or len(classifications) != len(headlines):
        logger.warning(
            "ai_headline_classification_shape_mismatch expected=%d got=%s",
            len(headlines),
            type(classifications).__name__ if classifications is not None else "missing",
        )
        return None
    valid_sentiments = {"POSITIVE", "NEGATIVE", "NEUTRAL", "UNKNOWN"}
    results: list[tuple[Sentiment, float, float]] = []
    for entry in classifications:
        if not isinstance(entry, dict) or entry.get("sentiment") not in valid_sentiments:
            return None
        relevance = entry.get("relevance", 0.0)
        try:
            relevance = max(0.0, min(1.0, float(relevance)))
        except (TypeError, ValueError):
            relevance = 0.0
        # AI classification carries the same confidence weight as a
        # confirmed keyword match (0.6) -- a real, bounded value, not the
        # model's own self-reported confidence (which this function
        # never reads), keeping the eventual NewsAgent confidence cap (40)
        # and SignalHunterAgent's +/-5% nudge cap the same real backstop
        # regardless of which classifier produced the sentiment.
        results.append((entry["sentiment"], 0.6, relevance))
    return results


def fetch_recent_news(ai_router: AIRouter | None = None, feeds: dict[str, str] | None = None) -> list[NewsItem]:
    """Real RSS headlines -> real NewsItems. Tries one batched real AI
    classification call first (if ai_router is given and configured);
    falls back to the real deterministic keyword classifier per-headline
    on any AI failure or shape mismatch -- never a crash, never a
    fabricated classification, same real aggregate_sentiment/NewsAgent
    pipeline either way.
    """
    raw_headlines = fetch_raw_headlines(feeds)
    if not raw_headlines:
        return []

    classifications: list[tuple[Sentiment, float, float]] | None = None
    if ai_router is not None:
        try:
            classifications = _classify_headlines_with_ai(raw_headlines, ai_router)
        except Exception as exc:  # noqa: BLE001 - any AI failure falls back to the real keyword classifier below, never a crash.
            logger.warning("ai_headline_classification_failed error=%s", exc)
            classifications = None

    items = []
    for i, raw in enumerate(raw_headlines):
        if classifications is not None:
            sentiment, confidence, relevance = classifications[i]
        else:
            sentiment, confidence, relevance = _classify_headline_keywords(raw["title"])
        items.append(
            NewsItem(
                timestamp=raw["timestamp"] or datetime.now(IST),
                headline=raw["title"],
                source=raw["source"],
                relevance=relevance,
                sentiment=sentiment,
                confidence=confidence,
            )
        )
    return items
