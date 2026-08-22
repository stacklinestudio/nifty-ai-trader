def volatility_score(atr: float, price: float) -> float:
    return 0.0 if price <= 0 else min(100.0, 10000 * atr / price)
