"""Real gap found by auditing every integration's construction against
whether it's actually reached during a normal live day: self.telegram
was constructed in Orchestrator.__init__ but self.telegram.send_message
was only ever called from the crash-recovery CRITICAL path -- a normal
research/signal/entry/exit cycle notified Discord (via _event()) but
never Telegram at all. This proves both notifiers now receive the same
real event stream from a real Orchestrator.run_cycle(), not just that
the code compiles.
"""

from __future__ import annotations

from agents.orchestrator import Orchestrator
from config import Settings
from integrations.discord import DiscordNotifier
from integrations.telegram import TelegramNotifier


class _FakeResponse:
    ok = True
    status_code = 200


def _recording_transport(calls: list) -> callable:
    def transport(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeResponse()

    return transport


def test_a_normal_cycle_event_reaches_both_discord_and_telegram(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)

    discord_calls: list = []
    telegram_calls: list = []
    # Real-looking (not empty) webhook/token/chat_id -- both notifiers
    # short-circuit to a no-op on empty config (by design, confirmed by
    # reading their own send_message/send_embed implementations), so this
    # test needs non-empty values to actually exercise the send path,
    # same as every other real notification test in this suite.
    orchestrator.discord = DiscordNotifier(
        "https://discord.test/webhook", transport=_recording_transport(discord_calls)
    )
    orchestrator.telegram = TelegramNotifier(
        "fake-token", "fake-chat-id", transport=_recording_transport(telegram_calls)
    )

    # Even a minimal, empty-context cycle publishes real SYSTEM_STARTED/
    # MARKET_PREP_STARTED events unconditionally at the top of run_cycle
    # -- enough to prove the wiring without needing a full real
    # candidate/fill scenario.
    orchestrator.run_cycle({"market_data_fresh": False, "market_open": False})

    assert discord_calls  # already true before this fix -- confirms the test itself is exercising real events
    assert telegram_calls  # the real gap this fix closes: previously always empty for a normal cycle


def test_one_notifiers_real_failure_does_not_block_the_other(tmp_path):
    """Discord and Telegram are dispatched from separate try/except
    blocks in Orchestrator._event() -- a real failure in one must not
    prevent the other from being attempted."""
    settings = Settings(database_path=tmp_path / "paper.db")
    orchestrator = Orchestrator(settings)

    telegram_calls: list = []

    def failing_transport(*args, **kwargs):
        raise ConnectionError("simulated real Discord outage")

    orchestrator.discord = DiscordNotifier("https://discord.test/webhook", transport=failing_transport)
    orchestrator.telegram = TelegramNotifier(
        "fake-token", "fake-chat-id", transport=_recording_transport(telegram_calls)
    )

    # Must not raise -- a real notification failure is caught locally in
    # _event(), never propagated into the trading loop.
    orchestrator.run_cycle({"market_data_fresh": False, "market_open": False})

    assert telegram_calls  # Telegram still received the real event stream despite Discord's real failure
