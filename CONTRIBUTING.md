# 贡献指南

感谢你关注 MusePlayer！本文档说明如何搭建开发环境、提交代码与维护项目一致性。

## 开发环境搭建

- Python 3.12 或更高版本
- Windows 平台（项目使用 comtypes 进行 Windows 任务栏集成、全局快捷键等）

```bash
# 克隆仓库后安装运行依赖
pip install -r requirements.txt

# 安装开发依赖（ruff / pytest / mypy 等）
pip install -e ".[dev]"
```

启动应用进行验证：

```bash
python main.py
```

## 代码规范

- **检查**：`ruff check .`
- **格式化**：`ruff format .`
- **行长度**：建议遵循 ruff 默认（88），如需调整在 `pyproject.toml` 中统一配置
- **import 排序**：使用 ruff 的 isort 规则（`I`），标准库 / 第三方 / 本地分组
- **类型提示**：新增公开 API 建议补充类型提示，依赖 `from __future__ import annotations`

## 提交信息规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

| 类型 | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `docs` | 文档变更 |
| `refactor` | 重构（不改变行为） |
| `test` | 新增/修改测试 |
| `chore` | 构建、依赖、脚手架等杂项 |

示例：`feat(player): 支持歌单导出为 .muse_playlist.json`

## 测试要求

- 新增功能需附带测试，放置于 `tests/` 目录
- 运行测试：`pytest tests/ -v`
- 修改 `core/` 模块后，务必确认音频播放不受影响
- 验证运行时控制接口可用：启动应用后发送 `ping` 命令

```bash
python -c "import socket,json;s=socket.socket();s.connect(('127.0.0.1',43121));s.sendall(json.dumps({'cmd':'ping'}).encode()+b'\n');print(s.recv(1024).decode())"
```

## 项目结构

项目结构、关键约束与常见陷阱见 [AGENTS.md](AGENTS.md)。提交前请确认已阅读“关键约束”章节，特别是：

- 不直接操作 `core/` 模块，所有播放操作通过 `PlayerService`
- sounddevice 回调中绝不阻塞
- 修改曲库数据后必须发射 `library_changed.emit()`
- 跨线程更新 UI 必须通过 Signal/Slot

## PR 流程

1. Fork 仓库
2. 基于最新主干创建分支：`git checkout -b feat/your-feature`
3. 提交符合规范的 commit
4. 推送到你的 Fork：`git push origin feat/your-feature`
5. 发起 Pull Request，描述变更内容与验证方式

PR 检查项：

- [ ] `ruff check .` 通过
- [ ] `pytest tests/ -v` 通过
- [ ] 新增/修改功能已在相关文档同步
- [ ] 已按功能变更检查清单逐项核对

## 功能变更检查清单

每次新增功能、修改模块或重构代码后，请按 [.trae/rules/project_rules.md](.trae/rules/project_rules.md) 逐项检查，确保代码与文档同步（代码变更、文档更新、配置变更、Git、测试、MusePlayer 专项）。

## Skill 系统

MusePlayer 通过 `.agents/skills/` 目录维护可执行的工作流 Skill（如 `player-control`、`library-manage`）。新增或修改功能时，需同步维护相关 Skill 文件：

- Skill 唯一存放目录：`.agents/skills/`
- 索引入口：[`.agents/skills/_index.md`](.agents/skills/_index.md)
- 新增 Skill 必须在 `_index.md` 中注册一行
- 修改运行时控制命令（`dispatch_command`）后，需同步更新 `player-control.md` 或 `library-manage.md`
- 创建新 Skill 可参考 `.agents/skills/skill-creator.md`
