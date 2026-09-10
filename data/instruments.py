from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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


def load_archived_instruments(archive_path: Path) -> list[OptionInstrument]:
    """Phase 2 Piece 7: loads a real, already-archived NFO instrument
    dump (data/instrument_archive.py's own real output, e.g. data/
    private/instrument_archives/nfo_instruments_<date>.json) and parses
    it via parse_kite_instruments above -- reused, not reimplemented, so
    a real archived row is resolved into an OptionInstrument the exact
    same way a live kite.instruments("NFO") response already is."""
    rows = json.loads(Path(archive_path).read_text(encoding="utf-8"))
    return parse_kite_instruments(rows)


def resolve_instrument_by_token(
    instruments: list[OptionInstrument], instrument_token: int
) -> OptionInstrument | None:
    """None (never a guessed contract identity) when the real
    instrument_token isn't found in the supplied real instrument list."""
    for instrument in instruments:
        if instrument.instrument_token == instrument_token:
            return instrument
    return None
