#!/usr/bin/env bash
# 开发启动脚本（Linux / macOS）
# 用法：./start.sh
set -e
cd "$(dirname "$0")"

if [ -d ".venv" ]; then
    # 优先使用项目虚拟环境
    .venv/bin/python main.py "$@"
else
    python3 main.py "$@"
fi
