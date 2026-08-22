from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Sentiment = Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "UNKNOWN"]


@dataclass(frozen=True)
class NewsItem:
    timestamp: datetime
    headline: str
    source: str
    relevance: float
    sentiment: Sentiment
    confidence: float


def aggregate_sentiment(items: list[NewsItem]) -> float:
    """News is a bounded supporting feature, never a trade trigger."""
    if not items:
        return 0.0
    weights = {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0, "UNKNOWN": 0.0}
    return sum(weights[i.sentiment] * i.confidence * i.relevance for i in items) / len(items)
