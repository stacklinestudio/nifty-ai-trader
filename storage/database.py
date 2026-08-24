"""Small append-only SQLite store for trading audit records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from events.contracts import Event
from storage.models import SignalRecord, Trade


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
