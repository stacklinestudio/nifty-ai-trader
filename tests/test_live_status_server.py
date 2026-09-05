"""Brief 25: a small, local, read-only live trade-monitoring page.
Real end-to-end tests run an actual `ThreadingHTTPServer` against a real
tmp_path SQLite database and make real HTTP GET requests -- the same
real `open_positions` table `Orchestrator` already maintains, updated
exactly the way a real entry/trailing-stop-update/exit would.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from agents.contracts import TradeCandidate, TradeThesis
from config import IST, Settings
from execution.position_persistence import position_state_to_dict
from execution.position_supervisor import PositionState
from monitoring.live_status_server import (
    build_live_status_server,
    build_mock_demo_position,
    current_position_view,
    live_status_url,
    real_local_ip,
    render_page,
)
from storage.database import Database


def _real_thesis(entry: float = 100.0, stop: float = 95.0, target: float = 115.0, quantity: int = 65) -> TradeThesis:
    candidate = TradeCandidate(
        direction="CALL",
        setup_type="MOMENTUM_CONTINUATION",
        underlying="NIFTY",
        confidence=80.0,
        evidence=("real evidence",),
        invalidations=(),
        entry_zone=(entry - 0.5, entry + 0.5),
        stop_zone=(stop - 0.5, stop + 0.5),
        target_zone=(target - 0.5, target + 0.5),
    )
    return TradeThesis(
        candidate, "NIFTY26SEP24000CE", entry, stop, target, quantity, 325.0, 80.0, ("real evidence",), ()
    )


def _open_real_position(database: Database, thesis: TradeThesis, order_id: str = "order-1") -> PositionState:
    opened_at = datetime.now(IST)
    state = PositionState.opening(thesis, opened_at, entry_order_id=order_id)
    database.save_open_position(order_id, opened_at.isoformat(), position_state_to_dict(state))
    return state


# --- current_position_view / render_page (pure, no server needed) ------


def test_current_position_view_reports_no_open_position_plainly(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()

    view = current_position_view(database)

    assert view == {"open": False}


def test_current_position_view_reflects_the_real_open_position_row(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    thesis = _real_thesis(entry=100.0, stop=95.0, target=115.0, quantity=65)
    state = _open_real_position(database, thesis)
    state.observe(108.0, datetime.now(IST), 0.15)
    database.save_open_position(state.entry_order_id, state.opened_at.isoformat(), position_state_to_dict(state))

    view = current_position_view(database)

    assert view["open"] is True
    assert view["symbol"] == "NIFTY26SEP24000CE"
    assert view["direction"] == "CALL"
    assert view["entry"] == 100.0
    assert view["current_ltp"] == 108.0
    assert view["target"] == 115.0
    assert view["quantity"] == 65
    assert view["unrealized_pnl"] == pytest.approx((108.0 - 100.0) * 65)


def test_render_page_says_no_open_position_plainly_never_stale_data():
    html = render_page({"open": False})

    assert "No open position" in html
    assert "100.00" not in html  # no leftover figure from a hypothetical prior trade


def test_render_page_shows_the_real_open_position_fields():
    view = {
        "open": True,
        "symbol": "NIFTY26SEP24000CE",
        "direction": "CALL",
        "setup_type": "MOMENTUM_CONTINUATION",
        "entry": 100.0,
        "current_ltp": 108.0,
        "current_stop": 96.2,
        "original_stop": 95.0,
        "stop_was_trailed": True,
        "target": 115.0,
        "quantity": 65,
        "unrealized_pnl": 520.0,
        "opened_at": "2026-09-08T09:20:00+05:30",
        "last_quote_at": "2026-09-08T09:25:00+05:30",
        "mae": 0.0,
        "mfe": 8.0,
    }

    html = render_page(view)

    assert "NIFTY26SEP24000CE" in html
    assert "100.00" in html
    assert "108.00" in html
    assert "96.20" in html
    assert "115.00" in html
    assert "+520.00" in html
    assert "trailed" in html.lower()
    assert "refresh" in html.lower()  # the real auto-refresh mechanism is present


def test_render_page_never_offers_any_control():
    view = {
        "open": True, "symbol": "X", "direction": "CALL", "setup_type": "Y", "entry": 1.0, "current_ltp": 1.0,
        "current_stop": 1.0, "original_stop": 1.0, "stop_was_trailed": False, "target": 1.0, "quantity": 1,
        "unrealized_pnl": 0.0, "opened_at": "x", "last_quote_at": "x", "mae": 0.0, "mfe": 0.0,
    }

    html = render_page(view)

    assert "<form" not in html.lower()
    assert "<button" not in html.lower()
    assert "read-only" in html.lower()


# --- real, structural read-only proof -----------------------------------


def test_live_status_handler_defines_no_write_methods():
    """Read-only by construction, not convention: the handler class must
    not define do_POST/do_PUT/do_DELETE/do_PATCH -- BaseHTTPRequestHandler's
    own default response for any of those is a real 501."""
    from monitoring.live_status_server import _make_handler  # the module's own handler factory

    handler_class = _make_handler(database=None)
    for method in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
        assert method not in handler_class.__dict__


# --- real end-to-end: an actual server, real HTTP requests --------------


@pytest.fixture
def live_server(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    server = build_live_status_server(database, port=0)  # port=0 -- OS picks a real free local port
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, database
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(server, path: str = "/live") -> tuple[int, str]:
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def test_real_server_shows_no_open_position_when_nothing_is_open(live_server):
    server, _database = live_server

    status, body = _get(server)

    assert status == 200
    assert "No open position" in body


def test_real_server_reflects_a_real_entry_a_real_trailing_stop_update_and_a_real_exit(live_server):
    """The required test: entry, a real trailing-stop update, and exit,
    each reflected live by the real running server as they happen."""
    server, database = live_server
    thesis = _real_thesis(entry=100.0, stop=95.0, target=130.0, quantity=65)

    # 1. A real entry.
    state = _open_real_position(database, thesis)
    status, body = _get(server)
    assert status == 200
    assert "NIFTY26SEP24000CE" in body
    assert "100.00" in body  # real entry
    assert "95.00" in body  # real, untrailed stop

    # 2. A real trailing-stop update (price moves favorably, matching
    # execution/position_supervisor.py::PositionState.observe's own real
    # trailing math -- exercised here exactly as run_supervised would).
    state.observe(120.0, datetime.now(IST) + timedelta(minutes=5), trail_pct=0.15)
    database.save_open_position(state.entry_order_id, state.opened_at.isoformat(), position_state_to_dict(state))
    assert state.current_stop != 95.0  # confirms the real trail actually moved it before asserting the page shows it

    status, body = _get(server)
    assert status == 200
    assert "120.00" in body  # real, updated LTP
    assert f"{state.current_stop:.2f}" in body  # the real, live-trailed stop -- not the original
    assert "trailed" in body.lower()

    # 3. A real exit -- the same real close_open_position call
    # Orchestrator._close_position makes.
    database.close_open_position(state.entry_order_id)
    status, body = _get(server)
    assert status == 200
    assert "No open position" in body
    assert "NIFTY26SEP24000CE" not in body  # no stale data from the just-closed real trade


def test_real_server_is_read_only_a_post_is_rejected(live_server):
    server, _database = live_server
    port = server.server_address[1]
    request = urllib.request.Request(f"http://127.0.0.1:{port}/live", method="POST", data=b"{}")

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request, timeout=5)

    assert exc_info.value.code == 501  # Not Implemented -- no write path exists at all


def test_real_server_returns_404_for_an_unknown_path(live_server):
    server, _database = live_server

    status, _body = _get(server, "/close-position")  # a hypothetical control path that must not exist

    assert status == 404


# --- real local IP / URL helpers ----------------------------------------


def test_real_local_ip_returns_a_real_looking_local_address_never_raises():
    ip = real_local_ip()

    assert isinstance(ip, str)
    assert ip.count(".") == 3  # a real IPv4-shaped address


def test_live_status_url_uses_the_real_configured_port():
    settings = Settings(live_status_port=9999)

    url = live_status_url(settings, ip="192.168.1.50")

    assert url == "http://192.168.1.50:9999/live"


# --- Wired into the real trading loop -----------------------------------


def _filled_cycle_context() -> dict:
    from data.instruments import OptionInstrument
    from data.option_chain import OptionQuote

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


def test_a_real_paper_fill_event_includes_a_real_live_status_link(tmp_path):
    """Requirement #3: a real PAPER_FILL event carries a real link to
    this page in its existing notification payload."""
    import json

    from agents.orchestrator import Orchestrator

    settings = Settings(database_path=tmp_path / "paper.db", max_trades_per_day=1)
    orchestrator = Orchestrator(settings, dry_run=True)

    cycle = orchestrator.run_cycle(_filled_cycle_context())

    assert cycle.order is not None
    events = orchestrator.database.events()
    paper_fill_events = [e for e in events if e["event_type"] == "PAPER_FILL"]
    assert len(paper_fill_events) == 1
    output = json.loads(paper_fill_events[0]["output_summary"])
    assert "live_status_url" in output
    assert output["live_status_url"].startswith("http://")
    assert output["live_status_url"].endswith(f":{settings.live_status_port}/live")


def test_supervise_once_keeps_the_real_persisted_position_state_current(tmp_path):
    """The real fix this brief made: open_positions used to be written
    once, at open time, and go stale immediately. Confirms a real
    supervised tick updates the real persisted row too, not just the
    real in-memory PositionState -- this is what makes the live status
    page's real LTP/trailed-stop actually current, not a snapshot."""
    from agents.orchestrator import Orchestrator

    settings = Settings(database_path=tmp_path / "paper.db", max_trades_per_day=1)
    orchestrator = Orchestrator(settings, dry_run=True)
    cycle = orchestrator.run_cycle(_filled_cycle_context())
    now = datetime.now(IST)
    state = orchestrator.open_position(cycle, now=now)

    rows_at_open = orchestrator.database.open_positions()
    assert len(rows_at_open) == 1
    assert rows_at_open[0]["state"]["last_valid_ltp"] == state.thesis.entry  # real, unmoved yet

    # A real supervised tick with a new real LTP, comfortably short of
    # target/stop so the position stays open.
    orchestrator.supervise_once(state, state.thesis.entry + 1.0, now + timedelta(minutes=1))

    rows_after_tick = orchestrator.database.open_positions()
    assert len(rows_after_tick) == 1
    assert rows_after_tick[0]["state"]["last_valid_ltp"] == state.thesis.entry + 1.0  # the real, fresh value


def test_monitoring_live_status_server_imports_standalone_without_agents_orchestrator_first():
    """A real bug found while manually running `python main.py live-status`:
    importing this module before `agents.orchestrator` has ever been
    imported raised a real ImportError (execution.position_persistence
    -> agents.contracts -> agents/__init__.py eagerly importing
    agents.orchestrator, which itself imports this module for
    live_status_url -- a genuine circular import). `python main.py ...`
    happened to work only because main.py's own import order loads
    agents.orchestrator first by chance -- this test proves the module
    no longer depends on import order at all, using a real subprocess
    so Python's module cache from this test run can't hide the bug."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "from monitoring.live_status_server import live_status_url"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


# --- Brief 26: demo/mock live status data, structurally isolated -------


def test_build_mock_demo_position_is_clearly_synthetic():
    view = build_mock_demo_position()

    assert view["open"] is True
    assert view["is_demo"] is True
    assert view["symbol"].startswith("DEMO-")
    assert view["setup_type"] == "DEMO_SETUP"


def test_database_demo_position_round_trips_and_is_a_real_singleton(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    now = datetime.now(IST)

    assert database.demo_position() is None  # honest: nothing written yet

    view = build_mock_demo_position(now)
    database.save_demo_position(view, now)
    assert database.demo_position() == view

    # A real singleton -- saving again overwrites, never accumulates a
    # second row.
    other_view = build_mock_demo_position(now)
    other_view["symbol"] = "DEMO-DIFFERENT"
    database.save_demo_position(other_view, now)
    assert database.demo_position()["symbol"] == "DEMO-DIFFERENT"

    database.clear_demo_position()
    assert database.demo_position() is None


def test_database_demo_position_table_is_wholly_separate_from_open_positions(tmp_path):
    """Structural isolation, the required property: writing a demo
    position must never touch the real open_positions table at all."""
    database = Database(tmp_path / "paper.db")
    database.initialize()

    database.save_demo_position(build_mock_demo_position(), datetime.now(IST))

    assert database.open_positions() == []  # completely untouched


def test_current_position_view_falls_back_to_demo_when_nothing_real_is_open(tmp_path):
    database = Database(tmp_path / "paper.db")
    database.initialize()
    demo_view = build_mock_demo_position()
    database.save_demo_position(demo_view, datetime.now(IST))

    view = current_position_view(database)

    assert view == demo_view


def test_current_position_view_prefers_a_real_open_position_over_demo_data(tmp_path):
    """The required safeguard: a real open position always takes
    priority, so lingering demo data can never mask or be confused with
    a real one."""
    database = Database(tmp_path / "paper.db")
    database.initialize()
    database.save_demo_position(build_mock_demo_position(), datetime.now(IST))
    thesis = _real_thesis(entry=100.0, stop=95.0, target=115.0, quantity=65)
    _open_real_position(database, thesis)

    view = current_position_view(database)

    assert view["is_demo"] is False
    assert view["symbol"] == "NIFTY26SEP24000CE"


def test_render_page_shows_the_demo_banner_prominently_for_demo_data():
    view = build_mock_demo_position()

    html = render_page(view)

    assert "DEMO DATA" in html
    assert "NOT A REAL POSITION" in html
    assert "DEMO" in html  # in the <title> too
    assert html.count("DEMO DATA") >= 2  # top and bottom of the page, not just once


def test_render_page_never_shows_the_demo_banner_for_a_real_position():
    """Checks the real, VISIBLE banner text is absent -- not the mere
    substring "DEMO", which also appears (harmlessly) inside the static
    `.demo-banner` CSS class selector present in every page's <style>
    block regardless of is_demo, since that class must exist for
    whenever the banner IS shown."""
    view = {
        "open": True, "is_demo": False, "symbol": "REAL-NIFTY24CE", "direction": "CALL", "setup_type": "Y",
        "entry": 1.0, "current_ltp": 1.0, "current_stop": 1.0, "original_stop": 1.0, "stop_was_trailed": False,
        "target": 1.0, "quantity": 1, "unrealized_pnl": 0.0, "opened_at": "x", "last_quote_at": "x",
        "mae": 0.0, "mfe": 0.0,
    }

    html = render_page(view)

    assert "DEMO DATA" not in html
    assert "NOT A REAL POSITION" not in html
    assert "demo-banner\">" not in html  # the banner <div> itself is never emitted


def test_render_page_never_shows_the_demo_banner_when_nothing_is_open():
    html = render_page({"open": False})

    assert "DEMO DATA" not in html
    assert "NOT A REAL POSITION" not in html
    assert "demo-banner\">" not in html


def test_real_server_shows_the_demo_banner_across_multiple_real_refreshes(live_server):
    """Requirement #3: the mock page auto-refreshes and correctly shows
    the demo label at all times, including after a refresh -- proven
    with real, repeated HTTP GETs against an actual running server."""
    server, database = live_server
    database.save_demo_position(build_mock_demo_position(), datetime.now(IST))

    for _ in range(3):  # simulates several real auto-refresh cycles
        status, body = _get(server)
        assert status == 200
        assert "DEMO DATA" in body
        assert "NOT A REAL POSITION" in body


def test_real_server_demo_data_never_masks_a_real_position_that_opens_later(live_server):
    server, database = live_server
    database.save_demo_position(build_mock_demo_position(), datetime.now(IST))
    status, body = _get(server)
    assert "DEMO DATA" in body

    thesis = _real_thesis(entry=100.0, stop=95.0, target=115.0, quantity=65)
    _open_real_position(database, thesis)

    status, body = _get(server)
    assert status == 200
    assert "DEMO DATA" not in body
    assert "NIFTY26SEP24000CE" in body


# --- python main.py demo-live-link --------------------------------------


def test_demo_live_link_never_touches_real_open_positions(tmp_path, monkeypatch):
    """The required test: the demo command never touches real position
    state."""
    import main

    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(tmp_path / "paper.db")
    database.initialize()

    class _RecordingNotifier:
        def __init__(self, *args, **kwargs):
            pass

        def send_event(self, event):
            return True

    monkeypatch.setattr(main, "DiscordNotifier", _RecordingNotifier)
    monkeypatch.setattr(main, "TelegramNotifier", _RecordingNotifier)

    result = main.demo_live_link(settings, database=database)

    assert database.open_positions() == []  # never touched
    assert database.demo_position() is not None
    assert database.demo_position()["is_demo"] is True
    assert result["discord_sent"] is True
    assert result["telegram_sent"] is True


def test_demo_live_link_sends_a_real_notification_with_the_real_working_link(tmp_path, monkeypatch):

    import main

    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(tmp_path / "paper.db")
    database.initialize()
    sent_messages = []

    class _RecordingNotifier:
        def __init__(self, *args, **kwargs):
            pass

        def send_event(self, event):
            sent_messages.append(event)
            return True

    monkeypatch.setattr(main, "DiscordNotifier", _RecordingNotifier)
    monkeypatch.setattr(main, "TelegramNotifier", _RecordingNotifier)

    result = main.demo_live_link(settings, database=database)

    # Sent via the exact real Event/send_event path PAPER_FILL itself uses.
    assert len(sent_messages) == 2  # Discord + Telegram
    for event in sent_messages:
        assert event.event_type.value == "PAPER_FILL"
        assert event.output_summary["live_status_url"] == result["live_status_url"]
        assert "DEMO" in event.output_summary["note"]
    assert result["live_status_url"].startswith("http://")
    assert result["live_status_url"].endswith(f":{settings.live_status_port}/live")
