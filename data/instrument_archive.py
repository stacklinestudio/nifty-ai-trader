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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from config import IST, Settings
from data.calendar import NseCalendar
from integrations.discord import DiscordNotifier, webhooks_by_category_from_settings
from integrations.telegram import TelegramNotifier
from monitoring.logger import configure_logger

logger = configure_logger(__name__)

ARCHIVE_DIR = Path("data/private/instrument_archives")
VALIDATED_MANIFEST_FILENAME = "validated_manifest.json"

# Brief 18 Part A #2: the real fields data/instruments.py::parse_kite_instruments
# actually needs. Includes "instrument_type" beyond the brief's own illustrative
# list -- parse_kite_instruments accesses it via row["instrument_type"] (a real
# KeyError risk, not a tolerant .get()), so its absence is exactly the kind of
# "file has some content but isn't actually usable" gap this brief closes.
REQUIRED_INSTRUMENT_FIELDS = (
    "tradingsymbol",
    "strike",
    "expiry",
    "instrument_type",
    "lot_size",
    "instrument_token",
    "segment",
)
# How many trailing validated archives' real NIFTY-option counts feed the
# rolling-average sanity check (Part A #4).
ROLLING_AVERAGE_WINDOW = 5
# A real, standard trailing-average drop-detection heuristic (not a fresh
# guess) -- flags a sudden, unexplained halving of real NIFTY-option
# contracts relative to recent real history.
ROLLING_AVERAGE_FLOOR_RATIO = 0.5


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


def _manifest_path(archive_dir: Path) -> Path:
    return archive_dir / VALIDATED_MANIFEST_FILENAME


def _load_validated_manifest(archive_dir: Path) -> list[dict]:
    path = _manifest_path(archive_dir)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # A corrupted manifest must never block or crash the scheduled
        # task -- treat it as no real validated history (falls back to
        # Part A #4's absolute floor rather than the rolling average).
        logger.warning("instrument_archive_manifest_corrupted path=%s", path)
        return []


def is_date_validated(archive_dir: Path, day: date) -> bool:
    """Part C: true only for a real archive that has already passed every
    Part A check -- the sole condition under which a later run must never
    overwrite it."""
    return any(entry.get("date") == day.isoformat() for entry in _load_validated_manifest(archive_dir))


def recent_validated_nifty_option_counts(archive_dir: Path, limit: int = ROLLING_AVERAGE_WINDOW) -> list[int]:
    manifest = _load_validated_manifest(archive_dir)
    return [entry["nifty_option_count"] for entry in manifest[-limit:]]


def _record_validated(archive_dir: Path, day: date, nifty_option_count: int) -> None:
    manifest = _load_validated_manifest(archive_dir)
    manifest.append(
        {
            "date": day.isoformat(),
            "nifty_option_count": nifty_option_count,
            "validated_at": datetime.now(IST).isoformat(),
        }
    )
    _manifest_path(archive_dir).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class ArchiveValidationResult:
    valid: bool
    reason: str
    nifty_option_count: int
    total_record_count: int


def validate_archive(
    path: Path, day: date, calendar: NseCalendar, recent_validated_counts: list[int]
) -> ArchiveValidationResult:
    """Brief 18 Part A: real checks applied to a just-written real archive
    file, before it is ever treated as a trustworthy success. Every real
    failure returns a specific, real reason -- never a generic "invalid"
    message -- so Part B's notification can say exactly what broke."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ArchiveValidationResult(False, f"could not read archive file: {exc}", 0, 0)
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as exc:
        return ArchiveValidationResult(False, f"invalid JSON: {exc}", 0, 0)
    if not isinstance(rows, list) or not rows:
        return ArchiveValidationResult(False, "archive contains no real records", 0, 0)

    missing_field_rows = [
        i for i, row in enumerate(rows) if any(field not in row for field in REQUIRED_INSTRUMENT_FIELDS)
    ]
    if missing_field_rows:
        first = missing_field_rows[0]
        missing = [f for f in REQUIRED_INSTRUMENT_FIELDS if f not in rows[first]]
        return ArchiveValidationResult(
            False,
            f"{len(missing_field_rows)} of {len(rows)} real records are missing required field(s) "
            f"data/instruments.py::parse_kite_instruments needs (e.g. record {first} is missing {missing})",
            0,
            len(rows),
        )

    wrong_exchange = sorted({row.get("exchange") for row in rows if row.get("exchange") != "NFO"})
    if wrong_exchange:
        return ArchiveValidationResult(
            False, f"segment mismatch: found non-NFO exchange value(s) {wrong_exchange}", 0, len(rows)
        )

    nifty_option_count = sum(1 for row in rows if row.get("name") == "NIFTY" and row.get("segment") == "NFO-OPT")
    if nifty_option_count < 1:
        return ArchiveValidationResult(
            False,
            "zero real NIFTY option records found -- this archive's entire stated purpose",
            nifty_option_count,
            len(rows),
        )
    if len(recent_validated_counts) >= 2:
        rolling_average = sum(recent_validated_counts) / len(recent_validated_counts)
        floor = rolling_average * ROLLING_AVERAGE_FLOOR_RATIO
        if nifty_option_count < floor:
            return ArchiveValidationResult(
                False,
                f"real NIFTY option count {nifty_option_count} is below "
                f"{ROLLING_AVERAGE_FLOOR_RATIO:.0%} of the rolling average of the last "
                f"{len(recent_validated_counts)} validated archives ({rolling_average:.1f})",
                nifty_option_count,
                len(rows),
            )

    if not calendar.is_trading_day(day):
        return ArchiveValidationResult(
            False, f"archived date {day.isoformat()} is not a real NSE trading day", nifty_option_count, len(rows)
        )

    return ArchiveValidationResult(True, "", nifty_option_count, len(rows))


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


def _archive_validation_failure_message(day: date, reason: str) -> str:
    """Part B: a distinct outcome from both a normal success and a normal
    (pre-write) failure -- the real file for `day` DID get written, but
    isn't trustworthy. Never the generic Brief 17 failure message."""
    return f"archive for {day.isoformat()} written but failed validation: {reason}"


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


def run_daily_archive(settings: Settings, archive_dir: Path = ARCHIVE_DIR) -> Path | None:
    """Fail-closed entry point for the real daily scheduled task (see
    scripts/archive_instruments.ps1 and the registered Windows Scheduled
    Task "NiftyAITrader-InstrumentArchive"). Returns None (never raises)
    when no valid real session exists today -- missing credentials, the
    kiteconnect package unavailable, or a real API failure (most often an
    expired/not-yet-refreshed access token) are all logged and treated
    the same honest way: nothing to archive today, try again once a
    fresh session exists, never fabricate a placeholder file. Also
    returns None (Brief 18) when a real session succeeds but the
    resulting file fails Part A validation -- a written-but-untrustworthy
    archive is deliberately treated the same as "nothing trustworthy was
    produced," even though a file now exists on disk for inspection.

    Also runs the real same-day gap safeguard first (see
    check_and_notify_missing_archive) -- checked unconditionally, before
    the credential check below, so a real gap in a *prior* day's archive
    still gets surfaced even on a day today's own session is also
    missing/expired. Separately, every real attempt here -- success,
    failure, or a written-but-invalid result -- also sends its own real
    status notification (notify_archive_status), so a silent success run
    is just as visible as a silent failure would otherwise be.

    Brief 18 Part C: once today's date is already recorded as validated,
    this returns the existing real path immediately without ever calling
    Kite again or touching the file -- a real, already-good archive is
    never silently overwritten by a later run the same day.
    """
    check_and_notify_missing_archive(settings, archive_dir=archive_dir)
    today = datetime.now(IST).date()
    if is_date_validated(archive_dir, today):
        logger.info("instrument_archive_already_validated date=%s", today)
        return archive_dir / f"nfo_instruments_{today.isoformat()}.json"
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
        path = archive_nfo_instruments(kite, archive_dir=archive_dir, today=today)
    except Exception as exc:  # noqa: BLE001 - a real, expired/invalid token (or any other real API failure) must be logged, never crash the scheduled task.
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("instrument_archive_failed error=%s", reason)
        notify_archive_status(settings, success=False, detail=_archive_failure_message(reason))
        return None

    result = validate_archive(path, today, NseCalendar(), recent_validated_nifty_option_counts(archive_dir))
    if not result.valid:
        logger.warning("instrument_archive_validation_failed date=%s reason=%s", today, result.reason)
        notify_archive_status(
            settings, success=False, detail=_archive_validation_failure_message(today, result.reason)
        )
        return None  # The real written file is left exactly as-is, for inspection -- not deleted, not retried this run.

    _record_validated(archive_dir, today, result.nifty_option_count)
    timestamp = datetime.now(IST)
    logger.info("instrument_archive_saved path=%s", path)
    notify_archive_status(
        settings,
        success=True,
        detail=_archive_success_message(today, result.total_record_count, timestamp),
    )
    return path
