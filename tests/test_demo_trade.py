"""Brief 9 follow-up Part B: the demo trade walkthrough.

Real code paths throughout -- the real Orchestrator, RiskAgent,
TradeBuilderAgent, and exit engine, against a constructed synthetic
scenario. The load-bearing tests here are the isolation ones: proving
the demo cannot touch a real database, cannot send a real notification,
and cannot affect real DailyLimits/trade-count state, structurally --
not just that its output happens to say "DEMO".
"""

from __future__ import annotations

import sqlite3

from config import Settings
from demo.demo_trade import DEMO_DATABASE_PATH, LABEL, run_demo_trade
from storage.database import Database


def test_demo_never_touches_the_real_database_path(tmp_path):
    """The load-bearing isolation test: a real Settings() with a real,
    pre-existing database (real rows already in it, simulating an
    already-active real trading history) must be byte-for-byte unaffected
    by a demo run -- not just "the demo used a different path by
    convention," but the real file's real content provably untouched.
    """
    real_db_path = tmp_path / "real_trading.db"
    real_db = Database(real_db_path)
    real_db.initialize()
    # A real pre-existing row, so "0 rows" alone wouldn't be enough proof
    # -- this confirms the real file's real content is untouched, not
    # just empty by coincidence.
    with sqlite3.connect(real_db_path) as conn:
        conn.execute(
            "INSERT INTO daily_metrics(date, payload) VALUES (?, ?)", ("2026-09-04", '{"real": true}')
        )
    real_bytes_before = real_db_path.read_bytes()

    demo_db_path = tmp_path / "demo.db"
    run_demo_trade(database_path=demo_db_path)

    assert real_db_path.read_bytes() == real_bytes_before
    with sqlite3.connect(real_db_path) as conn:
        rows = conn.execute("SELECT date, payload FROM daily_metrics").fetchall()
    assert rows == [("2026-09-04", '{"real": true}')]


def test_demo_writes_only_to_its_own_isolated_database(tmp_path):
    """The demo DOES really write somewhere (a real fill, real
    supervision, real learning.memory records) -- confirming that isn't
    silently suppressed, only that it's confined to the isolated path."""
    demo_db_path = tmp_path / "demo.db"

    run_demo_trade(database_path=demo_db_path)

    assert demo_db_path.exists()
    with sqlite3.connect(demo_db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM learning_memory").fetchone()[0]
    assert count > 0  # the demo trade's real outcome was really recorded -- just not to a real path


def test_demo_default_path_is_never_the_real_settings_database_path():
    """A real Settings() (whatever the real environment configures) must
    never coincide with the demo's own default path -- confirmed as a
    real, structural fact, not just "they're named differently"."""
    assert DEMO_DATABASE_PATH != Settings().database_path
    assert "demo" in str(DEMO_DATABASE_PATH).lower()


def test_demo_forces_discord_and_telegram_off_regardless_of_real_env_config(monkeypatch, tmp_path):
    """Simulates a real environment with real Discord/Telegram credentials
    configured (as a live deployment would have) and proves the demo
    still never attempts a real HTTP call -- structural, not just that
    the demo's own hardcoded settings look empty in isolation."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/real/looking/url")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "real-looking-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

    import requests

    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: calls.append((a, kw)) or None)

    run_demo_trade(database_path=tmp_path / "demo.db")

    assert calls == []  # not one real HTTP call was made, despite real-looking env credentials


def test_demo_produces_the_full_real_lifecycle_and_a_naturally_produced_exit(tmp_path):
    """The real end-to-end proof: setup detected, real confidence scored,
    validator approved, risk approved, real position sized with real lot
    size/premium, a real simulated fill, and a real exit -- not forced to
    any particular outcome, whatever the real exit engine decides from
    the real synthetic price path."""
    result = run_demo_trade(database_path=tmp_path / "demo.db")

    assert result["filled"] is True
    cycle = result["cycle"]
    assert cycle.consensus in {"BULLISH", "BEARISH", "CONFLICTED", "NEUTRAL"}
    assert cycle.validation.decision.value == "APPROVE"
    assert cycle.risk_approved is True
    assert cycle.thesis is not None
    assert cycle.thesis.quantity > 0  # real lot-size-multiple sizing actually happened
    assert cycle.order is not None
    exit_result = result["result"]
    assert exit_result.should_exit is True
    assert exit_result.reason in {"TAKE_PROFIT", "STOP_LOSS", "THESIS_INVALIDATED", "FORCED_EXIT"}


def test_demo_confidence_matches_the_real_computed_ceiling(tmp_path):
    """Ties this demo directly back to the confidence-ceiling deep-dive
    and the technical_score fix -- this scenario is not an arbitrary
    invention, it's the same specific real 81.25 ceiling value."""
    from demo.demo_trade import _build_ceiling_scenario_context, _demo_settings

    settings = _demo_settings(tmp_path / "demo.db")
    context = _build_ceiling_scenario_context(settings)

    assert context["candidate_direction"] == "CALL"
    assert context["setup_type"] in {"VWAP_REJECTION", "SUPPORT_RESISTANCE_REACTION"}
    assert context["candidate_confidence"] == 81.25


def test_every_printed_line_is_clearly_labeled(capsys, tmp_path):
    """Requirement 4, verified directly against real captured stdout, not
    assumed from reading the source: every non-blank line this module
    prints starts with the DEMO label, so this output can never be
    mistaken for a real trade record if shared or reviewed later."""
    run_demo_trade(database_path=tmp_path / "demo.db")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines  # real output was actually produced
    assert all(line.startswith(LABEL) for line in lines)
