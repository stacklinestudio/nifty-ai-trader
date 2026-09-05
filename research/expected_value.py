"""Brief 14 Phase 2a: real Expected Value (EV), in R multiples, computed
alongside (never replacing) the existing confidence score. MEASUREMENT
ONLY -- this module is never imported by RiskAgent, TradeBuilderAgent,
Orchestrator's decision path, or SignalEngine; nothing here changes how
a trade is approved, rejected, or sized. Phase 2b (actually using this
to rank/select candidates) is a distinct future brief.

    EV (R) = P(win) x AvgWin(R) - P(loss) x AvgLoss(R) - real_costs(R) - real_slippage(R)

1R = settings.max_risk_per_trade, read live on every call, never
hardcoded (it has already been revised once this project -- 400 to 600 --
and may be again).

Real probability/expectancy source, tiered and labeled, checked in this
order for each (setup_type, regime) combination:
  1. REAL_TRADE_DATA -- learning/pattern_memory.py::stats_for, once a
     statistically meaningful real sample exists (MIN_SAMPLES_FOR_
     CONFIDENCE=20, the same real bar pattern_memory.py already uses).
     Currently always empty (zero real trades this project has ever
     closed) -- checked first regardless, so it activates automatically
     once real trades accumulate, with no further code changes.
  2. COUNTERFACTUAL_PROXY -- research/counterfactual.py's real, already-
     built index-price-proxy win rate, once enough real counterfactual
     records exist for this exact (setup_type, regime) combination.
     P(win) here is real (a real fraction of real counterfactual
     records), but it measures the real INDEX's direction, never real
     option P&L -- COUNTERFACTUAL_PROXY as ev_source is the honest label
     for that distinction, present everywhere this tier's number appears.
  3. INSUFFICIENT_DATA -- neither tier has enough real data for this
     specific combination. No fabricated EV number; ev_r is None.

AvgWin(R)/AvgLoss(R) for tier 2, stated plainly since neither is real
option P&L (none exists for any window in this project):
  - AvgLoss(R) = 1.0, by definition of R itself -- a stop-loss exit
    realizes exactly the risked amount; this is not an estimate.
  - AvgWin(R) = REWARD_RISK_RATIO (below), this system's own real,
    already-coded target:stop spread ratio (execution/live_context.py::
    _atr_zones' real 1.5x lower-bound target multiple against its 1.0x
    stop multiple -- independently matches risk/risk_manager.py::
    RiskManager's own real reward_multiple=1.5 default, a real
    cross-check this project already effectively agrees with itself on
    this ratio in two separately-written places). A real, cited
    structural assumption, not measured, and never presented as measured
    -- clearly separate from the empirically-real P(win) tier-2 rate.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import Settings
from learning.memory import MemoryStore
from learning.pattern_memory import MIN_SAMPLES_FOR_CONFIDENCE, stats_for
from research.counterfactual import COUNTERFACTUAL_LABEL, CounterfactualRecord

REAL_TRADE_SOURCE = "REAL_TRADE_DATA"
COUNTERFACTUAL_SOURCE = "COUNTERFACTUAL_PROXY"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# This project's own real, already-coded target:stop ratio -- see this
# module's own docstring above for the citation. Not measured; a
# structural assumption used only for tier 2's AvgWin(R).
REWARD_RISK_RATIO = 1.5
AVG_LOSS_R = 1.0  # by definition of R -- not an estimate

# Same statistical-meaningfulness bar learning/pattern_memory.py already
# uses for real trade data, reused (not re-invented) so both tiers apply
# the same real standard for "enough real samples to trust."
MIN_COUNTERFACTUAL_SAMPLES = MIN_SAMPLES_FOR_CONFIDENCE

# A representative (not per-candidate-real -- real option premium data
# doesn't exist for any historical window in this project, confirmed
# repeatedly) near-the-money weekly NIFTY premium, matching this
# project's own already-established representative value
# (tests/test_supervision_quote_symbol.py::REPRESENTATIVE_OPTION_LTP =
# 120.5) -- reused here for consistency, not re-derived. Costs scale with
# this (see real_transaction_costs), so an EVEstimate's costs_r/
# slippage_r are the same representative-premium-based figure for every
# candidate in a given report run, not a fabricated per-candidate value.
REPRESENTATIVE_OPTION_PREMIUM = 120.5
REAL_LOT_SIZE = 65  # confirmed live against the real Kite API, execution/live_context.py

# Real, current NSE/Zerodha options fee schedule -- cited from
# https://zerodha.com/charges/ (fetched live, 2026-09), not assumed:
#   Brokerage: flat Rs 20 per executed order.
#   STT: 0.15% on sell side, on premium (options bought and squared off,
#     never exercised -- this project's paper trades always close via a
#     real sell order, matching PaperBroker's own design, so only the
#     sell-side premium rate applies, not the intrinsic-value-on-exercise
#     rate).
#   Exchange transaction charges: NSE 0.03553% on premium, both legs.
#   SEBI charges: Rs 10 / crore, both legs.
#   GST: 18% on (brokerage + exchange transaction charges + SEBI
#     charges) -- does not apply to STT or stamp duty.
#   Stamp duty: 0.003% (= Rs 300 / crore, the same rate in two units,
#     not two alternatives) on buy side only.
BROKERAGE_PER_ORDER = 20.0
STT_SELL_SIDE_RATE = 0.0015
EXCHANGE_TRANSACTION_RATE = 0.0003553
SEBI_CHARGES_PER_CRORE = 10.0
GST_RATE = 0.18
STAMP_DUTY_BUY_SIDE_RATE = 0.00003


def real_transaction_costs(
    entry_price: float = REPRESENTATIVE_OPTION_PREMIUM,
    exit_price: float = REPRESENTATIVE_OPTION_PREMIUM,
    quantity: int = REAL_LOT_SIZE,
) -> float:
    """Real ₹ round-trip cost (one buy + one sell) for a real NIFTY
    option position, per the current, cited Zerodha/NSE fee schedule
    (see module docstring). Defaults to the representative premium/lot
    size -- callers may supply a real entry/exit when one is genuinely
    known (e.g. a real closed trade, once tier 1 is active)."""
    buy_turnover = entry_price * quantity
    sell_turnover = exit_price * quantity
    brokerage = 2 * BROKERAGE_PER_ORDER
    stt = STT_SELL_SIDE_RATE * sell_turnover
    exchange_charges = EXCHANGE_TRANSACTION_RATE * (buy_turnover + sell_turnover)
    sebi_charges = (buy_turnover + sell_turnover) * (SEBI_CHARGES_PER_CRORE / 1e7)
    gst = GST_RATE * (brokerage + exchange_charges + sebi_charges)
    stamp_duty = STAMP_DUTY_BUY_SIDE_RATE * buy_turnover
    return brokerage + stt + exchange_charges + sebi_charges + gst + stamp_duty


def real_slippage(settings: Settings, quantity: int = REAL_LOT_SIZE) -> float:
    """Reuses execution/paper_broker.py::PaperBroker's own real, already-
    tested adverse-fill slippage formula exactly (adverse = slippage_ticks
    x tick_size, applied on both entry and exit) -- not a new estimate.
    """
    entry_adverse = settings.entry_slippage_ticks * settings.tick_size
    exit_adverse = settings.exit_slippage_ticks * settings.tick_size
    return (entry_adverse + exit_adverse) * quantity


def recompute_ev(
    win_rate: float, avg_win_r: float, avg_loss_r: float, costs_r: float, slippage_r: float
) -> float:
    """The pure arithmetic core of the EV formula, factored out so
    Brief 15's decomposition and AvgWin sensitivity sweep reuse the exact
    same computation compute_ev() itself uses for tier 2 -- never a
    separate, potentially-inconsistent calculation."""
    return win_rate * avg_win_r - (1 - win_rate) * avg_loss_r - costs_r - slippage_r


@dataclass(frozen=True)
class EVDecomposition:
    """Brief 15 Part A: the same four real terms recompute_ev() sums,
    reported as separate line items -- win_contribution + loss_contribution
    - costs - slippage always equals the estimate's own real ev_r exactly
    (tests/test_ev_decomposition.py proves this), so this is a real
    breakdown of the same number, never a second, divergent calculation.
    """

    win_contribution: float  # win_rate * avg_win_r
    loss_contribution: float  # (1 - win_rate) * avg_loss_r -- a real cost, reported positive; subtracted in total
    costs: float
    slippage: float

    @property
    def total(self) -> float:
        return self.win_contribution - self.loss_contribution - self.costs - self.slippage

    def dominant_driver(self) -> str:
        """The single term contributing the most real negative drag --
        loss_contribution and costs/slippage are real drags by
        construction; win_contribution is the only real positive term, so
        it's excluded from "which term is dragging this down"."""
        drags = {"loss_contribution": self.loss_contribution, "costs": self.costs, "slippage": self.slippage}
        return max(drags, key=drags.get)


@dataclass(frozen=True)
class EVEstimate:
    setup_type: str
    regime: str
    ev_source: str  # REAL_TRADE_DATA | COUNTERFACTUAL_PROXY | INSUFFICIENT_DATA
    sample_size: int
    win_rate: float | None
    avg_win_r: float | None
    avg_loss_r: float | None
    costs_r: float
    slippage_r: float
    ev_r: float | None
    one_r_rupees: float

    def decomposition(self) -> EVDecomposition | None:
        """Only meaningful for tier 2 (COUNTERFACTUAL_PROXY) -- tier 1's
        ev_r comes directly from a real stored trade pnl (already a
        single net real number, not decomposable into these four terms
        without re-deriving a real per-trade cost/slippage breakdown this
        module doesn't have); tier 3 has no real ev_r to decompose."""
        if self.ev_source != COUNTERFACTUAL_SOURCE:
            return None
        return EVDecomposition(
            win_contribution=self.win_rate * self.avg_win_r,
            loss_contribution=(1 - self.win_rate) * self.avg_loss_r,
            costs=self.costs_r,
            slippage=self.slippage_r,
        )

    def describe(self) -> str:
        if self.ev_r is None:
            return (
                f"{self.setup_type}/{self.regime}: ev_source={self.ev_source} "
                f"(sample_size={self.sample_size}) -- no real EV, not fabricated"
            )
        label = f" [{COUNTERFACTUAL_LABEL}]" if self.ev_source == COUNTERFACTUAL_SOURCE else ""
        return (
            f"{self.setup_type}/{self.regime}: ev_source={self.ev_source}{label} "
            f"sample_size={self.sample_size} win_rate={self.win_rate:.2f} "
            f"EV={self.ev_r:+.3f}R (1R=Rs{self.one_r_rupees:.0f})"
        )


def compute_ev(
    setup_type: str,
    regime: str,
    settings: Settings,
    memory_store: MemoryStore,
    counterfactual_records: list[CounterfactualRecord],
) -> EVEstimate:
    """Real, live 1R -- never hardcoded, re-read every call so a real
    mid-project change to settings.max_risk_per_trade (already happened
    once: 400 -> 600) is reflected automatically."""
    one_r = settings.max_risk_per_trade
    costs_r = real_transaction_costs() / one_r
    slippage_r = real_slippage(settings) / one_r

    # Tier 1: real trade data, checked first, every time -- inactive
    # today (0 real trades) but requires no further code changes once
    # real trades exist. real_stats.expectancy (agents/orchestrator.py::
    # _close_position's real `pnl = (fill_price - entry) * quantity -
    # estimated_costs`) is ALREADY net of real PaperBroker slippage
    # (baked into fill_price) and its own real cost model -- costs_r/
    # slippage_r below are NOT subtracted again here; doing so would
    # double-count real costs already realized in the stored pnl. They
    # apply only to tier 2's structural estimate, which has no real P&L
    # to have netted them out of.
    real_stats = stats_for(memory_store, setup_type, regime)
    if not real_stats.low_confidence:
        win_rate = real_stats.win_rate
        ev_r = (real_stats.expectancy or 0.0) / one_r
        return EVEstimate(
            setup_type, regime, REAL_TRADE_SOURCE, real_stats.sample_size,
            win_rate, None, None, 0.0, 0.0, ev_r, one_r,
        )

    # Tier 2: real counterfactual-derived proxy.
    matching = [
        r for r in counterfactual_records if r.setup_type == setup_type and r.regime == regime
    ]
    if len(matching) >= MIN_COUNTERFACTUAL_SAMPLES:
        wins = sum(1 for r in matching if r.profitable)
        win_rate = wins / len(matching)
        ev_r = recompute_ev(win_rate, REWARD_RISK_RATIO, AVG_LOSS_R, costs_r, slippage_r)
        return EVEstimate(
            setup_type, regime, COUNTERFACTUAL_SOURCE, len(matching),
            win_rate, REWARD_RISK_RATIO, AVG_LOSS_R, costs_r, slippage_r, ev_r, one_r,
        )

    # Tier 3: honestly insufficient -- no fabricated number.
    return EVEstimate(
        setup_type, regime, INSUFFICIENT_DATA, len(matching),
        None, None, None, costs_r, slippage_r, None, one_r,
    )
