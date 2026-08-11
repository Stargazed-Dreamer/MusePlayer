from __future__ import annotations

# 应用版本（语义化版本：主.次.修订）
# 与 pyproject.toml [project].version 保持一致
APP_VERSION = "1.0.1"

# 数据格式版本（library.json / session.json / settings.json / playback_stats.json 共用 schema）
# 主版本：破坏性 schema 变更（旧文件无法读取）；次版本：兼容性增量（新增字段、旧代码可忽略）
DATA_FORMAT_VERSION = "1.0"

# 通信协议版本（TCP JSON Lines 运行时控制协议，见 docs/CONTROL_PROTOCOL.md）
# 主版本：不兼容的命令/响应格式变更；次版本：新增命令或可选字段
PROTOCOL_VERSION = "1.0"

# 维护者/作者信息（个人维护，非团队）
AUTHOR = "Stargazed-Dreamer"
COPYRIGHT = "Copyright (C) 2025-2026 Stargazed-Dreamer"

# 许可证
LICENSE_NAME = "GPL-3.0"
LICENSE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"

# 仓库链接
REPO_URL = "https://github.com/Stargazed-Dreamer/MusePlayer"
REPO_ISSUES_URL = "https://github.com/Stargazed-Dreamer/MusePlayer/issues"
