# MusePlayer 架构设计（PySide6 版）

> **注意**：本文档为早期架构设计，内容较基础。
> 更完整、更详尽的架构文档请见 [architecture_design_cn.md](./architecture_design_cn.md)（含 Mermaid 流程图、导入流程、播放流程、统计设计、UI 交互等 11 个章节）。
> 本文件保留用于历史参考，新增内容请直接更新 `architecture_design_cn.md`。

## 1. 目标与约束
- 复用现有 `core.PyAVPlayerCore` 作为底层播放内核，不改其线程模型。
- UI 使用 PySide6，视觉风格偏 Web 化（圆角卡片、层次阴影、变量化配色）。
- 提供运行时控制接口，允许外部程序在播放器运行期间发命令控制。
- 支持歌单导入/管理、播放控制、歌词/封面/元信息展示、会话恢复。
- 提供可开关日志，默认关闭；启用后同一次运行始终使用同一个日志文件，重启后创建新文件，自动保留最近 10 个。

## 2. 模块划分

### 2.1 Domain/Data 层
- `app/models/entities.py`
  - `Track`：音频文件抽象（id/path/title/artist/album/duration_sec/track_no/year/added_at/source_*/extra_lyrics_paths）
  - `Playlist`：歌单（id/name/track_ids/created_at/updated_at/source_*/ordered）
  - `SessionState`：当前播放上下文（模式、当前歌单、当前曲目、进度、音量、随机种子/索引）
  - `Settings`：运行时接口（可启停）、端口、自动恢复、日志开关、崩溃日志开关、数据维护日志开关、播放模式项、播放数据采集、音频输出设备、全局增益、读取策略、歌单排序、随机显示顺序、日语歌词/罗马音显示、定时保存、深色主题、窗口几何记忆与限制
- `app/models/library_store.py`
  - 负责 `data/library.json` 的读写（歌单和曲库）
- `app/models/session_store.py`
  - 负责 `data/session.json` 的读写（上次歌曲、进度、随机模式定位数字）
- `app/models/settings_store.py`
  - 负责 `data/settings.json` 的读写（控制端口、恢复策略、日志开关等）
- `data/playback_stats.json`
  - 持久化播放统计（播放次数、主动搜索播放次数、早期跳过次数、累计播放百分比、累计播放秒数）

### 2.2 Service 层
- `app/services/metadata_service.py`
  - 读取标签元数据、内嵌封面、歌词（标签 USLT + 同名 `.lrc`）
- `app/services/random_order.py`
  - 实现"随机播放双数字定位"算法（seed + random_index）
- `app/services/player_service.py`
  - 包装 `PyAVPlayerCore`，统一对外提供 play/pause/seek/next/prev/切歌
  - 管理播放模式（单曲循环 / 可选歌单循环 / 歌单随机）
  - 管理播放队列、定时检查自然播放结束并触发下一首
  - 播放前 5 秒采用按需窗口读取，超过 5 秒自动提升为完整读取
  - 支持播放速率调整、输出设备切换、全局增益
- `app/services/playback_stats_service.py`
  - 统计并持久化播放行为数据（可在设置中开关）
- `app/services/library_service.py`
  - 歌单导入、文件夹扫描、搜索
  - 启动时清理无效路径歌曲
  - 文件夹导入默认按"文件夹名"自动创建/复用歌单，并汇总到"全部歌曲"
  - 删除普通歌单时，自动清理仅被该歌单引用的歌曲，并同步更新"全部歌曲"
- `app/services/app_controller.py`
  - 应用生命周期管理、命令分发、设置更新
  - 会话保存/恢复、定时保存调度
  - 控制接口启停

### 2.3 Runtime API 层
- `app/runtime/control_server.py`
  - 基于 TCP localhost 的 JSON Lines 协议（每行一条 JSON）
  - 命令：
    - `play`, `pause`, `toggle`
    - `seek`, `set_volume`, `set_mode`
    - `next`, `previous`
    - `import_folder`, `import_playlist_file`, `import_playlist_data`
    - `play_file`, `play_track`, `play_playlist`, `load_playlist`
    - `current_track`, `current_playlist`, `get_playlist`
    - `create_playlist`, `add_track_to_playlist`, `remove_track_from_playlist`
    - `state`, `ping`
  - 响应格式：`{"ok": true/false, "result": ..., "error": ...}`
  - 详细协议见 `docs/CONTROL_PROTOCOL.md`

### 2.4 UI 层
- `app/ui/main_window.py`
  - 主窗口门面文件，仅导出 `MainWindow`
- `app/ui/main_window_impl.py`
  - 主窗口核心实现（丰富模式/简洁模式、拖拽缩放、菜单、快捷键、侧边栏联动）
- `app/ui/main_window_helpers.py`
  - 主窗口可复用辅助组件：多提示状态栏、点击即跳转滑条、歌词/歌单委托绘制、Windows 任务栏进度桥接、图标绘制与歌词时间解析工具
- `app/ui/main_window_mixins/`
  - `playback_mixin.py`：播放、歌词、歌单列表、菜单动作等"业务交互"方法
  - `windowing_mixin.py`：无边框缩放、吸附、拖拽、侧边栏与几何恢复等"窗口行为"方法
- `app/ui/playlist_dialog.py`
  - 基础歌单管理（新建、重命名、删除、复制、合并、导入文件夹）
- `app/ui/settings_dialog.py`
  - 设置项分区：播放、歌词、音频、界面、网络控制接口、数据与日志
- `app/ui/theme.py`
  - QSS + 颜色变量定义，支持日间/夜间主题切换

## 3. 随机播放（重点）

### 3.1 状态定义
- `random_seed: int`：本轮随机序列种子（范围 1~2,000,000,000，溢出后回绕到 1）
- `random_index: int`：当前播放在随机序列中的位置

### 3.2 序列生成
对当前歌单的曲目集合（以 `track.id` 为稳定标识）生成固定乱序：
1. 对每个 `track.id` 计算 `key = SHA256(f"{seed}:{track_id}")`
2. 按 `key` 升序排序，得到稳定随机序列
3. `random_index` 对应当前曲目

在歌单不变且 seed 不变时，序列稳定可复现。

### 3.3 上一首/下一首行为
- 随机模式下：
  - `next`: `random_index += 1`
  - `previous`: `random_index -= 1`
- 当 `next` 超过末尾：
  - `seed += 1`
  - 重新生成新序列
  - `random_index = 0`

### 3.4 中途手动指定歌曲
当用户通过快捷侧边栏指定歌曲：
1. `seed += 1`
2. 依据新 seed 生成新随机序列
3. 定位该歌曲在新序列中的位置，写入 `random_index`
4. 从该位置开始继续随机模式

当设置"随机后顺序"显示时，双击曲目不会触发 seed 自增（`preserve_random=True`），因为列表显示的就是随机序列顺序。

### 3.5 会话恢复
退出时持久化：
- `play_mode`
- `random_seed`
- `random_index`
- 当前歌单 id
- 当前曲目 id
- 当前进度秒数（使用 `_safe_position()` 确保懒加载窗口模式下获取绝对位置）
- 音量

启动后按上述状态恢复，随机模式可精确回到上次歌曲与顺序。

## 4. 状态流
1. 启动：加载 settings -> library -> session
2. 初始化 PlayerService 与 Runtime Server
3. UI 订阅 PlayerService 状态信号刷新界面
4. 用户动作/外部命令 -> PlayerService -> Core
5. 周期性保存会话（窗口关闭前强制保存）

## 5. 文件布局
- `main.py`：应用入口
- `requirements.txt`：依赖
- `docs/ARCHITECTURE.md`：本文档
- `docs/architecture_design_cn.md`：中文架构设计文档
- `docs/CONTROL_PROTOCOL.md`：运行时控制协议
- `docs/playlist_export_format_cn.md`：歌单导出格式
- `docs/release_checklist.md`：发版检查清单
- `data/*.json`：运行数据

## 6. Settings 字段一览

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `control_host` | str | `"127.0.0.1"` | 控制接口监听地址 |
| `control_port` | int | `43121` | 控制接口端口 |
| `control_interface_enabled` | bool | `False` | 是否启用控制接口 |
| `auto_restore_session` | bool | `True` | 启动时恢复上次会话 |
| `logging_enabled` | bool | `False` | 是否启用应用日志 |
| `crash_logging_enabled` | bool | `True` | 是否启用崩溃日志 |
| `data_maintenance_logging_enabled` | bool | `True` | 是否启用数据维护日志 |
| `enable_single_loop_mode` | bool | `True` | 是否启用单曲循环模式 |
| `enable_playlist_loop_mode` | bool | `False` | 是否启用歌单循环模式 |
| `prefer_playlist_order` | bool | `False` | 优先使用歌单指定的顺序 |
| `playlist_loop_sort` | str | `"default"` | 歌单循环排序方式：`default`/`title`/`artist` |
| `random_display_order` | str | `"original"` | 随机模式显示顺序：`original`（随机前）/`random`（随机后） |
| `show_romaji` | bool | `True` | 日语歌词显示罗马音 |
| `show_japanese_lyrics` | bool | `True` | 显示日语歌词 |
| `collect_playback_data` | bool | `True` | 是否采集播放数据 |
| `global_gain_boost` | float | `1.0` | 全局音量增益倍率 |
| `read_strategy` | str | `"window"` | 音频读取策略：`window`/`full` |
| `output_device` | str | `""` | 输出设备名称，空字符串表示跟随系统 |
| `timed_save_enabled` | bool | `False` | 是否启用定时保存 |
| `timed_save_minutes` | int | `5` | 定时保存间隔（分钟） |
| `dark_theme` | bool | `True` | 是否使用深色主题 |
| `remember_window_geometry` | bool | `True` | 是否记住窗口位置和大小 |
| `window_x` | int | `-1` | 窗口 X 坐标 |
| `window_y` | int | `-1` | 窗口 Y 坐标 |
| `window_width` | int | `0` | 窗口宽度 |
| `window_height` | int | `0` | 窗口高度 |
| `max_window_width` | int | `0` | 最大窗口宽度（0 表示不限） |
| `max_window_height` | int | `0` | 最大窗口高度（0 表示不限） |

## 7. 分步落地计划
1. 先完成 data + service + runtime（可脚本控制）
2. 再完成 UI 与快捷交互
3. 最后做样式、恢复流程、联调与校验
