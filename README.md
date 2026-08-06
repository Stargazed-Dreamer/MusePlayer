# MusePlayer

> 基于 PySide6 + PyAV 的本地音乐播放器桌面应用，提供完整的音乐管理、播放控制、歌词/封面展示与会话恢复能力。

MusePlayer 使用 PyAV 作为底层音频解码内核，sounddevice 作为音频输出后端，通过四层分离架构（Domain / Service / Runtime / UI）实现高内聚低耦合。内置基于 TCP JSON Lines 的运行时控制协议，支持外部程序控制播放器的所有核心功能，便于与自动化脚本、外部歌单数据库等生态对接。

## 功能特性

- **音乐管理**：本地音频文件导入（单文件/文件夹）、多歌单管理、外部歌单文件（`.muse_playlist.json`）导入与统计回写、曲目元数据读取。
- **播放控制**：播放/暂停/切歌/跳转/音量调节，支持单曲循环、歌单循环、随机三种播放模式，队列与播放位置持久化。
- **歌词/封面展示**：内嵌歌词与外部歌词文件关联，支持日语原文/罗马音/翻译等多歌词文件；封面图自动提取与展示。
- **会话恢复**：关闭时持久化当前歌单、曲目、播放位置、音量、播放模式，启动时自动恢复上次播放上下文。
- **TCP 控制协议**：在 `127.0.0.1:43121`（可配置）暴露 JSON Lines 控制端点，外部程序可远程控制播放、管理歌单、导入曲目。
- **随机播放算法**：基于 SHA256 种子的确定性乱序（`app/services/random_order.py`），同一种子下顺序可复现，便于会话恢复与外部对齐。
- **播放统计**：累计播放次数、主动播放计数、早期跳过计数、累计播放秒数/百分比，支持统计数据导入（参见 `docs/musearc_stats_import_format_spec.md`）。
- **全局快捷键**：Windows 平台全局媒体快捷键注册，可在设置中自定义。
- **日间/夜间主题**：内置双主题 QSS 切换。
- **懒加载解码**：对前 6.2 秒按需解码的窗口读取策略，超过阈值后自动提升为完整读取，兼顾首响速度与续播平滑。
- **窗口几何恢复**：记忆窗口位置/尺寸，支持无边框缩放、吸附与侧边栏联动。
- **任务栏进度**：Windows 任务栏播放进度桥接（基于 comtypes）。
- **定时保存与日志**：可配置定时自动保存，分级日志与崩溃日志记录。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言/框架 | Python 3.12、PySide6 6.7+ |
| 音频解码 | PyAV >= 12.0 |
| 音频输出 | sounddevice >= 0.4.6 |
| 数值计算 | numpy >= 1.26 |
| 元数据 | mutagen >= 1.47 |
| Windows 集成 | comtypes >= 1.4 |

完整依赖见 [`requirements.txt`](requirements.txt)。

## 安装

需要 Python 3.12 或更高版本。

```bash
pip install -r requirements.txt
```

## 启动

```bash
# 方式一：直接运行
python main.py

# 方式二：批处理启动（Windows）
start.bat
```

## 开发说明

```bash
# 运行测试
pytest tests/ -v

# 代码检查
ruff check .

# 类型检查
mypy app/
```

更多开发规范与提交流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 架构文档

- [架构设计文档（中文）](docs/architecture_design_cn.md)
- [架构设计文档（英文）](docs/ARCHITECTURE.md)
- [运行时控制协议](docs/CONTROL_PROTOCOL.md)
- [歌单导出格式](docs/playlist_export_format_cn.md)
- [统计数据导入格式](docs/musearc_stats_import_format_spec.md)
- [发布检查清单](docs/release_checklist.md)
- 项目结构与关键约束见 [AGENTS.md](AGENTS.md)

## 控制协议

MusePlayer 在本地 TCP 端口暴露运行时控制接口，默认 `127.0.0.1:43121`，可在设置中修改。

- **传输**：TCP + JSON Lines（每行一个 JSON 对象），UTF-8
- **响应**：统一 `{"ok": true, "result": ...}` / `{"ok": false, "error": "..."}`
- **能力**：播放控制、曲目/歌单管理、文件与歌单导入、状态查询

快速验证（发送 `ping`）：

```bash
python -c "import socket,json;s=socket.socket();s.connect(('127.0.0.1',43121));s.sendall(json.dumps({'cmd':'ping'}).encode()+b'\n');print(s.recv(1024).decode())"
```

完整命令列表见 [docs/CONTROL_PROTOCOL.md](docs/CONTROL_PROTOCOL.md)。

## 许可证

[MIT License](LICENSE) © 2025-2026 MusePlayer Contributors

## 贡献

欢迎参与贡献，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
