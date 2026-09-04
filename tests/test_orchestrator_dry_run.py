"""Brief 10 Part 5: a real incident this session -- a one-off
investigation script built `Orchestrator(Settings())` without realizing
Settings() had already picked up this session's real Discord/Telegram
credentials from .env.local, and a normal cycle sent synthetic test data
to real channels. The fix at the time was manually zeroing every
notification field on Settings one at a time -- easy to get wrong by
omission (miss one of 8 fields). Orchestrator(..., dry_run=True) replaces
that with one flag, verified here to actually produce no-op-by-
construction notifiers regardless of what real-looking credentials
Settings carries.
"""

from __future__ import annotations

from agents.orchestrator import Orchestrator
from config import Settings


def _settings_with_real_looking_credentials(tmp_path) -> Settings:
    # Deliberately non-empty, real-shaped values -- exactly what a
    # Settings() built after main.py's load_dotenv(".env.local") would
    # carry in a real deployment with real notification channels wired up.
    return Settings(
        database_path=tmp_path / "paper.db",
        telegram_bot_token="real-looking-token",
        telegram_chat_id="real-looking-chat-id",
        discord_webhook_url="https://discord.com/api/webhooks/real/looking",
        obsidian_vault_path=str(tmp_path / "vault"),
    )


def test_dry_run_produces_genuinely_unconfigured_notifiers_despite_real_looking_settings(tmp_path):
    settings = _settings_with_real_looking_credentials(tmp_path)

    orchestrator = Orchestrator(settings, dry_run=True)

    # Real fail-closed check, not just "some flag is set" -- these are the
    # exact conditions DiscordNotifier.send_embed/TelegramNotifier.
    # send_message/ObsidianExporter.export already short-circuit on.
    assert orchestrator.telegram.token == ""
    assert orchestrator.telegram.chat_id == ""
    assert orchestrator.discord.webhook_url == ""
    assert orchestrator.obsidian.root is None


def test_dry_run_false_keeps_the_real_configured_notifiers_unaffected_default_behavior(tmp_path):
    """dry_run defaults to False -- main.py's real live path, and every
    existing test that never passes it, must be completely unaffected."""
    settings = _settings_with_real_looking_credentials(tmp_path)

    orchestrator = Orchestrator(settings)

    assert orchestrator.telegram.token == "real-looking-token"
    assert orchestrator.discord.webhook_url == "https://discord.com/api/webhooks/real/looking"
    assert orchestrator.obsidian.root is not None


def test_dry_run_does_not_affect_the_ai_router(tmp_path):
    """dry_run is specifically about notification/vault side effects, not
    AI provider selection -- Brief 10 Part A's own real-AI investigation
    needed genuine AI output with zero real notifications, exactly this
    combination."""
    settings = Settings(
        database_path=tmp_path / "paper.db",
        ai_provider="anthropic",
        anthropic_api_key="real-looking-key",
    )

    orchestrator = Orchestrator(settings, dry_run=True)

    assert type(orchestrator.ai_router.provider).__name__ == "AnthropicProvider"


def test_a_real_cycle_under_dry_run_runs_normally_and_touches_no_real_transport(tmp_path):
    """Not just construction -- a real run_cycle() must not attempt to
    reach a real transport at all under dry_run."""

    def failing_transport(*args, **kwargs):
        raise AssertionError("dry_run must never reach a real transport")

    settings = _settings_with_real_looking_credentials(tmp_path)
    orchestrator = Orchestrator(settings, dry_run=True)
    orchestrator.telegram.transport = failing_transport
    orchestrator.discord.transport = failing_transport

    # Must not raise -- send_message/send_embed both short-circuit before
    # ever calling transport when unconfigured (token/chat_id/webhook_url
    # empty), which is exactly what dry_run guarantees.
    orchestrator.run_cycle({"market_data_fresh": False, "market_open": False})
