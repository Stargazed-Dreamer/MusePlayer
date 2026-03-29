from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(slots=True)
class ShuffleCursor:
    seed: int
    index: int


class DeterministicShuffle:
    """Deterministic shuffle using two numbers: seed + index."""

    @staticmethod
    def make_order(track_ids: list[str], seed: int) -> list[str]:
        seed = int(seed)

        def sort_key(track_id: str) -> str:
            payload = f"{seed}:{track_id}".encode("utf-8")
            return hashlib.sha256(payload).hexdigest()

        return sorted(track_ids, key=sort_key)

    @staticmethod
    def clamp_index(order: list[str], index: int) -> int:
        if not order:
            return 0
        return max(0, min(int(index), len(order) - 1))

    @classmethod
    def locate_track(cls, track_ids: list[str], seed: int, track_id: str) -> tuple[list[str], int]:
        order = cls.make_order(track_ids, seed)
        if not order:
            return [], 0
        try:
            index = order.index(track_id)
        except ValueError:
            index = 0
        return order, index