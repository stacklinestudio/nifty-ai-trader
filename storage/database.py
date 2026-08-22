"""Small append-only SQLite store for trading audit records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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
