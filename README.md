# MusePlayer

<!-- screenshot: 主界面日间主题 -->
<!-- screenshot: 主界面夜间主题 -->

> 基于 PySide6 + PyAV 的本地音乐播放器桌面应用，内置 **TCP JSON Lines 远程控制协议**，
> 可被外部程序、脚本、自动化系统、AI Agent 完全控制。

MusePlayer 使用 PyAV 作为底层音频解码内核，sounddevice 作为音频输出后端，通过四层分离架构（Domain / Service / Runtime / UI）实现高内聚低耦合。除常规播放器功能外，MusePlayer 的核心特色是**完整的运行时控制协议**——播放器的几乎所有能力都可通过 TCP 接口由外部程序驱动，适合有自动化、集成、二次开发需求的用户。

## 特色功能

### 远程控制协议（核心）

MusePlayer 在本地 TCP 端口（默认 `127.0.0.1:43121`，可配置）暴露 JSON Lines 控制端点，**程序、脚本、自动化系统、AI Agent 都可完全控制播放器**。

- **25+ 命令**：播放控制（play/pause/seek/volume/mode/next/previous）、曲目与歌单管理（play_file/play_track/create_playlist/add_track/remove_track）、导入（import_folder/import_playlist_file/import_playlist_data）、状态查询（state/current_track/current_playlist/get_playlist）
- **统一响应格式**：`{"ok": true, "result": ...}` / `{"ok": false, "error": "..."}`
- **传输协议**：TCP + JSON Lines（每行一个 JSON 对象），UTF-8
- **典型场景**：外部脚本驱动播放、歌单数据库双向同步、自动化测试、AI Agent 接入

快速验证（发送 `ping`）：

```bash
python -c "import socket,json;s=socket.socket();s.connect(('127.0.0.1',43121));s.sendall(json.dumps({'cmd':'ping'}).encode()+b'\n');print(s.recv(1024).decode())"
```

完整命令列表与示例见 [docs/CONTROL_PROTOCOL.md](docs/CONTROL_PROTOCOL.md)。

### 完整会话恢复

关闭时持久化当前歌单、曲目、播放位置、音量、播放模式、随机种子；启动时**两阶段恢复**——先快速恢复当前曲目使其立即可播，后台再加载完整曲库，**大大优化启动速度**。重启后能无缝回到上次播放的位置与上下文。

### SHA256 种子化确定性随机播放

基于 SHA256 种子的确定性乱序算法（[app/services/random_order.py](app/services/random_order.py)），相同种子下顺序可复现。重启不丢随机上下文，便于会话恢复与外部程序对齐播放顺序。

### 懒加载窗口解码

对前 6.2 秒按需解码的窗口读取策略，超过阈值后自动提升为完整读取，并在后台预读取后续数据。兼顾首响速度与续播平滑，避免长曲目的启动延迟。

### PyAV 跨解码器统一内核

使用 PyAV 作为底层解码内核，将不同格式（FLAC/MP3/M4A/WAV/OGG/OPUS 等）统一为 48kHz float32 PCM 输出。线程安全内核设计，UI 层通过 `PlayerService` 间接调用，绝不直接操作底层。

### 6 维度播放统计

不只是简单的播放计数，而是记录 6 个维度的统计：累计播放次数、主动播放计数、早期跳过计数（early_skip）、累计播放秒数、累计播放百分比、峰值会话时长。支持统计数据的导入与导出，便于跨设备同步与外部数据分析。

### QRC 增强歌词

支持 QRC 格式歌词的逐词时间戳、日语假名注音（romaaji/kana）、多语言歌词合并显示。歌词文件可通过拖拽关联到曲目。

### Muse 歌单导出格式

自定义的 `.muse_playlist.json` 格式，包含完整曲目信息与元数据，支持统计回写。跨设备可移植——导出后在另一台机器导入即可还原完整歌单结构与统计。

### 体验亮点

- **全局增益增强**：0.5-5.0x 增益调节，音量可超 100%，适合低音量录音
- **音频输出设备热切换**：在设置中切换输出设备，无需重启应用
- **多提示状态栏**：并发的状态消息不互相覆盖，各消息独立计时消失
- **Windows 全局快捷键**：可自定义的媒体键全局注册，在任何应用前都能控制播放

## 安装

### Windows

**便携包（推荐，解压即用，无需安装 Python）**：

1. 从 [Releases](https://github.com/Stargazed-Dreamer/MusePlayer/releases) 下载 `MusePlayer_v1.0.0_windows.zip`
2. 解压到任意目录
3. 运行 `start.bat`（内部调用便携版 `.\python\python.exe main.py`）

便携包使用嵌入式 Python + 裁剪后的 PySide6 构建，体积小、自包含、版本可控。构建原理见下方[构建便携包](#构建便携包)章节。

**自构建（从源码运行）**：

```bash
# 需 Python 3.12+
git clone https://github.com/Stargazed-Dreamer/MusePlayer.git
cd MusePlayer
pip install -r requirements.txt
python main.py
# 或双击 start.bat
```

### Linux

需 Python 3.12+ 与 PortAudio 系统库：

```bash
# 安装系统依赖（PortAudio）
sudo apt-get install -y libportaudio2        # Debian/Ubuntu
# sudo dnf install -y portaudio              # Fedora
# sudo pacman -S portaudio                   # Arch

# 获取源码并运行
git clone https://github.com/Stargazed-Dreamer/MusePlayer.git
cd MusePlayer
pip install -r requirements.txt
python main.py
# 或 ./start.sh
```

> 注：Linux 下 Windows 专属功能（任务栏进度、全局快捷键）自动降级，核心播放功能正常。

### macOS

需 Python 3.12+：

```bash
git clone https://github.com/Stargazed-Dreamer/MusePlayer.git
cd MusePlayer
pip install -r requirements.txt
python main.py
# 或 ./start.sh
```

> 注：macOS 下 Windows 专属功能自动降级，核心播放功能正常。

## 快速使用

1. 启动应用后，点击"导入"按钮或拖拽音频文件到窗口导入音乐
2. 双击曲目开始播放
3. 在设置中可调整：TCP 控制端口、播放模式、主题、快捷键等
4. 远程控制示例见下方

**Python 控制示例**：

```python
import json, socket

with socket.create_connection(("127.0.0.1", 43121), timeout=5) as s:
    s.sendall((json.dumps({"cmd": "state"}) + "\n").encode("utf-8"))
    print(json.loads(s.recv(4096).decode("utf-8")))
```

**PowerShell 控制示例**：

```powershell
$client = New-Object System.Net.Sockets.TcpClient('127.0.0.1', 43121)
$writer = New-Object System.IO.StreamWriter($client.GetStream())
$writer.WriteLine('{"cmd":"state"}')
$writer.Flush()
$reader = New-Object System.IO.StreamReader($client.GetStream())
$reader.ReadLine()
```

## 远程控制协议

MusePlayer 的核心特色是运行时控制协议。所有播放器能力都可通过 TCP 接口驱动：

| 类别 | 命令 |
|------|------|
| 播放控制 | `ping` `state` `play` `pause` `toggle` `seek` `set_volume` `set_mode` `next` `previous` |
| 曲目与歌单 | `play_file` `play_track` `load_playlist` `play_playlist` `current_track` `current_playlist` `get_playlist` `create_playlist` `add_track_to_playlist` `remove_track_from_playlist` |
| 导入 | `import_folder` `import_playlist_file` `import_playlist_data` |

- **传输**：TCP + JSON Lines（每行一个 JSON 对象），UTF-8
- **响应**：统一 `{"ok": true, "result": ...}` / `{"ok": false, "error": "..."}`
- **默认端口**：`127.0.0.1:43121`，可在设置中修改

完整命令详情、参数说明、响应格式与多语言示例见 [docs/CONTROL_PROTOCOL.md](docs/CONTROL_PROTOCOL.md)。

## 配置

主要配置项（在应用内设置对话框中调整，持久化到 `data/settings.json`）：

- **TCP 控制端口**：默认 43121
- **播放模式**：单曲循环 / 歌单循环 / 随机（可独立启用/禁用）
- **全局增益**：0.5-5.0x
- **播放速率**：可调
- **主题**：日间 / 夜间
- **定时自动保存**：可配置间隔
- **Windows 全局快捷键**：可自定义

## 构建便携包

便携包（仅 Windows）使用 [tools/export_build.py](tools/export_build.py) 构建，原理：

1. 下载 Windows 嵌入式 Python（`python-3.x.x-embed-amd64.zip`）
2. 安装 pip + 装入项目依赖
3. 裁剪 PySide6 未用模块（QtWebEngine、Qt3D 等），减小体积
4. 复制项目代码，生成 `start.bat` / `start_debug.bat` 启动器
5. 产出便携式运行时包，解压即用

**本地构建**：

```bash
# 需 Python 3.12+（用于运行构建脚本）
python tools/export_build.py
# 产出在 .build/portable_runtime_v1.0.0/
```

**CI 自动构建**：打 `v*` tag 时 GitHub Actions 自动构建便携包并上传到 Release。也可在 Actions 页面手动触发（workflow_dispatch）。

> 注：便携包构建仅支持 Windows 主机（嵌入式 Python 是 Windows 专有发行格式）。Linux/macOS 用户请使用 pip install 方式。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言/框架 | Python 3.12、PySide6 6.7+ |
| 音频解码 | PyAV >= 12.0 |
| 音频输出 | sounddevice >= 0.4.6 |
| 数值计算 | numpy >= 1.26 |
| 元数据 | mutagen >= 1.47 |
| Windows 集成 | comtypes >= 1.4（仅 Windows，环境标记 `sys_platform == 'win32'`） |

完整依赖见 [requirements.txt](requirements.txt)。

## 开发

```bash
# 运行测试
pytest tests/ -v

# 代码检查
ruff check .

# 格式化检查
ruff format --check .

# 类型检查
mypy app/ core/
```

开发规范、提交流程、项目结构与关键约束见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [AGENTS.md](AGENTS.md)。

## 架构文档

- [架构设计文档（中文）](docs/architecture_design_cn.md)
- [架构设计文档（英文）](docs/ARCHITECTURE.md)
- [运行时控制协议](docs/CONTROL_PROTOCOL.md)
- [歌单导出格式](docs/playlist_export_format_cn.md)
- [统计数据导入格式](docs/musearc_stats_import_format_spec.md)
- [发布检查清单](docs/release_checklist.md)

## 贡献

欢迎参与贡献，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT License](LICENSE) © 2025-2026 MusePlayer Contributors
