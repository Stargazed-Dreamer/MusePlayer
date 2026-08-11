---
name: player-control
description: >
  通过 TCP JSON Lines 协议控制 MusePlayer 播放器。
  触发词：播放、暂停、下一首、上一首、切歌、调音量、跳转、播放模式、控制播放器、
  play、pause、next、previous、volume、seek、mode
---

# 播放器控制 (player-control)

## 触发词
播放、暂停、下一首、上一首、切歌、调音量、跳转、播放模式、控制播放器

## 概述
通过 MusePlayer 的 TCP JSON Lines 运行时控制协议，远程控制播放器的播放、暂停、切歌、音量、跳转、模式切换等操作。

## 前置条件
- MusePlayer 实例正在运行
- 运行时控制接口已启用（设置 → 网络控制 → 启用控制接口）
- 知道控制接口地址（默认 `127.0.0.1:43121`）

## 工作流

### 1. 确认连接

向播放器发送 `ping` 命令确认连接可用：

```python
import socket, json


def send_command(cmd: dict, host="127.0.0.1", port=43121) -> dict:
    """发送命令到 MusePlayer 控制接口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((host, port))
    s.sendall((json.dumps(cmd, ensure_ascii=False) + "\n").encode("utf-8"))
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.split(b"\n")[0].decode("utf-8"))
```

如果 `ping` 返回 `{"ok": true, "result": "pong"}`，则连接正常。

### 2. 查询当前状态

发送 `state` 命令获取完整状态快照：

```python
result = send_command({"cmd": "state"})
# result["result"]["player"] — 播放器状态
# result["result"]["playlists"] — 歌单列表
```

### 3. 执行控制命令

根据用户意图选择对应命令：

| 意图 | 命令 | 参数 |
|------|------|------|
| 播放 | `play` | 无 |
| 暂停 | `pause` | 无 |
| 播放/暂停切换 | `toggle` | 无 |
| 下一首 | `next` | 无 |
| 上一首 | `previous` | 无 |
| 跳转到指定位置 | `seek` | `position_sec`（秒，float） |
| 设置音量 | `set_volume` | `volume`（0.0~1.0，float） |
| 设置播放模式 | `set_mode` | `mode`（`single_loop`/`playlist_loop`/`random`） |
| 播放指定文件 | `play_file` | `path`（文件绝对路径） |
| 播放指定曲目 | `play_track` | `track_id`（曲目 ID） |
| 查询当前曲目 | `current_track` | 无 |
| 查询当前歌单 | `current_playlist` | 无 |

### 4. 报告结果

将命令执行结果以自然语言反馈给用户。如果 `ok` 为 `false`，报告 `error` 字段内容。

## 关键规则

- **音量范围是 0.0~1.0**，绝不传 0~100 的值
- **seek 的 position_sec 单位是秒**，绝不传毫秒或百分比
- **每个 TCP 连接只发一条命令**，发完即关闭；不要复用连接发多条命令
- **命令发送后必须读取响应**，确认 `ok` 为 `true`；如果为 `false`，向用户报告错误
- **绝不连续快速发送多个控制命令**（如连续切歌），每次操作后等待响应再执行下一个
- **play_file 使用文件绝对路径**，play_track 使用 track_id，两者不可混用

## 依赖

| 依赖 | 路径/接口 | 说明 |
|------|----------|------|
| MusePlayer 实例 | TCP 127.0.0.1:43121 | 运行时控制协议 |
| Python socket + json | 标准库 | TCP 通信 |

## 输入/输出
- 输入：用户的自然语言控制指令
- 输出：播放器状态反馈（自然语言 + JSON 数据）
