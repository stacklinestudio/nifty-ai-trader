"""Phase 2 Piece 7: deterministic option-price reconstruction from real
RAW Kite ticks (data/option_tick_capture.py).

RAW immutability, unchanged and reaffirmed: every function here is
READ-ONLY against the real capture files -- it opens them in "r" mode
only, never writes to or truncates them, and never returns a MODIFIED
copy of a raw tick's own fields; RawOptionTick.raw carries the exact,
untouched dict every real reader already gets from json.loads(line)
["tick"], byte-for-byte identical to what data/option_tick_capture.py
itself wrote. See tests/test_option_price_reconstruction.py::
test_raw_capture_file_is_byte_identical_after_a_real_reconstruction_run
for the real, direct proof (file bytes compared before/after, not just
"no write call was made").

Real, audited field shape (data/private/option_tick_capture/
nifty_option_ticks_2026-09-07.jsonl, first 2 real records, read
directly): every real option tick (MODE_FULL) carries `last_price`
(the real last-traded price -- always present whenever `tradable` is
true) and `depth.buy`/`depth.sell` (5-level real market depth, each
level `{price, quantity, orders}`) -- confirmed present on every real
option tick this project has ever captured, not merely documented as
possible. `exchange_timestamp` is real Kite second-resolution
("YYYY-MM-DD HH:MM:SS", no sub-second component) -- this project's own
real captured data has no finer real timestamp granularity than one
second; `received_at` (this project's own local capture wall-clock,
microsecond resolution) is NOT a substitute for the real market
timestamp and is never used for price selection here, only kept on
RawOptionTick for audit/debugging.

Deterministic price-source priority (documented, not merely implied by
code order):
  1. LAST_PRICE -- the real last-traded price (tick["last_price"]),
     whenever present and > 0. The real, direct evidence of an actual
     completed real transaction -- the most defensible real "price" for
     valuing a position. Confirmed to be present on every real option
     tick this project has captured to date (both real capture days).
  2. MID_BID_ASK -- (best real bid + best real ask) / 2, used ONLY as a
     documented fallback if last_price is genuinely missing/zero AND
     real depth exists. No real captured tick has ever needed this
     fallback (see above) -- kept as a real, deterministic rule for
     correctness, not exercised by real data today.
  3. Neither available -> no valid price. Returned as None, never a
     fabricated number.

Bid/ask is never silently dropped in favor of last_price: reconstruct_
option_price always carries the real best bid/ask (when present)
alongside whichever price it selected, so a caller can see the real
spread the reconstructed price was quoted inside.

No look-ahead, structurally: reconstruct_option_price only ever
considers ticks whose real exchange_timestamp is <= the requested
timestamp -- see its own docstring and tests/test_option_price_
reconstruction.py::test_rejects_a_tick_strictly_after_the_requested_
timestamp for the direct proof.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config import IST

PRICE_SOURCE_LAST_PRICE = "LAST_PRICE"
PRICE_SOURCE_MID_BID_ASK = "MID_BID_ASK"


@dataclass(frozen=True)
class RawOptionTick:
    """One real captured tick for one real instrument_token -- `raw` is
    the exact, untouched tick dict data/option_tick_capture.py itself
    wrote, never modified. `exchange_timestamp` is parsed for real
    ordering/selection; None (never a guess) when the real raw tick
    lacks it or it fails to parse, matching data/option_tick_capture.py
    ::_parse_tick_timestamp's own real, established fail-closed
    behavior (reused here, not reimplemented differently)."""

    instrument_token: int
    exchange_timestamp: datetime | None
    received_at: datetime | None
    last_price: float | None
    bid: float | None
    ask: float | None
    volume: int | None
    open_interest: int | None
    raw: dict[str, Any]


def _parse_tick_timestamp(tick: dict[str, Any]) -> datetime | None:
    """Byte-for-byte the same real parsing rule data/option_tick_capture.py
    ::_parse_tick_timestamp already uses -- not reimplemented
    differently, so a tick this project's own capture module considered
    well-formed is never silently treated as malformed here, or vice
    versa."""
    value = tick.get("exchange_timestamp")
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


def _parse_received_at(record: dict[str, Any]) -> datetime | None:
    value = record.get("received_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_raw_tick(record: dict[str, Any]) -> RawOptionTick | None:
    """Parses one real JSON line already loaded from a real capture file
    (see extract_ticks_for_instrument for the real file-reading loop) --
    None (never a partially-fabricated tick) when the record is missing
    its real "tick" envelope or a real instrument_token."""
    tick = record.get("tick")
    if not isinstance(tick, dict):
        return None
    instrument_token = tick.get("instrument_token")
    if instrument_token is None:
        return None
    depth = tick.get("depth") or {}
    buy_levels = depth.get("buy") or []
    sell_levels = depth.get("sell") or []
    bid = float(buy_levels[0]["price"]) if buy_levels else None
    ask = float(sell_levels[0]["price"]) if sell_levels else None
    last_price = tick.get("last_price")
    return RawOptionTick(
        instrument_token=int(instrument_token),
        exchange_timestamp=_parse_tick_timestamp(tick),
        received_at=_parse_received_at(record),
        last_price=float(last_price) if last_price is not None else None,
        bid=bid,
        ask=ask,
        volume=tick.get("volume_traded"),
        open_interest=tick.get("oi"),
        raw=tick,
    )


def extract_ticks_for_instrument(capture_file_path: Path, instrument_token: int) -> list[RawOptionTick]:
    """Streams the real raw capture file ONCE, read-only ("r" mode --
    never "w"/"r+"/"a", confirmed by the real RAW-immutability test in
    tests/test_option_price_reconstruction.py), collecting only the real
    ticks for `instrument_token`. Memory-bounded to just this one real
    instrument's real tick count for the real day (a real capture day
    subscribes ~43 real tokens -- see data/option_tick_capture.py::
    STRIKES_EITHER_SIDE -- so this is a small real fraction of the
    file's total real tick count, not the whole multi-hundred-MB file).
    Returns ticks in the real order they were captured -- callers that
    need them time-sorted should sort explicitly (real out-of-order
    ticks are a real, documented, flagged possibility -- see data/
    option_tick_capture.py's own `out_of_order` envelope field -- never
    silently assumed away)."""
    results: list[RawOptionTick] = []
    with capture_file_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            tick = record.get("tick")
            if not isinstance(tick, dict) or tick.get("instrument_token") != instrument_token:
                continue
            parsed = parse_raw_tick(record)
            if parsed is not None:
                results.append(parsed)
    return results


@dataclass(frozen=True)
class ReconstructedPrice:
    price: float
    price_source: str
    instrument_token: int
    tick_exchange_timestamp: datetime
    requested_timestamp: datetime
    age_seconds: float
    bid: float | None
    ask: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "price_source": self.price_source,
            "instrument_token": self.instrument_token,
            "tick_exchange_timestamp": self.tick_exchange_timestamp.isoformat(),
            "requested_timestamp": self.requested_timestamp.isoformat(),
            "age_seconds": self.age_seconds,
            "bid": self.bid,
            "ask": self.ask,
        }


def reconstruct_option_price(
    ticks: list[RawOptionTick], requested_timestamp: datetime
) -> ReconstructedPrice | None:
    """The ONE real, canonical place an option price is ever
    reconstructed from raw ticks in this codebase.

    No look-ahead: only real ticks with exchange_timestamp <=
    requested_timestamp are ever considered -- a tick strictly after
    the requested timestamp is invisible to this function, structurally
    (filtered before selection, not merely "not preferred").

    No interpolation: the selected price is always a real, single,
    already-captured tick's own real price -- never a computed average
    or interpolation between two real ticks.

    No fabrication: returns None, explicitly, when no real tick with a
    real usable price exists at or before requested_timestamp for this
    instrument -- never a guessed/default number.

    Among eligible real ticks (exchange_timestamp <= requested_timestamp,
    a real usable price present -- see the module's own price-source
    priority), the most recent one is selected -- ties (two real ticks
    with the identical real second-resolution exchange_timestamp) are
    broken by real list order (capture order), the earlier-appearing
    real tick losing to a later-appearing one at the same real second,
    consistent with "most recently known" being the real intent.
    """
    best: RawOptionTick | None = None
    for candidate_tick in ticks:
        if candidate_tick.exchange_timestamp is None:
            continue
        if candidate_tick.exchange_timestamp > requested_timestamp:
            continue  # no look-ahead: a future real tick is never considered
        has_price = (candidate_tick.last_price is not None and candidate_tick.last_price > 0) or (
            candidate_tick.bid is not None and candidate_tick.ask is not None
        )
        if not has_price:
            continue
        if best is None or candidate_tick.exchange_timestamp >= best.exchange_timestamp:
            best = candidate_tick
    if best is None:
        return None

    if best.last_price is not None and best.last_price > 0:
        price, source = best.last_price, PRICE_SOURCE_LAST_PRICE
    else:
        price, source = round((best.bid + best.ask) / 2, 2), PRICE_SOURCE_MID_BID_ASK

    return ReconstructedPrice(
        price=price,
        price_source=source,
        instrument_token=best.instrument_token,
        tick_exchange_timestamp=best.exchange_timestamp,
        requested_timestamp=requested_timestamp,
        age_seconds=(requested_timestamp - best.exchange_timestamp).total_seconds(),
        bid=best.bid,
        ask=best.ask,
    )


def is_stale(reconstructed: ReconstructedPrice, max_age_seconds: float) -> bool:
    """Staleness is a real, explicit POLICY decision, kept separate from
    reconstruction itself -- an old-but-real price is still real data,
    never discarded by reconstruct_option_price on its own; a caller
    that needs a freshness bound applies it here."""
    return reconstructed.age_seconds > max_age_seconds


@dataclass(frozen=True)
class ExcursionResult:
    status: str  # COMPUTED | INSUFFICIENT_DATA
    mfe: float | None
    mae: float | None
    tick_count_in_window: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mfe": self.mfe,
            "mae": self.mae,
            "tick_count_in_window": self.tick_count_in_window,
        }


INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
COMPUTED = "COMPUTED"


def compute_excursion(
    ticks: list[RawOptionTick],
    entry_timestamp: datetime,
    exit_timestamp: datetime,
    entry_price: float,
) -> ExcursionResult:
    """Real MFE/MAE from the real option price path during
    [entry_timestamp, exit_timestamp] -- the same real sign convention
    execution/position_supervisor.py::PositionState.observe already
    uses (gain = price - entry; mfe = max(mfe, gain); mae = max(mae,
    -gain)), so this stays directly comparable to the live-computed
    MFE/MAE Phase 2 Piece 5's TradeOutcomeRecord already carries --
    this function does NOT replace that live figure (see learning/
    option_pnl.py's own module docstring for why), it is a separate,
    tick-level cross-check. Returns INSUFFICIENT_DATA (mfe/mae both
    None, never 0.0 standing in for "no real data") when zero real
    ticks with a real usable price fall inside the real window -- 0.0
    would misleadingly claim "no real adverse/favorable move happened,"
    which is not the same real fact as "no real coverage exists.\""""
    in_window = [
        t
        for t in ticks
        if t.exchange_timestamp is not None
        and entry_timestamp <= t.exchange_timestamp <= exit_timestamp
        and t.last_price is not None
        and t.last_price > 0
    ]
    if not in_window:
        return ExcursionResult(INSUFFICIENT_DATA, None, None, 0)

    mfe = 0.0
    mae = 0.0
    for t in in_window:
        gain = t.last_price - entry_price
        mfe = max(mfe, gain)
        mae = max(mae, -gain)
    return ExcursionResult(COMPUTED, mfe, mae, len(in_window))
