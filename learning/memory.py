from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS learning_memory (memory_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, memory_type TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def append(self, memory_type: str, payload: dict[str, Any], timestamp: datetime) -> str:
        memory_id = str(uuid4())
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO learning_memory(memory_id,timestamp,memory_type,payload) VALUES(?,?,?,?)",
                (memory_id, timestamp.isoformat(), memory_type, json.dumps(payload, default=str)),
            )
        return memory_id

    def recent(self, memory_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        query = "SELECT memory_id,timestamp,memory_type,payload FROM learning_memory"
        params: tuple[Any, ...] = ()
        if memory_type:
            query += " WHERE memory_type=?"
            params = (memory_type,)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params += (limit,)
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "memory_id": row[0],
                "timestamp": row[1],
                "memory_type": row[2],
                "payload": json.loads(row[3]),
            }
            for row in rows
        ]
