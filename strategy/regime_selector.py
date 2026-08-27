"""Deterministic regime -> setup-type confidence weighting.

This is a confidence adjustment, never a hard filter: a candidate whose
setup type doesn't match the detected regime still passes through (at a
lower or unchanged weight) so the adversarial validator and risk veto keep
doing their job on top of this. Regime detection itself has error, and a
hard filter would let that error silently discard otherwise-valid trades.
"""

from __future__ import annotations

from dataclasses import dataclass

_TREND_REGIMES = {"TREND_UP", "TREND_DOWN", "GAP_UP", "GAP_DOWN"}
_RANGE_REGIMES = {"RANGE", "UNCERTAIN"}
_LOW_VOL_REGIMES = {"LOW", "NORMAL"}
_HEALTHY_BREADTH = {"BROAD", "MIXED"}

_TREND_FAVORED_SETUPS = {"MOMENTUM_CONTINUATION", "VWAP_BREAKOUT", "TREND_CONTINUATION"}
_RANGE_FAVORED_SETUPS = {
    "SUPPORT_RESISTANCE_REACTION",
    "VWAP_REJECTION",
    "OPENING_RANGE_REJECTION",
}
_RANGE_DISFAVORED_SETUPS = {"OPENING_RANGE_BREAKOUT", "VWAP_BREAKOUT", "MOMENTUM_CONTINUATION"}
_VOLATILITY_FAVORED_SETUPS = {"GAP_CONTINUATION", "GAP_REVERSAL"}
_BREAKOUT_SETUPS = {"OPENING_RANGE_BREAKOUT", "VWAP_BREAKOUT"}


@dataclass(frozen=True)
class RegimeWeight:
    multiplier: float
    reasons: tuple[str, ...]
    widen_invalidation: bool = False


def weight_for(
    setup_type: str,
    market_regime: str,
    volatility_regime: str,
    breadth_participation: str | None = None,
) -> RegimeWeight:
    reasons: list[str] = []
    multiplier = 1.0
    widen_invalidation = False

    trending = market_regime in _TREND_REGIMES
    healthy_breadth = breadth_participation is None or breadth_participation in _HEALTHY_BREADTH
    ranging = market_regime in _RANGE_REGIMES and volatility_regime in _LOW_VOL_REGIMES
    high_vol_expansion = volatility_regime == "HIGH"

    if trending and healthy_breadth and setup_type in _TREND_FAVORED_SETUPS:
        multiplier *= 1.25
        reasons.append(
            f"{market_regime} trend with {breadth_participation or 'unconfirmed'} breadth "
            f"favors {setup_type}"
        )

    if ranging:
        if setup_type in _RANGE_FAVORED_SETUPS:
            multiplier *= 1.20
            reasons.append(
                f"{market_regime} regime with {volatility_regime} volatility favors "
                f"range-reaction setups over breakout"
            )
        elif setup_type in _RANGE_DISFAVORED_SETUPS:
            multiplier *= 0.75
            reasons.append(
                f"{market_regime} regime with {volatility_regime} volatility disfavors "
                f"breakout-style setups (whipsaw risk in chop)"
            )

    if high_vol_expansion:
        if setup_type in _VOLATILITY_FAVORED_SETUPS:
            multiplier *= 1.20
            reasons.append("high volatility expansion favors gap continuation/reversal setups")
        if setup_type in _BREAKOUT_SETUPS:
            widen_invalidation = True
            reasons.append(
                "high volatility expansion increases whipsaw risk for breakout setups; "
                "invalidation widened rather than confidence changed"
            )

    if not reasons:
        reasons.append(
            f"no regime-specific weighting applies to {setup_type} in "
            f"{market_regime}/{volatility_regime}"
        )

    return RegimeWeight(multiplier, tuple(reasons), widen_invalidation)
