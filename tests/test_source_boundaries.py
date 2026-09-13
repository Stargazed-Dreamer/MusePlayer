"""源码边界元测试（防回归的静态扫描）。

背景：1.1.0 曲库字典私有化时，残留访问只 grep 了 app/ 目录，漏掉了仓库根的
main.py，导致真实启动时 QTimer 回调内 AttributeError（窗口照常显示、进程不退出，
CI 冒烟未拦截）。本文件把"哪些文件允许访问曲库内部结构"固化为测试，任何新出现的
越界访问（包括仓库根脚本、tools/ 等 app/ 之外的位置）都会在 CI 直接红掉。

扫描规则：
- 曲库字典 `.tracks` / `.playlists` 只允许出现在曲库家族四个文件中；
- `_set_initial_track_for_playlist` 已转正为公开方法，私有名全仓库零命中；
- 白名单用文件名匹配，新增曲库家族文件时在此登记。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# 仓库根：conftest.py 所在目录的上一级（与 conftest._PROJECT_ROOT 同源）
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)

# 允许直接访问 .tracks/.playlists 的文件（曲库家族：内部字典的 owner 与协作类）
_LIBRARY_FAMILY = {
    "library_service.py",
    "library_cleaner.py",
    "muse_playlist_importer.py",
    "playlist_exporter.py",
}

# 本文件自身（规则描述文本、白名单注释）豁免，避免自匹配
_SELF_EXEMPT = {"test_source_boundaries.py"}

_DICT_ACCESS_RE = re.compile(r"\.(tracks|playlists)\b")
_PRIVATE_INIT_TRACK_RE = re.compile(r"\b_set_initial_track_for_playlist\b")

_SKIP_DIRS = {
    ".venv",
    ".build",
    "build",
    "__pycache__",
    ".git",
    "museplayer.egg-info",
    ".workbuddy",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
}


def _iter_project_py_files() -> list[Path]:
    root = Path(_PROJECT_ROOT)
    return sorted(p for p in root.rglob("*.py") if not any(part in _SKIP_DIRS for part in p.parts))


def test_library_dict_access_confined_to_library_family() -> None:
    """曲库字典 .tracks/.playlists 不得被曲库家族之外的任何源码文件访问。"""
    violations: list[str] = []
    for path in _iter_project_py_files():
        if path.name in _LIBRARY_FAMILY or path.name in _SELF_EXEMPT:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if _DICT_ACCESS_RE.search(line):
                violations.append(f"{path.relative_to(_PROJECT_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, (
        "发现曲库字典越界访问（应改用 get_track/get_playlist/find_playlist/has_track/track_ids 访问器）：\n"
        + "\n".join(violations)
    )


def test_private_initial_track_method_not_referenced() -> None:
    """私有名 _set_initial_track_for_playlist 已转正，全仓库（含曲库家族）零命中。"""
    violations: list[str] = []
    for path in _iter_project_py_files():
        if path.name in _SELF_EXEMPT:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if _PRIVATE_INIT_TRACK_RE.search(line):
                violations.append(f"{path.relative_to(_PROJECT_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, "发现已转正方法的旧私有名引用（应改用 set_initial_track_for_playlist）：\n" + "\n".join(
        violations
    )


def test_main_module_importable_and_startup_probe_accessors_valid() -> None:
    """main.py（启动链路所在文件）必须可导入；其启动探针用到的曲库访问器必须真实存在。

    真实启动时启动探针在 QTimer 回调里执行，异常不会终止进程，普通冒烟拦不住；
    这里直接以函数调用验证同一批访问器，保证启动探针路径不因 API 变更而悄悄失效。
    """
    import main  # noqa: PLC0415  # 导入即验证顶层无错误
    from app.models.library_store import LibraryStore
    from app.services.library_service import LibraryService
    from app.services.metadata_service import MetadataService

    svc = LibraryService(LibraryStore(Path(_PROJECT_ROOT) / "_smoke_probe_library.json"), MetadataService())
    try:
        assert svc.track_ids() == set()
        assert svc.list_playlists() == []
        assert callable(main.main)
    finally:
        probe_file = Path(_PROJECT_ROOT) / "_smoke_probe_library.json"
        probe_file.unlink(missing_ok=True)


@pytest.mark.parametrize("family_file", sorted(_LIBRARY_FAMILY))
def test_library_family_files_exist(family_file: str) -> None:
    """白名单登记的曲库家族文件必须真实存在，防止白名单腐化成摆设。"""
    assert (Path(_PROJECT_ROOT) / "app" / "services" / family_file).is_file(), family_file
