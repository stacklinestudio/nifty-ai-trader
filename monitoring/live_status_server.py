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


def current_position_view(database: Database) -> dict[str, Any]:
    """The real, current open-position state, read directly from the
    real, already-existing `open_positions` table -- no new data
    source. `{"open": False}` plainly whenever nothing real is open and
    no real demo state exists, never stale data from the last real
    trade.

    Brief 26: a real open position ALWAYS takes priority over demo/mock
    data -- checked first, and if present, demo state is never even
    read. This means lingering demo data (e.g. a forgotten `demo-live-
    link` run) can never mask or be confused with a real position; at
    worst it fills in for the "no open position" case until cleared.

    `position_state_from_dict` is imported here, not at module level --
    it pulls in execution.position_persistence -> agents.contracts, and
    `agents/__init__.py` eagerly imports agents.orchestrator, which
    itself now imports this module (for `live_status_url`). Importing
    at module level here would create a real circular import that only
    fails depending on which module happens to be imported first --
    confirmed live (`python -c "from monitoring.live_status_server
    import live_status_url"` failed before this fix, while `python
    main.py ...` happened to work only because main.py's own import
    order loads agents.orchestrator first by chance). Deferring this
    import avoids depending on import order at all."""
    from execution.position_persistence import position_state_from_dict

    rows = database.open_positions()
    if rows:
        row = rows[-1]  # this project holds at most one real open position at a time
        state = position_state_from_dict(row["state"])
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
        }
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

    position = current_position_view(database)
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


def _render_gate_section(gate: Any) -> str:
    verdict_class = "verdict-ready" if gate.verdict == "READY" else "verdict-blocked"
    checks_html = "".join(_check_row(c) for c in gate.checks)
    return f"""
<section class="card" id="system-health">
<h2>1&middot; System Health <span class="verdict {verdict_class}">{gate.verdict}</span></h2>
{checks_html}
</section>
"""


def _render_market_section(view: dict[str, Any]) -> str:
    kite = view["kite_status"]
    kite_dot = "dot-ok" if kite and kite.status == "OK" else "dot-fail"
    ltp = view["nifty_ltp"]
    ltp_html = (
        f'<div class="big-number">{ltp["ltp"]:.2f}</div>' if ltp.get("ltp") is not None else '<div class="not-yet">no real LTP available -- ' + _esc(ltp["detail"]) + "</div>"
    )
    return f"""
<section class="card" id="market-status">
<h2>2&middot; Kite / Market Status</h2>
<div class="check"><span class="dot {kite_dot}"></span>{_esc(kite.detail) if kite else "not run"}</div>
<p class="label">NIFTY 50 LTP</p>
{ltp_html}
</section>
"""


def _render_chart_section(view: dict[str, Any]) -> str:
    source_note = (
        f"real archived candles from {_esc(view['candles_source'])}"
        if view["candles_source"]
        else "no real archived candle file found"
    )
    return f"""
<section class="card card-wide" id="chart">
<h2>3&middot; NIFTY Price</h2>
<p class="label">{source_note} &mdash; real, already-archived minute bars, not a live intraday tick feed. Refreshed from disk on every poll.</p>
<div id="chart-container" style="height:340px;"></div>
</section>
"""


def _render_pipeline_section(view: dict[str, Any]) -> str:
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
                f'<div class="stage"><span class="stage-label">{label}</span>'
                f'<span class="stage-value">{_esc(event["event_type"])} @ {_esc(event["timestamp"])}</span></div>'
            )
    ev_html = (
        f'<div class="stage"><span class="stage-label">EV (measurement only)</span>'
        f'<span class="stage-value">{_esc(ev.describe())}</span></div>'
        if ev is not None
        else '<div class="stage"><span class="stage-label">EV (measurement only)</span><span class="not-yet">no candidate evaluated today yet</span></div>'
    )
    return f"""
<section class="card" id="pipeline">
<h2>4&middot; Research &rarr; Signal &rarr; EV &rarr; Adversarial &rarr; Supervisor</h2>
{''.join(rows[:2])}
{ev_html}
{''.join(rows[2:])}
</section>
"""


def _render_candidate_section(view: dict[str, Any]) -> str:
    signal = view["latest_signal"]
    if signal is None:
        return """
<section class="card" id="candidate">
<h2>5&middot; Current Candidate</h2>
<p class="not-yet">No candidate evaluated yet today.</p>
</section>
"""
    rows = []
    for key, label in _SEVEN_COMPONENTS:
        value = signal.get(key)
        cell = f"{value:.1f}" if isinstance(value, (int, float)) else "not present"
        rows.append(f'<div class="attr-row"><span>{label}</span><span class="mono">{cell}</span></div>')
    return f"""
<section class="card" id="candidate">
<h2>5&middot; Current Candidate &mdash; {_esc(signal.get('setup_type', 'unknown'))} ({_esc(signal.get('direction', '?'))})</h2>
<p class="label">confidence {signal.get('confidence', 0):.1f} &middot; regime {_esc(signal.get('regime', 'unknown'))} &middot; {_esc(signal.get('timestamp', ''))}</p>
{''.join(rows)}
</section>
"""


def _render_pnl_section(view: dict[str, Any]) -> str:
    position = view["position"]
    unrealized = position["unrealized_pnl"] if position.get("open") else 0.0
    realized = view["realized_pnl_today"]
    total = realized + unrealized
    total_class = "profit" if total >= 0 else "loss"
    trades_used = view["trades_today_count"]
    trades_cap = view["max_trades_per_day"]
    loss_cap = view["max_daily_loss"]
    loss_utilization = min(100.0, max(0.0, (-realized / loss_cap * 100.0))) if loss_cap else 0.0
    return f"""
<section class="card" id="pnl">
<h2>6&middot; Paper P&amp;L / Risk</h2>
<div class="attr-row"><span>Realized P&amp;L today</span><span class="mono">{realized:+.2f}</span></div>
<div class="attr-row"><span>Unrealized P&amp;L (open position)</span><span class="mono">{unrealized:+.2f}</span></div>
<div class="attr-row"><span>Total</span><span class="mono {total_class}">{total:+.2f}</span></div>
<div class="attr-row"><span>Trades used today</span><span class="mono">{trades_used} / {trades_cap}</span></div>
<div class="attr-row"><span>Daily loss cap utilization</span><span class="mono">{loss_utilization:.1f}% of Rs{loss_cap:.0f}</span></div>
</section>
"""


def _render_capture_section(view: dict[str, Any]) -> str:
    check = view["capture_status"]
    return f"""
<section class="card" id="capture">
<h2>7&middot; Option Tick Capture</h2>
{_check_row(check)}
</section>
"""


def _render_notifications_section(view: dict[str, Any]) -> str:
    check = view["notifications_status"]
    return f"""
<section class="card" id="notifications">
<h2>8&middot; Notifications</h2>
{_check_row(check)}
</section>
"""


def _render_event_row(event: dict[str, Any]) -> str:
    event_type = event.get("event_type", "")
    if event_type in _FILL_EVENT_TYPES:
        badge = '<span class="badge badge-fill">REAL FILL/EXIT</span>'
    elif event_type in _NO_TRADE_EVENT_TYPES:
        badge = '<span class="badge badge-no-trade">NO TRADE</span>'
    else:
        badge = '<span class="badge badge-system">SYSTEM</span>'
    return (
        f'<div class="event-row">{badge}<span class="event-type">{_esc(event_type)}</span>'
        f'<span class="event-time mono">{_esc(event.get("timestamp", ""))}</span>'
        f'<span class="event-agent">{_esc(event.get("agent", ""))}</span></div>'
    )


def _render_timeline_section(view: dict[str, Any]) -> str:
    events = view["events"]
    if not events:
        rows = '<p class="not-yet">No events recorded yet today.</p>'
    else:
        rows = "".join(_render_event_row(e) for e in events[:100])
    return f"""
<section class="card card-wide" id="timeline">
<h2>9&amp;10&middot; Recent Decisions / Live Event Timeline</h2>
<p class="label">Every real recorded event -- NO_TRADE/RISK_REJECTED entries are always labeled distinctly from real fills, never shown as completed trades.</p>
<div class="timeline">{rows}</div>
</section>
"""


def render_dashboard(view: dict[str, Any], refresh_seconds: int = DASHBOARD_REFRESH_SECONDS, now: datetime | None = None) -> str:
    """The one, single Command Center page -- every section above is a
    `<section>` on this one document, never a separate route/page.
    Read-only: no `<form>`, no `<button>`, no write-triggering JS
    anywhere. TradingView Lightweight Charts (CDN, real, free,
    open-source) renders section 3 using its own documented incremental
    `series.update()` pattern for live polls, seeded once via
    `setData()` from `/api/candles` -- never a full-series
    tear-down/rebuild on every poll."""
    timestamp = (now or datetime.now(IST)).isoformat(timespec="seconds")
    candles_json = json.dumps(view["candles"])
    body = "".join(
        [
            _render_gate_section(view["gate"]),
            _render_market_section(view),
            _render_chart_section(view),
            _render_pipeline_section(view),
            _render_candidate_section(view),
            _render_pnl_section(view),
            _render_capture_section(view),
            _render_notifications_section(view),
            _render_timeline_section(view),
        ]
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>NIFTY AI Trader &mdash; Command Center</title>
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root {{
  --bg: #0b0e14; --card: #131722; --border: #232838; --text: #e6e9ef; --muted: #8b93a7;
  --ok: #16c784; --fail: #ea3943; --amber: #f0a020; --accent: #4f8cff;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 24px; background: var(--bg); color: var(--text);
  font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
}}
h1 {{ font-size: 1.4em; margin: 0 0 4px 0; }}
h2 {{ font-size: 1.0em; margin: 0 0 12px 0; color: var(--text); display: flex; align-items: center; gap: 10px; }}
.top-bar {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 20px; }}
.top-bar .meta {{ color: var(--muted); font-size: 0.85em; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; }}
.card-wide {{ grid-column: 1 / -1; }}
.mono {{ font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }}
.label {{ color: var(--muted); font-size: 0.82em; margin: 0 0 10px 0; }}
.big-number {{ font-family: "SFMono-Regular", Consolas, monospace; font-size: 2em; font-weight: 600; }}
.not-yet {{ color: var(--muted); font-style: italic; }}
.verdict {{ font-size: 0.7em; padding: 3px 10px; border-radius: 20px; font-weight: 700; letter-spacing: 0.04em; }}
.verdict-ready {{ background: rgba(22,199,132,0.15); color: var(--ok); }}
.verdict-blocked {{ background: rgba(234,57,67,0.15); color: var(--fail); }}
.check {{ display: flex; align-items: center; gap: 8px; padding: 5px 0; font-size: 0.88em; border-bottom: 1px solid var(--border); }}
.check:last-child {{ border-bottom: none; }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
.dot-ok {{ background: var(--ok); }} .dot-fail {{ background: var(--fail); }} .dot-unknown {{ background: var(--muted); }}
.stage {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.88em; }}
.stage:last-child {{ border-bottom: none; }}
.stage-label {{ color: var(--muted); }}
.attr-row {{ display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid var(--border); font-size: 0.9em; }}
.attr-row:last-child {{ border-bottom: none; }}
.profit {{ color: var(--ok); }} .loss {{ color: var(--fail); }}
.timeline {{ max-height: 420px; overflow-y: auto; }}
.event-row {{ display: grid; grid-template-columns: auto 1fr auto auto; gap: 10px; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.85em; }}
.badge {{ font-size: 0.68em; font-weight: 700; padding: 2px 8px; border-radius: 4px; letter-spacing: 0.03em; white-space: nowrap; }}
.badge-fill {{ background: rgba(22,199,132,0.18); color: var(--ok); }}
.badge-no-trade {{ background: rgba(240,160,32,0.18); color: var(--amber); }}
.badge-system {{ background: rgba(139,147,167,0.18); color: var(--muted); }}
.event-time {{ color: var(--muted); }}
.event-agent {{ color: var(--muted); }}
.footer {{ margin-top: 20px; color: var(--muted); font-size: 0.8em; }}
</style>
</head>
<body>
<div class="top-bar">
<div><h1>NIFTY AI Trader &mdash; Command Center</h1><span class="meta">Read-only observability. No controls. Local network only.</span></div>
<div class="meta">Page rendered {timestamp} &middot; data as of {_esc(view.get('computed_at', timestamp))} &middot; auto-refresh every {refresh_seconds}s &middot; <a href="{LIVE_PATH}" style="color: var(--accent);">live position page</a></div>
</div>
<div class="grid">
{body}
</div>
<p class="footer">This page never accepts writes: no form, no button, nothing here can close, open, or modify a position. It is a window into the system, not a control surface.</p>
<script>
(function() {{
  var container = document.getElementById('chart-container');
  if (!container || typeof LightweightCharts === 'undefined') return;
  var chart = LightweightCharts.createChart(container, {{
    layout: {{ background: {{ color: '#131722' }}, textColor: '#8b93a7' }},
    grid: {{ vertLines: {{ color: '#232838' }}, horzLines: {{ color: '#232838' }} }},
    timeScale: {{ timeVisible: true }},
    width: container.clientWidth,
    height: 340,
  }});
  var series = chart.addCandlestickSeries();
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
                self._respond_html(render_dashboard(_cached_dashboard_view()))
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
