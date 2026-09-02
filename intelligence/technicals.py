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
    x["vwap"] = _session_vwap(x)
    x["momentum"] = x.close.pct_change(5)
    return x


def _session_vwap(x: pd.DataFrame) -> pd.Series:
    """Real session-anchored VWAP (resets each trading day -- VWAP is a
    single-session concept, not a multi-day cumulative one) that degrades
    to a real, honest cumulative average of typical price -- never a
    fabricated value, never a silent NaN -- when real volume is
    genuinely zero for that session. NIFTY 50 index volume from Kite is
    structurally always 0 (confirmed against the real 42-day captured
    dataset, Brief 5) -- price*0/0 previously produced NaN every single
    bar, which _technical_features' own NaN->0.0 fallback quietly turned
    into a permanent vwap=0.0 that always sat below any real positive
    price. That silently made every "close > vwap" bullish read (both
    here and in agents/research_agents.py::TechnicalAgent) vacuously true
    regardless of real price action -- a real, pre-existing correctness
    bug this fix corrects, not a new indicator being invented.
    """
    typical = (x.high + x.low + x.close) / 3
    volume = x.get("volume", pd.Series(1, index=x.index)).astype(float)
    session = pd.Series(x.index.date, index=x.index)
    cum_volume = volume.groupby(session).cumsum()
    weighted = (typical * volume).groupby(session).cumsum() / cum_volume.replace(0, float("nan"))
    unweighted = typical.groupby(session).cumsum() / pd.Series(1, index=x.index).groupby(
        session
    ).cumsum()
    return weighted.where(cum_volume > 0, unweighted)
