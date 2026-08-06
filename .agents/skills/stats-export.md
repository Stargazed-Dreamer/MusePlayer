---
name: stats-export
description: >
  将 MusePlayer 的播放统计数据导出为 MuseArc 兼容的 .muse_stats.json 文件。
  触发词：导出统计、播放统计、统计导出、导出播放记录、muse_stats
---

# 播放统计导出 (stats-export)

## 触发词
导出统计、播放统计、统计导出、导出播放记录、muse_stats

## 概述
将 MusePlayer 内部累积的播放统计（播放次数、主动播放次数、完播次数、播放秒数、早期跳过次数等）导出为 MuseArc 兼容的 `musearc_playlist_export_v1` 格式 JSON 文件，便于在 MuseArc 端导入并计算"喜爱程度"标签。

## 前置条件
- MusePlayer 实例正在运行
- 已有播放统计记录（`data/playback_stats.json` 非空）
- 用户已确认导出范围与目标路径

## 工作流

### 1. 确认导出范围

向用户确认导出范围：

- **全量导出**（默认）：导出 `PlaybackStatsService` 中所有曲目的统计。UI 入口（"文件 → 导出统计数据"）即全量导出
- **按歌单导出**：仅导出指定歌单内曲目的统计。需先通过 `state` / `get_playlist` 命令拿到歌单的 `track_id` 列表，再过滤

同时确认目标路径与文件名。默认文件名为 `playback_stats.muse_stats.json`，建议遵循 `{name}.muse_stats.json` 命名规范（如 `rock_stats.muse_stats.json`）。

### 2. 调用导出

**方式 A：通过 UI 触发（推荐）**

引导用户在 MusePlayer 主窗口点击 "文件 → 导出统计数据"，在弹出的文件保存对话框中选择路径并保存。此方式调用 `app/ui/main_window_mixins/playback_mixin.py::_export_stats`，自动完成字段映射与 `playlist_hash` 生成。

**方式 B：直接调用 PlaybackStatsService（脚本/调试场景）**

当无法通过 UI 操作时，可编写脚本读取 `PlaybackStatsService` 并组装 payload。核心调用：

```python
from app.services.playback_stats_service import PlaybackStatsService
from pathlib import Path

stats_svc = PlaybackStatsService(Path("data"))
stats_svc._load()  # 加载 data/playback_stats.json
for track_id, item in stats_svc._entries.items():
    stats = stats_svc.export_stats_for_track(track_id)  # 返回标准化的 stats dict
```

**注意：当前 TCP 控制协议（`dispatch_command`）不提供 `export_stats` 命令**，不能通过 `127.0.0.1:43121` 触发统计导出。只能走 UI 或直接调用 Service。

### 3. 字段映射

导出时必须严格按以下映射转换字段（参考 `docs/musearc_stats_import_format_spec.md`）：

| MusePlayer 内部字段 | 导出字段 | 转换说明 |
|--------------------|---------|---------|
| `play_count` | `stats.play_count` | 直接映射 |
| `active_play_count` | `stats.manual_play_count` | **语义对应：主动播放次数**，不是 `manual_play_count` |
| `complete_play_count` | `stats.complete_play_count` | 直接映射 |
| `played_seconds_total` | `stats.play_seconds` | **浮点→整数**，`int(round(...))` |
| `early_skip_count` | `stats.early_skip_count` | 直接映射 |
| `peak_session_play_count` | `stats.peak_session_play_count` | 直接映射 |
| `peak_session_play_at` | `stats.peak_session_play_at` | 直接映射 |
| `played_percent_total` | *(无对应)* | MuseArc 不使用，忽略 |
| `updated_at` | *(无对应)* | MuseArc 不使用，忽略 |

每个 `tracks[i]` 元素还需附带：
- `track_id`：**必须加 `trk_` 前缀**（MusePlayer 内部 ID 若不以 `trk_` 开头需补齐）
- `source_sha256`：强烈推荐提供，跨库跨路径匹配成功率最高
- `storage_relpath`：推荐提供，作为第三匹配兜底

### 4. 生成 playlist_hash

基于 `tracks` 列表内容生成稳定哈希：

```python
import hashlib, json

content_hash = hashlib.sha256(
    json.dumps(tracks_list, sort_keys=True, ensure_ascii=False).encode("utf-8")
).hexdigest()[:16]
playlist_hash = f"museplayer_stats_{content_hash}"
```

相同数据必须产生相同 `playlist_hash`，确保重复导入时是覆盖而非累加。

### 5. 验证格式

导出后用以下检查清单验证（参考 `docs/musearc_stats_import_format_spec.md` 第 9 节）：

- [ ] 顶层 `schema` 为 `"musearc_playlist_export_v1"`
- [ ] 顶层包含 `playlist_hash`（基于内容生成）
- [ ] `tracks` 是数组，每个元素含 `stats` 嵌套对象
- [ ] `track_id` 加了 `trk_` 前缀
- [ ] 尽量提供 `source_sha256`
- [ ] `play_seconds` 为整数（非浮点）
- [ ] `active_play_count` 已映射为 `manual_play_count`
- [ ] 文件扩展名为 `.muse_stats.json`

## 关键规则

- **统计数据必须通过 `PlaybackStatsService` 获取**：绝不直接读 `data/playback_stats.json` 文件，否则可能读到未落盘的脏数据。Service 内部维护 `_dirty` 标志，未调用 `save_if_dirty()` 前 JSON 文件可能滞后
- **字段映射最高频陷阱**：`active_play_count` → `manual_play_count`（名字不一致），`played_seconds_total`（浮点）→ `play_seconds`（整数），漏改会导致 MuseArc 端统计错误
- **匹配优先级**：`source_sha256` > `track_id` > `storage_relpath`。三个字段至少提供一个，提供越多匹配率越高。导出全量统计时务必带上 `source_sha256`
- **`track_id` 必须加 `trk_` 前缀**：MuseArc 数据库 ID 格式为 `trk_<32位hex>`，纯 UUID 无法匹配
- **`playlist_hash` 决定去重行为**：同一 `playlist_hash` 重复导入会覆盖旧贡献，不同 hash 会按来源累加。生成时必须 `sort_keys=True` 保证稳定性
- **当前 TCP 控制协议不支持导出命令**：`dispatch_command` 中没有 `export_stats`，只能走 UI 或直接调用 Service。若用户要求通过 TCP 触发，需明确告知此限制
- **空统计不可导出**：`PlaybackStatsService._entries` 为空时 UI 会提示"没有统计数据可导出"，脚本场景也应先检查

## 依赖

| 依赖 | 路径/接口 | 说明 |
|------|----------|------|
| MusePlayer 实例 | UI "文件 → 导出统计数据" | 推荐入口 |
| PlaybackStatsService | `app/services/playback_stats_service.py` | 直接调用入口 |
| 格式规范 | `docs/musearc_stats_import_format_spec.md` | schema 与字段映射权威来源 |

## 输入/输出
- 输入：导出范围（全量/按歌单）、目标路径、文件名
- 输出：`{name}.muse_stats.json` 文件，符合 `musearc_playlist_export_v1` schema
