"""版本号一致性元测试。

背景：应用版本号是双份真实来源——`app/version.py` 的 `APP_VERSION` 负责运行时
展示与构建产物命名（`tools/export_build.py`），`pyproject.toml` 的
`[project].version` 负责打包元数据。两处都带"保持一致"的注释却没有任何自动化
校验，发版时漏改一处会产生"关于对话框写 1.2.1、pip 元数据写 1.2.0"这类不一致，
且不会有任何报错。本文件把该不变式固化下来。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from app.version import APP_VERSION

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    with (_PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_app_version_matches_pyproject():
    """app/version.py 与 pyproject.toml 的应用版本必须一致。"""
    assert _pyproject_version() == APP_VERSION


def test_app_version_is_semver():
    """应用版本必须是三段式语义化版本，构建产物目录名依赖该格式。"""
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), APP_VERSION
