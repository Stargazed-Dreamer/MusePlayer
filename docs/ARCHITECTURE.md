# MusePlayer 架构设计（PySide6 版）

## 1. 目标与约束
- 复用现有 `core.PyAVPlayerCore` 作为底层播放内核，不改其线程模型。
- UI 使用 PySide6，视觉风格偏 Web 化（圆角卡片、层次阴影、变量化配色）。
- 提供运行时控制接口，允许外部程序在播放器运行期间发命令控制。
- 支持歌单导入/管理、播放控制、歌词/封面/元信息展示、会话恢复。
- 提供可开关日志，默认关闭；启用后每次启动新建 `data/logs/museplayer_*.log`，并自动保留最近 10 个文件。

## 2. 模块划分

### 2.1 Domain/Data 层
- `app/models/entities.py`
  - `Track`：音频文件抽象（id/path/title/artist/album/duration/...）
  - `Playlist`：歌单（id/name/track_ids/created_at/updated_at）
  - `PlaybackState`：当前播放上下文（模式、当前歌单、当前曲目、进度、音量）
  - `Settings`：运行时接口（可启停）、端口、自动恢复、日志开关、播放模式项、播放数据采集
- `app/models/library_store.py`
  - 负责 `data/library.json` 的读写（歌单和曲库）
- `app/models/session_store.py`
  - 负责 `data/session.json` 的读写（上次歌曲、进度、随机模式定位数字）
- `app/models/settings_store.py`
  - 负责 `data/settings.json` 的读写（控制端口、恢复策略、日志开关）
- `data/playback_stats.json`
  - 持久化播放统计（播放次数、主动搜索播放次数、累计播放百分比、累计播放秒数）

### 2.2 Service 层
- `app/services/metadata_service.py`
  - 读取标签元数据、内嵌封面、歌词（标签 USLT + 同名 `.lrc`）
- `app/services/random_order.py`
  - 实现“随机播放双数字定位”算法（seed + random_index）
- `app/services/player_service.py`
  - 包装 `PyAVPlayerCore`，统一对外提供 play/pause/seek/next/prev/切歌
  - 管理播放模式（单曲循环 / 可选歌单循环 / 歌单随机）
  - 管理播放队列、定时检查自然播放结束并触发下一首
  - 播放前 5 秒采用按需窗口读取，超过 5 秒自动提升为完整读取
- `app/services/playback_stats_service.py`
  - 统计并持久化播放行为数据（可在设置中开关）
- `app/services/library_service.py`
  - 歌单导入、文件夹扫描、搜索
  - 启动时清理无效路径歌曲
  - 文件夹导入默认按“文件夹名”自动创建/复用歌单，并汇总到“全部歌曲”
  - 删除普通歌单时，自动清理仅被该歌单引用的歌曲，并同步更新“全部歌曲”

### 2.3 Runtime API 层
- `app/runtime/control_server.py`
  - 基于 TCP localhost 的 JSON Lines 协议（每行一条 JSON）
  - 命令：
    - `play`, `pause`, `toggle`
    - `seek`, `set_volume`
    - `next`, `previous`
    - `import_folder`, `play_file`, `play_track`, `play_playlist`
    - `load_playlist`, `state`
  - 响应格式：`{"ok": true/false, "result": ..., "error": ...}`

### 2.4 UI 层
- `app/ui/main_window.py`
  - 主窗口、菜单栏、主控区（进度条/音量/模式/元信息）
  - 右侧快捷侧栏：当前歌单 + 搜索 + 双击切歌
  - 控制栏图标化：模式循环图标 / 上一首 / 播放暂停 / 下一首
  - 简洁模式：显示控制栏 + 当前歌曲名 + 当前歌词行，支持无边框、透明度、锁定位置、窗口置顶与任意位置拖动
  - 歌词行支持双击跳转、悬停显示起止时间；支持选中复制
  - 侧栏可拖拽调整宽度，支持收起/展开
- `app/ui/playlist_dialog.py`
  - 基础歌单管理（新建、重命名、删除、复制、合并、导入文件夹）
- `app/ui/settings_dialog.py`
  - 设置项（控制接口启停、控制端口、恢复策略、日志开关、播放模式项、播放数据采集）
- `app/ui/theme.py`
  - QSS + 颜色变量定义

## 3. 随机播放（重点）

### 3.1 状态定义
- `random_seed: int`：本轮随机序列种子
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

### 3.5 会话恢复
退出时持久化：
- `mode`
- `random_seed`
- `random_index`
- 当前歌单 id
- 当前曲目 id
- 当前进度秒数
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
- `data/*.json`：运行数据

## 6. 分步落地计划
1. 先完成 data + service + runtime（可脚本控制）
2. 再完成 UI 与快捷交互
3. 最后做样式、恢复流程、联调与校验
