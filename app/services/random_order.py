from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(slots=True)
class ShuffleCursor:
    """随机播放游标。
    
    用于跟踪随机播放的当前位置，保持可重现的随机序列。
    使用slots=True优化内存使用。
    """
    seed: int
    """随机种子，决定播放顺序"""
    index: int
    """当前在随机序列中的索引位置"""


class DeterministicShuffle:
    """确定性随机播放序列生成器。
    
    使用种子和哈希算法生成可重现的"随机"播放序列。
    相同的种子总是产生相同的播放顺序，确保用户体验的一致性。
    """

    @staticmethod
    def make_order(track_ids: list[str], seed: int) -> list[str]:
        """生成确定性随机播放序列。
        
        基于种子和曲目ID使用SHA256哈希算法生成可重现的随机顺序。
        
        Args:
            track_ids: 曲目ID列表
            seed: 随机种子
            
        Returns:
            list[str]: 重排序后的曲目ID列表
        """
        seed = int(seed)

        def sort_key(track_id: str) -> str:
            """生成排序键，使用种子和曲目ID的哈希值。"""
            payload = f"{seed}:{track_id}".encode("utf-8")
            return hashlib.sha256(payload).hexdigest()

        return sorted(track_ids, key=sort_key)

    @staticmethod
    def clamp_index(order: list[str], index: int) -> int:
        """限制索引在有效范围内。
        
        确保索引不超出播放序列的范围。
        
        Args:
            order: 播放序列
            index: 要限制的索引值
            
        Returns:
            int: 限制在有效范围内的索引值
        """
        if not order:
            return 0
        return max(0, min(int(index), len(order) - 1))

    @classmethod
    def locate_track(cls, track_ids: list[str], seed: int, track_id: str) -> tuple[list[str], int]:
        """定位曲目在随机序列中的位置。
        
        Args:
            track_ids: 曲目ID列表
            seed: 随机种子
            track_id: 要定位的曲目ID
            
        Returns:
            tuple[list[str], int]: (播放序列, 曲目在序列中的索引)
                                 如果曲目不存在，索引返回0
        """
        order = cls.make_order(track_ids, seed)
        if not order:
            return [], 0
        try:
            index = order.index(track_id)
        except ValueError:
            index = 0
        return order, index