"""Adversarial independent validator; it tries to disprove every thesis."""

from __future__ import annotations

from agents.contracts import Decision, TradeThesis, Validation


class IndependentTradeValidator:
    def validate(
        self,
        thesis: TradeThesis | None,
        spread: float | None,
        data_fresh: bool,
        conflicting_evidence: bool,
        blocked_reentry: bool = False,
    ) -> Validation:
        reasons: list[str] = []
        if thesis is None:
            reasons.append("no complete trade thesis")
        else:
            if thesis.target <= thesis.entry or thesis.stop >= thesis.entry:
                reasons.append("invalid reward/risk levels")
            if thesis.estimated_risk <= 0:
                reasons.append("non-positive estimated risk")
        if spread is not None and spread > 2.0:
            reasons.append("option spread is too wide")
        if not data_fresh:
            reasons.append("market data is stale")
        if conflicting_evidence:
            reasons.append("conflicting agent evidence")
        if blocked_reentry:
            # Brief 3, Part B item 4 (user decision): a same-direction,
            # same-setup-type re-entry after today's stop-out, in the same
            # regime it was stopped out in, must not just re-fire on the
            # strength of an otherwise-passing validation -- it needs a
            # different setup type or a regime change to prove the thesis
            # isn't the same broken one repeating.
            reasons.append(
                "same-direction re-entry after today's stop-out without a "
                "regime change or different setup type"
            )
        return Validation(
            Decision.REJECT if reasons else Decision.APPROVE,
            tuple(reasons or ["No disqualifying deterministic evidence found."]),
            90 if not reasons else 0,
        )
