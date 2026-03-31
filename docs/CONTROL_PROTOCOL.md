# Runtime Control Protocol

MusePlayer exposes a runtime control endpoint on `127.0.0.1:43121` by default.

## Transport
- TCP
- JSON Lines (one JSON object per line)
- UTF-8

Each response is one JSON line:
- success: `{"ok": true, "result": ...}`
- failure: `{"ok": false, "error": "..."}`

## Commands
- `{"cmd":"ping"}`
- `{"cmd":"state"}`
- `{"cmd":"play"}`
- `{"cmd":"pause"}`
- `{"cmd":"toggle"}`
- `{"cmd":"seek","position_sec":123.4}`
- `{"cmd":"set_volume","volume":0.65}`
- `{"cmd":"set_mode","mode":"single_loop|playlist_loop|random"}`（`playlist_loop` 需在设置中启用）
- `{"cmd":"next"}`
- `{"cmd":"previous"}`
- `{"cmd":"import_folder","path":"D:/Music","playlist_id":"optional"}`（不传 `playlist_id` 时默认按文件夹名创建/复用歌单）
- `{"cmd":"import_playlist_file","path":"D:/playlist/foo.muse_playlist.json"}`
- `{"cmd":"import_playlist_data","playlist":{...},"source_hint":"optional"}`（`playlist` 也可用 `data` 或 `content` 字段）
- `{"cmd":"play_file","path":"D:/Music/song.mp3"}`
- `{"cmd":"load_playlist","playlist_id":"..."}`
- `{"cmd":"play_playlist","playlist_id":"...","track_id":"optional"}`
- `{"cmd":"play_track","track_id":"..."}`
- `{"cmd":"create_playlist","name":"My List"}`

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
