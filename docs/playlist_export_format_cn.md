# 歌单导出格式（`musearc_playlist_export_v1`）

## 文件类型

- 扩展名：`*.muse_playlist.json`
- 编码：`UTF-8`
- 顶层字段：
  - `schema`：固定为 `musearc_playlist_export_v1`
  - `playlist_hash`：歌单唯一哈希（由导出时歌曲顺序+导出时间计算）
  - `playlist_name`：导出时的歌单名称
  - `exported_at`：UTC ISO8601 时间
  - `database_location`：导出时数据库根目录绝对路径
  - `track_count`：歌曲数量
  - `stats_summary`：统计汇总保留字段（对象）
  - `tracks`：歌曲数组

## tracks[i] 字段

- `track_id`：数据库歌曲 ID（如 `trk_xxx`）
- `storage_relpath`：数据库内音频文件相对路径（如 `data/tracks/ab/trk_xxx.flac`）
- `source_sha256`：音频来源 SHA256（用于跨库/跨路径匹配）
- `title`：标题
- `artist`：艺术家
- `album`：专辑
- `lyrics_storage_relpath`：数据库内歌词相对路径（无则空字符串）
- `stats`：播放器回填统计对象
  - `play_count`：播放次数（整数）
  - `manual_play_count`：指定播放次数（整数）
  - `play_seconds`：播放总秒数（整数）

## 统计回导规则

- 使用 `playlist_hash` 去重：
  - 同一 `playlist_hash` 再次导入会覆盖旧贡献，不会重复累加。
- 不同歌单（不同 `playlist_hash`）对同一首歌会按歌单维度累加。
- 映射优先级：
  1. `source_sha256`
  2. `track_id`
  3. `storage_relpath`
