from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from data.option_chain import OptionQuote


@dataclass(frozen=True)
class SelectedOption:
    quote: OptionQuote
    quantity: int
    score: float


class OptionSelector:
    def select(
        self,
        options: list[OptionQuote],
        direction: str,
        spot: float,
        max_position_value: float,
        max_spread: float = 2.0,
        min_volume: int = 1,
    ) -> SelectedOption | None:
        kind = "CE" if direction == "CALL" else "PE"
        valid = [
            o
            for o in options
            if o.instrument.option_type == kind
            and o.ltp > 0
            and o.instrument.lot_size > 0
            and o.instrument.expiry >= datetime.now(ZoneInfo("Asia/Kolkata")).date()
            and (o.volume or 0) >= min_volume
            and (o.spread is None or o.spread <= max_spread)
        ]
        scored = []
        for o in valid:
            qty = int(max_position_value // (o.ltp * o.instrument.lot_size)) * o.instrument.lot_size
            if qty:
                score = (
                    -abs(o.instrument.strike - spot)
                    - (o.spread or 0) * 3
                    + min((o.volume or 0) / 1000, 5)
                )
                scored.append(SelectedOption(o, qty, score))
        return max(scored, key=lambda x: x.score) if scored else None
