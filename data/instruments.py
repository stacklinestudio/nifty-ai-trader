from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class OptionInstrument:
    symbol: str
    strike: float
    expiry: date
    option_type: str
    lot_size: int
    instrument_token: int | None = None


def parse_kite_instruments(rows: list[dict]) -> list[OptionInstrument]:
    result = []
    for row in rows:
        if row.get("name") == "NIFTY" and row.get("segment") == "NFO-OPT":
            result.append(
                OptionInstrument(
                    row["tradingsymbol"],
                    float(row["strike"]),
                    pd.Timestamp(row["expiry"]).date(),
                    row["instrument_type"],
                    int(row["lot_size"]),
                    row.get("instrument_token"),
                )
            )
    return result


def download_kite_nifty_options(kite: object) -> list[OptionInstrument]:
    """Download current NFO instruments through the authenticated official SDK."""
    return parse_kite_instruments(kite.instruments("NFO"))
