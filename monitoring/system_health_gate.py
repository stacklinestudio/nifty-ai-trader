"""Brief 23: System Health Gate -- aggregates real, already-built checks
into one honest readiness answer. No new data sources, no new
intelligence: every check below is a real, deterministic threshold
against a real, already-computed value from an earlier brief (Brief
13's data_completeness, Brief 18's validate_archive, Brief 19/22's real
capture segment/gap files, the existing notification wiring). AI never
decides whether data is "good enough" here -- that would defeat the
entire point of a deterministic readiness gate.

This is a REPORTING tool only, in this brief. It does not block
`main.py run` or any other decision path -- whether to wire it as an
actual pre-flight gate that refuses to trade when BLOCKED is a separate,
future decision, made once real evidence from a few real days of this
report exists. Confirmed structurally: this module is never imported by
`agents/orchestrator.py` or any agent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from config import IST, Settings
from data.instrument_archive import ARCHIVE_DIR, real_archive_status
from data.option_tick_capture import CAPTURE_DIR, read_capture_gaps
from execution.paper_broker import PaperBroker
from integrations.discord import DiscordNotifier, webhooks_by_category_from_settings
from integrations.telegram import TelegramNotifier
from monitoring.logger import configure_logger
from risk.risk_manager import RiskManager
from storage.database import Database

logger = configure_logger(__name__)

OK = "OK"
FAIL = "FAIL"

# Real, justified minimum -- not invented silently. execution/live_
# context.py's real score_attribution has 7 components; of those,
# technical_score, opening_score, and risk_penalty are always real (no
# external dependency -- Brief 12/13). volume_score/option_score require
# 2 consecutive real option-chain snapshots to exist, which needs a
# continuously-running real capture pipeline this project does not yet
# have scheduled (Brief 19/22's own findings). global_score/news_score
# are real wiring over still-empty real data, a known, documented,
# unresolved gap (Brief 5/8). Requiring more than 3/7 would report
# BLOCKED every single real day for a structural reason distinct from
# an actual regression -- this threshold exists to catch a regression IN
# the guaranteed real baseline, not to enforce a target this system
# cannot yet structurally reach daily.
MIN_DATA_COMPLETENESS_PERCENT = 3 / 7 * 100  # ~42.9%


@dataclass(frozen=True)
class GateCheck:
    name: str
    status: str  # OK or FAIL
    detail: str  # the real underlying number/reason, never just pass/fail


@dataclass(frozen=True)
class GateReport:
    verdict: str  # READY or BLOCKED
    checks: tuple[GateCheck, ...]

    @property
    def blocking_reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.status == FAIL]

    def describe(self) -> str:
        lines = [f"System Health Gate: {self.verdict}"]
        for check in self.checks:
            lines.append(f"  [{check.status}] {check.name}: {check.detail}")
        if self.verdict == "BLOCKED":
            lines.append("BLOCKED: " + "; ".join(self.blocking_reasons))
        lines.append(
            "(Reporting only -- this gate does not block main.py run in this brief.)"
        )
        return "\n".join(lines)


def check_kite_connection(settings: Settings, kite_factory: Callable[[], object] | None = None) -> GateCheck:
    """Real, current session validity -- a real live API call
    (kite.profile()), not just "is a token string configured" (the
    weaker check monitoring/health.py::system_health's own "kite"
    component makes -- a real, expired-but-still-configured token, the
    exact situation this project hit at the start of Brief 19, would
    pass that check and fail this one)."""
    if not (settings.kite_api_key and settings.kite_access_token):
        return GateCheck("kite_connection", FAIL, "no real Kite credentials configured")
    if kite_factory is None:
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            return GateCheck("kite_connection", FAIL, "kiteconnect not installed")

        def kite_factory() -> object:
            kite = KiteConnect(api_key=settings.kite_api_key)
            kite.set_access_token(settings.kite_access_token)
            return kite

    try:
        kite = kite_factory()
        profile = kite.profile()
    except Exception as exc:  # noqa: BLE001 - any real API/auth failure means the session is not valid right now.
        return GateCheck("kite_connection", FAIL, f"real session invalid: {type(exc).__name__}: {exc}")
    return GateCheck("kite_connection", OK, f"real session valid, user_id={profile.get('user_id')}")


def check_ai_provider(settings: Settings) -> GateCheck:
    """Reuses monitoring/health.py::system_health's own real AI-provider
    check verbatim (`settings.ai_provider == "unavailable"`) -- a real
    configuration check, not a live API probe. A live probe would spend
    real money/quota against an account already confirmed (Brief 8 Part
    C) to have no credit balance, and this brief adds no new checks
    beyond what's already built."""
    if settings.ai_provider == "unavailable":
        return GateCheck("ai_provider", FAIL, "AI provider unavailable (UnavailableProvider selected)")
    return GateCheck("ai_provider", OK, f"provider={settings.ai_provider}")


def check_option_tick_capture(capture_dir: Path = CAPTURE_DIR, day: date | None = None) -> GateCheck:
    """Real status from the real capture segment/gap files Brief 19/22
    already write to disk -- CaptureSessionResult itself is never
    persisted anywhere (confirmed: no code writes it to a file), so this
    reads the real, already-computed artifacts a real capture session
    leaves behind, not a new manifest invented for this brief. Real gaps
    are reported for visibility, not treated as disqualifying by
    themselves -- Brief 22 built gap recording specifically so a
    real, honest gap is an expected, handled outcome, not a failure."""
    day = day or datetime.now(IST).date()
    segments = sorted(capture_dir.glob(f"nifty_option_ticks_{day.isoformat()}*.jsonl"))
    if not segments:
        return GateCheck("option_tick_capture", FAIL, f"no real capture segment found for {day.isoformat()}")
    tick_count = 0
    for segment in segments:
        with segment.open(encoding="utf-8") as handle:
            tick_count += sum(1 for _ in handle)
    gaps = read_capture_gaps(capture_dir, day)
    detail = f"{len(segments)} real segment(s), {tick_count} real ticks, {len(gaps)} real gap(s) for {day.isoformat()}"
    return GateCheck("option_tick_capture", OK, detail)


def check_instrument_archive(archive_dir: Path = ARCHIVE_DIR) -> GateCheck:
    """Reuses Brief 18's real validate_archive verbatim (via data/
    instrument_archive.py::real_archive_status, shared with main.py's
    Obsidian sync so both read the exact same real check)."""
    status, detail = real_archive_status(archive_dir)
    return GateCheck("instrument_archive", OK if status == "VALID" else FAIL, detail)


def check_data_completeness(
    database: Database, minimum: float = MIN_DATA_COMPLETENESS_PERCENT
) -> GateCheck:
    """The real, current data_completeness percentage from the most
    recently persisted real signal (Brief 13's work, storage/database.py
    ::Database.recent_signals) -- never recomputed, never guessed."""
    signals = database.recent_signals(limit=1)
    if not signals:
        return GateCheck("data_completeness", FAIL, "no real signal recorded yet")
    percent = signals[-1].get("data_completeness")
    if percent is None:
        return GateCheck("data_completeness", FAIL, "most recent real signal has no data_completeness field")
    if percent < minimum:
        return GateCheck(
            "data_completeness", FAIL, f"{percent:.1f}% (below the real minimum {minimum:.1f}%)"
        )
    return GateCheck("data_completeness", OK, f"{percent:.1f}%")


def check_notifications(settings: Settings) -> GateCheck:
    """Real reachability -- the same real send_message() calls `python
    main.py notifications` already makes, reused verbatim, not
    reimplemented."""
    telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    discord = DiscordNotifier(
        settings.discord_webhook_url, webhooks_by_category=webhooks_by_category_from_settings(settings)
    )
    telegram_ok = telegram.send_message("INFO", "System Health Gate check; no trade.")
    discord_ok = discord.send_message("INFO", "System Health Gate check; no trade.")
    detail = (
        f"telegram={'reachable' if telegram_ok else 'unreachable/not configured'}, "
        f"discord={'reachable' if discord_ok else 'unreachable/not configured'}"
    )
    return GateCheck("notifications", OK if (telegram_ok or discord_ok) else FAIL, detail)


def check_risk_and_broker_construction(settings: Settings) -> GateCheck:
    """A sanity check, not new logic: confirms RiskManager/PaperBroker
    are constructible from the current real Settings, exactly as
    main.py::engine already constructs them."""
    try:
        RiskManager(settings.max_risk_per_trade, settings.max_position_value)
        PaperBroker(settings.tick_size, settings.entry_slippage_ticks, settings.exit_slippage_ticks)
    except Exception as exc:  # noqa: BLE001 - any construction failure here is exactly what this check exists to catch.
        return GateCheck("risk_and_broker", FAIL, f"construction failed: {type(exc).__name__}: {exc}")
    return GateCheck("risk_and_broker", OK, "RiskManager/PaperBroker construct cleanly from current real Settings")


def run_system_health_gate(
    settings: Settings,
    database: Database,
    kite_factory: Callable[[], object] | None = None,
    capture_dir: Path = CAPTURE_DIR,
    archive_dir: Path = ARCHIVE_DIR,
    today: date | None = None,
) -> GateReport:
    """The gate itself: BLOCKED if any single real check fails, with
    every real blocking reason named explicitly (never just the first
    one found)."""
    today = today or datetime.now(IST).date()
    checks = (
        check_kite_connection(settings, kite_factory),
        check_ai_provider(settings),
        check_option_tick_capture(capture_dir, today),
        check_instrument_archive(archive_dir),
        check_data_completeness(database),
        check_notifications(settings),
        check_risk_and_broker_construction(settings),
    )
    verdict = "READY" if all(check.status == OK for check in checks) else "BLOCKED"
    logger.info("system_health_gate_verdict=%s", verdict)
    return GateReport(verdict, checks)
