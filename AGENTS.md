# AGENTS.md - MusePlayer 项目指引

> **这是 AI Agent 的入口文档。** Agent 每次启动时首先读取此文件，了解项目全貌。
> 人类开发者也应阅读此文件，它是项目唯一的"真相源"。

## 项目定位

MusePlayer 是一个基于 PySide6 + PyAV 的本地音乐播放器桌面应用，提供完整的音乐管理、播放控制、歌词/封面展示、会话恢复功能。

## 项目简介

MusePlayer 使用 PyAV 作为底层音频解码内核，sounddevice 作为音频输出后端，通过四层分离架构（Domain/Service/Runtime/UI）实现高内聚低耦合。内置 TCP JSON Lines 运行时控制协议，支持外部程序控制播放器的所有核心功能。

## 技术栈

- **语言/框架**：Python 3.12 + PySide6 6.7+
- **核心依赖**：PyAV>=12.0（音频解码）、sounddevice>=0.4.6（音频输出）、numpy>=1.26（PCM 数据）、mutagen>=1.47（元数据）、comtypes>=1.4（**仅 Windows**，任务栏进度，须以环境标记 `; sys_platform == 'win32'` 声明）
- **构建工具**：tools/export_build.py（嵌入式 Python + PySide6 模块裁剪，非 PyInstaller）
- **部署方式**：本地桌面应用，便携式运行时包打包（`build.bat` → `tools/export_build.py`）

## 启动方式

```bash
# 方式1：直接运行（跨平台）
python main.py

# 方式2：开发启动脚本
start.bat        # Windows
./start.sh       # Linux / macOS
```

> 便携式运行时包（`build.bat` → `tools/export_build.py`）目前仅支持 Windows；
> macOS / Linux 用户请使用 `pip install -r requirements.txt && python main.py`。

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

### 隐私与公开发布

> 项目已公开发布。所有提交进入仓库的内容都会被公开，维护时必须遵守以下隐私红线。

- **绝不向仓库提交个人数据**：本地绝对路径、用户名、邮箱、手机号、密钥、Token、个人音乐库结构、对话/调试记录一律不得入库
- **运行时数据走 `data/` 目录**：所有运行时生成的状态（session/library/settings/playback_stats/logs/crashlogs）只能落在已被 `.gitignore` 覆盖的 `data/` 目录下，**绝不**在项目根目录或其他位置生成数据文件
- **崩溃/日志文件名固定**：`crash.txt`、`crashlog.log`、`*.log` 已被 gitignore；新增日志/转储文件必须使用 `.log` 后缀或显式加入 `.gitignore`
- **开发笔记/对话存档不入库**：`todo.txt`、`对话存档.txt`、`*存档*.txt` 已被 gitignore；新增临时笔记请沿用相似命名或加入 gitignore
- **测试素材不入库**：`testFile/`、`test*`、`_library_test_*` 已被 gitignore；测试音频/歌词只放这些目录
- **导出产物及时清理**：根目录下的 `*.muse_stats.json`、`playback_stats.muse_stats.json` 等导出文件已被 gitignore，发布前应物理删除
- **代码中绝不硬编码本机路径**：所有路径必须基于 `Path(__file__)`、`Settings` 字段或用户选择动态计算；`C:\Users\`、`/home/<user>`、`F:\codex\` 等绝对路径禁止出现在源码中
- **错误堆栈可能泄露路径**：`crash.txt` / 日志中的 traceback 会暴露本机路径，这些文件必须留在 gitignore 之内
- **新增依赖前检查是否携带个人配置**：例如某些库会在 `~/.config/` 写文件，文档中应说明
- **公开前清单**：每次发布前确认 `data/`、`.build/`、`testFile/`、`*.log`、`crash.txt`、`todo.txt`、`*.muse_stats.json`、`对话存档.txt` 均已物理清理（即使已被 gitignore，避免打包误入）

### 跨平台兼容性

> 项目目标：Windows 一等公民，macOS / Linux 实验性支持。新代码必须避免不必要的平台绑定。

- **平台专有代码必须加守卫**：任何调用 `ctypes.windll`、`winreg`、`comtypes`、`RegisterHotKey` 等 Windows 专有 API 的代码，必须包裹在 `if sys.platform.startswith("win"):` 内，或在模块顶部用 `try: import ... except ImportError: ... = None` 软导入
- **平台专有依赖必须用环境标记**：`comtypes`、`pywin32` 等仅 Windows 的包，在 `requirements.txt` 和 `pyproject.toml` 中必须写为 `comtypes>=1.4; sys_platform == 'win32'`，禁止无条件依赖
- **非 Windows 平台必须能启动**：缺失 Windows 专有模块时，应用应正常启动并自动降级（任务栏进度 no-op、全局快捷键返回提示），**绝不**抛 `ImportError` 崩溃
- **路径处理统一用 `pathlib.Path` 或 `os.path.join`**：禁止硬编码 `\\` 分隔符；需要规范化时用 `replace("\\", "/")` 是允许的（双向兼容）
- **调用外部程序必须按平台分发**：`subprocess.Popen(["explorer.exe", ...])` 这类调用必须先判断 `sys.platform`，Windows 用 `explorer.exe`、macOS 用 `open -R`、Linux 用 `xdg-open`
- **中文字体按平台选择**：Windows→`Microsoft YaHei`、macOS→`PingFang SC`、Linux→`Noto Sans CJK SC`
- **音频 hostapi 偏好不写死索引**：`core/output.py` 中"优先 WASAPI"应理解为"优先低延迟 hostapi"，注释/变量名不要写死 Windows 语义
- **Windows 专有功能可保留专属实现**：任务栏进度（`ITaskbarList3`）、全局快捷键（`RegisterHotKey`）无跨平台等价物时，保留 Windows 实现 + 非 Windows 用 no-op / 提示即可，不必强行重写
- **便携式打包保留 Windows 专用**：`tools/export_build.py` 的嵌入式 Python 方案是 Windows 专有设计，无需跨平台化；macOS / Linux 通过 `pip install` 运行
- **新增 `.bat` 启动脚本时同步考虑 `.sh`**：跨平台入口脚本应成对提供
- **`pyproject.toml` classifiers 维护三平台**：Windows / Linux / macOS 均应列出，避免被 PyPI 误判为 Windows-only
- **死代码及时清理**：未使用的 Windows 常量（如 `WM_NCHITTEST`、`HTLEFT` 等）应删除，避免误导后续维护者以为是活跃绑定

## 项目结构

```
MusePlayer/
├── main.py                   # 应用入口（崩溃兜底 + Qt 启动）
├── start.bat                 # 开发启动脚本（.venv\Scripts\python main.py）
├── build.bat                 # 打包入口（调用 tools/export_build.py）
├── icon.ico                  # 应用图标
├── requirements.txt          # 依赖清单（pip install -r）
├── pyproject.toml            # 项目元数据 + ruff/mypy 配置
├── .pre-commit-config.yaml   # 预提交钩子（ruff + ruff-format）
├── LICENSE                   # MIT 许可证
├── README.md                 # 项目说明（面向用户/贡献者）
├── CHANGELOG.md              # 版本变更日志
├── CONTRIBUTING.md           # 贡献指南
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
│   │   ├── shortcut_settings_dialog.py # 快捷键设置对话框
│   │   ├── shortcut_settings.py # 快捷键操作与默认值
│   │   ├── global_hotkeys.py # Windows 全局快捷键注册
│   │   └── theme.py          # QSS 主题（日间/夜间）
│   ├── utils/                # 工具
│   │   └── logging_setup.py  # 日志配置（文件轮转、会话复用）
│   └── version.py            # 版本号
├── tests/                    # 自动化测试（pytest）
│   ├── conftest.py           # 测试 fixtures
│   ├── test_random_order.py  # 随机播放算法测试
│   ├── test_entities.py      # 数据模型测试
│   ├── test_stores.py        # 持久化存储测试
│   ├── test_stats_service.py # 播放统计服务测试
│   └── test_control_server.py# 控制协议 dispatch 测试
├── data/                     # 运行时数据（JSON 持久化，gitignore）
├── docs/                     # 架构文档、协议文档、格式规范
├── tools/                    # 构建脚本
│   └── export_build.py       # 嵌入式 Python + PySide6 裁剪打包
├── .agents/skills/           # Skill 定义文件
│   ├── _index.md             # Skill 索引（入口）
│   └── *.md                  # 各 Skill 定义
├── .github/workflows/        # CI/CD
│   └── ci.yml                # lint + 类型检查 + 测试
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
# 自动化测试（pytest）
pytest tests/ -v

# 运行时控制接口验证（启动应用后）
python -c "import socket,json;s=socket.socket();s.connect(('127.0.0.1',43121));s.sendall(json.dumps({'cmd':'ping'}).encode()+b'\n');print(s.recv(1024).decode())"
```

- **单元测试**：`tests/` 目录下覆盖数据模型、持久化存储、随机播放算法、播放统计、控制协议分发
- **运行时验证**：启动应用后通过 TCP 控制接口发送 `ping` 命令验证服务可用性
- **CI**：`.github/workflows/ci.yml` 自动执行 ruff 检查 + pytest

## 功能变更检查清单

见 `.trae/rules/project_rules.md`。
