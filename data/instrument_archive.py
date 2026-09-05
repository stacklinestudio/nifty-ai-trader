"""Brief 13 Part 2: proactive daily archiving of Kite's real /instruments
dump.

Real, live investigation (an earlier brief) confirmed Kite's
/instruments endpoint is completely purged of any contract whose expiry
has already passed -- this project lost the ability to backtest with
real option data for its already-elapsed 42-day window because no dump
was ever saved before those contracts expired. This closes that gap
going forward: every real day this runs with a valid session, that
day's real, raw NFO instrument list (strikes, expiries, lot sizes,
instrument_tokens -- everything data/instruments.py::parse_kite_instruments
needs, and more, saved raw rather than pre-filtered so a future backtest
isn't limited to what this project happened to parse today) is saved,
timestamped, to data/private/instrument_archives/ (gitignored, matching
this project's other real captured data -- never committed).

Real operational constraint, stated plainly: Kite access tokens are
single-day and require a genuine interactive browser login each real day
(auth/kite_auth.py -- this codebase never automates broker credentials,
by design). A scheduled run on a day nobody has completed that day's
login will correctly find no valid session and skip -- logged honestly,
not a crash, not a fabricated archive.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from config import IST, Settings
from monitoring.logger import configure_logger

logger = configure_logger(__name__)

ARCHIVE_DIR = Path("data/private/instrument_archives")


def archive_nfo_instruments(kite: object, archive_dir: Path = ARCHIVE_DIR, today: date | None = None) -> Path:
    """The real, raw kite.instruments("NFO") response for `today`, saved
    as-is. Overwrites cleanly if run more than once the same real day
    (idempotent, not append-only -- there is only ever one real "today's
    instrument list")."""
    today = today or datetime.now(IST).date()
    rows = kite.instruments("NFO")
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"nfo_instruments_{today.isoformat()}.json"
    path.write_text(json.dumps(rows, default=str), encoding="utf-8")
    return path


def run_daily_archive(settings: Settings) -> Path | None:
    """Fail-closed entry point for the real daily scheduled task (see
    scripts/archive_instruments.ps1 and the registered Windows Scheduled
    Task "NiftyAITrader-InstrumentArchive"). Returns None (never raises)
    when no valid real session exists today -- missing credentials, the
    kiteconnect package unavailable, or a real API failure (most often an
    expired/not-yet-refreshed access token) are all logged and treated
    the same honest way: nothing to archive today, try again once a
    fresh session exists, never fabricate a placeholder file.
    """
    if not (settings.kite_api_key and settings.kite_access_token):
        logger.warning("instrument_archive_skipped reason=no_kite_credentials_configured")
        return None
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        logger.warning("instrument_archive_skipped reason=kiteconnect_not_installed")
        return None
    kite = KiteConnect(api_key=settings.kite_api_key)
    kite.set_access_token(settings.kite_access_token)
    try:
        path = archive_nfo_instruments(kite)
    except Exception as exc:  # noqa: BLE001 - a real, expired/invalid token (or any other real API failure) must be logged, never crash the scheduled task.
        logger.warning("instrument_archive_failed error=%s: %s", type(exc).__name__, exc)
        return None
    logger.info("instrument_archive_saved path=%s", path)
    return path
