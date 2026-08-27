"""Options open-interest buildup/unwinding by strike.

Comparing OI *level* across two snapshots -- never a single snapshot's
absolute OI, which says nothing about new positioning versus old. This is
the closest honest, legitimate public-data equivalent to "watching where
large participants are positioning": NSE option-chain OI is public,
updates through the trading day, and is available to any retail-tier data
feed -- not insider information, and not a live feed of individual large
orders (which doesn't exist for retail-tier access). It is one informational
input, never a trade trigger or validator override on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from data.option_chain import OptionQuote


@dataclass(frozen=True)
class OiBuildup:
    call_oi_change: int
    put_oi_change: int
    bias: str  # "CALL_BUILDUP" | "PUT_BUILDUP" | "BALANCED" | "UNAVAILABLE"
    reasons: tuple[str, ...]


def detect_buildup(
    current: list[OptionQuote], previous: list[OptionQuote], significance_ratio: float = 1.5
) -> OiBuildup:
    """Aggregates OI change across all strikes, split by option type.
    significance_ratio is how much larger one side's net change must be
    than the other before this calls it a real bias rather than BALANCED --
    informational either way; nothing here gates a trade decision by itself.
    """
    if not previous or not current:
        return OiBuildup(0, 0, "UNAVAILABLE", ("No prior snapshot to compare OI change against.",))

    previous_oi = {
        (q.instrument.strike, q.instrument.option_type): q.open_interest or 0 for q in previous
    }
    call_change = 0
    put_change = 0
    for quote in current:
        key = (quote.instrument.strike, quote.instrument.option_type)
        change = (quote.open_interest or 0) - previous_oi.get(key, 0)
        if quote.instrument.option_type == "CE":
            call_change += change
        else:
            put_change += change

    if call_change <= 0 and put_change <= 0:
        return OiBuildup(call_change, put_change, "BALANCED", ("No net OI buildup on either side.",))

    larger, smaller, bias = (
        (call_change, put_change, "CALL_BUILDUP")
        if call_change >= put_change
        else (put_change, call_change, "PUT_BUILDUP")
    )
    if smaller <= 0 or larger >= smaller * significance_ratio:
        label = bias.replace("_", " ").title()
        return OiBuildup(
            call_change,
            put_change,
            bias,
            (f"{label}: call OI change {call_change}, put OI change {put_change}.",),
        )
    return OiBuildup(
        call_change, put_change, "BALANCED", ("OI change roughly balanced between calls and puts.",)
    )
