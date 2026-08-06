from __future__ import annotations

import hashlib
import time

import pytest

from app.services.random_order import DeterministicShuffle, ShuffleCursor


# ---- ShuffleCursor ----

def test_shuffle_cursor_fields():
    cursor = ShuffleCursor(seed=42, index=3)
    assert cursor.seed == 42
    assert cursor.index == 3


def test_shuffle_cursor_is_dataclass_like():
    cursor = ShuffleCursor(seed=1, index=0)
    # 相同字段构造应相等
    assert cursor == ShuffleCursor(seed=1, index=0)
    assert cursor != ShuffleCursor(seed=2, index=0)


# ---- make_order 确定性 ----

def test_make_order_deterministic_same_seed():
    ids = ["t1", "t2", "t3", "t4", "t5"]
    order_a = DeterministicShuffle.make_order(ids, 123)
    order_b = DeterministicShuffle.make_order(ids, 123)
    assert order_a == order_b


def test_make_order_different_seed_likely_different():
    ids = ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"]
    order_a = DeterministicShuffle.make_order(ids, 1)
    order_b = DeterministicShuffle.make_order(ids, 2)
    assert order_a != order_b


# ---- make_order 元素完整性 ----

def test_make_order_contains_all_elements():
    ids = ["a", "b", "c", "d", "e"]
    order = DeterministicShuffle.make_order(ids, 99)
    assert sorted(order) == sorted(ids)
    assert len(order) == len(ids)


def test_make_order_no_duplicates():
    ids = [f"t{i}" for i in range(20)]
    order = DeterministicShuffle.make_order(ids, 7)
    assert len(order) == len(set(order))


# ---- make_order 边界情况 ----

def test_make_order_empty_list():
    assert DeterministicShuffle.make_order([], 123) == []


def test_make_order_single_element():
    assert DeterministicShuffle.make_order(["only"], 123) == ["only"]


def test_make_order_two_elements_stable():
    ids = ["x", "y"]
    order = DeterministicShuffle.make_order(ids, 10)
    assert sorted(order) == ["x", "y"]


# ---- seed 不影响元素集合 ----

def test_make_order_seed_does_not_change_element_set():
    ids = ["x1", "x2", "x3", "x4"]
    for seed in range(10):
        order = DeterministicShuffle.make_order(ids, seed)
        assert sorted(order) == sorted(ids)


# ---- 大规模输入 ----

def test_make_order_large_scale_correctness_and_performance():
    ids = [f"track-{i:04d}" for i in range(1000)]
    start = time.perf_counter()
    order = DeterministicShuffle.make_order(ids, 2024)
    elapsed = time.perf_counter() - start
    assert len(order) == 1000
    assert sorted(order) == sorted(ids)
    assert len(set(order)) == 1000
    # 1000 个元素的排序应在 1 秒内完成
    assert elapsed < 1.0


# ---- seed 类型转换 ----

def test_make_order_seed_int_conversion():
    ids = ["t1", "t2", "t3"]
    order_int = DeterministicShuffle.make_order(ids, 5)
    # 内部 int(seed) 转换，字符串 "5" 应与整数 5 等价
    order_str = DeterministicShuffle.make_order(ids, "5")
    assert order_int == order_str


# ---- 排序键验证 ----

def test_make_order_matches_sha256_sort_key():
    """验证排序键确实基于 sha256(f'{seed}:{track_id}')。"""
    ids = ["alpha", "beta", "gamma"]
    seed = 777

    def expected_key(tid: str) -> str:
        return hashlib.sha256(f"{seed}:{tid}".encode("utf-8")).hexdigest()

    expected = sorted(ids, key=expected_key)
    assert DeterministicShuffle.make_order(ids, seed) == expected


# ---- clamp_index ----

def test_clamp_index_empty_order():
    assert DeterministicShuffle.clamp_index([], 5) == 0


def test_clamp_index_within_range():
    order = ["a", "b", "c"]
    assert DeterministicShuffle.clamp_index(order, 1) == 1


def test_clamp_index_negative():
    order = ["a", "b", "c"]
    assert DeterministicShuffle.clamp_index(order, -5) == 0


def test_clamp_index_too_large():
    order = ["a", "b", "c"]
    assert DeterministicShuffle.clamp_index(order, 100) == 2


def test_clamp_index_zero():
    order = ["a", "b", "c"]
    assert DeterministicShuffle.clamp_index(order, 0) == 0


def test_clamp_index_int_conversion():
    order = ["a", "b", "c"]
    # 内部 int() 转换，字符串数字应被支持
    assert DeterministicShuffle.clamp_index(order, "1") == 1


# ---- locate_track ----

def test_locate_track_found():
    ids = ["t1", "t2", "t3", "t4"]
    order, index = DeterministicShuffle.locate_track(ids, 42, "t2")
    assert order[index] == "t2"
    assert "t2" in order


def test_locate_track_not_found_returns_zero_index():
    ids = ["t1", "t2", "t3"]
    order, index = DeterministicShuffle.locate_track(ids, 42, "nonexistent")
    assert index == 0
    assert sorted(order) == sorted(ids)


def test_locate_track_empty_input():
    order, index = DeterministicShuffle.locate_track([], 42, "t1")
    assert order == []
    assert index == 0


def test_locate_track_order_consistent_with_make_order():
    ids = ["t1", "t2", "t3", "t4", "t5"]
    seed = 321
    order, _ = DeterministicShuffle.locate_track(ids, seed, "t3")
    assert order == DeterministicShuffle.make_order(ids, seed)
