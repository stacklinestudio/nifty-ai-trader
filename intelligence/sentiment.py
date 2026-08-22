from data.news import NewsItem, aggregate_sentiment


def score(items: list[NewsItem]) -> float:
    return aggregate_sentiment(items)
