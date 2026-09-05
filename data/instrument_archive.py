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
from datetime import date, datetime, timedelta
from pathlib import Path

from config import IST, Settings
from data.calendar import NseCalendar
from integrations.discord import DiscordNotifier, webhooks_by_category_from_settings
from integrations.telegram import TelegramNotifier
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


def find_missing_previous_archive(
    archive_dir: Path, calendar: NseCalendar, today: date
) -> date | None:
    """The most recent real trading day strictly before `today`. Returns
    that date if no real archive file exists for it -- but only when at
    least one earlier real archive already exists somewhere in
    `archive_dir`, proving archiving was genuinely running before. A
    brand-new install's very first real day has no prior history to be
    "missing" against, so it never raises a false alarm.

    Uses the same real, weekday-only `NseCalendar` fail-closed convention
    already used elsewhere in this project (main.py, demo/demo_trade.py)
    -- explicit NSE holidays are not currently loaded, so a real holiday
    could still be misidentified as a missed trading day. Documented, not
    silently assumed away.
    """
    candidate = today - timedelta(days=1)
    while not calendar.is_trading_day(candidate):
        candidate -= timedelta(days=1)
    if (archive_dir / f"nfo_instruments_{candidate.isoformat()}.json").exists():
        return None
    if not archive_dir.exists() or not any(archive_dir.glob("nfo_instruments_*.json")):
        return None
    return candidate


def notify_missing_archive(settings: Settings, missing_day: date) -> None:
    """Reuses the existing Discord "system" channel / Telegram wiring
    (integrations/discord.py, integrations/telegram.py) -- the same
    notifiers `main.py notifications` already exercises. Each notifier
    fails closed on its own (returns False, never raises) when no real
    webhook/token is configured; nothing here treats that as an error."""
    message = f"instrument archive missing for {missing_day.isoformat()}, check the scheduled task"
    discord = DiscordNotifier(
        settings.discord_webhook_url,
        webhooks_by_category=webhooks_by_category_from_settings(settings),
    )
    telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    discord.send_message("WARNING", message, "system")
    telegram.send_message("WARNING", message)


def check_and_notify_missing_archive(
    settings: Settings, archive_dir: Path = ARCHIVE_DIR, today: date | None = None
) -> date | None:
    """Real, same-day safeguard: run at the start of every scheduled
    archive attempt (see run_daily_archive below), independent of whether
    today's own Kite session is valid -- a real gap in a prior day's
    archive is worth surfacing even on a day today's own archive also
    fails closed. Never raises: a notification-transport failure is
    logged, never allowed to break the scheduled archiving task, matching
    every other notification path in this codebase.
    """
    today = today or datetime.now(IST).date()
    missing_day = find_missing_previous_archive(archive_dir, NseCalendar(), today)
    if missing_day is None:
        return None
    logger.warning("instrument_archive_gap_detected missing_day=%s", missing_day)
    try:
        notify_missing_archive(settings, missing_day)
    except Exception as exc:  # noqa: BLE001 - a notification failure must never break the scheduled archiving task.
        logger.warning("instrument_archive_gap_notify_failed error=%s: %s", type(exc).__name__, exc)
    return missing_day


def _archive_success_message(day: date, instrument_count: int, timestamp: datetime) -> str:
    return (
        f"Instrument archive succeeded for {day.isoformat()}: "
        f"{instrument_count} real instruments archived at {timestamp.isoformat()}"
    )


def _archive_failure_message(reason: str) -> str:
    return f"Instrument archive failed: {reason} -- check the scheduled task"


def notify_archive_status(settings: Settings, *, success: bool, detail: str) -> None:
    """Real daily status notification, sent on *every* scheduled archive
    attempt (success or failure) to the existing Discord "system"
    channel -- a small addition alongside, never a replacement for, the
    separate `check_and_notify_missing_archive` gap safeguard above: that
    one reports a *prior* day's silent gap; this one reports *today's*
    own real outcome every single time it runs. Guards its own send
    internally (unlike `notify_missing_archive`, which relies on its one
    caller for this) since this has three real call sites below, and a
    notification-transport failure must never break the scheduled
    archiving task either way."""
    try:
        discord = DiscordNotifier(
            settings.discord_webhook_url,
            webhooks_by_category=webhooks_by_category_from_settings(settings),
        )
        discord.send_message("INFO" if success else "WARNING", detail, "system")
    except Exception as exc:  # noqa: BLE001 - a notification failure must never break the scheduled archiving task.
        logger.warning("instrument_archive_status_notify_failed error=%s: %s", type(exc).__name__, exc)


def run_daily_archive(settings: Settings) -> Path | None:
    """Fail-closed entry point for the real daily scheduled task (see
    scripts/archive_instruments.ps1 and the registered Windows Scheduled
    Task "NiftyAITrader-InstrumentArchive"). Returns None (never raises)
    when no valid real session exists today -- missing credentials, the
    kiteconnect package unavailable, or a real API failure (most often an
    expired/not-yet-refreshed access token) are all logged and treated
    the same honest way: nothing to archive today, try again once a
    fresh session exists, never fabricate a placeholder file.

    Also runs the real same-day gap safeguard first (see
    check_and_notify_missing_archive) -- checked unconditionally, before
    the credential check below, so a real gap in a *prior* day's archive
    still gets surfaced even on a day today's own session is also
    missing/expired. Separately, every real attempt here -- success or
    failure -- also sends its own real status notification
    (notify_archive_status), so a silent success run is just as visible
    as a silent failure would otherwise be.
    """
    check_and_notify_missing_archive(settings)
    today = datetime.now(IST).date()
    if not (settings.kite_api_key and settings.kite_access_token):
        reason = "no_kite_credentials_configured"
        logger.warning("instrument_archive_skipped reason=%s", reason)
        notify_archive_status(settings, success=False, detail=_archive_failure_message(reason))
        return None
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        reason = "kiteconnect_not_installed"
        logger.warning("instrument_archive_skipped reason=%s", reason)
        notify_archive_status(settings, success=False, detail=_archive_failure_message(reason))
        return None
    kite = KiteConnect(api_key=settings.kite_api_key)
    kite.set_access_token(settings.kite_access_token)
    try:
        path = archive_nfo_instruments(kite, today=today)
    except Exception as exc:  # noqa: BLE001 - a real, expired/invalid token (or any other real API failure) must be logged, never crash the scheduled task.
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("instrument_archive_failed error=%s", reason)
        notify_archive_status(settings, success=False, detail=_archive_failure_message(reason))
        return None
    instrument_count = len(json.loads(path.read_text(encoding="utf-8")))
    timestamp = datetime.now(IST)
    logger.info("instrument_archive_saved path=%s", path)
    notify_archive_status(
        settings, success=True, detail=_archive_success_message(today, instrument_count, timestamp)
    )
    return path
