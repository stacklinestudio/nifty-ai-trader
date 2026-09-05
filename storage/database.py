"""Small append-only SQLite store for trading audit records."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from data.option_chain import OptionQuote, quotes_from_json, quotes_to_json
from events.contracts import Event
from storage.models import SignalRecord, Trade

OPTION_CHAIN_SNAPSHOT_SOURCE = "option_chain"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY, timestamp TEXT, direction TEXT, confidence REAL, payload TEXT);
                CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, order_id TEXT UNIQUE, symbol TEXT, side TEXT, quantity INTEGER, entry_price REAL, exit_price REAL, opened_at TEXT, closed_at TEXT, exit_reason TEXT, net_pnl REAL);
                CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, timestamp TEXT, source TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS daily_metrics (date TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS strategy_runs (id INTEGER PRIMARY KEY, timestamp TEXT, run_type TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS audit_events (event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, agent TEXT NOT NULL, event_type TEXT NOT NULL, input_summary TEXT NOT NULL, output_summary TEXT NOT NULL, confidence REAL, source TEXT, strategy_version TEXT);
                CREATE TABLE IF NOT EXISTS open_positions (order_id TEXT PRIMARY KEY, opened_at TEXT NOT NULL, state_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS counterfactual_records (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, setup_type TEXT NOT NULL, direction TEXT NOT NULL, rejection_reason TEXT NOT NULL, exit_reason TEXT NOT NULL, profitable INTEGER NOT NULL, label TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS demo_live_position (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL, saved_at TEXT NOT NULL);
            """)

    def save_signal(self, signal: SignalRecord) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO signals(timestamp,direction,confidence,payload) VALUES(?,?,?,?)",
                (
                    signal.timestamp.isoformat(),
                    signal.direction,
                    signal.confidence,
                    json.dumps(signal.serializable(), default=str),
                ),
            )

    def recent_signals(self, limit: int = 1000) -> list[dict]:
        """Brief 12 Part A: real, queryable score-attribution history --
        previously each candidate's 7-input breakdown existed only as a
        log line, never aggregable after the fact (see
        reports/score_diagnostic.py, which reads this). `payload` is the
        full serialized SignalRecord (see save_signal); `features` -- the
        7-input attribution dict Orchestrator.run_cycle passes in --
        flattened up to the top level for convenience, real column values
        (timestamp/direction/confidence) taking precedence over anything
        with the same key inside `features`.
        """
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT payload FROM signals ORDER BY timestamp"
            ).fetchall()
        results = []
        for (payload,) in rows[-limit:]:
            record = json.loads(payload)
            features = record.pop("features", {}) or {}
            results.append({**features, **record})
        return results

    def save_trade(self, trade: Trade) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO trades(order_id,symbol,side,quantity,entry_price,exit_price,opened_at,closed_at,exit_reason,net_pnl) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    trade.order_id,
                    trade.symbol,
                    trade.side,
                    trade.quantity,
                    trade.entry_price,
                    trade.exit_price,
                    trade.opened_at.isoformat(),
                    trade.closed_at.isoformat() if trade.closed_at else None,
                    trade.exit_reason,
                    trade.net_pnl,
                ),
            )

    def save_event(self, event: Event) -> None:
        """Duplicate event IDs are ignored; historic event rows are never updated."""
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO audit_events(event_id,timestamp,agent,event_type,input_summary,output_summary,confidence,source,strategy_version) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.agent,
                    event.event_type.value,
                    json.dumps(event.input_summary, default=str),
                    json.dumps(event.output_summary, default=str),
                    event.confidence,
                    event.source,
                    event.strategy_version,
                ),
            )

    def events(self, limit: int = 100) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT event_id,timestamp,agent,event_type,input_summary,output_summary,confidence,source,strategy_version FROM audit_events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = [
            "event_id",
            "timestamp",
            "agent",
            "event_type",
            "input_summary",
            "output_summary",
            "confidence",
            "source",
            "strategy_version",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def save_open_position(self, order_id: str, opened_at: str, state_payload: dict) -> None:
        """One row per currently-open position. Deleted on close via
        close_open_position -- this table's contents are exactly "what a
        restarted process must resume or escalate on," never a history."""
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO open_positions(order_id,opened_at,state_json) VALUES(?,?,?)",
                (order_id, opened_at, json.dumps(state_payload, default=str)),
            )

    def open_positions(self) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT order_id, opened_at, state_json FROM open_positions ORDER BY opened_at"
            ).fetchall()
        return [{"order_id": r[0], "opened_at": r[1], "state": json.loads(r[2])} for r in rows]

    def close_open_position(self, order_id: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM open_positions WHERE order_id = ?", (order_id,))

    def save_demo_position(self, payload: dict, saved_at: datetime) -> None:
        """Brief 26: a wholly separate real table from `open_positions` --
        never read by `recover_open_positions` or any real position-
        supervision code path, so a demo/mock state can never be mistaken
        for, or interfere with, a real open position. A real singleton
        (CHECK (id = 1) in the schema): only ever one demo state at a
        time, always overwritten, never accumulating rows."""
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO demo_live_position(id, payload, saved_at) VALUES(1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, saved_at = excluded.saved_at",
                (json.dumps(payload, default=str), saved_at.isoformat()),
            )

    def demo_position(self) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT payload FROM demo_live_position WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else None

    def clear_demo_position(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM demo_live_position WHERE id = 1")

    def save_option_chain_snapshot(self, timestamp: datetime, quotes: list[OptionQuote]) -> None:
        """Persists the option chain fetched this cycle so the NEXT cycle
        can supply it as `previous_option_quotes` -- the real prior-session
        snapshot intelligence/oi_buildup.py::detect_buildup needs, and that
        no live source persisted before Brief 5 (execution/live_context.py
        KNOWN_GAPS). Reuses the `snapshots` table, which existed in the
        schema but had no reader/writer anywhere until this -- not a new
        table. Never called with an empty list: an empty snapshot would
        silently poison the *next* cycle's real "unavailable" read into a
        fabricated "compared against nothing and found no buildup."
        """
        if not quotes:
            return
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO snapshots(timestamp,source,payload) VALUES(?,?,?)",
                (timestamp.isoformat(), OPTION_CHAIN_SNAPSHOT_SOURCE, quotes_to_json(quotes)),
            )

    def latest_option_chain_snapshot(self) -> list[OptionQuote]:
        """Real prior-session data when one has been persisted; an explicit
        empty list (not fabricated) the first time this ever runs, or after
        any gap where no snapshot exists yet -- callers already treat an
        empty previous_option_quotes as "unavailable," not "no buildup."
        """
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT payload FROM snapshots WHERE source = ? ORDER BY timestamp DESC LIMIT 1",
                (OPTION_CHAIN_SNAPSHOT_SOURCE,),
            ).fetchone()
        return quotes_from_json(row[0]) if row else []

    def save_counterfactual(self, record) -> None:
        """Brief 12 Part B: structurally separate from `trades` (real paper
        trades) and never touches learning.memory -- a dedicated table, own
        reader below, own real `label` column stamped on every row in
        addition to the same label always present inside `payload`. See
        research/counterfactual.py::CounterfactualRecord -- `label` is a
        read-only property there, not a field a caller can override.

        `record` deliberately untyped here (duck-typed on
        .timestamp/.setup_type/.direction/.rejection_reason/.exit_reason/
        .profitable/.label/.to_dict()) -- research.counterfactual imports
        execution.live_context, which transitively imports
        agents.orchestrator, which imports this module; a top-level (or
        even TYPE_CHECKING-only) import of CounterfactualRecord here would
        be circular.
        """
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO counterfactual_records"
                "(timestamp,setup_type,direction,rejection_reason,exit_reason,profitable,label,payload)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    record.timestamp.isoformat(),
                    record.setup_type,
                    record.direction,
                    record.rejection_reason,
                    record.exit_reason,
                    int(record.profitable),
                    record.label,
                    json.dumps(record.to_dict(), default=str),
                ),
            )

    def recent_counterfactuals(self, limit: int = 10000) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT payload FROM counterfactual_records ORDER BY timestamp"
            ).fetchall()
        return [json.loads(payload) for (payload,) in rows[-limit:]]
