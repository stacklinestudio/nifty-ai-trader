from __future__ import annotations

from agents.research_agents import SignalHunterAgent
from strategy.regime_selector import weight_for


def test_trend_favored_setup_gets_boosted_in_healthy_trend():
    weight = weight_for("MOMENTUM_CONTINUATION", "TREND_UP", "NORMAL", "BROAD")
    assert weight.multiplier > 1.0
    assert "favors" in weight.reasons[0]


def test_breakout_setup_disfavored_in_range_low_volatility():
    weight = weight_for("OPENING_RANGE_BREAKOUT", "RANGE", "LOW", "MIXED")
    assert weight.multiplier < 1.0


def test_range_favored_setup_boosted_in_range_low_volatility():
    weight = weight_for("VWAP_REJECTION", "RANGE", "LOW", "MIXED")
    assert weight.multiplier > 1.0


def test_high_volatility_widens_invalidation_for_breakout_without_changing_confidence():
    weight = weight_for("OPENING_RANGE_BREAKOUT", "TREND_UP", "HIGH", "BROAD")
    # Trend-favored setups aren't in the breakout set, so no confidence boost
    # from the trend rule here; but the whipsaw-risk flag must still fire.
    assert weight.widen_invalidation is True
    assert weight.multiplier == 1.0


def test_gap_setup_boosted_in_volatility_expansion():
    weight = weight_for("GAP_CONTINUATION", "GAP_UP", "HIGH", None)
    assert weight.multiplier > 1.0


def test_no_regime_match_leaves_multiplier_unchanged():
    weight = weight_for("SUPPORT_RESISTANCE_REACTION", "TREND_UP", "NORMAL", "BROAD")
    assert weight.multiplier == 1.0
    assert "no regime-specific weighting" in weight.reasons[0]


def test_signal_hunter_applies_weight_without_discarding_mismatched_candidate():
    # A breakout setup in a disfavoring regime must still produce a
    # candidate (confidence scaling, not a hard filter) -- just at a lower
    # confidence, and with the widened invalidation logged.
    review = SignalHunterAgent().run(
        {
            "candidate_direction": "CALL",
            "candidate_confidence": 80,
            "setup_type": "OPENING_RANGE_BREAKOUT",
            "market_regime": "RANGE",
            "volatility_regime": "LOW",
            "breadth_participation": "MIXED",
        }
    )
    candidates = review.data["candidates"]
    assert len(candidates) == 1
    assert 0 < candidates[0].confidence < 80
    assert review.data["regime_weight_multiplier"] < 1.0
    assert any("disfavors" in reason for reason in review.data["regime_weight_reasons"])


def test_signal_hunter_boosts_confidence_for_matching_trend_setup():
    review = SignalHunterAgent().run(
        {
            "candidate_direction": "CALL",
            "candidate_confidence": 60,
            "setup_type": "TREND_CONTINUATION",
            "market_regime": "TREND_UP",
            "volatility_regime": "NORMAL",
            "breadth_participation": "BROAD",
        }
    )
    candidate = review.data["candidates"][0]
    assert candidate.confidence > 60
    assert any("favors" in evidence for evidence in candidate.evidence)
