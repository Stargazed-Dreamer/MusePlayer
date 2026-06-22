# AGENTS.md - MusePlayer 项目指引

> **这是 AI Agent 的入口文档。** Agent 每次启动时首先读取此文件，了解项目全貌。
> 人类开发者也应阅读此文件，它是项目唯一的"真相源"。

## 项目定位

MusePlayer 是一个基于 PySide6 + PyAV 的本地音乐播放器桌面应用，提供完整的音乐管理、播放控制、歌词/封面展示、会话恢复功能。

## 项目简介

MusePlayer 使用 PyAV 作为底层音频解码内核，sounddevice 作为音频输出后端，通过四层分离架构（Domain/Service/Runtime/UI）实现高内聚低耦合。内置 TCP JSON Lines 运行时控制协议，支持外部程序控制播放器的所有核心功能。

## 技术栈

- **语言/框架**：Python 3.12 + PySide6 6.7+
- **核心依赖**：PyAV>=12.0（音频解码）、sounddevice>=0.4.6（音频输出）、numpy>=1.26（PCM 数据）、mutagen>=1.47（元数据）、comtypes>=1.4（Windows 任务栏）
- **构建工具**：无（纯 Python，pip install）
- **部署方式**：本地桌面应用，PyInstaller 打包（build.bat）

## 启动方式

```bash
# 方式1：直接运行
python main.py

# 方式2：批处理启动
start.bat
```

## 关键约束

### 音频内核

- **绝不直接操作 core/ 模块**：所有播放操作必须通过 `PlayerService` 进行，`PyAVPlayerCore` 是线程安全的底层内核，UI 层绝不直接调用
- **sounddevice 回调中绝不阻塞**：音频输出回调在实时线程中运行，任何阻塞操作会导致音频卡顿
- **窗口读取阈值 6.2 秒**：`LazyDecodeMixin` 对前 6.2 秒按需解码，超过后自动提升为完整读取，修改此阈值需同步更新 `PlayerService` 和 `LazyDecodeMixin`

### Qt 线程安全

- **信号槽跨线程通信**：UI 操作必须在主线程执行，跨线程更新 UI 必须通过 Signal/Slot
- **绝不在线程中直接操作 QWidget**：会导致随机崩溃

### 数据持久化

- **JSON 文件是唯一数据源**：`data/library.json`、`data/session.json`、`data/settings.json`、`data/playback_stats.json`
- **绝不跳过 `library_changed.emit()`**：修改曲库数据后必须发射此信号，否则 UI 不会刷新
- **统计数据必须通过 `PlaybackStatsService`**：直接修改 JSON 文件会导致内存/磁盘不一致

### 运行时控制协议

- **协议格式**：TCP localhost + JSON Lines，每行一个 JSON 命令，响应也是 JSON Lines
- **默认端口**：`127.0.0.1:43121`，可在设置中修改
- **响应格式**：统一 `{"ok": true/false, "result": ..., "error": ...}`

### API / 接口常见陷阱

- **`dispatch_command` 的 cmd 字段必须小写**：内部做了 `.strip().lower()`，但传入时保持小写可避免歧义
- **`seek` 的 position 参数单位是秒（float）**：不是毫秒，不是百分比
- **`set_volume` 的 volume 范围是 0.0~1.0**：不是 0~100
- **`remove_track_from_playlist` 可能删除全局曲目**：如果曲目只属于一个歌单，移除后统计数据也会被清理
- **`play_track` 需要 track_id 而非文件路径**：文件路径用 `play_file`

### IDE / 工具使用注意

- 项目使用 PySide6（Qt for Python），类型提示依赖 `from __future__ import annotations`
- `data/` 目录下的 JSON 文件在运行时被修改，不要手动编辑正在运行的实例的数据文件

## 项目结构

```
MusePlayer/
├── main.py                   # 应用入口（崩溃兜底 + Qt 启动）
├── core/                     # 底层音频播放内核
│   ├── core.py               # PyAVPlayerCore（解码 + 播放控制）
│   ├── output.py             # AudioOutputBackend 接口 + SoundDevice 实现
│   └── types.py              # AudioMeta、PlayerCoreError、PlaybackWindow
├── app/                      # 应用主代码
│   ├── models/               # 数据实体 + 持久化存储
│   │   ├── entities.py       # Track、Playlist、SessionState、Settings
│   │   ├── library_store.py  # library.json 读写
│   │   ├── session_store.py  # session.json 读写
│   │   └── settings_store.py # settings.json 读写
│   ├── services/             # 业务服务层
│   │   ├── app_controller.py # 核心编排中枢（初始化、命令分发）
│   │   ├── player_service.py # 播放器服务（模式、队列、懒加载）
│   │   ├── library_service.py# 曲库服务（导入、搜索、清理）
│   │   ├── metadata_service.py# 元数据服务（标签、封面、歌词）
│   │   ├── playback_stats_service.py # 播放统计
│   │   ├── random_order.py   # SHA256 种子化确定性乱序
│   │   └── player_service_mixins/ # 混入类
│   ├── runtime/              # 运行时控制
│   │   └── control_server.py # TCP JSON Lines 控制服务器
│   ├── ui/                   # PySide6 UI 层
│   │   ├── main_window.py    # 门面导出
│   │   ├── main_window_impl.py # 主窗口核心实现
│   │   ├── main_window_helpers.py # 辅助组件
│   │   ├── main_window_mixins/ # 播放交互 + 窗口行为混入
│   │   ├── playlist_dialog.py # 歌单管理对话框
│   │   ├── settings_dialog.py # 设置对话框
│   │   └── theme.py          # QSS 主题（日间/夜间）
│   ├── utils/                # 工具（日志配置）
│   └── version.py            # 版本号
├── data/                     # 运行时数据（JSON 持久化）
├── docs/                     # 架构文档、协议文档
├── tools/                    # 构建脚本
├── .agents/skills/           # Skill 定义文件
│   ├── _index.md             # Skill 索引（入口）
│   └── *.md                  # 各 Skill 定义
├── .trae/rules/              # AI 规则
│   └── project_rules.md      # 功能变更检查清单
└── .gitignore
```

## Skill 系统

所有 Skill 定义在 `.agents/skills/` 目录，索引文件为 `_index.md`。

- **Skill 是可执行的知识**：不是文档，而是一组精确的工作流指令
- **唯一目录**：`.agents/skills/` 是 Skill 文件的唯一存放位置
- **索引入口**：`_index.md` 列出所有 Skill 的触发词、依赖、输出

## 测试

```bash
# 项目当前无自动化测试框架
# 验证方式：启动应用后通过运行时控制接口发送 ping 命令
python -c "import socket,json;s=socket.socket();s.connect(('127.0.0.1',43121));s.sendall(json.dumps({'cmd':'ping'}).encode()+b'\n');print(s.recv(1024).decode())"
```

## 功能变更检查清单

见 `.trae/rules/project_rules.md`。
