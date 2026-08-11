# Runtime Control Protocol

> Protocol Version: 1.0 (defined in `app/version.py` `PROTOCOL_VERSION`)

MusePlayer exposes a runtime control endpoint on `127.0.0.1:43121` by default.

## Transport
- TCP
- JSON Lines (one JSON object per line)
- UTF-8

Each response is one JSON line:
- success: `{"ok": true, "result": ...}`
- failure: `{"ok": false, "error": "..."}`

## Commands

### 播放控制
- `{"cmd":"ping"}`
- `{"cmd":"state"}` — 返回当前播放器状态（见下方 `state` 响应格式）
- `{"cmd":"play"}`
- `{"cmd":"pause"}`
- `{"cmd":"toggle"}`
- `{"cmd":"seek","position_sec":123.4}` — `position_sec` 也可用 `position` 字段名
- `{"cmd":"set_volume","volume":0.65}` — 音量范围 0.0~1.0
- `{"cmd":"set_mode","mode":"single_loop|playlist_loop|random"}` — 模式别名：`repeat_one`/`single`/`one`/`loop` → `single_loop`；`list_loop`/`sequential` → `playlist_loop`；`shuffle` → `random`。无法识别的模式静默回退为 `single_loop`。`playlist_loop` 需在设置中启用。
- `{"cmd":"next"}`
- `{"cmd":"previous"}`

### 曲目与歌单
- `{"cmd":"play_file","path":"D:/Music/song.mp3"}`
- `{"cmd":"play_track","track_id":"..."}`
- `{"cmd":"load_playlist","playlist_id":"..."}`
- `{"cmd":"play_playlist","playlist_id":"...","track_id":"optional"}`
- `{"cmd":"current_track"}` — 返回当前播放曲目的完整信息（含统计数据和收藏状态），无曲目时返回 `null`
- `{"cmd":"current_playlist"}` — 返回当前播放歌单的完整信息（含曲目列表摘要），无歌单时返回 `null`
- `{"cmd":"get_playlist","playlist_id":"..."}` — 返回指定歌单的完整信息（含曲目列表），不切换当前播放歌单。曲目信息包含 `source_sha256`（如有），便于外部程序通过哈希匹配歌曲。歌单不存在时返回 `null`
- `{"cmd":"create_playlist","name":"新建歌单"}`
- `{"cmd":"add_track_to_playlist","track_id":"...","playlist_id":"..."}` — 将指定曲目添加到指定歌单
- `{"cmd":"remove_track_from_playlist","track_id":"...","playlist_id":"..."}` — 从指定歌单移除曲目。引用检查规则：从"全部歌曲"移除时，从所有歌单中移除；从分支歌单移除时，若该曲目不再被任何歌单引用，则自动从"全部歌曲"中移除。响应中 `removed_globally` 列出被全局移除的曲目 ID

### 导入
- `{"cmd":"import_folder","path":"D:/Music","playlist_id":"optional"}` — 不传 `playlist_id` 时默认按文件夹名创建/复用歌单
- `{"cmd":"import_playlist_file","path":"D:/playlist/foo.muse_playlist.json"}`
- `{"cmd":"import_playlist_data","playlist":{...},"source_hint":"optional"}` — `playlist` 也可用 `data` 或 `content` 字段

## `state` 响应格式

```json
{
  "ok": true,
  "result": {
    "player": {
      "playlist_id": "all_songs",
      "track_id": "trk_abc123",
      "track_title": "歌曲标题",
      "position_sec": 45.2,
      "duration_sec": 240.0,
      "playing": true,
      "mode": "single_loop",
      "volume": 0.65,
      "random_seed": 42,
      "random_index": 3,
      "playback_rate": 1.0
    },
    "playlists": [
      {"id": "all_songs", "name": "全部歌曲", "count": 1234},
      {"id": "pl_xxx", "name": "我喜欢", "count": 56}
    ]
  }
}
```

`player` 各字段：
| 字段 | 类型 | 说明 |
|------|------|------|
| `playlist_id` | string or null | 当前播放列表 ID |
| `track_id` | string or null | 当前曲目 ID |
| `track_title` | string | 当前曲目标题 |
| `position_sec` | float | 当前播放位置（秒） |
| `duration_sec` | float | 曲目总时长（秒） |
| `playing` | bool | 是否正在播放 |
| `mode` | string | 播放模式：`single_loop` / `playlist_loop` / `random` |
| `volume` | float | 音量（0.0~1.0） |
| `random_seed` | int | 随机播放种子 |
| `random_index` | int | 随机顺序索引 |
| `playback_rate` | float | 播放速率 |

`playlists` 是歌单摘要列表，每个元素含 `id`、`name`、`count`。

## Python Example
```python
import json
import socket

HOST, PORT = "127.0.0.1", 43121

with socket.create_connection((HOST, PORT), timeout=5) as s:
    payload = {"cmd": "state"}
    s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    line = b""
    while not line.endswith(b"\n"):
        line += s.recv(4096)
    print(json.loads(line.decode("utf-8")))
```

## PowerShell Example
```powershell
$client = New-Object System.Net.Sockets.TcpClient('127.0.0.1', 43121)
$stream = $client.GetStream()
$writer = New-Object System.IO.StreamWriter($stream)
$reader = New-Object System.IO.StreamReader($stream)

$writer.AutoFlush = $true
$writer.WriteLine('{"cmd":"state"}')
$response = $reader.ReadLine()
$response

$reader.Close()
$writer.Close()
$client.Close()
```
