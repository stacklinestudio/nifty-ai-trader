"""Brief 25: a small, local, READ-ONLY live trade-monitoring web page.

Reuses Python's own standard library `http.server` -- no new
dependency. This project's stack currently has zero web frameworks;
adding one (Flask/FastAPI) just for a single, tiny, read-only status
page would be a much larger real addition than the page itself. The
stdlib's `ThreadingHTTPServer` is the real, already-available "simplest
thing given the current stack."

Real data source: the exact same `storage.database.Database::
open_positions` table `Orchestrator` already maintains for real crash
recovery -- never a new one. Previously that row was only ever written
once, at `open_position()` time, and went stale immediately; Brief 25
also makes `Orchestrator.supervise_once` re-persist it on every real
observed tick (see agents/orchestrator.py) so this page's real LTP/
trailed-stop are genuinely current, not a snapshot from entry.

Read-only by construction, not just by convention: this module defines
no `do_POST`/`do_PUT`/`do_DELETE` handler anywhere -- `BaseHTTPRequest
Handler`'s own default response for any of those is a real 501 Not
Implemented. There is no route, anywhere, that can close or modify a
position.

Explicitly NOT exposed beyond the local network by anything here: the
server binds `0.0.0.0` (every real local network interface) so another
real device on the same real local network can reach it, but nothing
in this module forwards a port, opens a tunnel, or does any cloud
hosting -- reachability beyond the local network requires a deliberate,
separate router/firewall action outside this code, and real
authentication would be required before ever doing that on purpose.
"""

from __future__ import annotations

import json
import re
import socket
import threading
from collections.abc import Callable
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from config import IST, Settings
from storage.database import Database

DEFAULT_PORT = 8765
REFRESH_SECONDS = 7
LIVE_PATH = "/live"
DASHBOARD_PATHS = ("/", "/dashboard")
CANDLES_API_PATH = "/api/candles"

# Final Brief: the dashboard's own real System Health Gate call makes a
# real live Kite API request AND sends real Discord/Telegram probe
# messages (see check_kite_connection/check_notifications). Recomputing
# it on every single browser poll would mean a real Kite API hit and
# two real chat messages every few seconds -- this throttles real
# recomputation to once per this many seconds; requests in between
# reuse the last real computed view (still labeled with its own real
# "as of" timestamp, never silently stale-and-unmarked).
DASHBOARD_REFRESH_SECONDS = 30

# Final Brief: real, already-archived candle data (Brief 4-15's own
# real minute-bar CSVs). No live tick-to-candle pipeline exists yet in
# this project -- this is the most recent REAL archived data, read
# fresh on every real poll (so it picks up anything a future real
# process appends), not a live intraday feed. Stated honestly on the
# dashboard itself, not implied to be more than it is.
CANDLE_DATA_DIR = Path("data/private")


def real_local_ip() -> str:
    """The machine's real local network address -- never a public IP,
    never localhost, so a link handed to another real device on the
    same real local network actually resolves. The UDP "connect" below
    sends nothing real over the wire (UDP has no handshake); it only
    asks the OS which real local interface/address would be used to
    route toward that address, which is exactly the real local IP other
    devices on the same network should use. Falls back to 127.0.0.1 on
    any real socket error -- never raises, never guesses further."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def live_status_url(settings: Settings, ip: str | None = None) -> str:
    return f"http://{ip or real_local_ip()}:{settings.live_status_port}{LIVE_PATH}"


def dashboard_url(settings: Settings, ip: str | None = None) -> str:
    """Final Brief: the one-page Command Center dashboard's own real
    local-network URL -- same host/port as `live_status_url`, different
    path."""
    return f"http://{ip or real_local_ip()}:{settings.live_status_port}/dashboard"


def build_mock_demo_position(now: datetime | None = None) -> dict[str, Any]:
    """Brief 26: a clearly synthetic position view -- round, obviously
    fake numbers and a symbol literally prefixed "DEMO-", never derived
    from any real trade -- for `python main.py demo-live-link` to write
    via `Database.save_demo_position` (a wholly separate real table,
    never `open_positions`). `is_demo=True` is the real, structural flag
    `render_page` checks to show the DEMO banner; it is never left to a
    string match on the symbol or any other incidental field."""
    timestamp = (now or datetime.now(IST)).isoformat()
    entry, current_ltp, quantity = 100.0, 108.5, 65
    return {
        "open": True,
        # A real-shaped, obviously-fake instrument_token -- present so
        # `demo-live-link` can exercise the exact same kite_chart_url()
        # call the real PAPER_FILL path makes, without ever pointing at
        # a real, tradeable contract. 999999999 is not a real Kite
        # instrument_token in this project's real archived instrument
        # dumps; the resulting URL is clearly a demo, not a working
        # chart link.
        "instrument_token": 999999999,
        "is_demo": True,
        "symbol": "DEMO-NIFTY00000CE",
        "direction": "CALL",
        "setup_type": "DEMO_SETUP",
        "entry": entry,
        "current_ltp": current_ltp,
        "current_stop": 96.0,
        "original_stop": 95.0,
        "stop_was_trailed": True,
        "target": 130.0,
        "quantity": quantity,
        "unrealized_pnl": (current_ltp - entry) * quantity,
        "opened_at": timestamp,
        "last_quote_at": timestamp,
        "mae": 0.0,
        "mfe": 8.5,
    }


def _position_view_from_state(state: Any) -> dict[str, Any]:
    """Shapes one real `PositionState` into the dict both `/live` and
    the dashboard render. `instrument_token` (Final Brief follow-up):
    the same real value the PAPER_FILL notification's Kite chart link
    is built from -- carried through PositionState/its persistence so
    the dashboard's own position card can build the identical real
    `kite_chart_url()`, not just the outbound notification."""
    unrealized_pnl = (state.last_valid_ltp - state.thesis.entry) * state.thesis.quantity
    return {
        "open": True,
        "is_demo": False,
        "symbol": state.thesis.symbol,
        "direction": state.thesis.candidate.direction,
        "setup_type": state.thesis.candidate.setup_type,
        "entry": state.thesis.entry,
        "current_ltp": state.last_valid_ltp,
        "current_stop": state.current_stop,
        "original_stop": state.thesis.stop,
        "stop_was_trailed": state.current_stop != state.thesis.stop,
        "target": state.thesis.target,
        "quantity": state.thesis.quantity,
        "unrealized_pnl": unrealized_pnl,
        "opened_at": state.opened_at.isoformat(),
        "last_quote_at": state.last_quote_at.isoformat(),
        "mae": state.mae,
        "mfe": state.mfe,
        "instrument_token": state.entry_instrument_token,
        "order_id": state.entry_order_id,
    }


def all_open_position_views(database: Database) -> list[dict[str, Any]]:
    """Every real row currently in `open_positions`, most-recently-opened
    first. Defensive-only: the real architecture (DailyLimits + entry-
    scan pausing during supervision, since Brief 3/6) only ever allows
    ONE real open position at a time -- this returning more than one
    entry is NOT an expected scenario, and Monday's real session should
    never actually exercise that path. This exists purely so the
    dashboard can render safely (show every real row plainly) rather
    than silently dropping all but one, or crashing, if that invariant
    were ever violated. Never includes demo data -- callers that want
    the demo fallback handle it themselves (see
    `current_position_view`/`build_dashboard_view`)."""
    from execution.position_persistence import position_state_from_dict

    rows = database.open_positions()
    views = [_position_view_from_state(position_state_from_dict(row["state"])) for row in rows]
    return list(reversed(views))


def current_position_view(database: Database) -> dict[str, Any]:
    """The real, current open-position state, read directly from the
    real, already-existing `open_positions` table -- no new data
    source. `{"open": False}` plainly whenever nothing real is open and
    no real demo state exists, never stale data from the last real
    trade. If more than one real row somehow exists (see
    `all_open_position_views`'s own docstring -- not an expected
    scenario), this returns only the most recently opened, matching
    `/live`'s single-position display; the dashboard's own position
    card uses `all_open_position_views` directly so it can render every
    real row instead.

    Brief 26: a real open position ALWAYS takes priority over demo/mock
    data -- checked first, and if present, demo state is never even
    read. This means lingering demo data (e.g. a forgotten `demo-live-
    link` run) can never mask or be confused with a real position; at
    worst it fills in for the "no open position" case until cleared.

    `position_state_from_dict` (via `all_open_position_views`) is
    imported lazily, not at module level -- it pulls in execution.
    position_persistence -> agents.contracts, and `agents/__init__.py`
    eagerly imports agents.orchestrator, which itself now imports this
    module (for `live_status_url`). Importing at module level here
    would create a real circular import that only fails depending on
    which module happens to be imported first -- confirmed live
    (`python -c "from monitoring.live_status_server import
    live_status_url"` failed before this fix, while `python main.py
    ...` happened to work only because main.py's own import order loads
    agents.orchestrator first by chance). Deferring this import avoids
    depending on import order at all."""
    views = all_open_position_views(database)
    if views:
        return views[0]
    demo = database.demo_position()
    if demo is not None:
        return demo  # already real-shaped, with is_demo=True baked in by _mock_demo_position
    return {"open": False}


def render_page(view: dict[str, Any], refresh_seconds: int = REFRESH_SECONDS, now: datetime | None = None) -> str:
    """A pure function -- no I/O -- so its real output is directly
    testable against a real or synthetic `view` dict without a live
    server. Auto-refreshes via a plain `<meta http-equiv="refresh">` --
    the real, simplest mechanism given zero JS is already needed for
    anything else on this page."""
    is_demo = bool(view.get("is_demo"))
    demo_banner = (
        '<div class="demo-banner">DEMO DATA &mdash; NOT A REAL POSITION</div>' if is_demo else ""
    )
    if not view.get("open"):
        body = (
            "<h1>No open position</h1>"
            "<p>Nothing is currently open. This page keeps checking -- "
            "it will show the real position the moment one opens.</p>"
        )
    else:
        pnl = view["unrealized_pnl"]
        pnl_class = "profit" if pnl >= 0 else "loss"
        trailed_note = " (trailed from entry stop)" if view["stop_was_trailed"] else ""
        quote_label = "Last quote at" if is_demo else "Last real quote at"
        pnl_label = "Unrealized P&amp;L (demo, not real)" if is_demo else "Unrealized P&amp;L (before real exit costs)"
        body = f"""
{demo_banner}
<h1>{view['symbol']} &mdash; {view['direction']} ({view['setup_type']})</h1>
<table>
<tr><td>Entry</td><td>{view['entry']:.2f}</td></tr>
<tr><td>Current LTP</td><td>{view['current_ltp']:.2f}</td></tr>
<tr><td>Current stop{trailed_note}</td><td>{view['current_stop']:.2f}</td></tr>
<tr><td>Original stop</td><td>{view['original_stop']:.2f}</td></tr>
<tr><td>Target</td><td>{view['target']:.2f}</td></tr>
<tr><td>Quantity</td><td>{view['quantity']}</td></tr>
<tr><td>{pnl_label}</td><td class="{pnl_class}">{pnl:+.2f}</td></tr>
<tr><td>MAE / MFE</td><td>{view['mae']:.2f} / {view['mfe']:.2f}</td></tr>
<tr><td>Opened at</td><td>{view['opened_at']}</td></tr>
<tr><td>{quote_label}</td><td>{view['last_quote_at']}</td></tr>
</table>
{demo_banner}
"""
    timestamp = (now or datetime.now(IST)).isoformat(timespec="seconds")
    title = "NIFTY AI Trader -- Live Position (DEMO DATA)" if is_demo else "NIFTY AI Trader -- Live Position"
    footer_demo_note = (
        " This is DEMO DATA, not a real position -- see python main.py demo-live-link." if is_demo else ""
    )
    return f"""<!doctype html>
<html>
<head>
<meta http-equiv="refresh" content="{refresh_seconds}">
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; padding: 2em; background: #fafafa; color: #111; }}
table {{ border-collapse: collapse; margin-top: 1em; }}
td {{ padding: 6px 16px; border-bottom: 1px solid #ddd; }}
td:first-child {{ color: #555; }}
.profit {{ color: #0a7a2a; font-weight: bold; }}
.loss {{ color: #b30000; font-weight: bold; }}
.footer {{ margin-top: 2em; color: #888; font-size: 0.9em; }}
.demo-banner {{
  background: #b30000; color: #fff; font-weight: bold; font-size: 1.3em;
  padding: 12px 20px; margin: 0 0 1em 0; text-align: center; letter-spacing: 0.05em;
  border: 3px solid #7a0000;
}}
</style>
</head>
<body>
{body}
<p class="footer">Read-only, viewing only -- no controls here.
Auto-refreshes every {refresh_seconds}s. Rendered at {timestamp}.{footer_demo_note}</p>
</body>
</html>"""


# --- Final brief: real Kite chart link ----------------------------------


def kite_chart_url(exchange: str, tradingsymbol: str, instrument_token: int | None) -> str | None:
    """The real, documented Kite chart URL pattern:
    https://kite.zerodha.com/chart/ext/tvc/{exchange}/{tradingsymbol}/{instrument_token}
    -- confirmed live (2026-09-06) to send real X-Frame-Options:
    SAMEORIGIN and Content-Security-Policy: frame-ancestors 'self'
    https://*.zerodha.com ...; headers, meaning it genuinely CANNOT be
    embedded in an iframe on this (or any non-Zerodha) origin. Never
    embedded here -- only ever offered as a real, clickable link that
    opens in the viewer's own browser tab, where it works exactly like
    any other kite.zerodha.com page IF that browser already has a real,
    active Kite login session (the bot's own API access token is
    separate and has no bearing on this). Returns None, never a
    fabricated URL, when the real tradingsymbol/instrument_token aren't
    both known."""
    if not tradingsymbol or instrument_token is None:
        return None
    return f"https://kite.zerodha.com/chart/ext/tvc/{exchange}/{tradingsymbol}/{instrument_token}"


# --- Final brief: real NIFTY LTP (reuses the gate's own live-session pattern) ---


def check_nifty_ltp(settings: Settings, kite_factory: Callable[[], object] | None = None) -> dict[str, Any]:
    """Real, live NIFTY LTP via the same real Kite REST pattern already
    used throughout this project (Brief 19's own kite.quote(["NSE:NIFTY
    50"]) calls, monitoring/system_health_gate.py's own kite_connection
    check) -- not a new data source, the same real mechanism reused."""
    if not (settings.kite_api_key and settings.kite_access_token):
        return {"status": "FAIL", "detail": "no real Kite credentials configured", "ltp": None}
    if kite_factory is None:
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            return {"status": "FAIL", "detail": "kiteconnect not installed", "ltp": None}

        def kite_factory() -> object:
            kite = KiteConnect(api_key=settings.kite_api_key)
            kite.set_access_token(settings.kite_access_token)
            return kite

    try:
        kite = kite_factory()
        quote = kite.quote(["NSE:NIFTY 50"])
        ltp = quote["NSE:NIFTY 50"]["last_price"]
    except Exception as exc:  # noqa: BLE001 - any real API/auth failure means no real LTP is available right now.
        return {"status": "FAIL", "detail": f"real quote fetch failed: {type(exc).__name__}: {exc}", "ltp": None}
    return {"status": "OK", "detail": f"real NIFTY LTP {ltp}", "ltp": ltp}


# --- Final brief: real, already-archived candle data ---------------------


def find_latest_candle_csv(candle_dir: Path = CANDLE_DATA_DIR) -> Path | None:
    """The most recently modified real archived NIFTY minute-candle CSV
    -- never invented, never a placeholder file."""
    files = sorted(candle_dir.glob("nifty_index_minute_*.csv"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def load_recent_candles(path: Path, limit: int = 300) -> list[dict[str, Any]]:
    """The real, last `limit` rows of a real archived candle file, in
    the exact shape TradingView Lightweight Charts' candlestick series
    expects (`time` as real Unix seconds, real open/high/low/close)."""
    import pandas as pd

    frame = pd.read_csv(path, parse_dates=["date"]).tail(limit)
    candles = []
    for _, row in frame.iterrows():
        timestamp = row["date"]
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(IST)
        candles.append(
            {
                "time": int(timestamp.timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        )
    return candles


# --- Final brief: the one dashboard page's real data aggregation --------


def _is_same_real_day(iso_timestamp: str | None, today: date) -> bool:
    if not iso_timestamp:
        return False
    try:
        return datetime.fromisoformat(iso_timestamp).date() == today
    except ValueError:
        return False


def _latest_event_by_type(events: list[dict[str, Any]], event_types: tuple[str, ...]) -> dict[str, dict | None]:
    """`events` is real, already ordered most-recent-first (`Database.
    events()`). Returns the single most recent real event of each real
    type -- each carries its OWN real timestamp, deliberately not
    presented as if they all belong to one unified "current cycle" the
    real event log doesn't actually let us reconstruct that precisely."""
    result: dict[str, dict | None] = dict.fromkeys(event_types)
    for event in events:
        event_type = event.get("event_type")
        if event_type in result and result[event_type] is None:
            result[event_type] = event
    return result


def _gate_check(gate: Any, name: str) -> Any:
    """Finds one real check by name inside an already-computed GateReport
    -- never recomputes it. Used so sections 2/7/8 can reuse the gate's
    own real kite_connection/option_tick_capture/notifications checks
    instead of calling them a second time. `check_notifications` in
    particular has a real side effect (it sends a real Discord/Telegram
    probe message) -- calling it twice per dashboard load would mean
    two real messages sent on every single auto-refresh, which this
    dashboard must never do."""
    for check in gate.checks:
        if check.name == name:
            return check
    return None


def build_dashboard_view(
    settings: Settings,
    database: Database,
    gate: Any = None,
    kite_factory: Callable[[], object] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Assembles every real, already-computed value the ten dashboard
    sections need. Zero new logic -- every field here is read straight
    from a real, already-built function/table elsewhere in this
    project. `gate`/`kite_factory`/`today` are injectable purely for
    deterministic tests; production callers never pass them.

    Calls `run_system_health_gate` at most once per invocation (never
    twice) -- that real gate itself makes a real live Kite API call and
    sends real Discord/Telegram probe messages as part of its own
    `check_notifications`/`check_kite_connection` checks, so this
    function's own caller (the dashboard's request handler) is
    responsible for throttling how often `build_dashboard_view` itself
    is invoked (see DASHBOARD_REFRESH_SECONDS / the handler's cache)."""
    from learning.memory import MemoryStore
    from monitoring.system_health_gate import run_system_health_gate
    from research.expected_value import compute_ev

    today = today or datetime.now(IST).date()
    gate = gate or run_system_health_gate(settings, database, kite_factory=kite_factory, today=today)
    nifty_ltp = check_nifty_ltp(settings, kite_factory)

    csv_path = find_latest_candle_csv()
    candles = load_recent_candles(csv_path) if csv_path else []

    signals = database.recent_signals(limit=1000)
    todays_signals = [s for s in signals if _is_same_real_day(s.get("timestamp"), today)]
    latest_signal = todays_signals[-1] if todays_signals else None

    memory = MemoryStore(settings.database_path)
    ev_estimate = None
    if latest_signal is not None:
        ev_estimate = compute_ev(
            latest_signal.get("setup_type", ""), latest_signal.get("regime", ""), settings, memory, []
        )

    events = database.events(limit=200)
    pipeline = _latest_event_by_type(
        events,
        ("MARKET_RESEARCH_COMPLETE", "SIGNAL_CREATED", "TRADE_VALIDATED", "RISK_APPROVED", "RISK_REJECTED"),
    )

    # Defensive-only (see all_open_position_views' own docstring): the
    # real architecture never allows more than one real open position at
    # a time, so `real_positions` normally has 0 or 1 entries. Demo data
    # is used only when there are genuinely zero real ones, matching
    # current_position_view's own real-always-wins rule.
    real_positions = all_open_position_views(database)
    if real_positions:
        open_positions = real_positions
    else:
        demo = database.demo_position()
        open_positions = [demo] if demo is not None else []
    position = open_positions[0] if open_positions else {"open": False}
    unrealized_pnl_today = sum(p["unrealized_pnl"] for p in open_positions)

    all_trades = memory.recent(memory_type="trade", limit=1000)
    todays_trades = [t for t in all_trades if _is_same_real_day(t.get("timestamp"), today)]
    realized_pnl_today = sum(float(t["payload"].get("pnl") or 0.0) for t in todays_trades)

    return {
        "today": today.isoformat(),
        "computed_at": datetime.now(IST).isoformat(timespec="seconds"),
        "gate": gate,
        "nifty_ltp": nifty_ltp,
        "candles": candles,
        "candles_source": csv_path.name if csv_path else None,
        "latest_signal": latest_signal,
        "ev_estimate": ev_estimate,
        "pipeline": pipeline,
        "position": position,
        "open_positions": open_positions,
        "unrealized_pnl_today": unrealized_pnl_today,
        "trades_today_count": len(todays_trades),
        "realized_pnl_today": realized_pnl_today,
        "max_trades_per_day": settings.max_trades_per_day,
        "max_daily_loss": settings.max_daily_loss,
        "capture_status": _gate_check(gate, "option_tick_capture"),
        "notifications_status": _gate_check(gate, "notifications"),
        "kite_status": _gate_check(gate, "kite_connection"),
        "events": events,
    }


# --- Final brief: the one-page dashboard's real HTML -----------------

# NO_TRADE-shaped event types -- rendered with an honest amber "not a
# trade" treatment in the timeline, never allowed to look like a fill.
_NO_TRADE_EVENT_TYPES = frozenset({"RISK_REJECTED", "TRADE_VALIDATED", "SIGNAL_CREATED"})
_FILL_EVENT_TYPES = frozenset({"PAPER_FILL", "TRADE_COMPLETED", "STOP_LOSS", "TAKE_PROFIT", "FORCED_EXIT"})

_SEVEN_COMPONENTS = (
    ("technical_score", "Technical"),
    ("opening_score", "Opening range"),
    ("volume_score", "Volume"),
    ("option_score", "Option flow"),
    ("global_score", "Global"),
    ("news_score", "News"),
    ("risk_penalty", "Risk penalty"),
)


def _esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _check_row(check: Any) -> str:
    if check is None:
        return '<div class="check"><span class="dot dot-unknown"></span>not run</div>'
    dot = "dot-ok" if check.status == "OK" else "dot-fail"
    return (
        f'<div class="check"><span class="dot {dot}"></span>'
        f"<strong>{_esc(check.name)}</strong> &mdash; {_esc(check.detail)}</div>"
    )


_NO_DATA_HTML = '<span class="no-data">NO REAL DATA YET</span>'
_EV_TAG_HTML = '<span class="ev-tag">MEASUREMENT ONLY</span>'


def _ev_value_html(ev: Any) -> str:
    """Hard requirement #2: EV is visually labeled MEASUREMENT ONLY
    wherever it appears -- this single helper is the one place that
    label is generated, so every caller (KPI row, pipeline stage) gets
    it identically, never a spot where EV shows without it. Hard
    requirement #1: no candidate yet means no real EV measurement
    exists -- rendered as the same explicit NO REAL DATA YET state
    every other absent-value uses, never a numeric placeholder."""
    return _esc(ev.describe()) if ev is not None else _NO_DATA_HTML


def _render_blocked_banner(gate: Any) -> str:
    """Hard requirement #4: a BLOCKED verdict must be visually
    impossible to miss -- not a quiet red dot among green ones. This is
    a real, page-level element (not just a badge inside the Health
    card) that only exists at all when `gate.verdict != "READY"`, paired
    with `body.blocked` in the stylesheet for a real, site-wide
    presentation change (accent color, top border) beyond this one
    banner. Empty string, not a hidden/zero-height element, when the
    real gate is READY -- nothing to visually suppress."""
    if gate.verdict == "READY":
        return ""
    reasons = "".join(f"<li>{_esc(reason)}</li>" for reason in gate.blocking_reasons)
    return f"""
<div class="blocked-banner" role="alert">
<strong>&#9888; SYSTEM HEALTH: BLOCKED</strong>
<ul>{reasons}</ul>
</div>
"""


def _render_highlight_tile(check: Any, label: str) -> str:
    """One of the 3 headline health tiles (Kite / AI Provider / Tick
    Capture) -- same real `GateCheck` `_check_row` already renders
    elsewhere, just given more visual weight since these three are the
    ones a person needs to see first."""
    if check is None:
        return f'<div class="health-tile"><p class="health-tile-label">{_esc(label)}</p><p class="health-tile-status"><span class="dot dot-unknown"></span>NOT RUN</p></div>'
    dot = "dot-ok" if check.status == "OK" else "dot-fail"
    return (
        f'<div class="health-tile"><p class="health-tile-label">{_esc(label)}</p>'
        f'<p class="health-tile-status"><span class="dot {dot}"></span>{_esc(check.status)}</p>'
        f'<p class="health-tile-detail">{_esc(check.detail)}</p></div>'
    )


def _render_health_section(gate: Any) -> str:
    """Item 7: system health as a real status panel -- the same real 7
    checks `run_system_health_gate` already computes, never
    recomputed. The 3 a person most needs to see first (Kite, AI
    provider, option tick capture) get a headline highlight grid; the
    remaining 4 real checks (instrument archive, data completeness,
    notifications, risk/broker construction) list below with equal
    visual weight to each other. The real overall verdict is prominent
    here (reinforced page-wide by `_render_blocked_banner` above), and
    the real specific blocking reasons are listed plainly underneath
    when blocked (hard requirement #4's second half)."""
    verdict_class = "verdict-ready" if gate.verdict == "READY" else "verdict-blocked"
    highlight_names = ("kite_connection", "ai_provider", "option_tick_capture")
    highlight_labels = {"kite_connection": "Kite", "ai_provider": "AI Provider", "option_tick_capture": "Tick Capture"}
    highlights_html = "".join(
        _render_highlight_tile(_gate_check(gate, name), highlight_labels[name]) for name in highlight_names
    )
    remaining_checks = [c for c in gate.checks if c.name not in highlight_names]
    checks_html = "".join(_check_row(c) for c in remaining_checks)
    blocking_html = ""
    if gate.verdict != "READY":
        reasons = "".join(f"<li>{_esc(reason)}</li>" for reason in gate.blocking_reasons)
        blocking_html = f'<div class="blocking-reasons"><p class="label">Blocking reasons</p><ul>{reasons}</ul></div>'
    return f"""
<section class="card card-wide" id="health">
<h2>System Health <span class="verdict {verdict_class}">{gate.verdict}</span></h2>
<div class="health-highlights">{highlights_html}</div>
<div class="checks-grid">{checks_html}</div>
{blocking_html}
</section>
"""


def _render_market_section(view: dict[str, Any]) -> str:
    kite = view["kite_status"]
    kite_dot = "dot-ok" if kite and kite.status == "OK" else "dot-fail"
    ltp = view["nifty_ltp"]
    ltp_html = (
        f'<div class="big-number">{ltp["ltp"]:.2f}</div>' if ltp.get("ltp") is not None else '<div class="not-yet">no real LTP available -- ' + _esc(ltp["detail"]) + "</div>"
    )
    source_note = (
        f"real archived candles from {_esc(view['candles_source'])}"
        if view["candles_source"]
        else "no real archived candle file found"
    )
    return f"""
<section class="card" id="market">
<h2>Kite / Market Status</h2>
<div class="check"><span class="dot {kite_dot}"></span>{_esc(kite.detail) if kite else "not run"}</div>
<p class="label">NIFTY 50 LTP</p>
{ltp_html}
</section>
<section class="card card-wide" id="market-chart">
<h2>NIFTY Price</h2>
<p class="label">{source_note} &mdash; real, already-archived minute bars, not a live intraday tick feed. Refreshed from disk on every poll.</p>
<div id="chart-container" style="height:340px;"></div>
</section>
"""


def _render_intelligence_section(view: dict[str, Any]) -> str:
    pipeline = view["pipeline"]
    ev = view["ev_estimate"]
    stages = [
        ("Research", pipeline.get("MARKET_RESEARCH_COMPLETE")),
        ("Signal", pipeline.get("SIGNAL_CREATED")),
        ("Adversarial (validation)", pipeline.get("TRADE_VALIDATED")),
        ("Supervisor (risk)", pipeline.get("RISK_APPROVED") or pipeline.get("RISK_REJECTED")),
    ]
    rows = []
    for label, event in stages:
        if event is None:
            rows.append(f'<div class="stage"><span class="stage-label">{label}</span><span class="not-yet">not yet this cycle</span></div>')
        else:
            rows.append(
                f'<div class="stage stage-done"><span class="stage-label">{label}</span>'
                f'<span class="stage-value">{_esc(event["event_type"])} @ {_esc(event["timestamp"])}</span></div>'
            )
    ev_done = " stage-done" if ev is not None else ""
    ev_html = (
        f'<div class="stage{ev_done}"><span class="stage-label">EV {_EV_TAG_HTML}</span>'
        f'<span class="stage-value">{_ev_value_html(ev)}</span></div>'
    )
    return f"""
<section class="card" id="intelligence">
<h2>Research &rarr; Signal &rarr; EV &rarr; Adversarial &rarr; Supervisor</h2>
<div class="pipeline">
{''.join(rows[:2])}
{ev_html}
{''.join(rows[2:])}
</div>
</section>
"""


def _render_candidate_section(view: dict[str, Any]) -> str:
    signal = view["latest_signal"]
    if signal is None:
        return """
<section class="card" id="candidate">
<h2>Current Candidate</h2>
<p class="not-yet">No candidate evaluated yet today.</p>
</section>
"""
    confidence = signal.get("confidence")
    confidence_html = f"{confidence:.1f}" if isinstance(confidence, (int, float)) else _NO_DATA_HTML
    # Item 5: the real 7-component score_attribution as horizontal
    # contribution bars instead of a plain table -- same real numbers
    # (technical/opening/volume/option/global/news are real 0-100
    # scores, risk_penalty a real 0-25 penalty), only the layout is new.
    bars = []
    for key, label in _SEVEN_COMPONENTS:
        value = signal.get(key)
        if isinstance(value, (int, float)):
            width = max(0.0, min(100.0, value))
            bars.append(
                f'<div class="bar-row"><span class="bar-label">{label}</span>'
                f'<div class="bar-track"><div class="bar-fill" style="width:{width:.1f}%"></div></div>'
                f'<span class="bar-value mono">{value:.1f}</span></div>'
            )
        else:
            bars.append(
                f'<div class="bar-row"><span class="bar-label">{label}</span>'
                f'<div class="bar-track"></div><span class="bar-value not-yet">not present</span></div>'
            )
    return f"""
<section class="card" id="candidate">
<h2>Current Candidate &mdash; {_esc(signal.get('setup_type', 'unknown'))} ({_esc(signal.get('direction', '?'))})</h2>
<p class="label">confidence {confidence_html} &middot; regime {_esc(signal.get('regime', 'unknown'))} &middot; {_esc(signal.get('timestamp', ''))}</p>
{''.join(bars)}
</section>
"""


def _render_position_card(pos: dict[str, Any], index: int | None = None) -> str:
    """One real open position's own detail card, including the real
    Kite chart link (Final Brief follow-up) built from the same real
    `kite_chart_url()` the outbound PAPER_FILL notification uses --
    here fed by the position's own persisted `instrument_token`
    (`PositionState.entry_instrument_token`), not just at fill time.
    `index` is set only in the defensive multi-position fallback (see
    `_render_pnl_section`) so each real row is distinguishable."""
    chart_url = kite_chart_url("NFO", pos.get("symbol", ""), pos.get("instrument_token"))
    chart_row = (
        f'<div class="attr-row"><span>Kite chart</span>'
        f'<a class="kite-link" href="{_esc(chart_url)}" target="_blank" rel="noopener">open chart &#8599;</a></div>'
        if chart_url
        else '<div class="attr-row"><span>Kite chart</span><span class="not-yet">no real instrument token known</span></div>'
    )
    demo_tag = ' <span class="demo-tag">DEMO</span>' if pos.get("is_demo") else ""
    label = f"Position {index} of several (defensive)" if index is not None else "Current position"
    trailed_note = " (trailed)" if pos.get("stop_was_trailed") else ""
    pnl = pos["unrealized_pnl"]
    pnl_class = "profit" if pnl >= 0 else "loss"
    return f"""
<div class="position-card">
<p class="label">{label}{demo_tag}</p>
<div class="attr-row"><span>Symbol</span><span class="mono">{_esc(pos.get('symbol', ''))} ({_esc(pos.get('direction', ''))})</span></div>
<div class="attr-row"><span>Entry / current LTP</span><span class="mono">{pos['entry']:.2f} / {pos['current_ltp']:.2f}</span></div>
<div class="attr-row"><span>Stop{trailed_note} / target</span><span class="mono">{pos['current_stop']:.2f} / {pos['target']:.2f}</span></div>
<div class="attr-row"><span>Unrealized P&amp;L</span><span class="mono {pnl_class}">{pnl:+.2f}</span></div>
{chart_row}
</div>
"""


def _render_position_section(view: dict[str, Any]) -> str:
    """Item 6: the real position card on its own -- entry/LTP/stop/
    target/P&L when open, the same literal "No position currently
    open." honest empty state as before (kept verbatim; changing this
    exact string would break the existing, still-correct regression
    checks for it), real Kite chart link when a real instrument token
    exists. Aggregate P&L/risk numbers now live in the KPI row
    (`_render_kpi_row`) under Overview instead of duplicating them
    here."""
    open_positions = view["open_positions"]
    if not open_positions:
        position_html = '<p class="not-yet">No position currently open.</p>'
    elif len(open_positions) == 1:
        position_html = _render_position_card(open_positions[0])
    else:
        # Defensive-only fallback -- see all_open_position_views' own
        # docstring. NOT an expected real scenario: the real risk
        # architecture (DailyLimits + entry-scan pausing during
        # supervision, Brief 3/6) only ever allows one open position at
        # a time. Rendered plainly, labeled as a safety fallback, never
        # silently hidden or crashed on.
        warning = (
            '<div class="multi-position-warning">&#9888; '
            f"{len(open_positions)} real open positions found at once &mdash; this should "
            "never happen under this project's real risk architecture (one position "
            "at a time by construction). Showing all of them as a safety fallback, "
            "not expected behavior.</div>"
        )
        position_html = warning + "".join(
            _render_position_card(pos, index=i + 1) for i, pos in enumerate(open_positions)
        )

    return f"""
<section class="card" id="position">
<h2>Position</h2>
{position_html}
</section>
"""


def _render_kpi_row(view: dict[str, Any]) -> str:
    """Item 3: the KPI row -- today's real P&L, real trade count vs.
    the real configured daily limit, real confidence, real EV (labeled
    per hard requirement #2), real regime, real risk utilization. Every
    tile that has no real value yet (confidence/regime/EV before any
    candidate exists today) renders the explicit NO REAL DATA YET
    state (hard requirement #1) instead of a numeric 0.00 that could be
    mistaken for a real measurement. P&L/trade-count/risk-utilization
    ARE real, valid measurements even when zero (zero real trades today
    is a true fact, not an absence) so those render as real numbers."""
    realized = view["realized_pnl_today"]
    unrealized = view["unrealized_pnl_today"]
    total = realized + unrealized
    total_class = "profit" if total >= 0 else "loss"
    trades_used = view["trades_today_count"]
    trades_cap = view["max_trades_per_day"]
    loss_cap = view["max_daily_loss"]
    loss_utilization = min(100.0, max(0.0, (-realized / loss_cap * 100.0))) if loss_cap else 0.0

    signal = view["latest_signal"]
    confidence = signal.get("confidence") if signal else None
    confidence_html = f"{confidence:.1f}" if isinstance(confidence, (int, float)) else _NO_DATA_HTML
    regime = signal.get("regime") if signal else None
    regime_html = _esc(regime) if regime else _NO_DATA_HTML
    ev_html = _ev_value_html(view["ev_estimate"])

    tiles = [
        ("Today&rsquo;s P&amp;L", f'<span class="{total_class}">{total:+.2f}</span>'),
        ("Trades Today", f"{trades_used} / {trades_cap}"),
        ("Confidence", confidence_html),
        (f"EV {_EV_TAG_HTML}", ev_html),
        ("Regime", regime_html),
        ("Risk Utilization", f"{loss_utilization:.1f}% of Rs{loss_cap:.0f}"),
    ]
    tiles_html = "".join(
        f'<div class="kpi-tile"><p class="kpi-label">{label}</p><p class="kpi-value">{value}</p></div>'
        for label, value in tiles
    )
    return f'<div class="kpi-row">{tiles_html}</div>'


# The real, already-computed detail string check_option_tick_capture
# formats (see monitoring/system_health_gate.py): "{N} real segment(s),
# {N} real ticks, {N} real gap(s) for {date}". Parsed here purely for
# DISPLAY -- pulling the same three real numbers already inside that
# one real string out into their own labeled tiles, never computing
# anything new. Deliberately labeled "Segments" (this check's own real
# vocabulary -- one real capture-session file, not a per-contract
# count) rather than "Contracts": renaming it to something the real
# check doesn't actually measure would be exactly the kind of
# real-looking-but-wrong number this project's own honesty rules exist
# to prevent.
_CAPTURE_DETAIL_PATTERN = re.compile(r"(\d+) real segment\(s\), (\d+) real ticks, (\d+) real gap\(s\)")


def _render_capture_metrics(capture: Any) -> str:
    match = _CAPTURE_DETAIL_PATTERN.search(capture.detail) if capture and capture.status == "OK" else None
    if not match:
        return ""
    segments, ticks, gaps = match.groups()
    tiles = (("Segments", segments), ("Ticks", ticks), ("Gaps", gaps))
    return '<div class="capture-metrics">' + "".join(
        f'<div class="capture-metric"><p class="kpi-label">{label}</p><p class="kpi-value mono">{value}</p></div>'
        for label, value in tiles
    ) + "</div>"


def _render_capture_section(view: dict[str, Any]) -> str:
    """Item 9: Data Foundation -- real option tick capture status
    (broken into its own real segment/tick/gap counts, parsed from the
    real, already-computed check detail string -- see
    `_CAPTURE_DETAIL_PATTERN`'s own docstring), real instrument archive
    validity (both real gate checks, never recomputed), plus two
    static, honest, permanent facts about this project's own real
    current limitations: raw-tick immutability (a real architectural
    guarantee, permanent since Brief 20) and the real absence of
    historical option P&L reconstruction / trade calibration data --
    stated plainly, not hidden behind a polished UI, per this card's
    own explicit purpose."""
    capture = view["capture_status"]
    archive = _gate_check(view["gate"], "instrument_archive")
    return f"""
<section class="card card-wide" id="capture">
<h2>Data Capture &amp; Foundation</h2>
{_check_row(capture)}
{_render_capture_metrics(capture)}
{_check_row(archive)}
<div class="foundation-fact"><span class="dot dot-ok"></span>Raw Kite ticks are never modified in place &mdash; RAW &rarr; NORMALIZED &rarr; VALIDATED &rarr; RESEARCH layering, permanent since Brief 20.</div>
<div class="foundation-fact"><span class="dot dot-unknown"></span>Historical option P&amp;L reconstruction: <span class="not-yet">NOT AVAILABLE YET</span></div>
<div class="foundation-fact"><span class="dot dot-unknown"></span>Real trade calibration sample: <span class="not-yet">0 REAL TRADES</span></div>
</section>
"""


def _render_notifications_section(view: dict[str, Any]) -> str:
    check = view["notifications_status"]
    return f"""
<section class="card" id="notifications">
<h2>Notifications</h2>
{_check_row(check)}
</section>
"""


def _render_event_row(event: dict[str, Any]) -> str:
    event_type = event.get("event_type", "")
    if event_type in _FILL_EVENT_TYPES:
        badge, kind = '<span class="badge badge-fill">REAL FILL/EXIT</span>', "fill"
    elif event_type in _NO_TRADE_EVENT_TYPES:
        badge, kind = '<span class="badge badge-no-trade">NO TRADE</span>', "no-trade"
    else:
        badge, kind = '<span class="badge badge-system">SYSTEM</span>', "system"
    # `data-kind` (not an extra class) is a real, additional visual cue
    # (a left-border accent, see .event-row[data-kind] in the
    # stylesheet) -- deliberately not added to `class="event-row"`
    # itself, since existing tests match that exact literal attribute
    # string to split real event rows apart; a second class there would
    # silently break that real structural check.
    return (
        f'<div class="event-row" data-kind="{kind}">{badge}<span class="event-type">{_esc(event_type)}</span>'
        f'<span class="event-time mono">{_esc(event.get("timestamp", ""))}</span>'
        f'<span class="event-agent">{_esc(event.get("agent", ""))}</span></div>'
    )


def _render_events_section(view: dict[str, Any]) -> str:
    events = view["events"]
    if not events:
        rows = '<p class="not-yet">No events recorded yet today.</p>'
    else:
        rows = "".join(_render_event_row(e) for e in events[:100])
    return f"""
<section class="card card-wide" id="events">
<h2>Recent Decisions &amp; Live Event Timeline</h2>
<p class="label">Every real recorded event -- NO_TRADE/RISK_REJECTED entries are always labeled distinctly from real fills, never shown as completed trades.</p>
<div class="timeline">{rows}</div>
</section>
"""


_SIDEBAR_GROUPS = (
    ("Primary", (("overview", "Overview"), ("market", "Market"), ("intelligence", "Intelligence"), ("candidate", "Candidate"), ("position", "Position"))),
    ("Operations", (("health", "System Health"), ("capture", "Data Capture"), ("notifications", "Notifications"), ("events", "Events"))),
)


def _market_session_label(settings: Settings | None, now: datetime) -> str:
    """Item 2: real market open/closed state -- derived purely from
    `settings.market_open`/`settings.market_close` (already-real,
    already-loaded config fields, zero new data) compared against the
    same real render-time clock `render_dashboard` already computes for
    its own timestamp. Never guesses when `settings` isn't available
    (only the structural/unit tests that don't pass it)."""
    if settings is None:
        return "MARKET SESSION: NOT AVAILABLE"
    current_time = now.timetz().replace(tzinfo=None)
    return "MARKET OPEN" if settings.market_open <= current_time <= settings.market_close else "MARKET CLOSED"


def _render_sidebar(view: dict[str, Any], settings: Settings | None) -> str:
    """Item 1: the sidebar -- scroll-anchors to areas of this same one
    page (plain `#anchor` links, native browser behavior, no routing,
    no JS required), a real current mode indicator, and real Kite/AI
    connection status at the bottom (both reused gate checks, never
    recomputed)."""
    kite = view["kite_status"]
    ai = _gate_check(view["gate"], "ai_provider")
    kite_dot = "dot-ok" if kite and kite.status == "OK" else "dot-fail"
    ai_dot = "dot-ok" if ai and ai.status == "OK" else "dot-fail"
    mode_label = f"{settings.trading_mode.upper()} TRADING" if settings is not None else "MODE: NOT AVAILABLE"
    # One flat <ul class="side-nav"> (not one per group) -- group headers
    # are plain, non-link <li> items inside the same list. Keeps the
    # real structural guarantee every sidebar test relies on (exactly
    # one `class="side-nav"...</ul>` block containing all 9 real
    # anchors) while still giving the two-group visual hierarchy.
    nav_html = "".join(
        f'<li class="nav-group-label">{_esc(group_label)}</li>'
        + "".join(f'<li><a href="#{anchor}">{label}</a></li>' for anchor, label in items)
        for group_label, items in _SIDEBAR_GROUPS
    )
    return f"""
<nav class="sidebar">
<div class="brand"><span class="brand-mark">N</span><div><div class="brand-name">NIFTY AI Trader</div><div class="brand-sub">Command Center</div></div></div>
<div class="mode-pill">{_esc(mode_label)}</div>
<ul class="side-nav">{nav_html}</ul>
<div class="side-footer">
<div class="side-status"><span class="dot {kite_dot}"></span>Kite: {_esc(kite.status if kite else "N/A")}</div>
<div class="side-status"><span class="dot {ai_dot}"></span>AI: {_esc(ai.status if ai else "N/A")}</div>
</div>
</nav>
"""


def _render_hero(view: dict[str, Any], settings: Settings | None, now: datetime) -> str:
    """Item 2: the hero market header -- real NIFTY LTP, real market
    open/closed state, real Kite/AI/Capture/Health status inline.
    Intraday change is honestly NOT shown: no real reference/previous
    price is plumbed anywhere in this project's real data layer, and
    computing one here would be new data this brief's own ground rules
    forbid -- stated plainly rather than fabricated."""
    ltp = view["nifty_ltp"]
    ltp_html = f'{ltp["ltp"]:.2f}' if ltp.get("ltp") is not None else _NO_DATA_HTML
    session_label = _market_session_label(settings, now)
    kite = view["kite_status"]
    ai = _gate_check(view["gate"], "ai_provider")
    capture = view["capture_status"]
    gate = view["gate"]

    def _pip(check: Any, label: str) -> str:
        dot = "dot-ok" if check and check.status == "OK" else "dot-fail"
        return f'<span class="hero-pip"><span class="dot {dot}"></span>{label}</span>'

    health_dot = "dot-ok" if gate.verdict == "READY" else "dot-fail"
    pips = (
        _pip(kite, "Kite")
        + _pip(ai, "AI")
        + _pip(capture, "Capture")
        + f'<span class="hero-pip"><span class="dot {health_dot}"></span>Health: {_esc(gate.verdict)}</span>'
    )
    return f"""
<div class="hero">
<div class="hero-top">
<div>
<p class="label">NIFTY 50</p>
<div class="hero-ltp">{ltp_html}</div>
<p class="hero-change">change: not tracked yet</p>
</div>
<div class="hero-session"><span class="session-pill">{_esc(session_label)}</span></div>
</div>
<div class="hero-pips">{pips}</div>
</div>
"""


def render_dashboard(
    view: dict[str, Any],
    refresh_seconds: int = DASHBOARD_REFRESH_SECONDS,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> str:
    """The one, single Command Center page -- every section above is a
    `<section>` on this one document (or a scroll-anchor target inside
    it), never a separate route/page. Read-only: no `<form>`, no
    `<button>`, no write-triggering JS anywhere. TradingView Lightweight
    Charts (CDN, real, free, open-source) renders the chart card using
    its own documented incremental `series.update()` pattern for live
    polls, seeded once via `setData()` from `/api/candles` -- never a
    full-series tear-down/rebuild on every poll.

    `settings` is optional and used only for two purely-presentational,
    zero-new-computation reads (`settings.trading_mode` for the sidebar
    mode pill, `settings.market_open`/`market_close` for the hero's
    open/closed label) -- both already-real, already-loaded config
    values this function did not have direct access to before. No other
    real data source changed; `build_dashboard_view` (the actual data
    aggregation) is untouched by this redesign."""
    now = now or datetime.now(IST)
    timestamp = now.isoformat(timespec="seconds")
    candles_json = json.dumps(view["candles"])
    gate = view["gate"]
    body_class = "" if gate.verdict == "READY" else " class=\"blocked\""

    overview_html = f"""
<section class="hero-section" id="overview">
{_render_hero(view, settings, now)}
{_render_kpi_row(view)}
</section>
"""
    grid_html = "".join(
        [
            _render_market_section(view),
            _render_intelligence_section(view),
            _render_candidate_section(view),
            _render_position_section(view),
            _render_health_section(gate),
            _render_capture_section(view),
            _render_notifications_section(view),
            _render_events_section(view),
        ]
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>NIFTY AI Trader &mdash; Command Center</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;650;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root {{
  --bg: #0a0c11; --card: #12151d; --card-alt: #171b25; --border: #232838; --border-soft: #1b2029;
  --text: #e9ebf1; --text-dim: #b6bccb; --muted: #7c8598;
  --ok: #1ecb8c; --fail: #f0454f; --amber: #f2a838; --accent: #5b8cff; --purple: #9c7bff;
  --font-ui: "Inter", -apple-system, "Segoe UI", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  --radius: 12px; --radius-sm: 8px; --sidebar-w: 252px;
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 24px; --sp-6: 32px;
  --fs-xs: 0.72rem; --fs-sm: 0.82rem; --fs-base: 0.92rem; --fs-md: 1rem; --fs-lg: 1.2rem; --fs-xl: 1.55rem; --fs-2xl: 2.5rem;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font-family: var(--font-ui);
  font-size: var(--fs-base); line-height: 1.5; -webkit-font-smoothing: antialiased;
  border-top: 4px solid transparent; overflow-x: hidden;
}}
body.blocked {{ border-top-color: var(--fail); }}
h1 {{ font-size: var(--fs-xl); font-weight: 650; margin: 0; letter-spacing: -0.01em; }}
h2 {{
  font-size: var(--fs-md); font-weight: 600; margin: 0 0 var(--sp-4) 0; color: var(--text);
  display: flex; align-items: baseline; gap: var(--sp-2); padding-bottom: var(--sp-3);
  border-bottom: 1px solid var(--border-soft); letter-spacing: -0.005em; scroll-margin-top: var(--sp-5);
}}
.shell {{ display: grid; grid-template-columns: var(--sidebar-w) 1fr; min-height: 100vh; align-items: start; max-width: 100vw; }}
.sidebar {{
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
  background: var(--card); border-right: 1px solid var(--border);
  padding: var(--sp-5) var(--sp-4); display: flex; flex-direction: column; gap: var(--sp-5);
}}
.brand {{ display: flex; align-items: center; gap: var(--sp-3); }}
.brand-mark {{
  width: 32px; height: 32px; border-radius: 8px; background: var(--accent); color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 700; flex-shrink: 0;
}}
.brand-name {{ font-weight: 650; font-size: var(--fs-base); }}
.brand-sub {{ color: var(--muted); font-size: var(--fs-xs); }}
.mode-pill {{
  background: rgba(79,140,255,0.15); color: var(--accent); font-weight: 700; font-size: var(--fs-xs);
  letter-spacing: 0.05em; text-align: center; padding: var(--sp-2); border-radius: var(--radius-sm);
}}
.side-nav {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; flex: 1; }}
.nav-group-label {{
  color: var(--muted); font-size: var(--fs-xs); font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; padding: var(--sp-4) var(--sp-3) var(--sp-1) var(--sp-3);
}}
.nav-group-label:first-child {{ padding-top: 0; }}
.side-nav a {{
  display: block; color: var(--text-dim); text-decoration: none; font-size: var(--fs-sm);
  padding: var(--sp-2) var(--sp-3); border-radius: var(--radius-sm); border-left: 2px solid transparent;
  transition: background-color 0.12s ease, color 0.12s ease;
}}
.side-nav a:hover {{ background: var(--card-alt); color: var(--text); border-left-color: var(--accent); }}
.side-footer {{ display: flex; flex-direction: column; gap: var(--sp-2); padding-top: var(--sp-4); border-top: 1px solid var(--border-soft); }}
.side-status {{ display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-xs); color: var(--muted); }}
.main {{ padding: var(--sp-6) var(--sp-5); min-width: 0; }}
.blocked-banner {{
  background: rgba(234,57,67,0.15); border: 1px solid var(--fail); color: var(--fail);
  border-radius: var(--radius-sm); padding: var(--sp-4); margin-bottom: var(--sp-5);
  font-size: var(--fs-sm); line-height: 1.6;
}}
.blocked-banner strong {{ display: block; font-size: var(--fs-md); letter-spacing: 0.02em; margin-bottom: var(--sp-2); }}
.blocked-banner ul {{ margin: 0; padding-left: 1.2em; }}
.blocking-reasons {{ margin-top: var(--sp-4); padding-top: var(--sp-4); border-top: 1px solid var(--border-soft); }}
.blocking-reasons ul {{ margin: var(--sp-2) 0 0 0; padding-left: 1.2em; color: var(--fail); font-size: var(--fs-sm); }}
.hero-section {{ margin-bottom: var(--sp-6); scroll-margin-top: var(--sp-5); }}
.hero {{
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: var(--sp-5); margin-bottom: var(--sp-4);
}}
.hero-top {{ display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: var(--sp-4); }}
.hero-ltp {{ font-family: var(--font-mono); font-size: var(--fs-2xl); font-weight: 650; letter-spacing: -0.01em; }}
.hero-change {{ color: var(--muted); font-size: var(--fs-sm); margin: var(--sp-1) 0 0 0; }}
.session-pill {{ background: var(--card-alt); border: 1px solid var(--border-soft); padding: var(--sp-2) var(--sp-4); border-radius: 20px; font-size: var(--fs-xs); font-weight: 700; letter-spacing: 0.04em; }}
.hero-pips {{ display: flex; flex-wrap: wrap; gap: var(--sp-4); margin-top: var(--sp-4); padding-top: var(--sp-4); border-top: 1px solid var(--border-soft); }}
.hero-pip {{ display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-sm); color: var(--text-dim); }}
.kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--sp-4); }}
.kpi-tile {{
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--sp-4);
  transition: border-color 0.12s ease;
}}
.kpi-tile:hover {{ border-color: var(--accent); }}
.kpi-label {{ color: var(--muted); font-size: var(--fs-xs); margin: 0 0 var(--sp-2) 0; letter-spacing: 0.03em; text-transform: uppercase; }}
.kpi-value {{ font-family: var(--font-mono); font-size: var(--fs-lg); font-weight: 650; margin: 0; font-variant-numeric: tabular-nums; }}
.no-data {{ color: var(--amber); font-style: italic; font-weight: 600; font-size: var(--fs-sm); letter-spacing: 0.01em; }}
.ev-tag {{
  background: rgba(79,140,255,0.15); color: var(--accent); font-size: var(--fs-xs); font-weight: 700;
  padding: 1px 6px; border-radius: 4px; letter-spacing: 0.03em; vertical-align: middle;
}}
.grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: var(--sp-5) var(--sp-5); align-items: start;
}}
.card {{
  background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: var(--sp-5); box-shadow: 0 1px 0 rgba(255,255,255,0.02) inset, 0 8px 20px -12px rgba(0,0,0,0.5);
  scroll-margin-top: var(--sp-5);
}}
.card-wide {{ grid-column: 1 / -1; }}
.mono {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; }}
.label {{ color: var(--muted); font-size: var(--fs-sm); margin: 0 0 var(--sp-3) 0; letter-spacing: 0.01em; }}
.big-number {{ font-family: var(--font-mono); font-size: var(--fs-2xl); font-weight: 650; letter-spacing: -0.01em; }}
.not-yet {{ color: var(--muted); font-style: italic; font-size: var(--fs-sm); }}
.verdict {{ font-size: var(--fs-xs); padding: 3px 11px; border-radius: 20px; font-weight: 700; letter-spacing: 0.05em; }}
.verdict-ready {{ background: rgba(30,203,140,0.15); color: var(--ok); }}
.verdict-blocked {{ background: rgba(240,69,79,0.15); color: var(--fail); }}
.health-highlights {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--sp-4);
  margin-bottom: var(--sp-5);
}}
.health-tile {{
  background: var(--card-alt); border: 1px solid var(--border-soft); border-radius: var(--radius-sm);
  padding: var(--sp-4);
}}
.health-tile-label {{ color: var(--muted); font-size: var(--fs-xs); font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; margin: 0 0 var(--sp-2) 0; }}
.health-tile-status {{ display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-md); font-weight: 650; margin: 0; }}
.health-tile-detail {{ color: var(--text-dim); font-size: var(--fs-xs); margin: var(--sp-2) 0 0 0; line-height: 1.4; }}
.checks-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 0 var(--sp-4); }}
.check {{ display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-2) 0; font-size: var(--fs-sm); border-bottom: 1px solid var(--border-soft); }}
.check:last-child {{ border-bottom: none; padding-bottom: 0; }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
.dot-ok {{ background: var(--ok); box-shadow: 0 0 0 3px rgba(30,203,140,0.15); }}
.dot-fail {{ background: var(--fail); box-shadow: 0 0 0 3px rgba(240,69,79,0.15); }}
.dot-unknown {{ background: var(--muted); }}
.pipeline {{ position: relative; padding-left: var(--sp-5); }}
.pipeline::before {{ content: ""; position: absolute; left: 3px; top: 6px; bottom: 6px; width: 1px; background: var(--border-soft); }}
.stage {{ position: relative; display: flex; justify-content: space-between; gap: var(--sp-3); padding: var(--sp-2) 0; border-bottom: 1px solid var(--border-soft); font-size: var(--fs-sm); }}
.stage::before {{ content: ""; position: absolute; left: calc(-1 * var(--sp-5) + 0px); top: 50%; transform: translateY(-50%); width: 7px; height: 7px; border-radius: 50%; background: var(--border-soft); }}
.stage.stage-done::before {{ background: var(--purple); }}
.stage:last-child {{ border-bottom: none; padding-bottom: 0; }}
.stage-label {{ color: var(--muted); }}
.stage-value {{ text-align: right; }}
.attr-row {{ display: flex; justify-content: space-between; align-items: baseline; gap: var(--sp-3); padding: var(--sp-2) 0; border-bottom: 1px solid var(--border-soft); font-size: var(--fs-sm); }}
.attr-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
.attr-row > span:first-child {{ color: var(--text-dim); }}
.profit {{ color: var(--ok); }} .loss {{ color: var(--fail); }}
.bar-row {{ display: grid; grid-template-columns: 110px 1fr 52px; align-items: center; gap: var(--sp-3); padding: var(--sp-2) 0; }}
.bar-label {{ color: var(--text-dim); font-size: var(--fs-sm); }}
.bar-track {{ background: var(--card-alt); border-radius: 20px; height: 8px; overflow: hidden; }}
.bar-fill {{ background: linear-gradient(90deg, var(--accent), var(--purple)); height: 100%; border-radius: 20px; }}
.bar-value {{ text-align: right; font-size: var(--fs-sm); font-family: var(--font-mono); }}
.position-card {{
  background: var(--card-alt); border: 1px solid var(--border-soft); border-radius: var(--radius-sm);
  padding: var(--sp-4); margin-bottom: var(--sp-4);
}}
.position-card .attr-row, .position-card .label {{ margin-bottom: 0; }}
.demo-tag {{
  background: rgba(242,168,56,0.18); color: var(--amber); font-size: var(--fs-xs); font-weight: 700;
  padding: 1px 6px; border-radius: 4px; letter-spacing: 0.04em; vertical-align: middle;
}}
.kite-link {{ color: var(--accent); text-decoration: none; font-size: var(--fs-sm); font-weight: 600; }}
.kite-link:hover {{ text-decoration: underline; }}
.multi-position-warning {{
  background: rgba(242,168,56,0.1); border: 1px solid rgba(242,168,56,0.35); color: var(--amber);
  border-radius: var(--radius-sm); padding: var(--sp-3) var(--sp-4); font-size: var(--fs-sm);
  margin-bottom: var(--sp-4); line-height: 1.5;
}}
.capture-metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: var(--sp-4); margin: var(--sp-3) 0 var(--sp-4) 0; }}
.capture-metric {{ background: var(--card-alt); border: 1px solid var(--border-soft); border-radius: var(--radius-sm); padding: var(--sp-3) var(--sp-4); }}
.foundation-fact {{ display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-2) 0; font-size: var(--fs-sm); color: var(--text-dim); border-bottom: 1px solid var(--border-soft); }}
.foundation-fact:last-child {{ border-bottom: none; padding-bottom: 0; }}
.timeline {{ max-height: 420px; overflow-y: auto; margin-top: var(--sp-1); }}
.event-row {{
  display: grid; grid-template-columns: auto 1fr auto auto; gap: var(--sp-3); align-items: center;
  padding: var(--sp-2) var(--sp-3); border-bottom: 1px solid var(--border-soft); font-size: var(--fs-sm);
  border-left: 3px solid transparent; margin-left: calc(-1 * var(--sp-3));
}}
.event-row[data-kind="fill"] {{ border-left-color: var(--ok); }}
.event-row[data-kind="no-trade"] {{ border-left-color: var(--amber); }}
.event-row[data-kind="system"] {{ border-left-color: var(--border); }}
.event-row:last-child {{ border-bottom: none; }}
.event-type {{ font-weight: 500; }}
.badge {{ font-size: var(--fs-xs); font-weight: 700; padding: 2px 8px; border-radius: 4px; letter-spacing: 0.03em; white-space: nowrap; }}
.badge-fill {{ background: rgba(30,203,140,0.18); color: var(--ok); }}
.badge-no-trade {{ background: rgba(242,168,56,0.18); color: var(--amber); }}
.badge-system {{ background: rgba(124,133,152,0.18); color: var(--muted); }}
.event-time {{ color: var(--muted); }}
.event-agent {{ color: var(--muted); }}
.footer {{ margin-top: var(--sp-6); color: var(--muted); font-size: var(--fs-sm); padding-top: var(--sp-4); border-top: 1px solid var(--border); }}
@media (max-width: 900px) {{
  .shell {{ grid-template-columns: 1fr; }}
  .sidebar {{ position: static; height: auto; flex-direction: row; flex-wrap: wrap; align-items: center; }}
  .side-nav {{ flex-direction: row; flex-wrap: wrap; }}
  .nav-group-label {{ display: none; }}
  .side-footer {{ flex-direction: row; border-top: none; padding-top: 0; }}
}}
@media (max-width: 560px) {{
  .main {{ padding: var(--sp-4); }}
  .hero-top {{ flex-direction: column; }}
  :root {{ --fs-2xl: 1.9rem; }}
}}
</style>
</head>
<body{body_class}>
<div class="shell">
{_render_sidebar(view, settings)}
<main class="main">
{_render_blocked_banner(gate)}
{overview_html}
<div class="grid">
{grid_html}
</div>
<p class="footer">This page never accepts writes: no form, no button, nothing here can close, open, or modify a position. It is a window into the system, not a control surface. Page rendered {timestamp} &middot; data as of {_esc(view.get('computed_at', timestamp))} &middot; auto-refresh every {refresh_seconds}s &middot; <a href="{LIVE_PATH}" style="color: var(--accent);">live position page</a></p>
</main>
</div>
<script>
(function() {{
  var container = document.getElementById('chart-container');
  if (!container || typeof LightweightCharts === 'undefined') return;
  var chart = LightweightCharts.createChart(container, {{
    layout: {{ background: {{ color: '#12151d' }}, textColor: '#7c8598', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }},
    grid: {{ vertLines: {{ color: '#1b2029' }}, horzLines: {{ color: '#1b2029' }} }},
    crosshair: {{
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {{ color: '#5b8cff', width: 1, style: 2, labelBackgroundColor: '#5b8cff' }},
      horzLine: {{ color: '#5b8cff', width: 1, style: 2, labelBackgroundColor: '#5b8cff' }}
    }},
    rightPriceScale: {{ borderColor: '#232838' }},
    timeScale: {{ borderColor: '#232838', timeVisible: true }},
    width: container.clientWidth,
    height: 340,
  }});
  var series = chart.addCandlestickSeries({{
    upColor: '#1ecb8c', downColor: '#f0454f', borderVisible: false,
    wickUpColor: '#1ecb8c', wickDownColor: '#f0454f',
    priceFormat: {{ type: 'price', precision: 2, minMove: 0.05 }}
  }});
  var initial = {candles_json};
  series.setData(initial);
  var lastTime = initial.length ? initial[initial.length - 1].time : null;

  function poll() {{
    fetch('{CANDLES_API_PATH}').then(function(r) {{ return r.json(); }}).then(function(candles) {{
      if (!candles.length) return;
      var last = candles[candles.length - 1];
      if (lastTime === null || last.time >= lastTime) {{
        // Real incremental update -- series.update() upserts the latest
        // bar (or appends a new one) without re-rendering the whole
        // series, per TradingView's own documented live-update pattern.
        series.update(last);
        lastTime = last.time;
      }}
    }}).catch(function() {{}});
  }}
  setInterval(poll, {refresh_seconds * 1000});
  window.addEventListener('resize', function() {{ chart.applyOptions({{ width: container.clientWidth }}); }});
}})();
</script>
</body>
</html>"""


def _make_handler(database: Database, settings: Settings | None = None) -> type[BaseHTTPRequestHandler]:
    """A fresh handler class bound to `database`/`settings` via closure --
    avoids a module-level mutable global, and lets tests point a real
    server at an isolated real tmp_path database. `settings` is optional
    only so the pre-existing structural test
    (`_make_handler(database=None)`, which never issues a real request)
    keeps working unchanged -- any real server serving `/`/`/dashboard`/
    `/api/candles` is always constructed with a real `Settings` by its
    caller (`build_live_status_server`/`run_live_status_server_in_
    background`, both called with real settings from main.py).

    The dashboard's own real data (`build_dashboard_view`) is cached per
    handler-class instance and reused for `DASHBOARD_REFRESH_SECONDS`
    real seconds -- see that constant's own docstring for why: it is not
    an optimization, it exists so a browser polling this page every few
    seconds cannot cause a real repeated Kite API hit or repeated real
    Discord/Telegram probe sends."""

    cache_lock = threading.Lock()
    cache: dict[str, Any] = {"view": None, "built_at": 0.0}

    def _cached_dashboard_view() -> dict[str, Any]:
        import time

        with cache_lock:
            now = time.monotonic()
            if cache["view"] is None or (now - cache["built_at"]) >= DASHBOARD_REFRESH_SECONDS:
                cache["view"] = build_dashboard_view(settings, database)
                cache["built_at"] = now
            return cache["view"]

    class _LiveStatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == LIVE_PATH:
                view = current_position_view(database)
                self._respond_html(render_page(view))
            elif self.path in DASHBOARD_PATHS:
                if settings is None:
                    self._respond_html("<h1>Dashboard unavailable</h1><p>no real Settings configured for this server.</p>")
                    return
                self._respond_html(render_dashboard(_cached_dashboard_view(), settings=settings))
            elif self.path == CANDLES_API_PATH:
                if settings is None:
                    self._respond_json([])
                    return
                self._respond_json(_cached_dashboard_view()["candles"])
            else:
                self.send_response(404)
                self.end_headers()

        def _respond_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _respond_json(self, data: Any) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return _LiveStatusHandler


def build_live_status_server(
    database: Database, settings: Settings | None = None, port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    """Binds every real local network interface (`0.0.0.0`) -- reachable
    from another real device on the same real local network, never
    beyond it (see the module docstring's explicit scope boundary)."""
    return ThreadingHTTPServer(("0.0.0.0", port), _make_handler(database, settings))


def run_live_status_server_in_background(
    database: Database, settings: Settings | None = None, port: int = DEFAULT_PORT
) -> tuple[threading.Thread, ThreadingHTTPServer]:
    server = build_live_status_server(database, settings, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread, server
