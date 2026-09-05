"""Real gap found by auditing every integration's construction against
whether it's actually reached during a normal live day: ObsidianExporter
was only ever invoked by the standalone `export-obsidian` CLI command
(a single placeholder note on manual request) -- never during a normal
live day. Proves, with real files written to a real (tmp_path) vault,
that a real trade close and a real completed day both now write real
journal entries automatically.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agents.orchestrator import Orchestrator
from config import IST, Settings
from data.instruments import OptionInstrument
from data.option_chain import OptionQuote


def filled_cycle_context() -> dict:
    instrument = OptionInstrument(
        "NIFTY24CE", 22000, datetime.now(IST).date() + timedelta(days=3), "CE", 25
    )
    quote = OptionQuote(instrument, 10, datetime.now(IST), 9.75, 10.25, 1000)
    return {
        "candidate_direction": "CALL",
        "candidate_confidence": 88,
        "entry_zone": (10.0, 10.5),
        "stop_zone": (8.0, 8.5),
        "target_zone": (13.0, 14.0),
        "option_quotes": [quote],
        "spot": 22000,
        "option_atr": 1,
        "market_data_fresh": True,
        "market_open": True,
        "features": {"ema_fast": 2, "ema_slow": 1, "close": 2, "vwap": 1, "atr": 10},
    }


def market_open_time() -> datetime:
    return datetime(2026, 8, 24, 10, 0, tzinfo=IST)


def test_a_real_trade_close_writes_a_real_trade_journal_entry(tmp_path):
    vault = tmp_path / "vault"
    settings = Settings(
        database_path=tmp_path / "paper.db", obsidian_vault_path=str(vault), max_trades_per_day=1
    )
    orchestrator = Orchestrator(settings)
    now = market_open_time()

    cycle = orchestrator.run_cycle(filled_cycle_context())
    assert cycle.order is not None
    state = orchestrator.open_position(cycle, now=now)

    result = orchestrator.supervise_once(state, 20.0, now)  # comfortably past target -- real TAKE_PROFIT
    assert result.should_exit and result.reason == "TAKE_PROFIT"

    journal_dir = vault / "NIFTY AI Trader" / "06-Trades" / str(now.year) / now.date().isoformat()
    assert journal_dir.exists()
    files = list(journal_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "NIFTY24CE" in content
    assert "TAKE_PROFIT" in content
    assert "pnl" in content.lower()


def test_a_completed_day_writes_a_real_daily_research_entry(tmp_path, monkeypatch):
    """run_scheduled_day's own calendar/clock are real (datetime.now(IST)),
    which makes calling it unmodified in a test dependent on today's real
    weekday and, on a real trading day, a real waiting loop -- exactly the
    class of wall-clock-drift fragility already found elsewhere in this
    suite. Instead, monkeypatch main.run_trading_day/resume_open_positions
    (imported names main.py actually calls) so this test is fast and
    deterministic on any day, while still exercising the REAL export call
    with a REAL summary dict and a REAL file write to tmp_path."""
    import main
    from execution.scheduler import DayResult

    vault = tmp_path / "vault"
    settings = Settings(database_path=tmp_path / "paper.db", obsidian_vault_path=str(vault))

    monkeypatch.setattr(main, "resume_open_positions", lambda *a, **k: [])
    monkeypatch.setattr(
        main, "run_trading_day", lambda *a, **k: DayResult(ran=True, reason="scan_cutoff_reached", rounds=[])
    )

    result = main.run_scheduled_day(settings)

    assert result["day_ran"] is True
    research_dir = vault / "NIFTY AI Trader" / "08-Reports"
    assert research_dir.exists()
    files = list(research_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "day_reason" in content


def test_no_vault_configured_is_fail_closed_not_a_crash(tmp_path):
    """The real, common case (no ObsidianVaultPath configured) must not
    raise or otherwise disrupt a real trade close."""
    settings = Settings(database_path=tmp_path / "paper.db", obsidian_vault_path="", max_trades_per_day=1)
    orchestrator = Orchestrator(settings)
    now = market_open_time()

    cycle = orchestrator.run_cycle(filled_cycle_context())
    state = orchestrator.open_position(cycle, now=now)

    result = orchestrator.supervise_once(state, 20.0, now)  # must not raise

    assert result.should_exit and result.reason == "TAKE_PROFIT"


def test_a_real_vault_write_failure_does_not_break_the_trading_loop(tmp_path, monkeypatch):
    """A real OSError-raising vault path (a file where a directory is
    expected) must not prevent the real trade from closing correctly --
    fail closed exactly like Discord/Telegram."""
    # A regular file at the vault root -- ObsidianExporter will try to
    # mkdir underneath it and hit a real OSError.
    blocked_vault = tmp_path / "not_a_directory"
    blocked_vault.write_text("not a real vault")
    settings = Settings(
        database_path=tmp_path / "paper.db", obsidian_vault_path=str(blocked_vault), max_trades_per_day=1
    )
    orchestrator = Orchestrator(settings)
    now = market_open_time()

    cycle = orchestrator.run_cycle(filled_cycle_context())
    state = orchestrator.open_position(cycle, now=now)

    result = orchestrator.supervise_once(state, 20.0, now)  # must not raise despite the real write failure

    assert result.should_exit and result.reason == "TAKE_PROFIT"
    assert orchestrator.paper_broker.get_positions() == []  # the real trade still closed correctly
