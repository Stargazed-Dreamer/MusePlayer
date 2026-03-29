from __future__ import annotations

import json
from pathlib import Path

from .entities import SessionState


class SessionStore:
    def __init__(self, data_dir: Path):
        self._path = Path(data_dir).resolve() / "session.json"

    def load(self) -> SessionState:
        if not self._path.exists():
            return SessionState()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return SessionState()
        if not isinstance(payload, dict):
            return SessionState()
        return SessionState.from_dict(payload)

    def save(self, state: SessionState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )