#!/usr/bin/env bash
# 构建入口（Linux / macOS 调用，但便携包导出目前仅支持 Windows）
# 用法：./build.sh [args...]
set -e
cd "$(dirname "$0")"

if [ -d ".venv" ]; then
    .venv/bin/python tools/export_build.py "$@"
else
    python3 tools/export_build.py "$@"
fi
