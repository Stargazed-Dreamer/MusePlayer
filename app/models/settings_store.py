from __future__ import annotations

import json
from pathlib import Path

from .entities import Settings


class SettingsStore:
    def __init__(self, data_dir: Path):
        self._path = Path(data_dir).resolve() / "settings.json"

    def load(self) -> Settings:
        if not self._path.exists():
            return Settings()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return Settings()
        if not isinstance(payload, dict):
            return Settings()
        return Settings.from_dict(payload)

    def save(self, settings: Settings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )