"""FII/DII net institutional flow -- a real, legitimate, commonly-used
directional-bias input, published daily by NSE (T+1, after market close).
This is morning context, not an intraday feed: it does not update through
the trading day, and is fed to GlobalResearchAgent as one more
ContextValue among the several it already averages -- not a new agent,
and never able to dominate that average on its own (see to_context_value's
normalization below).

No live fetcher is implemented here (matching data/global_market.py's own
"inject a lawful provider" pattern) -- this module is the data shape and
the normalization only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from data.global_market import ContextValue


@dataclass(frozen=True)
class FiiDiiFlow:
    as_of: date
    fii_net_crores: float
    dii_net_crores: float


def to_context_value(
    flow: FiiDiiFlow, timestamp: datetime, reference_crores: float = 2000.0
) -> ContextValue:
    """Normalizes combined FII+DII net flow to a bounded score in roughly
    the same order of magnitude GlobalResearchAgent's other context values
    are expected to use (its confidence is `min(80, abs(score))`, implying
    a score scale of roughly tens, not thousands of crores). Without this,
    a single large FII day (routinely +/-thousands of crores) would swamp
    every other context value in that average -- exactly the "a single
    day's FII number overrides everything else" failure Brief 3 explicitly
    warned against. reference_crores is the flow size treated as "a full
    +/-20 point move" -- chosen to be a large-but-not-extreme daily FII+DII
    number; tune via config if real data shows this needs adjusting.
    """
    net = flow.fii_net_crores + flow.dii_net_crores
    score = max(-20.0, min(20.0, (net / reference_crores) * 20.0))
    return ContextValue(
        name="FII_DII_NET_FLOW",
        value=score,
        timestamp=timestamp,
        source="NSE",
        available=True,
    )
