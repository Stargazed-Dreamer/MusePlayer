
<p align="center">
<p align="center"><img width="267" height="267" alt="图标改" src="https://github.com/user-attachments/assets/f23a8f9d-e805-4c1d-9546-18bac762d6d8" />
</p>

<h1 align="center">MusePlayer</h1>



  基于 PySide6 + PyAV 的本地音乐播放器桌面应用<br/>
  内置 <b>TCP JSON Lines 远程控制协议</b>，可被外部程序、脚本、自动化系统、AI Agent 完全控制
</p>

<p align="center">
  <img alt="License" src="https://img.shields.io/badge/license-GPL--3.0-blue.svg" />
  <img alt="Python" src="https://img.shields.io/badge/python-3.12%2B-blue.svg" />
  <img alt="PySide6" src="https://img.shields.io/badge/PySide6-6.7%2B-green.svg" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg" />
</p>

---

<img width="1361" height="1018" alt="museplayer主黑" src="https://github.com/user-attachments/assets/b8f0ff4a-8817-4f5d-ad02-0ad5e53a56cf" />
<img width="1361" height="1018" alt="museplayer主白" src="https://github.com/user-attachments/assets/5ff00b3c-baba-493d-a597-da49177d2fb5" />
<img width="865" height="216" alt="museplayer小黑" src="https://github.com/user-attachments/assets/db511771-c072-45e5-b58a-51b255b633d7" />

MusePlayer 使用 PyAV 作为底层音频解码内核，sounddevice 作为音频输出后端，通过四层分离架构（Domain / Service / Runtime / UI）实现高内聚低耦合。除常规播放器功能外，MusePlayer 的核心特色是**完整的运行时控制协议**——播放器的几乎所有能力都可通过 TCP 接口由外部程序驱动，适合有自动化、集成、二次开发需求的用户。

## 特色功能

### 远程控制协议

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

### 双解码模式

提供两种解码策略，均实现"边读边播"以避免起播卡顿：

- **窗口解码（默认）**：以 6.2 秒为一块按需解码，后台预读取下一窗口实现无缝衔接。本质是分块边读边播，内存占用低，适合长曲目。
- **完整读取**：顺序流式解码整文件到内存，首块就绪即起播；解码完成后转为纯内存模式，可任意位置瞬时拖动。读取未完成时拖动进度条，则从目标位置重新开始流式解码。

### 6 维度播放统计

记录 6 个维度的统计：累计播放次数、主动播放计数、早期跳过计数（early_skip）、累计播放秒数、累计播放百分比、峰值会话时长。支持统计数据的导入与导出，便于跨设备同步与外部数据分析。

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

1. 从本仓库 [Releases](./releases) 页面下载
2. 解压到任意目录
3. 运行 `start.bat`（内部调用便携版 `.\python\python.exe main.py`）。您可以为此文件创建快捷方式并绑定icon文件。

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

1. 启动应用后，在歌单界面点击"导入"按钮或拖拽音频文件到窗口导入音乐
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

## 贡献

欢迎Pr和Issue！

## 许可证

[GNU General Public License v3.0](LICENSE)
