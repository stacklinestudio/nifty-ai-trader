"""Final Brief, Part A: the one-page Command Center dashboard.

Every test below feeds `build_dashboard_view`/`render_dashboard` real,
explicitly-constructed data (a real tmp_path SQLite Database, real
MemoryStore trade records, a real injected GateReport) -- never a live
network call. `check_kite_connection`/`check_notifications` inside a
real `run_system_health_gate` call are exercised via dependency
injection (`gate=` / `kite_factory=`) exactly like Brief 23's own tests,
so these tests never hit the real Kite API or send a real Discord/
Telegram message.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

import pytest

from config import IST, Settings
from events.contracts import Event, EventType
from learning.memory import MemoryStore
from monitoring.live_status_server import (
    build_dashboard_view,
    build_live_status_server,
    kite_chart_url,
    render_dashboard,
)
from monitoring.system_health_gate import OK, GateCheck, GateReport
from storage.database import Database


def _ready_gate() -> GateReport:
    names = (
        "kite_connection",
        "ai_provider",
        "option_tick_capture",
        "instrument_archive",
        "data_completeness",
        "notifications",
        "risk_and_broker",
    )
    return GateReport("READY", tuple(GateCheck(n, OK, f"real {n} detail") for n in names))


def _blocked_gate() -> GateReport:
    checks = list(_ready_gate().checks)
    checks[0] = GateCheck("kite_connection", "FAIL", "real session invalid: TokenException")
    return GateReport("BLOCKED", tuple(checks))


# --- section-by-section: real injected data, no drift --------------------


def test_build_dashboard_view_reflects_a_real_injected_gate_verdict(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_blocked_gate(), today=date(2026, 9, 6))

    assert view["gate"].verdict == "BLOCKED"
    html = render_dashboard(view)
    assert "BLOCKED" in html
    assert "TokenException" in html


def test_build_dashboard_view_reflects_real_recorded_trades_and_signals(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    today = datetime(2026, 9, 6, 10, 0, tzinfo=IST)

    database.save_event(
        Event(EventType.SIGNAL_CREATED, "signal_engine", today, output_summary={}, confidence=80.0)
    )
    from storage.models import SignalRecord

    database.save_signal(
        SignalRecord(
            timestamp=today,
            direction="CALL",
            confidence=82.0,
            features={
                "setup_type": "MOMENTUM_CONTINUATION",
                "direction": "CALL",
                "regime": "TREND",
                "confidence": 82.0,
                "technical_score": 75.0,
                "opening_score": 60.0,
                "volume_score": 40.0,
                "option_score": 0.0,
                "global_score": 0.0,
                "news_score": 0.0,
                "risk_penalty": 0.0,
            },
        )
    )
    memory = MemoryStore(settings.database_path)
    memory.append("trade", {"pnl": 450.0, "order_id": "o1"}, today)
    memory.append("trade", {"pnl": -120.0, "order_id": "o2"}, today)

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=today.date())

    assert view["latest_signal"]["setup_type"] == "MOMENTUM_CONTINUATION"
    assert view["latest_signal"]["technical_score"] == 75.0
    assert view["realized_pnl_today"] == pytest.approx(330.0)
    assert view["trades_today_count"] == 2

    html = render_dashboard(view)
    assert "MOMENTUM_CONTINUATION" in html
    assert "330.00" in html or "+330.00" in html


def test_build_dashboard_view_reflects_a_real_open_position(tmp_path):
    from agents.contracts import TradeCandidate, TradeThesis
    from execution.position_persistence import position_state_to_dict
    from execution.position_supervisor import PositionState

    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    candidate = TradeCandidate(
        direction="CALL", setup_type="MOMENTUM_CONTINUATION", underlying="NIFTY", confidence=80.0,
        evidence=("e",), invalidations=(), entry_zone=(99.5, 100.5), stop_zone=(94.5, 95.5),
        target_zone=(114.5, 115.5),
    )
    thesis = TradeThesis(candidate, "NIFTY26SEP24000CE", 100.0, 95.0, 115.0, 65, 325.0, 80.0, ("e",), ())
    opened_at = datetime.now(IST)
    state = PositionState.opening(thesis, opened_at, entry_order_id="order-1")
    state.observe(108.0, opened_at + timedelta(minutes=5), 0.15)
    database.save_open_position("order-1", opened_at.isoformat(), position_state_to_dict(state))

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=opened_at.date())

    assert view["position"]["open"] is True
    assert view["position"]["symbol"] == "NIFTY26SEP24000CE"
    html = render_dashboard(view)
    assert "+520.00" in html  # (108-100)*65 unrealized, from the real open position's P&L card


def _open_real_position(
    database: Database, order_id: str, symbol: str = "NIFTY26SEP24000CE", instrument_token: int | None = None
):
    from agents.contracts import TradeCandidate, TradeThesis
    from execution.position_persistence import position_state_to_dict
    from execution.position_supervisor import PositionState

    candidate = TradeCandidate(
        direction="CALL", setup_type="MOMENTUM_CONTINUATION", underlying="NIFTY", confidence=80.0,
        evidence=("e",), invalidations=(), entry_zone=(99.5, 100.5), stop_zone=(94.5, 95.5),
        target_zone=(114.5, 115.5),
    )
    thesis = TradeThesis(candidate, symbol, 100.0, 95.0, 115.0, 65, 325.0, 80.0, ("e",), ())
    opened_at = datetime.now(IST)
    state = PositionState.opening(
        thesis, opened_at, entry_order_id=order_id, entry_instrument_token=instrument_token
    )
    state.observe(108.0, opened_at + timedelta(minutes=5), 0.15)
    database.save_open_position(order_id, opened_at.isoformat(), position_state_to_dict(state))
    return opened_at


# --- Part 2: real Kite chart link on the dashboard's own position card --


def test_dashboard_position_card_includes_a_real_kite_chart_link_when_instrument_token_known(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    opened_at = _open_real_position(database, "order-1", "NIFTY24CE", instrument_token=17512194)

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=opened_at.date())
    html = render_dashboard(view)

    assert kite_chart_url("NFO", "NIFTY24CE", 17512194) in html
    assert 'class="kite-link"' in html


def test_dashboard_position_card_shows_no_kite_chart_link_without_a_real_instrument_token(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    opened_at = _open_real_position(database, "order-1", "NIFTY24CE", instrument_token=None)

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=opened_at.date())
    html = render_dashboard(view)

    assert "kite.zerodha.com" not in html  # never a fabricated/partial link
    assert "no real instrument token known" in html


def test_dashboard_shows_no_position_card_and_no_kite_link_when_nothing_is_open(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    html = render_dashboard(view)

    assert "No position currently open." in html
    assert "kite.zerodha.com" not in html


# --- Part 3: defensive-only multi-position fallback ----------------------


def test_dashboard_renders_multiple_real_open_positions_safely_as_a_defensive_fallback(tmp_path):
    """Synthetic multi-position data, deliberately constructed here since
    the real risk architecture (see tests/test_scheduler.py::
    test_normal_operation_never_produces_more_than_one_real_open_
    position_at_a_time) never actually produces this state -- this
    proves the dashboard's DISPLAY handles it safely (never crashes,
    never silently drops a row) if it were somehow ever violated, not
    that the scenario is expected."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    opened_at = _open_real_position(database, "order-1", "NIFTY24CE", instrument_token=111)
    _open_real_position(database, "order-2", "NIFTY24PE", instrument_token=222)

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=opened_at.date())
    assert len(view["open_positions"]) == 2  # both real rows surfaced, neither silently dropped

    html = render_dashboard(view)  # must not raise

    assert "2 real open positions found at once" in html
    assert "should never happen" in html
    assert "NIFTY24CE" in html and "NIFTY24PE" in html
    assert kite_chart_url("NFO", "NIFTY24CE", 111) in html
    assert kite_chart_url("NFO", "NIFTY24PE", 222) in html
    assert "<form" not in html.lower() and "<button" not in html.lower()  # still read-only


# --- honest "not yet" states, tested explicitly, not just happy path -----


def test_build_dashboard_view_reports_no_candidate_plainly(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))

    assert view["latest_signal"] is None
    assert view["ev_estimate"] is None
    html = render_dashboard(view)
    assert "No candidate evaluated yet today" in html


def test_build_dashboard_view_reports_no_open_position_plainly(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))

    assert view["position"] == {"open": False}


def test_build_dashboard_view_reports_no_capture_today_plainly(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    gate = _ready_gate()
    checks = list(gate.checks)
    checks[2] = GateCheck("option_tick_capture", "FAIL", "no real capture segment found for 2026-09-06")
    gate = GateReport("BLOCKED", tuple(checks))

    view = build_dashboard_view(settings, database, gate=gate, today=date(2026, 9, 6))

    html = render_dashboard(view)
    assert "no real capture segment found" in html


def test_build_dashboard_view_reports_no_real_nifty_ltp_without_kite_credentials(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")  # no KITE_API_KEY/ACCESS_TOKEN set in env
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))

    assert view["nifty_ltp"]["ltp"] is None
    html = render_dashboard(view)
    assert "no real LTP available" in html


# --- NO_TRADE / rejected entries never conflated with real fills --------


def test_timeline_labels_no_trade_and_fill_events_distinctly(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    now = datetime.now(IST)
    database.save_event(Event(EventType.RISK_REJECTED, "risk_manager", now, output_summary={"reason": "daily loss cap"}))
    database.save_event(Event(EventType.PAPER_FILL, "paper_broker", now + timedelta(seconds=1), output_summary={"order_id": "o1"}))

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=now.date())
    html = render_dashboard(view)

    assert html.count("NO TRADE") == 1
    assert html.count("REAL FILL/EXIT") == 1
    # Each real event's own row must carry its own correct badge -- never
    # the other event's badge, and never both badges on one row (events
    # are DESC by timestamp, so PAPER_FILL's row renders before
    # RISK_REJECTED's).
    rows = html.split('class="event-row"')[1:]
    assert len(rows) == 2
    fill_row, rejected_row = rows
    assert "PAPER_FILL" in fill_row and "REAL FILL/EXIT" in fill_row and "NO TRADE" not in fill_row
    assert "RISK_REJECTED" in rejected_row and "NO TRADE" in rejected_row and "REAL FILL/EXIT" not in rejected_row


def test_hard_requirement_3_no_trade_and_fill_badges_have_genuinely_different_visual_treatment(tmp_path):
    """Hard requirement #3: not just different text -- genuinely
    different visual treatment, verified against the actual CSS rules
    shipped in the page (not asserted from reading the code), so the
    distinction really is unmistakable even in a fast skim, not just
    technically different in the markup."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    now = datetime.now(IST)
    database.save_event(Event(EventType.RISK_REJECTED, "risk_manager", now, output_summary={}))
    database.save_event(
        Event(EventType.PAPER_FILL, "paper_broker", now + timedelta(seconds=1), output_summary={"order_id": "o1"})
    )

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=now.date())
    html = render_dashboard(view)

    fill_rule = html[html.index(".badge-fill {") : html.index(".badge-fill {") + 120]
    no_trade_rule = html[html.index(".badge-no-trade {") : html.index(".badge-no-trade {") + 120]
    assert fill_rule != no_trade_rule
    assert "var(--ok)" in fill_rule  # real fills: the same green used for profit
    assert "var(--amber)" in no_trade_rule  # NO_TRADE: the same amber used for warnings, never green


def test_event_rows_carry_a_distinct_data_kind_attribute_per_real_event_type(tmp_path):
    """UI/UX redesign: each real event row also gets a `data-kind`
    attribute (a real left-border accent hook, not just the badge) --
    added without touching the existing `class="event-row"` attribute
    itself, since the pre-existing regression tests split on that exact
    literal string to isolate real rows."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    now = datetime.now(IST)
    database.save_event(Event(EventType.SIGNAL_CREATED, "signal_engine", now, output_summary={}))
    database.save_event(Event(EventType.RISK_REJECTED, "risk_manager", now + timedelta(seconds=1), output_summary={}))
    database.save_event(
        Event(EventType.PAPER_FILL, "paper_broker", now + timedelta(seconds=2), output_summary={"order_id": "o1"})
    )

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=now.date())
    html = render_dashboard(view)

    rows = html.split('class="event-row"')[1:]
    assert len(rows) == 3
    fill_row, rejected_row, signal_row = rows  # DESC by timestamp
    assert 'data-kind="fill"' in fill_row
    assert 'data-kind="no-trade"' in rejected_row
    assert 'data-kind="no-trade"' in signal_row


# --- hard requirement #1: no real-looking zero for unmeasured values ----


def test_hard_requirement_1_no_real_looking_zero_for_unmeasured_values(tmp_path):
    """Hard requirement #1: confidence/EV/regime must never render as a
    real-looking 0.00/blank when nothing has actually been measured --
    only the explicit NO REAL DATA YET state is acceptable. Trade
    count/risk utilization ARE real, valid zeros here (zero real trades
    today is a true measurement) so those are deliberately excluded
    from this check."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    assert view["latest_signal"] is None
    assert view["ev_estimate"] is None

    html = render_dashboard(view)

    tiles = html.split('class="kpi-tile"')[1:]
    assert len(tiles) == 6  # P&L, Trades, Confidence, EV, Regime, Risk Utilization
    _pnl, _trades, confidence_tile, ev_tile, regime_tile, _risk = tiles

    for tile, name in ((confidence_tile, "confidence"), (ev_tile, "EV"), (regime_tile, "regime")):
        assert "NO REAL DATA YET" in tile, f"{name} tile is missing the explicit no-data state"
        assert "0.0<" not in tile and ">0.00<" not in tile, f"{name} tile shows a real-looking zero instead"


# --- hard requirement #2: EV always carries its MEASUREMENT ONLY label --


def test_hard_requirement_2_ev_always_carries_the_measurement_only_label(tmp_path):
    """Hard requirement #2: EV is visually labeled MEASUREMENT ONLY
    wherever it appears -- both when a real EV value exists and when
    it's genuinely absent (no candidate yet). Checked in both real
    places EV appears on the page: the KPI row and the Intelligence
    pipeline stage."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    no_candidate_view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    html = render_dashboard(no_candidate_view)
    assert html.count("MEASUREMENT ONLY") >= 2  # KPI row + Intelligence pipeline stage

    from storage.models import SignalRecord

    today = datetime(2026, 9, 6, 10, 0, tzinfo=IST)
    database.save_signal(
        SignalRecord(
            timestamp=today,
            direction="CALL",
            confidence=82.0,
            features={
                "setup_type": "MOMENTUM_CONTINUATION", "direction": "CALL", "regime": "TREND", "confidence": 82.0,
                "technical_score": 75.0, "opening_score": 60.0, "volume_score": 40.0, "option_score": 0.0,
                "global_score": 0.0, "news_score": 0.0, "risk_penalty": 0.0,
            },
        )
    )
    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=today.date())
    html_with_candidate = render_dashboard(view)
    assert html_with_candidate.count("MEASUREMENT ONLY") >= 2


# --- hard requirement #4: a BLOCKED verdict is visually impossible to miss --


def test_hard_requirement_4_blocked_verdict_shows_a_real_page_level_banner(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    blocked_view = build_dashboard_view(settings, database, gate=_blocked_gate(), today=date(2026, 9, 6))
    ready_view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    blocked_html = render_dashboard(blocked_view)
    ready_html = render_dashboard(ready_view)

    assert 'class="blocked-banner"' in blocked_html
    assert "SYSTEM HEALTH: BLOCKED" in blocked_html
    assert "TokenException" in blocked_html  # the real specific blocking reason, not a generic label
    assert 'class="blocked-banner"' not in ready_html  # never shown when genuinely READY

    # A real, page-level presentation change beyond the banner itself --
    # not just a quiet badge among green ones.
    assert '<body class="blocked">' in blocked_html
    assert "<body>" in ready_html
    assert '<body class="blocked">' not in ready_html


# --- chart: real incremental-update pattern, not full setData() on poll --


def test_dashboard_chart_uses_the_real_incremental_update_pattern(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    html = render_dashboard(view)

    assert "series.setData(initial)" in html  # one-time seed
    assert "series.update(last)" in html  # real incremental live-update call
    # The live poll path must call update(), not setData() again.
    poll_body = html.split("function poll()")[1].split("setInterval(poll")[0]
    assert "setData" not in poll_body
    assert "series.update(last)" in poll_body


def test_chart_container_uses_the_real_larger_height(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    html = render_dashboard(view)

    assert 'id="chart-container" style="height:480px;"' in html
    assert "height: 480," in html  # the real chart JS config, not just the container's own CSS


# --- real bug report: staleness vs. genuine redesign shortfall -----------


def test_footer_shows_a_real_build_marker_with_commit_hash_and_timestamp(tmp_path):
    """A real bug report: a redesign was reported as visually unchanged.
    This build marker exists specifically to make staleness (an old
    cached page, a server still running a previous build) immediately,
    visibly obvious -- computed fresh on every real render, never
    cached alongside the page itself."""
    import re as _re

    from monitoring.live_status_server import real_git_commit_hash

    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    now = datetime(2026, 9, 6, 16, 45, 12, tzinfo=IST)

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=now.date())
    html = render_dashboard(view, now=now)

    real_hash = real_git_commit_hash()
    assert real_hash != "unknown"  # this real checkout has real git history
    assert _re.fullmatch(r"[0-9a-f]{6,}", real_hash)
    assert f"build {real_hash}" in html
    assert "2026-09-06T16:45:12" in html  # the exact real render timestamp, not a stale one
    assert 'class="build-marker"' in html
    assert 'class="side-build"' in html  # visible at the top of the page too, not just the footer


def test_health_section_is_the_first_real_section_after_overview(tmp_path):
    """A real structural change (not just CSS): System Health now
    renders immediately after Overview -- before Market/Intelligence/
    Candidate/Position -- so system status is the first thing a person
    sees below the hero."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    html = render_dashboard(view)

    overview_idx = html.index('id="overview"')
    health_idx = html.index('id="health"')
    market_idx = html.index('id="market"')
    assert overview_idx < health_idx < market_idx


def test_sidebar_nav_items_carry_real_icons(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    html = render_dashboard(view)

    assert html.count('class="nav-icon"') == 9  # one per real sidebar anchor
    assert html.count("<svg") >= 9


# --- kite chart URL -------------------------------------------------------


def test_kite_chart_url_matches_the_real_documented_pattern():
    url = kite_chart_url("NFO", "NIFTY26SEPFUT", 17512194)
    assert url == "https://kite.zerodha.com/chart/ext/tvc/NFO/NIFTY26SEPFUT/17512194"


def test_kite_chart_url_is_none_without_real_instrument_data():
    assert kite_chart_url("NFO", "", None) is None
    assert kite_chart_url("NFO", "NIFTY26SEPFUT", None) is None


# --- full regression: still no write handler anywhere on the page -------


def test_dashboard_and_candles_handler_defines_no_write_methods():
    from monitoring.live_status_server import _make_handler

    handler_class = _make_handler(database=None, settings=None)
    for method in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
        assert method not in handler_class.__dict__


def test_rendered_dashboard_html_offers_no_form_or_button(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_ready_gate(), today=date(2026, 9, 6))
    html = render_dashboard(view)

    assert "<form" not in html.lower()
    assert "<button" not in html.lower()


# --- real end-to-end: one page, not a multi-page app ---------------------


@pytest.fixture
def dashboard_server(tmp_path):
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    server = build_live_status_server(database, settings, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fetch(server, path: str) -> tuple[int, bytes]:
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def test_root_and_dashboard_serve_the_same_single_page(dashboard_server):
    status_root, body_root = _fetch(dashboard_server, "/")
    status_dash, body_dash = _fetch(dashboard_server, "/dashboard")

    assert status_root == 200
    assert status_dash == 200
    assert b"NIFTY AI Trader" in body_root
    assert b"Command Center" in body_root
    assert body_root == body_dash  # literally one page, served identically at both paths


def test_sidebar_anchors_are_real_scroll_targets_not_dead_links(dashboard_server):
    """Item 1: the sidebar's 9 links (Overview/Market/Intelligence/
    Candidate/Position/Health/Data Capture/Notifications/Events) are
    real scroll-anchors into THIS one page (plain `#id` hrefs), never
    separate routes. Confirms every real `href="#..."` in the sidebar
    has a real matching `id="..."` element somewhere on the same real
    page -- not a decorative link that goes nowhere."""
    status, body = _fetch(dashboard_server, "/dashboard")
    html = body.decode("utf-8")
    assert status == 200

    nav_start = html.index('class="side-nav"')
    nav_end = html.index("</ul>", nav_start)
    nav_html = html[nav_start:nav_end]
    anchors = re.findall(r'href="#([a-z-]+)"', nav_html)

    assert len(anchors) == 9  # Overview, Market, Intelligence, Candidate, Position, Health, Data Capture, Notifications, Events
    for anchor in anchors:
        assert f'id="{anchor}"' in html, f"sidebar links to #{anchor} but no element has id=\"{anchor}\""


def test_sidebar_has_two_real_nav_groups(dashboard_server):
    """The sidebar visually groups Overview/Market/Intelligence/
    Candidate/Position under "Primary" and System Health/Data Capture/
    Notifications/Events under "Operations" -- both group labels are
    plain non-link `<li>` items inside the SAME one `side-nav` list
    (not a second list), so the real anchor-count/structural
    guarantees above still hold unchanged."""
    status, body = _fetch(dashboard_server, "/dashboard")
    html = body.decode("utf-8")
    assert status == 200
    assert html.count('class="nav-group-label"') == 2
    assert ">Primary<" in html
    assert ">Operations<" in html


def test_health_highlights_show_kite_ai_and_tick_capture_with_real_detail(tmp_path):
    """UI/UX redesign: the 3 checks a person needs to see first (Kite,
    AI provider, option tick capture) get a headline highlight grid --
    same real GateCheck objects, never recomputed, real detail text
    included."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()

    view = build_dashboard_view(settings, database, gate=_blocked_gate(), today=date(2026, 9, 6))
    html = render_dashboard(view)

    assert 'class="health-highlights"' in html
    highlights_start = html.index('class="health-highlights"')
    highlights_html = html[highlights_start : highlights_start + 1500]
    assert "Kite" in highlights_html
    assert "AI Provider" in highlights_html
    assert "Tick Capture" in highlights_html
    assert "TokenException" in highlights_html  # the real kite_connection detail, not a generic label


def test_capture_metrics_parses_real_segments_ticks_gaps_when_available(tmp_path):
    """The real, already-computed option_tick_capture detail string is
    parsed purely for display into 3 labeled tiles -- same real
    numbers, never recomputed."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    gate = _ready_gate()
    checks = list(gate.checks)
    checks[2] = GateCheck(
        "option_tick_capture", OK, "2 real segment(s), 1543 real ticks, 1 real gap(s) for 2026-09-06"
    )
    gate = GateReport("READY", tuple(checks))

    view = build_dashboard_view(settings, database, gate=gate, today=date(2026, 9, 6))
    html = render_dashboard(view)

    assert 'class="capture-metrics"' in html
    metrics_start = html.index('class="capture-metrics"')
    metrics_html = html[metrics_start : metrics_start + 700]
    assert "Segments" in metrics_html and ">2<" in metrics_html
    assert "Ticks" in metrics_html and ">1543<" in metrics_html
    assert "Gaps" in metrics_html and ">1<" in metrics_html


def test_capture_metrics_honestly_absent_when_the_real_detail_cannot_be_parsed(tmp_path):
    """No real capture segment today -- the structured metric tiles
    must not appear at all (never a fabricated 0/0/0 that could look
    like a real, parsed measurement); the real honest FAIL detail text
    is still shown via the normal check row."""
    settings = Settings(database_path=tmp_path / "paper.db")
    database = Database(settings.database_path)
    database.initialize()
    gate = _ready_gate()
    checks = list(gate.checks)
    checks[2] = GateCheck("option_tick_capture", "FAIL", "no real capture segment found for 2026-09-06")
    gate = GateReport("BLOCKED", tuple(checks))

    view = build_dashboard_view(settings, database, gate=gate, today=date(2026, 9, 6))
    html = render_dashboard(view)

    assert 'class="capture-metrics"' not in html
    assert "no real capture segment found" in html


def test_live_path_is_unchanged_by_the_dashboard_addition(dashboard_server):
    status, body = _fetch(dashboard_server, "/live")

    assert status == 200
    assert b"Live Position" in body
    assert b"Command Center" not in body  # /live stays the pre-existing, separate real position page


def test_candles_api_returns_real_json(dashboard_server):
    status, body = _fetch(dashboard_server, "/api/candles")

    assert status == 200
    data = json.loads(body)
    assert isinstance(data, list)


def test_unknown_path_still_404s(dashboard_server):
    status, _ = _fetch(dashboard_server, "/nonexistent")
    assert status == 404


def test_dashboard_post_is_rejected(dashboard_server):
    port = dashboard_server.server_address[1]
    request = urllib.request.Request(f"http://127.0.0.1:{port}/dashboard", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(request, timeout=5)
    assert exc_info.value.code == 501
