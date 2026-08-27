from __future__ import annotations

from datetime import date

from agents.research_agents import GlobalResearchAgent
from config import IST
from data.fii_dii import FiiDiiFlow, to_context_value
from data.global_market import ContextValue


def test_normalization_is_bounded_regardless_of_flow_size():
    huge_inflow = FiiDiiFlow(date(2026, 8, 27), fii_net_crores=50_000, dii_net_crores=20_000)
    huge_outflow = FiiDiiFlow(date(2026, 8, 27), fii_net_crores=-50_000, dii_net_crores=-20_000)

    inflow_value = to_context_value(huge_inflow, timestamp=_now())
    outflow_value = to_context_value(huge_outflow, timestamp=_now())

    assert inflow_value.value == 20.0
    assert outflow_value.value == -20.0


def test_sign_matches_net_flow_direction():
    modest_inflow = FiiDiiFlow(date(2026, 8, 27), fii_net_crores=500, dii_net_crores=200)
    value = to_context_value(modest_inflow, timestamp=_now())
    assert value.value > 0


def test_a_single_huge_fii_day_cannot_swamp_other_disagreeing_evidence():
    """The whole point of normalizing before treating this as one more
    ContextValue: three other sources say bearish, one enormous FII inflow
    day says (clamped) bullish -- the average must still land bearish, not
    get dragged to a false BULLISH by one oversized raw number.
    """
    bearish_sources = [
        ContextValue("us_markets", -10.0, _now(), "test", True),
        ContextValue("crude_oil", -8.0, _now(), "test", True),
        ContextValue("dollar_index", -6.0, _now(), "test", True),
    ]
    huge_fii_day = FiiDiiFlow(date(2026, 8, 27), fii_net_crores=100_000, dii_net_crores=50_000)
    fii_value = to_context_value(huge_fii_day, timestamp=_now())

    review = GlobalResearchAgent().run({"global_context": [*bearish_sources, fii_value]})

    assert review.data["global_direction"] == "BEARISH"


def _now():
    from datetime import datetime

    return datetime.now(IST)
