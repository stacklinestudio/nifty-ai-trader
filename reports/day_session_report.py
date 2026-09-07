"""Automated end-of-day live-session report.

Extracted from the real, manually-compiled 2026-09-07 "Day 1 Live
Session Report" -- the exact same real queries against the exact same
real sources (`storage.database.Database`, `monitoring.system_health_
gate.check_option_tick_capture`, `data.instrument_archive`), now a
real, reusable, injectable-for-tests function instead of one-off ad
hoc queries run by hand.

One section from that manual report is NOT reproduced with real
content here: "incidents found and fixed during the session". There is
no real code anywhere in this project that detects "a bug was found
and fixed" -- that section only ever existed because a human compiled
it by hand, in conversation, after the fact. Rather than fabricate an
automated incident detector that does not exist, this module keeps the
section heading (so the format matches the original) and states
plainly that it requires manual compilation. See `build_day_session_
report`'s own docstring.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from config import IST, Settings
from data.instrument_archive import ARCHIVE_DIR, real_archive_status, real_gap_check_status
from integrations.discord import DiscordNotifier, webhooks_by_category_from_settings
from integrations.telegram import TelegramNotifier
from learning.memory import MemoryStore
from monitoring.logger import configure_logger
from monitoring.system_health_gate import check_option_tick_capture
from storage.database import Database

logger = configure_logger(__name__)

REPORTS_DIR = Path("reports/generated")


def _fmt_time(ts: str) -> str:
    return ts.split("T")[1].split(".")[0].split("+")[0]


def build_day_session_report(database: Database, settings: Settings, today: date) -> str:
    """The real report-compilation logic, reused exactly as built for
    2026-09-07: every real signal evaluated today (all fields, not a
    sample), a real tick-capture summary, real instrument-archive
    status, the full real event timeline, and confirmed real totals.
    Zero new computation -- every value here is read straight from the
    same real functions/tables the original manual report used
    (`Database.recent_signals`/`events`, `check_option_tick_capture`,
    `real_archive_status`/`real_gap_check_status`, `MemoryStore`).

    EV is deliberately not included per-signal, for the same real
    reason noted in the original report: it is not a field stored on
    individual signal records (only computed live, on demand, for the
    current/latest candidate) -- recomputing it here against today's
    settings/memory would not reflect what, if anything, was actually
    shown at that historical moment, so it is omitted rather than
    fabricated.
    """
    iso = today.isoformat()

    all_signals = database.recent_signals(limit=5000)
    today_signals = sorted(
        (s for s in all_signals if str(s.get("timestamp", "")).startswith(iso)), key=lambda s: s["timestamp"]
    )

    all_events = database.events(limit=20000)
    today_events = sorted(
        (e for e in all_events if str(e.get("timestamp", "")).startswith(iso)), key=lambda e: e["timestamp"]
    )

    capture_check = check_option_tick_capture(day=today)
    archive_status, archive_detail = real_archive_status()
    gap_status = real_gap_check_status(today=today)
    archive_files = sorted(ARCHIVE_DIR.glob("nfo_instruments_*.json")) if ARCHIVE_DIR.exists() else []

    memory = MemoryStore(settings.database_path)
    total_trades = len(memory.recent(memory_type="trade", limit=1_000_000))

    now_str = datetime.now(IST).isoformat(timespec="seconds")

    lines: list[str] = []
    a = lines.append

    a(f"# {iso} Live Session Report")
    a("")
    a(f"**Trading day:** {iso} &middot; **Compiled:** {now_str}")
    a("")
    a(
        "Factual compilation only. Every number below is pulled directly from today's real "
        f"database (`{settings.database_path}`) and real capture/archive files on disk -- no "
        "analysis, no recommendation."
    )
    a("")
    a("| | |")
    a("|---|---|")
    a(f"| Trading day | {iso} |")
    if today_events:
        a(f"| First real event | {_fmt_time(today_events[0]['timestamp'])} IST |")
        a(f"| Last real event | {_fmt_time(today_events[-1]['timestamp'])} IST |")
    else:
        a("| Real events today | none recorded |")
    a(f"| Total real trades (all time) | {total_trades} |")
    a("")
    a("---")
    a("")

    # --- 1. Signals ---
    a("## 1. Every real signal evaluated today")
    a("")
    if not today_signals:
        a("No real signal records for this date.")
    else:
        direction_counts = Counter(s["direction"] for s in today_signals)
        setup_counts = Counter(s["setup_type"] for s in today_signals)
        regime_counts = Counter(s["regime"] for s in today_signals)
        cleared = sum(1 for s in today_signals if s.get("cleared_threshold"))
        confidences = [s["confidence"] for s in today_signals]
        a(
            f"{len(today_signals)} real signal records, "
            f"`storage.database.Database.recent_signals()`, timestamps "
            f"{_fmt_time(today_signals[0]['timestamp'])}-{_fmt_time(today_signals[-1]['timestamp'])} IST."
        )
        a("")
        a("- Direction: " + ", ".join(f"{n} {d}" for d, n in direction_counts.most_common()))
        a("- Setup type: " + ", ".join(f"{n} {s}" for s, n in setup_counts.most_common()))
        a("- Regime: " + ", ".join(f"{n} {r}" for r, n in regime_counts.most_common()))
        a(f"- Confidence range: {min(confidences):.1f}-{max(confidences):.1f}")
        a(f"- Cleared the real confidence threshold: {cleared} of {len(today_signals)}")
        a("")
        a(
            "> **EV is not a stored field on individual signal records.** See this module's "
            "own docstring for why it is omitted here rather than recomputed or fabricated."
        )
        a("")
        a(
            "| Time (IST) | Setup | Dir | Regime | Confidence | Technical | Opening | Volume | "
            "Option Flow | Global | News | Risk Pen. | Data % | Cleared Threshold |"
        )
        a("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for s in today_signals:
            a(
                f"| {_fmt_time(s['timestamp'])} | {s['setup_type']} | {s['direction']} | {s['regime']} | "
                f"{s['confidence']:.1f} | {s['technical_score']:.1f} | {s['opening_score']:.1f} | "
                f"{s['volume_score']:.1f} | {s['option_score']:.1f} | {s['global_score']:.2f} | "
                f"{s['news_score']:.1f} | {s['risk_penalty']:.1f} | {s['data_completeness']:.0f} | "
                f"{'YES' if s.get('cleared_threshold') else 'no'} |"
            )
        a("")
        a(f"{len(today_signals)} rows total, all real, all today, sorted chronologically.")
    a("")
    a("---")
    a("")

    # --- 2. Tick capture ---
    a("## 2. Tick capture -- today, full day")
    a("")
    a(f"Status: **{capture_check.status}** -- {capture_check.detail}")
    a("")
    a(
        "Source: `monitoring.system_health_gate.check_option_tick_capture()`, real segment/"
        "tick/gap counts parsed from the real capture check's own detail string."
    )
    a("")
    a("---")
    a("")

    # --- 3. Archive ---
    a("## 3. Instrument archive status")
    a("")
    a(f"**{archive_status}** -- {archive_detail}")
    a("")
    a(f"Gap check: **{gap_status}**")
    a("")
    if archive_files:
        a(f"{len(archive_files)} real archive file(s) on disk, `{ARCHIVE_DIR}/`:")
        a("")
        a("| File | Size (bytes) |")
        a("|---|---:|")
        for f in archive_files:
            a(f"| {f.name} | {f.stat().st_size:,} |")
    else:
        a("No real archive files found on disk.")
    a("")
    a("---")
    a("")

    # --- 4. Events ---
    a("## 4. Full real event timeline")
    a("")
    if not today_events:
        a("No real events recorded for this date.")
    else:
        type_counts = Counter(e["event_type"] for e in today_events)
        a(
            f"{len(today_events)} real events, `storage.database.Database.events()`, "
            f"{_fmt_time(today_events[0]['timestamp'])}-{_fmt_time(today_events[-1]['timestamp'])} IST, "
            "chronological."
        )
        a("")
        a("Breakdown: " + ", ".join(f"{n} x {t}" for t, n in type_counts.most_common()))
        a("")
        a("| Time (IST) | Event Type | Agent | Real Output Summary |")
        a("|---|---|---|---|")
        for e in today_events:
            try:
                out = json.loads(e.get("output_summary") or "{}")
                detail = "; ".join(f"{k}={v}" for k, v in out.items())
            except (ValueError, TypeError):
                detail = str(e.get("output_summary", ""))
            detail = detail.replace("|", "\\|")
            a(f"| {_fmt_time(e['timestamp'])} | {e['event_type']} | {e.get('agent', '')} | {detail} |")
        a("")
        a(f"{len(today_events)} rows total -- every real SYSTEM/MARKET/TRADE/RISK event today, in order.")
    a("")
    a("---")
    a("")

    # --- 5. Incidents (structural placeholder only -- see module docstring) ---
    a("## 5. Real incidents found and fixed during today's live session")
    a("")
    a(
        "*Not automatically detected.* No code in this system currently tracks bugs found or "
        "fixed during a session -- the equivalent section in the original, manually-compiled "
        "2026-09-07 report existed only because a human compiled it by hand, in conversation, "
        "after the fact. This heading is kept so the report's structure matches that original "
        "format; populate it manually if a session-specific incident log is wanted for this day."
    )
    a("")
    a("---")
    a("")

    # --- 6. Confirmed values ---
    a("## 6. Confirmed real values")
    a("")
    a("| | |")
    a("|---|---:|")
    a(f"| Real trading days with a validated instrument archive on disk | {len(archive_files)} |")
    a(f'| Total real trades, all time (`MemoryStore.recent(memory_type="trade")`) | {total_trades} |')
    a("")
    a("---")
    a("")
    a(
        f"*Compiled from real, live-queried sources: `{settings.database_path}` (signals, events) "
        f"&middot; `{capture_check.detail}` &middot; `{ARCHIVE_DIR}/`. No fabricated or placeholder "
        f"values. Compiled {now_str}.*"
    )

    content = "\n".join(lines)
    return content.replace("&middot;", "·")


def save_day_session_report(database: Database, settings: Settings, today: date) -> Path:
    """Builds the real report and writes it to the real, predictable,
    dated path `reports/generated/day_<YYYY-MM-DD>_live_session_report.md`
    -- `reports/generated/` matches this project's own existing
    convention for generated output (already used by the real backtest
    report, already gitignored), rather than the `reports/` package
    directory itself, which holds this project's own source code."""
    content = build_day_session_report(database, settings, today)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"day_{today.isoformat()}_live_session_report.md"
    path.write_text(content, encoding="utf-8")
    return path


def _notify_report_ready(settings: Settings, today: date, path: Path) -> None:
    """Reuses the exact same real Discord "daily_report" channel /
    Telegram wiring already built for this exact purpose
    (`settings.discord_webhook_daily_report`) -- verbatim, matching
    the pattern `data/option_tick_capture.py`'s own notify helpers use.
    A notification failure here must never propagate -- see
    `generate_and_notify_day_report`'s own docstring for why. Named by
    real date, not an invented "Day N" counter this project has no
    real source for."""
    message = f"Live session report saved for {today.isoformat()}: {path}"
    try:
        discord = DiscordNotifier(
            settings.discord_webhook_url, webhooks_by_category=webhooks_by_category_from_settings(settings)
        )
        telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        discord.send_message("INFO", message, "daily_report")
        telegram.send_message("INFO", message)
    except Exception as exc:  # noqa: BLE001 - a notification failure must never break report generation.
        logger.warning("day_session_report_notify_failed error=%s: %s", type(exc).__name__, exc)


def _notify_report_failure(settings: Settings, today: date, reason: str) -> None:
    """Same real "daily_report" channel, same fail-closed shape --
    mirrors `data/option_tick_capture.py`'s own `_notify_capture_
    failure`."""
    message = f"Day {today.isoformat()} report generation FAILED: {reason}"
    try:
        discord = DiscordNotifier(
            settings.discord_webhook_url, webhooks_by_category=webhooks_by_category_from_settings(settings)
        )
        telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        discord.send_message("WARNING", message, "daily_report")
        telegram.send_message("WARNING", message)
    except Exception as exc:  # noqa: BLE001 - a notification failure must never break report generation.
        logger.warning("day_session_report_notify_failed error=%s: %s", type(exc).__name__, exc)


def generate_and_notify_day_report(
    settings: Settings, database: Database | None = None, today: date | None = None
) -> dict:
    """The single, real, fail-closed entry point -- called automatically
    from `main.py::start_day()` right after the real background option-
    tick-capture thread's own `.join()` returns, i.e. right after
    `option_tick_capture_complete` fires, the same real signal that
    already marks the session as genuinely done.

    Any failure ANYWHERE in this function (a real database error, a
    real file-write failure, a real notification failure) is caught
    here, logged, and reported via the real, existing Discord/Telegram
    "daily_report" channel -- never re-raised. This is a deliberate,
    absolute fail-closed boundary: `start_day()` has already completed
    every real trading step (health gate, archiving, tick capture, the
    trading scheduler) by the time this runs, and nothing about this
    purely after-the-fact summary step may affect that already-real
    outcome, or any real trading data, in any way. The entire body
    (including resolving `today`/`database`, not just the report build
    itself) lives inside one try/except -- a single, real point of
    failure this function can never raise past, deliberately, rather
    than trusting each individual step to fail closed on its own."""
    resolved_today = today
    try:
        resolved_today = resolved_today or datetime.now(IST).date()
        resolved_database = database or Database(settings.database_path)
        resolved_database.initialize()
        path = save_day_session_report(resolved_database, settings, resolved_today)
    except Exception as exc:  # noqa: BLE001 - report generation must never affect the real trading day.
        reason = f"{type(exc).__name__}: {exc}"
        logger.error("day_session_report_failed date=%s error=%s", resolved_today, reason)
        _notify_report_failure(settings, resolved_today or datetime.now(IST).date(), reason)
        return {"status": "FAILED", "detail": reason}

    logger.info("day_session_report_saved date=%s path=%s", resolved_today.isoformat(), path)
    _notify_report_ready(settings, resolved_today, path)
    return {"status": "OK", "detail": str(path)}
