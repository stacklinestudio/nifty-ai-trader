"""Token persistence avoids committing access tokens."""

from __future__ import annotations

from pathlib import Path


class TokenManager:
    def __init__(self, path: Path = Path("secrets/kite_access.token")) -> None:
        self.path = path

    def save(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(token.strip(), encoding="utf-8")

    def load(self) -> str | None:
        return self.path.read_text(encoding="utf-8").strip() if self.path.exists() else None
