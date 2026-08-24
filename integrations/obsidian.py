from __future__ import annotations

from pathlib import Path
from typing import Any


class ObsidianExporter:
    def __init__(self, vault_path: str = "") -> None:
        self.root = Path(vault_path) / "NIFTY AI Trader" if vault_path else None

    def export(self, category: str, title: str, facts: dict[str, Any]) -> Path | None:
        if self.root is None:
            return None
        try:
            destination = self.root / category
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / f"{title}.md"
            body = "\n".join(f"- **{key}**: {value}" for key, value in facts.items())
            path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
            return path
        except OSError:
            return None
