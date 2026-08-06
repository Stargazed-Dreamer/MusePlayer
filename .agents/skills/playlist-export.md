---
name: playlist-export
description: >
  将 MusePlayer 的歌单导出为 musearc_playlist_export_v2 格式的 .muse_playlist.json 文件，
  包含曲目元数据、播放统计和多语言歌词。
  触发词：导出歌单、歌单导出、备份歌单、导出播放列表
---

# 歌单导出 (playlist-export)

## 触发词
导出歌单、歌单导出、备份歌单、导出播放列表

## 概述
将 MusePlayer 的指定歌单（包括"全部歌曲"、"我喜欢"或任意用户自建歌单）导出为 `musearc_playlist_export_v2` 格式的 JSON 文件，包含曲目元数据、播放统计、歌词文件信息，便于跨库迁移或备份。

## 前置条件
- MusePlayer 实例正在运行
- 目标歌单存在且包含至少 1 首仍在本库中的曲目
- 用户已确认导出路径

## 工作流

### 1. 确认歌单

向用户确认要导出哪个歌单。可通过 TCP 控制协议查询现有歌单列表：

```python
result = send_command({"cmd": "state"})
playlists = result["result"]["playlists"]
# 每个 playlist: {"id": "all_songs"|"pl_xxx", "name": "...", "count": N}
```

如需查看歌单详情（含曲目与 `source_sha256`），调用 `get_playlist`：

```python
result = send_command({"cmd": "get_playlist", "playlist_id": "pl_xxx"})
# result["result"]["tracks"] — 曲目列表摘要
```

注意：`all_songs`（全部歌曲）和 `favorites`（我喜欢）也可导出，UI 对话框会默认选中当前活跃歌单。

### 2. 确认导出路径

向用户确认输出目录。导出文件名由 `LibraryService` 自动生成为：

```
{safe_name}_{playlist_hash[:10]}.muse_playlist.json
```

其中 `safe_name` 是清理后的歌单名，`playlist_hash` 是歌单的导出哈希（见下文"关键规则"）。用户只需指定目录，无需指定完整文件名。

### 3. 执行导出

**方式 A：通过 UI 触发（推荐）**

引导用户：
1. 主窗口打开 "歌单 → 管理歌单" 对话框（`playlist_dialog.py`）
2. 在左侧列表选中要导出的歌单
3. 点击底部 "导出选中歌单" 按钮
4. 在弹出的目录选择对话框中选择输出目录

此方式调用 `app_controller.export_playlist` → `library_service.export_playlist_file`，自动完成全部字段组装。

**方式 B：直接调用 LibraryService（脚本/调试场景）**

```python
from app.services.library_service import LibraryService
from pathlib import Path

library = LibraryService(Path("data/library.json"))  # 实际通过 AppController 获取已初始化实例
file_path = library.export_playlist_file(
    playlist_id="pl_xxx",
    out_dir=Path("D:/exports"),
    playback_stats_service=stats_svc,  # 必须传入，用于回填 stats
)
```

**注意：当前 TCP 控制协议（`dispatch_command`）不提供 `export_playlist` 命令**，不能通过 `127.0.0.1:43121` 触发歌单导出。只能走 UI 或直接调用 Service。

### 4. 验证格式

导出文件必须符合 `musearc_playlist_export_v2` schema（参考 `docs/playlist_export_format_cn.md`）：

**顶层字段**：

| 字段 | 必填 | 说明 |
|------|------|------|
| `schema` | 是 | 固定 `"musearc_playlist_export_v2"` |
| `playlist_hash` | 是 | 歌单导出哈希（SHA1 of `"playlist:{id}"`，由 `_get_or_create_export_hash` 生成） |
| `playlist_name` | 是 | 歌单名称 |
| `ordered` | 是 | 布尔，曲目顺序是否有意义（默认 `true`） |
| `exported_at` | 是 | UTC ISO8601 时间 |
| `database_location` | 是 | 导出时数据库根目录绝对路径 |
| `track_count` | 是 | 曲目数量 |
| `stats_summary` | 是 | 统计汇总（含 `total_*` 字段与 `updated_at`） |
| `tracks` | 是 | 曲目数组 |

**tracks[i] 字段**：

| 字段 | 必填 | 说明 |
|------|------|------|
| `track_id` | 是 | 优先使用 `source_track_id`，否则用内部 ID |
| `storage_relpath` | 是 | 音频文件相对 `database_location` 的路径 |
| `source_sha256` | 推荐 | 音频源 SHA256，跨库匹配最高优先级 |
| `title` / `artist` / `album` | 是 | 曲目元数据 |
| `lyrics` | 是 | 歌词文件数组（见下文） |
| `lyrics_storage_relpath` | 是 | 主歌词相对路径，向后兼容，取 `lyrics[0].relpath` |
| `stats` | 是 | 播放统计对象（含 7 个字段） |

### 5. 验证歌词字段

`lyrics` 是数组，每个元素含：

- `relpath`：歌词文件相对 `database_location` 的路径
- `lang`：歌词语言，取值 `original` / `japanese` / `romaji` / `translation`

支持的歌词文件格式：
- **LRC**（`.lrc`）：标准时间标签 `[mm:ss.xx]歌词文本`
- **QRC**（`.qrc` / `.qrc.txt`）：QQ音乐增强格式，支持字级时间标签与 `[kana:...]` 假名注音

多歌词合并显示规则（导出时不合并，导入后由播放器按时间戳合并）：
- 第一行：假名注音（QRC `[kana:...]`）
- 第二行：日语原文
- 第三行：罗马音
- 第四行：中文翻译

## 关键规则

- **`playlist_hash` 与统计导出的 hash 不同**：歌单导出使用 `_get_or_create_export_hash`，基于 `"playlist:{playlist.id}"` 的 SHA1；统计导出使用基于 tracks 内容的 SHA256 前 16 位。两者不可混用
- **`playlist_hash` 首次导出会持久化**：`_get_or_create_export_hash` 会将生成的 hash 写回 `playlist.source_playlist_hash` 并调用 `save()`，确保同一歌单多次导出产生相同 hash（用于去重覆盖）
- **`export_playlist_file` 必须传入 `playback_stats_service`**：否则无法回填每首曲目的 `stats` 字段。脚本调用时切勿传 `None`
- **空歌单不可导出**：`track_ids` 过滤后为空时抛出 `ValueError("歌单没有可导出的歌曲")`，操作前应通过 `get_playlist` 检查 `count`
- **`track_id` 优先使用 `source_track_id`**：导入时保留的原始 ID 优先，否则用内部 ID。注意与统计导出不同，歌单导出的 `track_id` **不加** `trk_` 前缀（前缀由 MuseArc 端处理）
- **匹配优先级**：`source_sha256` > `track_id` > `storage_relpath`，与统计导出一致
- **`lyrics_storage_relpath` 必须等于 `lyrics[0].relpath`**：这是 v1→v2 向后兼容字段，不能随意填写
- **v1 → v2 兼容性**：v1 文件（`musearc_playlist_export_v1`）可被导入，但不会包含 `ordered` / `lyrics` 字段；v2 是当前导出格式，新增了 `ordered`、`lyrics` 数组、歌词 `lang` 区分、QRC 假名注音支持
- **当前 TCP 控制协议不支持导出命令**：`dispatch_command` 中没有 `export_playlist`，只能走 UI 或直接调用 Service

## 喜爱程度计算公式

MuseArc 导入后会基于导出的 `stats` 计算"喜爱程度"标签（-100~100 整数），当前 v2 实现公式：

```
a = play_count
b = manual_play_count
c = play_seconds
d = early_skip_count
g = complete_play_count
e = 全库总播放次数（所有歌曲 a 求和）
f = 歌曲长度秒数

t1 = c / f / a        (平均每次播放完整度)
t2 = b / a            (主动播放占比)
t3 = a / e            (相对播放频次)
t4 = d / a            (早期跳过占比)
t5 = g / a            (完播占比)

t = 0.1*t3 + 0.3*t1 + 0.4*t2 + 0.2*t5 - t4
喜爱程度 = clamp(round(t * 100), -100, 100)
```

注意：统计导出格式规范（`docs/musearc_stats_import_format_spec.md`）中记录的是旧版公式（无 `t5` 项），与歌单导出格式的当前实现（`docs/playlist_export_format_cn.md`）略有差异。以 `playlist_export_format_cn.md` 为准。

## 依赖

| 依赖 | 路径/接口 | 说明 |
|------|----------|------|
| MusePlayer 实例 | UI "歌单 → 管理歌单 → 导出选中歌单" | 推荐入口 |
| LibraryService | `app/services/library_service.py::export_playlist_file` | 直接调用入口 |
| PlaybackStatsService | `app/services/playback_stats_service.py` | 必须传入以回填 stats |
| 格式规范 | `docs/playlist_export_format_cn.md` | v2 schema 权威来源 |
| player-control Skill | `.agents/skills/player-control.md` | 共用 TCP 通信函数 |

## 输入/输出
- 输入：歌单 ID、输出目录
- 输出：`{safe_name}_{playlist_hash[:10]}.muse_playlist.json` 文件，符合 `musearc_playlist_export_v2` schema
