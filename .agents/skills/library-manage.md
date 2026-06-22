---
name: library-manage
description: >
  管理 MusePlayer 的曲库和歌单，包括导入文件夹、导入歌单文件、新建/删除歌单、
  添加/移除曲目等操作。
  触发词：导入、导入文件夹、导入歌单、新建歌单、删除歌单、曲库管理、歌单管理、
  添加曲目、移除曲目、导出歌单
---

# 曲库与歌单管理 (library-manage)

## 触发词
导入、导入文件夹、导入歌单、新建歌单、删除歌单、曲库管理、歌单管理、添加曲目、移除曲目、导出歌单

## 概述
通过 MusePlayer 的 TCP JSON Lines 运行时控制协议，管理曲库的导入、歌单的增删改查、曲目的添加移除等操作。

## 前置条件
- MusePlayer 实例正在运行
- 运行时控制接口已启用
- 知道控制接口地址（默认 `127.0.0.1:43121`）

## 工作流

### 1. 确认连接

同 `player-control` Skill，发送 `ping` 确认连接。

### 2. 查询现有歌单

发送 `state` 命令获取歌单列表，了解当前曲库状态：

```python
result = send_command({"cmd": "state"})
playlists = result["result"]["playlists"]
# 每个 playlist: {"id": "...", "name": "...", "count": N}
```

### 3. 执行管理命令

根据用户意图选择对应命令：

| 意图 | 命令 | 参数 | 说明 |
|------|------|------|------|
| 导入文件夹 | `import_folder` | `path`（文件夹路径），可选 `playlist_id` | 递归扫描导入音频文件 |
| 导入歌单文件 | `import_playlist_file` | `path`（.muse_playlist.json 文件路径） | 导入导出的歌单文件 |
| 导入歌单数据 | `import_playlist_data` | `playlist`/`data`/`content`（JSON 对象或字符串） | 通过数据导入歌单 |
| 新建歌单 | `create_playlist` | `name`（歌单名称） | 返回新歌单 ID |
| 加载歌单 | `load_playlist` | `playlist_id` | 切换到指定歌单 |
| 播放歌单 | `play_playlist` | `playlist_id`，可选 `track_id` | 切换并播放指定歌单 |
| 查询歌单详情 | `get_playlist` | `playlist_id` | 返回歌单及曲目信息 |
| 添加曲目到歌单 | `add_track_to_playlist` | `track_id`、`playlist_id` | 将曲目添加到歌单 |
| 从歌单移除曲目 | `remove_track_from_playlist` | `track_id`、`playlist_id` | 移除曲目（可能全局删除） |

### 4. 报告结果

将操作结果反馈给用户。导入操作报告导入数量，歌单操作报告歌单 ID，错误时报告 `error` 字段。

## 关键规则

- **import_folder 的路径必须是绝对路径**，相对路径会失败
- **remove_track_from_playlist 可能导致曲目全局删除**：如果曲目只属于一个歌单，移除后统计数据也会被清理，操作前必须确认用户意图
- **create_playlist 的 name 默认是"新建歌单"**：如果用户没指定名称，使用此默认值
- **import_playlist_data 支持三种字段名**：`playlist`、`data`、`content`，任选其一
- **每个 TCP 连接只发一条命令**，发完即关闭
- **导入大量文件时操作较慢**（递归扫描 + 元数据读取），必须告知用户等待

## 依赖

| 依赖 | 路径/接口 | 说明 |
|------|----------|------|
| MusePlayer 实例 | TCP 127.0.0.1:43121 | 运行时控制协议 |
| player-control Skill | `.agents/skills/player-control.md` | 共用 TCP 通信函数 |

## 输入/输出
- 输入：用户的自然语言管理指令
- 输出：操作结果反馈（自然语言 + JSON 数据）
