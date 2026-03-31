# MusePlayer 架构设计文档（中文）

> 文档目标：说明当前播放器的内部架构、核心数据结构和关键流程，确保后续重构与功能迭代保持一致。

## 1. 总体架构

系统采用“UI 层 + 控制器层 + 服务层 + 存储层 + 播放内核层”分层。

```mermaid
flowchart LR
    UI["UI 层\nMainWindow"] --> C["AppController"]
    C --> L["LibraryService"]
    C --> P["PlayerService"]
    C --> S["PlaybackStatsService"]
    C --> R["ControlServer"]

    L --> LS["LibraryStore\nlibrary.json"]
    C --> SS["SessionStore\nsession.json"]
    C --> STS["SettingsStore\nsettings.json"]
    S --> PS["playback_stats.json"]

    P --> CORE["PyAVPlayerCore"]
    P --> L
```

分层原则：
- UI 层只做展示和交互，不直接落盘。
- `AppController` 做编排、生命周期、命令分发。
- 业务规则（导入、播放、统计）在服务层集中。
- 持久化只在 Store 层。

---

## 2. 代码结构（重构后）

`app/ui/main_window.py`
- 主窗口门面文件，仅导出 `MainWindow`，降低入口复杂度。

`app/ui/main_window_impl.py`
- 主窗口核心实现（丰富模式/简洁模式、拖拽缩放、菜单、快捷键、侧边栏联动）。

`app/ui/main_window_helpers.py`
- 主窗口可复用辅助组件：
  - 多提示状态栏 `MultiHintStatusBar`
  - 点击即跳转滑条 `ClickJumpSlider`
  - 歌词/歌单委托绘制
  - Windows 任务栏进度桥接
  - 图标绘制与歌词时间解析工具

---

## 3. 关键数据结构

### 3.1 Track（歌曲实体）
核心字段：
- 基础展示：`title / artist / album / duration_sec / path`
- 外部歌单映射：`source_track_id / source_storage_relpath / source_sha256`
- 歌词映射：`source_lyrics_storage_relpath / source_lyrics_path`

用途：
- 既支持本地文件导入，也支持数据库导出歌单（`.muse_playlist.json`）回放与回写统计。

### 3.2 Playlist（歌单实体）
核心字段：
- 基础：`id / name / track_ids`
- 外部来源：`source_schema / source_file / source_playlist_hash / source_database_location / source_exported_at`

用途：
- 本地歌单管理
- 与外部 DB 导出格式建立可追踪映射

### 3.3 播放统计（PlaybackStatsEntry）
字段语义：
- `play_count`：每次开始播放都记一次
- `active_play_count`：主动触发播放计数
- `early_skip_count`：前 5% 被切走计数
- `played_seconds_total`：累计播放秒数
- `played_percent_total`：累计播放百分比（可超过 100%）

---

## 4. 导入流程设计

### 4.1 文件夹导入
入口：`LibraryService.import_folder(...)`

流程：
1. 扫描所选目录下音频文件（根目录歌曲归入根歌单）。
2. 仅扫描第一级子目录，每个子目录单独建/复用歌单。
3. 导入过程做去重映射，避免重复曲目膨胀。
4. 完成后刷新“全部歌曲”与相关歌单。

设计原因：
- 用户常用“目录即歌单”组织方式。
- 第一级限制可控，避免深层目录导入不可预期。

### 4.2 数据库歌单导入（`.muse_playlist.json`）
入口：
- 文件导入：`import_muse_playlist(file_path)`
- 直接数据导入：`import_muse_playlist_payload(payload)`（控制接口可直接传 JSON）

流程要点：
1. 校验 `schema == musearc_playlist_export_v1`。
2. 解析歌曲元信息与 `storage_relpath` 映射真实文件路径。
3. 解析 `lyrics_storage_relpath`，生成 `source_lyrics_path` 供歌词展示。
4. 保留 `source_*` 字段用于后续统计回写。

---

## 5. 播放流程与随机播放设计

### 5.1 播放核心流程
入口：`PlayerService.play_track(...)`

主要步骤：
1. 校验目标曲目存在并可访问。
2. 维护顺序索引/随机索引。
3. 加载解码（窗口模式或全量模式）。
4. 触发播放状态信号与进度更新。
5. 在切歌时判定上一首是否属于 early skip。

### 5.2 随机播放（seed + idx）
状态字段：
- `random_seed`
- `random_index`

规则：
1. 固定歌单 + 固定 seed => 固定乱序结果。
2. `random_index` 表示当前乱序序列中的位置。
3. 到序列末尾时 `seed += 1` 生成新序列。
4. 中途手动选歌时也 `seed += 1`，并重新定位 index，保证后续随机链路稳定。

该设计保证：
- 随机模式下仍然能稳定“上一首/下一首”。
- 应用重启后可恢复随机链路上下文。

---

## 6. 音频读取策略

设置项：`read_strategy`
- `window`（默认）
- `full`

### 6.1 window 策略
- 首次先加载约 6.2s 窗口。
- 后台预读取下一窗口。
- 播放接近窗口末尾时切入预读取结果，尽量降低卡顿。

### 6.2 full 策略
- 一次性加载整首音频。
- 路径简单、兼容性强，但首载开销更高。

---

## 7. 统计设计与保存策略

### 7.1 统计触发
- `play_count`：每次播放起点触发
- `active_play_count`：主动操作触发
- `early_skip_count`：切歌时检查“上一首播放比例 < 5%”
- `played_percent_total`：每 tick 增量累加

### 7.2 不应统计的场景
- 启动恢复会话
- 应用退出保存流程

实现：`PlayerService.suspend_stats_collection()`
- 在 `restore_session` 与 `AppController.shutdown` 中包裹使用。

### 7.3 保存机制
- 手动：菜单 `文件 -> 保存统计数据`（`Ctrl+S`）
- 定时：可配置分钟间隔
- 关闭：`shutdown()`
- 兜底：`aboutToQuit / atexit / sys.excepthook / threading.excepthook / SIGINT/SIGTERM`

---

## 8. 控制接口协议（运行时）

实现：`ControlServer + AppController.dispatch_command`。

已支持（核心）：
- `play / pause / toggle / seek / next / previous`
- `set_volume / set_mode`
- `import_folder`
- `import_playlist_file`
- `import_playlist_data`（直接传歌单 JSON）
- `play_file / load_playlist / play_playlist / play_track`

目标：让外部程序可在运行时控制播放与导入，不依赖人工界面操作。

---

## 9. UI 交互细节

### 9.1 模式
- 丰富模式：完整信息与管理能力
- 简洁模式：控制优先，窗口无边框、可置顶、可锁定位置、透明度可调

### 9.2 无边框行为
- 边缘拉伸：通过 `WM_NCHITTEST`
- 标题栏吸附：顶部最大化，左右半屏
- 最大化/吸附后拖动标题栏：先还原再拖动

### 9.3 状态栏
`MultiHintStatusBar` 支持多提示并存，用 `-` 连接，例如：
- 播放状态
- 音量状态
- 下一首预告

---

## 10. 回写到数据库歌单格式

在 `save_session()/定时保存/手动保存` 时，`sync_muse_playlist_stats(...)` 会回写：
- 每首歌：`play_count / manual_play_count / play_seconds / early_skip_count`
- 汇总字段：总播放次数、总主动播放次数、总早期跳过次数等

回写原则：
- 仅更新可匹配曲目
- 本地无统计时不覆盖文件已有统计值

---

## 11. 后续重构建议

1. 将 `MainWindow` 进一步按职责拆分为 mixin（播放控制、窗口行为、列表同步）。
2. 为控制协议补充版本号与 schema 校验，增强兼容性。
3. 补充自动化测试：
   - 随机序恢复一致性
   - early skip 判定边界
   - DB 歌单导入/回写回归测试
