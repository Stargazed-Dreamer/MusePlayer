# 变更日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 修复
- 全局快捷键注册成功但按下毫无反应（无任何报错）：`GlobalHotkeyManager.handle_native_event` 里 `event_type not in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}` 的判断恒为真，导致每个 `WM_HOTKEY` 在第一步就被丢弃。根因是 Qt6/PySide6 传给 `nativeEvent` 的 `event_type` 是 `QByteArray`，它与 `bytes` 比较相等（`==` 为 True）但哈希不一致（`hash()` 不同），而 set 成员判断走哈希 ⇒ 恒 False。现在先 `bytes(event_type).decode()` 归一化为 `str` 再比较。此 bug 与窗口句柄无关（`setWindowFlag()` 在本项目 Qt6 下不重建 `winId()`），也与外部程序占用无关。
- 全局快捷键冲突提示太弱（一闪而过、看不到是哪几项）：`_refresh_global_hotkeys` 过去只把失败文案在状态栏显示 6 秒。现在改为**错误红 + 30 秒**，并在按键设置对话框里逐项定位到具体操作。

### 新增
- 状态栏消息支持"性质配色"：`MultiHintStatusBar` 的 `showMessage`/`set_hint` 新增可选 `severity`（`info`/`success`/`warning`/`error`，常量见 `app/ui/theme.py`）。不传则沿用主题正文色（浅色近黑、深色近白）；传了则按当前主题取对应那套颜色（浅色用压暗版保对比度、深色用提亮版），错误=红、成功=绿、警告=琥珀。主题切换时经 `set_dark_theme()` 自动换色，无需重发消息。
- 按键设置对话框逐项标注全局冲突：上一次注册失败的项会在【操作】格下方显示红色小字`【全局按键冲突】`，同时该行的**全局快捷键输入框描错误红**表示当前不可用（悬浮显示失败原因，如"已被其他程序占用：Ctrl+Alt+Right"）；顶部另有一条汇总说明。用户改动该项组合后标记立即撤销（改完要保存并重新注册才知结果）；全局快捷键总开关关闭时不再标注。

### 变更
- `GlobalHotkeyManager.register()` 返回值由 `list[str]` 改为 `dict[str, str]`（`action_id -> 失败原因`），并新增 `conflicts` 属性保存上次注册结果，供 UI 定位失败项；`unregister_all()` 一并清空 `conflicts`，因此关闭全局快捷键后不会残留过期标记。

## [1.2.2] - 2026-09-25

### 修复
- 会话恢复预览后按播放：第一窗到第二窗边界会卡顿一下、后续正常。根因是懒加载下一窗预读只在 `_load_current_track(auto_play=True)` 里安排；预览走 `auto_play=False` 只加载不预读，之后 `play()` 的"从暂停恢复"分支同样未补排预读，于是第一窗尾部只能兜底同步 `_reload_lazy_window` 解码下一窗（该重载末尾才把预读流水线拉起来，所以只有首边界卡）。现在 `play()` 从暂停恢复分支在 `_core.play(local_start)` 之后显式调一次 `_schedule_lazy_prefetch(base + local_duration)`，与 `_load_current_track(auto_play=True)` 对齐。
- `AGENTS.md` 音频内核节新增红线级"懒加载预读调度：必须覆盖所有让播放真正跑起来的入口"关注点，列清当前合法调度点与唯一不排预读的入口（`_load_current_track(auto_play=False)`），并要求后续新增播放入口时显式补齐预读，防止再次回归。

### 测试
- 新增 `tests/test_player_session_restore.py::test_play_from_pause_schedules_lazy_prefetch`：模拟会话预览 → 用户按播放，断言下一窗 future 被安排、`_prefetch_transition_sec` 与窗口边界（起点 30.0 + 窗口 6.2 = 36.2）对齐。测试基线 219 → 220。

## [1.2.1] - 2026-09-17

### 修复
- 搜索框焦点困住导致单键快捷键"全局失效"：搜索框（`QLineEdit`）持有焦点时，空格、字母、方向键等单键会被输入框吞掉，界面快捷键（播放/暂停、切换喜欢、快进快退等）无法触发。现在 Esc 清空过滤词后把焦点交还曲目列表，且应用级事件过滤器会在点击主窗口内其他不接受点击焦点的控件（按钮/标签/空白卡片）时同样交还焦点，保证"点击别处退出搜索"始终可用；搜索框自身与其清除按钮不触发该逻辑。
- 启动竞态：快速启动后（窗口已出现、当前曲目已预备）立刻播放的歌曲，会在曲库加载/磁盘会话恢复完成时被"主动暂停"。原因是 `PlayerService.restore_session` 无条件按会话位置重新加载当前曲目（`auto_play=False`），覆盖了用户刚发起的播放。现在新增"用户已接管播放"监视：`AppController.restore_session_preview` 在预备完成后武装（`arm_playback_takeover_watch`），`play` / `play_track` / `seek` 及内核播放状态任一命中即判定接管，会话恢复随即只对齐随机序列等不触碰音频的上下文——既不改播放状态，也不取消在飞的预读（避免窗口边界卡顿）。

### 测试
- 新增 `tests/test_player_session_restore.py`（5 例）：启动预备后用户播放 / 播放后暂停并拖动进度条 / 用户未接管（保持原行为）/ 内核兜底判定 / 重新武装。
- 新增 `tests/test_version_consistency.py`（2 例）：把"`app/version.py` 的 `APP_VERSION` 与 `pyproject.toml` 的 `[project].version` 必须一致、且为三段式语义化版本"固化为自动化校验（此前双份版本号仅靠注释约定，漏改一处无任何报错），测试基线 212 → 219。

## [1.2.0] - 2026-09-13

### 新增
- 播放统计时间维度：每条曲目新增年分桶（播放/主动/完播/秒数/早期跳过），服务层新增全局聚合块（月度序列、24 小时分布、最狂热一天）；旧格式文件自动兼容，累计值不受影响，历史年份不回填。数据格式版本 1.0 → 1.1。
- 统计检查点系统（`stats_checkpoint_service`）：统计数据只读快照，支持保存（默认名"年份+月份"，可自定义纪念名）、查看、改名（仅显示名）、删除；跨年自动保存默认开启（设置中可关，`Settings.checkpoint_auto_year_save` / `stats_last_seen_year`），启动检测到跨年自动存档"{上一年} 年度报告"。
- 年度报告生成器（`stats_report_builder`，纯逻辑）：支持主数据（年份分桶 / 检查点快照）与副数据对比（KPI 百分比、Top 榜排名 ▲▼/NEW 标记），产出 KPI 四卡、月度节奏、Top10、歌手/专辑榜与趣味发现（时段偏好、单曲循环之王、探索 vs 怀旧、切歌总数、耐听冠军、最狂热一天）。
- 菜单栏新增「统计」菜单（两个子项）：
  - 「检查点管理」：检查点增删查改名对话框。
  - 「生成年度报告」：主/副选择向导 → 独立报告查看窗口（QPainter 自绘月度柱状图，悬停明细），支持重选主/副。
- 报告导出三种格式（`stats_report_exporter`，打开即看）：自包含 HTML（内联样式、深色调与主题一致）、PNG（报告页截图）、TXT 纯文本摘要。
- 测试基线 182 → 212：年分桶与聚合块 7 例、检查点服务 9 例、报告聚合器与导出器 7 例。

### 修复
- 报告对话框曲目元数据查询改经 `controller.library_service`（原直访父窗口属性落空导致全部显示"未知标题/未知歌手"）。
- 报告对话框仅在年份与检查点数据源均为空时才提示"暂无数据"（原仅检查年份源，存量统计无分桶时误报）。
- 自绘月度图修复非法 `QColor(字符串, alpha)` 构造——该异常发生在 Qt 绘制路径内会直接段错误；月度全 0 时显示空态提示。
- 报告页富文本榜单改用表格布局（Qt 对 `float`/`inline-block` 支持不可靠导致序号/次数挤贴歌名），换行标签高度在布局完成后按 `heightForWidth` 重算（修复首次打开卡片被压矮、需手动缩放窗口才恢复）。

## [1.1.0] - 2026-09-13

### 新增
- `LibraryService` 封装收紧：曲目/歌单内部字典私有化，新增 `find_playlist` / `has_track` / `track_ids` 公开访问器，外部模块（控制器、播放服务、UI）不再直接触碰内部字典。
- `PlayerService` 新增公开接口 `set_current_playlist_id`（会话恢复预览用的轻量歌单对准）与 `set_initial_track_for_playlist`（原私有方法转正），消除控制器对服务私有成员的直接写入。
- 曲库服务特征测试 20 例、Muse 歌单导入路径测试 14 例、播放内核并发回归测试 8 例（测试基线 140 → 182）。

### 修复
- 启动链路：`main.py` 启动探针/延迟清理仍直访曲库字典 `tracks`/`playlists`，封装收紧后真实启动时在 QTimer 回调内抛 `AttributeError`（窗口照常显示、进程不退出，自动化冒烟难以捕获）。已改用公开访问器，并新增源码边界元测试（`tests/test_source_boundaries.py`）把"曲库字典只允许曲库家族文件访问"固化为 CI 防线。
- 音频内核：异步关流改为按调度时捕获的流句柄关闭，修复竞态窗口内误杀重开新流导致的"切歌后无声"；`load_streaming` 回退路径透传 `start_sec`，seek 重启流式解码增加换曲竞态守卫。
- Muse 歌单导入：运行时导入无 `playlist_hash` 的 payload 时，以内容 SHA1 作为有效哈希参与查重，修复同一 payload 重复导入生成重复歌单（`_2`、`_3` 后缀）的问题。
- `.gitignore`：`test*` 通配误伤 `tests/` 目录导致新增测试文件无法入库，已追加再包含规则；补充清理 `tests/` 此前从未被 lint 扫描到的存量告警。
- 统计导出：曲目元数据查询改经 `get_track` 访问器，消除曲库私有化后 `getattr` 静默返回空导致元数据无声丢失的隐患。

### 变更
- 大规模可维护性重构：`LibraryService` 拆分出清理器 / Muse 导入器 / 导出器（公共 API 不变）；歌词解析下沉至 `lyrics_parser`（零 Qt 依赖）；统计导出下沉至 `PlaybackStatsService.export_to_file`；`dispatch_command` 由分支链改为命令注册表（23 个命令，响应结构不变）；UI 混入职责归位、防御性属性访问清零、图标绘制样板收敛；删除死代码与调试残留。
- 文件夹导入 `import_folder` 两个近重复分支合并，语义不变（保留"根目录无文件不建歌单"行为）。

## [0.5.5] - 2026-08-06

### 新增
- 完整的音乐管理能力：本地文件/文件夹导入、多歌单管理、外部歌单文件（`.muse_playlist.json`）导入与统计回写。
- 播放控制：播放/暂停/切歌/跳转/音量调节，支持单曲循环、歌单循环、随机三种播放模式。
- 歌词与封面展示：内嵌与外部歌词关联，支持日语原文/罗马音/翻译等多歌词文件；封面自动提取。
- 会话恢复：关闭时持久化当前歌单、曲目、播放位置、音量与播放模式，启动时自动恢复。
- 基于 TCP JSON Lines 的运行时控制协议（默认 `127.0.0.1:43121`），支持外部程序控制播放、管理歌单与导入曲目。
- 基于 SHA256 种子的确定性随机播放算法，同一种子下顺序可复现。
- 播放统计：累计播放次数、主动播放计数、早期跳过计数、累计播放秒数/百分比，并支持统计数据导入。
- Windows 全局媒体快捷键注册，可在设置中自定义。
- 日间/夜间双主题切换。
- 懒加载解码策略：前 6.2 秒按需解码，超过阈值后自动提升为完整读取。
- 窗口几何恢复、无边框缩放、吸附与侧边栏联动。
- Windows 任务栏播放进度桥接（comtypes）。
- 定时自动保存与分级日志（含崩溃日志）。

### 变更
- 补全面向长期维护的工程基础设施：新增 README、LICENSE、CHANGELOG、CONTRIBUTING、pyproject.toml、tests/ 与 CI 等工程化基线，统一开发与发布流程。

### 修复
- 受限于无 Git 历史访问，本版本可追溯的具体修复条目暂缺。

[Unreleased]: /compare/v1.2.1...HEAD
[1.2.1]: /releases/tag/v1.2.1
[1.2.0]: /releases/tag/v1.2.0
[1.1.0]: /releases/tag/v1.1.0
[0.5.5]: /releases/tag/v0.5.5
