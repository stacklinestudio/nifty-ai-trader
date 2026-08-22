from __future__ import annotations

import pandas as pd


def feature_frame(candles: pd.DataFrame) -> pd.DataFrame:
    x = candles.copy()
    x["ema_fast"] = x.close.ewm(span=9, adjust=False).mean()
    x["ema_slow"] = x.close.ewm(span=21, adjust=False).mean()
    delta = x.close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    x["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))
    tr = pd.concat(
        [x.high - x.low, (x.high - x.close.shift()).abs(), (x.low - x.close.shift()).abs()], axis=1
    ).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    typical = (x.high + x.low + x.close) / 3
    x["vwap"] = (typical * x.get("volume", pd.Series(1, index=x.index))).cumsum() / x.get(
        "volume", pd.Series(1, index=x.index)
    ).cumsum()
    x["momentum"] = x.close.pct_change(5)
    return x
