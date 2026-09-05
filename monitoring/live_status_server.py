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

import socket
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from config import IST, Settings
from storage.database import Database

DEFAULT_PORT = 8765
REFRESH_SECONDS = 7
LIVE_PATH = "/live"


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


def current_position_view(database: Database) -> dict[str, Any]:
    """The real, current open-position state, read directly from the
    real, already-existing `open_positions` table -- no new data
    source. `{"open": False}` plainly whenever nothing real is open,
    never stale data from the last real trade.

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
    if not rows:
        return {"open": False}
    row = rows[-1]  # this project holds at most one real open position at a time
    state = position_state_from_dict(row["state"])
    unrealized_pnl = (state.last_valid_ltp - state.thesis.entry) * state.thesis.quantity
    return {
        "open": True,
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


def render_page(view: dict[str, Any], refresh_seconds: int = REFRESH_SECONDS, now: datetime | None = None) -> str:
    """A pure function -- no I/O -- so its real output is directly
    testable against a real or synthetic `view` dict without a live
    server. Auto-refreshes via a plain `<meta http-equiv="refresh">` --
    the real, simplest mechanism given zero JS is already needed for
    anything else on this page."""
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
        body = f"""
<h1>{view['symbol']} &mdash; {view['direction']} ({view['setup_type']})</h1>
<table>
<tr><td>Entry</td><td>{view['entry']:.2f}</td></tr>
<tr><td>Current LTP</td><td>{view['current_ltp']:.2f}</td></tr>
<tr><td>Current stop{trailed_note}</td><td>{view['current_stop']:.2f}</td></tr>
<tr><td>Original stop</td><td>{view['original_stop']:.2f}</td></tr>
<tr><td>Target</td><td>{view['target']:.2f}</td></tr>
<tr><td>Quantity</td><td>{view['quantity']}</td></tr>
<tr><td>Unrealized P&amp;L (before real exit costs)</td><td class="{pnl_class}">{pnl:+.2f}</td></tr>
<tr><td>MAE / MFE</td><td>{view['mae']:.2f} / {view['mfe']:.2f}</td></tr>
<tr><td>Opened at</td><td>{view['opened_at']}</td></tr>
<tr><td>Last real quote at</td><td>{view['last_quote_at']}</td></tr>
</table>
"""
    timestamp = (now or datetime.now(IST)).isoformat(timespec="seconds")
    return f"""<!doctype html>
<html>
<head>
<meta http-equiv="refresh" content="{refresh_seconds}">
<meta charset="utf-8">
<title>NIFTY AI Trader -- Live Position</title>
<style>
body {{ font-family: system-ui, sans-serif; padding: 2em; background: #fafafa; color: #111; }}
table {{ border-collapse: collapse; margin-top: 1em; }}
td {{ padding: 6px 16px; border-bottom: 1px solid #ddd; }}
td:first-child {{ color: #555; }}
.profit {{ color: #0a7a2a; font-weight: bold; }}
.loss {{ color: #b30000; font-weight: bold; }}
.footer {{ margin-top: 2em; color: #888; font-size: 0.9em; }}
</style>
</head>
<body>
{body}
<p class="footer">Read-only, viewing only -- no controls here.
Auto-refreshes every {refresh_seconds}s. Rendered at {timestamp}.</p>
</body>
</html>"""


def _make_handler(database: Database) -> type[BaseHTTPRequestHandler]:
    """A fresh handler class bound to `database` via closure -- avoids a
    module-level mutable global, and lets tests point a real server at
    an isolated real tmp_path database."""

    class _LiveStatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in ("/", LIVE_PATH):
                self.send_response(404)
                self.end_headers()
                return
            view = current_position_view(database)
            body = render_page(view).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return _LiveStatusHandler


def build_live_status_server(database: Database, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Binds every real local network interface (`0.0.0.0`) -- reachable
    from another real device on the same real local network, never
    beyond it (see the module docstring's explicit scope boundary)."""
    return ThreadingHTTPServer(("0.0.0.0", port), _make_handler(database))


def run_live_status_server_in_background(
    database: Database, port: int = DEFAULT_PORT
) -> tuple[threading.Thread, ThreadingHTTPServer]:
    server = build_live_status_server(database, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread, server
