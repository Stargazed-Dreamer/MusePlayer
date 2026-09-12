# MusePlayer 可维护性改造批次方案

> **文档性质**：面向 AI 执行 agent 的分批改造任务书。每批独立可验证、可回滚。
> **基准**：基于 2026-09-12 的工作区状态（已包含文末"已完成项"中的四个修复）。
> **定位规则**：文中行号是写作时快照，**只用于辅助理解，不作为定位依据**。执行时必须用符号名（函数/类/常量名）grep 定位。

---

## 0. 给执行 Agent 的必读规则

1. **先读 `AGENTS.md`**（项目唯一真相源），尤其是"关键约束"与"隐私与公开发布"两节。本文件与其冲突时以 `AGENTS.md` 为准。
2. **只改本批次"触碰文件白名单"内的文件与符号**。发现白名单外的代码有问题 → 记录到交付报告，不顺手修。
3. **批次描述与实际代码不符时立即停止并报告**（例如符号不存在、签名不一致、行数对不上）。禁止凭猜测继续。
4. **禁止事项**：
   - 修改任何公共 API 签名（除非批次明确要求）
   - 引入新第三方依赖
   - 重排 import、重排方法顺序、批量改注释等格式噪音
   - 修改 `app/services/player_service.py` 的 `_LAZY_*` 常量区与 `AGENTS.md`（有其他工作正在进行）
5. **每个编辑步骤后**运行批次"验收"中的命令；任何一步失败即回滚该步。
6. **不执行 `git commit`**（除非编排者明确要求）；交付时报告改动文件清单与验证输出摘要。
7. 注释与 docstring 保持中文，风格与周边代码一致。
8. 运行时数据只能写入 `data/` 目录；测试临时文件一律用 `tmp_path` fixture。

---

## 1. 环境与验收命令

```bash
# Windows + Git Bash 环境，统一用项目虚拟环境
.venv/Scripts/python.exe -m pytest tests/ -q          # 必须全绿（当前基线：140 passed）
.venv/Scripts/python.exe -m ruff check app/ core/     # 必须零告警
.venv/Scripts/python.exe -c "import main"             # 导入冒烟（导入成功无输出/正常退出）
```

UI 结构性批次（B10/B11/B13）额外验收：启动 `python main.py` 确认窗口正常渲染、切歌/切歌单不报错（由编排者或人工执行）。

---

## 2. 已完成项（2026-09-12，勿重复执行）

| 项 | 内容 |
|---|---|
| 死代码删除 | `LibraryService.sync_muse_playlist_stats` 整个方法（原 1492-1655 行，无条件 `return 0` 且全库零调用）已删除 |
| 重复初始化 | `main_window_impl.py` `__init__` 中重复的第二段窗口状态初始化块（原 139-157 行）已删除 |
| 调试 print | `app_controller.py` 两处 `[Services计时]`、`main_window_impl.py` 一处 `[MainWindow计时]` 已改为 `logger.debug` |
| 静默导入失败 | `AppController.import_files` 单文件失败的 `except: continue` 已补 `logger.warning` |
| S1 关流竞态 | `core/core.py` `_async_close_stream` 改为按 `capture_stream()` 句柄调度关闭，`output.py` 后端新增 `capture_stream`/`release_stream` 句柄语义；竞态窗口内重开的新流不再被误杀。回归测试：`tests/test_player_core.py` |
| S2 起点丢失 | `load_streaming` 回退路径透传 `start_sec`；`_decode_to_pcm` 无窗口解码同样执行 seek+前导裁剪（原仅窗口模式生效） |
| S2 seek TOCTOU | `load_streaming` 新增内部守卫 `expected_source`：加锁时源已变化（换曲/卸载）则放弃重启并返回 None；`seek()` 恢复播放前二次校验 `_source_path`。回归测试见上 |
| 基线更新 | 测试基线现为 **148 passed**（含 `tests/test_player_core.py` 8 例）；各批次验收以此为准 |
| 弱模型批次 B1–B13 | 2026-09-13 全部执行完毕并通过强模型核查（详见第 3 节"强模型核查结论"）；测试基线更新为 **168 passed**；附带修复 `.gitignore` 对 `tests/` 的误伤 |
| 封装收紧 + Muse 补强（1.1.0） | 2026-09-13：`tracks/playlists` 私有化 + `find_playlist`/`has_track`/`track_ids` 访问器（外部模块全部迁移）；`PlayerService` 公开 `set_current_playlist_id`/`set_initial_track_for_playlist` 消除越界写入；`import_folder` 重复分支合并；Muse 导入路径测试 14 例——**并由此发现并修复运行时 payload 重复导入生成重复歌单的 bug**；删除死函数 `_make_plus_minus_icon`；版本 1.0.1 → 1.1.0。测试基线 **182 passed** |
| B3 静默吞异常加日志 | `library_service.py`、`app_controller.py`、`core/core.py`（新增 `logger`）、`metadata_service.py` 中所有非实时回调路径的 `except Exception: pass/continue` 补 `logger.warning/debug`；`core.py` `_audio_callback` 实时路径保持原样（白名单） |
| B1 曲库服务特征测试 | 新增 `tests/test_library_service.py`（20 例）：`_normalize_relpath`/`_deduplicate_tracks`/`_drop_missing_tracks`/`_normalize_playlist_tracks`/歌单 CRUD/`export_playlist_file` 往返。基线升至 **168 passed** |
| B2 UI 微清理 | 6 处 `0x0100` → `Qt.ItemDataRole.UserRole`（playlist_dialog×2、playback_mixin×2、windowing_mixin×2）；新建 `app/ui/file_types.py`（`AUDIO_EXTENSIONS`/`AUDIO_FILE_FILTER`）替换 4 处魔法集合/字符串；`settings_dialog.py` 移除 `from core.output` 导入，改由 `AppController.list_output_devices` 注入；三命令全过，`grep "from core" app/ui/` 与 `grep "0x0100" app/` 零命中 |
| B4 歌词解析下沉 | `main_window_helpers.py` 中 700+行无Qt依赖的歌词解析逻辑（3个数据类+18个函数）移至 `app/services/lyrics_parser.py`；原UI文件通过 `from app.services.lyrics_parser import ...` 再导出保持兼容；`grep -n "PySide6" app/services/lyrics_parser.py` 零命中；三命令全过 |
| B5 统计导出下沉 | `playback_mixin._export_stats` 中的 JSON 序列化/SHA256/写盘逻辑搬入 `PlaybackStatsService.export_to_file(dest, *, library=None)`，新增 `has_entries` 属性供 UI 预判；UI 仅保留对话框与状态栏提示；`grep "_entries" app/ui/` 仅余 `_lyrics_*` 与公开 `has_entries`；三命令全过（168 passed） |
| B6 LibraryCleaner 拆分 | 新建 `app/services/library_cleaner.py`（组合持有 `LibraryService`）；`_drop_missing_tracks`/`_cleanup_missing_lyrics_paths`/`_deduplicate_tracks`/`_normalize_playlist_tracks` + 辅助 `_dedupe_key` 共 5 个方法体原样搬移到 `LibraryCleaner`；`LibraryService` 保留原方法名作为委托门面（`return self._cleaner.<method>()`），`__init__` 末尾构造 `self._cleaner`；`ALL_SONGS_ID` 通过方法内延迟导入避免循环依赖；三命令全过，B1 测试 20/20 全绿 |
| B10 窗口职责移入 windowing_mixin | `_on_opacity_changed`/`_toggle_compact_lock`/`_toggle_always_on_top` 三个方法从 `playback_mixin.py` 剪切粘贴到 `windowing_mixin.py`（`_toggle_compact_mode` 附近）；并清理 `playback_mixin.py` 中残留的两组重复方法；三命令全过，168 passed |
| B11 导入/歌词职责移入 playback_mixin | `_import_folder_as_playlist`/`_attach_lyrics_to_current_track` 两个方法从 `windowing_mixin.py` 剪切粘贴到 `playback_mixin.py`（紧邻 `_reload_current_lyrics`）；`dropEvent` 仍留在 `windowing_mixin` 并通过 `self.` 调用（组合后同一对象）；`playback_mixin.py` 顶部补 `AUDIO_EXTENSIONS` 导入；三命令全过，168 passed |
| B9 dispatch_command 注册表化 | `dispatch_command` 中 23 个 `if cmd ==` 分支提取为 23 个 `_cmd_<name>(payload) -> dict` 方法；`__init__` 末尾构建 `self._command_handlers: dict[str, Callable]` 注册表；`dispatch_command` 改为查表分发 + unknown 错误兜底；响应 JSON 结构逐字保留；`test_control_server.py` fixture 同步绑定真实处理器方法到 mock（`types.MethodType`）；三命令全过，30/30 协议分发用例全绿 |
| B12 懒初始化属性显式化 | `MainWindow.__init__` 新增 24 个 UI 控件占位默认值（`rich_min_btn`/`compact_top_bar`/`track_list`/`main_splitter`/`favorite_btn`/`play_btn`/… 均为 `None`）+ 2 个状态属性（`_last_random_seed`/`_skip_next_settings_reload`）；两个 mixin 中 24 处 `hasattr(self,` 全部改为 `self.X is None`/`is not None`；7 处字符串字面量 `getattr(self, "X", default)` 改为直接 `self.X`；`_bind_shortcuts` 方法守卫移除（方法始终存在）；1 处动态 `getattr(self, label_name, None)` 保留（变量名非字符串字面量，非本批目标）；三命令全过，168 passed |
| B13 图标绘制样板收敛 | `main_window_helpers.py` 新增 `_render_icon(color, pen_width, draw, *, brush=NoBrush)` 高阶辅助，统一 24x24 透明画布 + Antialiasing + RoundCap/RoundJoin 画笔生命周期；15 个 `_make_*_icon` 函数体各缩为 `_render_icon(...)` 单行调用 + 内嵌 `_draw(p)` 闭包保留原绘图指令（坐标/线宽/颜色逐字不动）；`_make_lock_icon`/`_make_pin_icon` 中途 setPen/setBrush 覆盖在闭包内就地重建 QPen；新增 `from collections.abc import Callable`；ruff 零告警 + 168 passed + 30 个图标变体实例化渲染通过。**待人工目检确认与改造前肉眼无差异后合入** |
| B8 PlaylistExporter 拆分 | 新建 `app/services/playlist_exporter.py`（组合持有 `LibraryService`，本身不持有独立状态）；`export_playlist_file` + 5 个辅助方法（`_get_or_create_export_hash`/`_sanitize_export_name`/`_export_track_lyrics`/`_get_track_lyrics_paths`/`_export_relpath`）原样搬移到 `PlaylistExporter`；`LibraryService` 保留 `export_playlist_file` 作为委托门面（`return self._exporter.export_playlist_file(...)`），公开 API 零变化；`MUSE_PLAYLIST_SCHEMA` 通过方法内延迟导入避免循环依赖；`LibraryService.__init__` 末尾构造 `self._exporter`；顺带清理 `library_service.py` 中因搬移而未用的 `datetime.UTC` 导入；三命令全过，B1 导出往返测试 20/20 全绿（168 passed） |
| B7 MusePlaylistImporter 拆分 | 新建 `app/services/muse_playlist_importer.py`（组合持有 `LibraryService`，本身不持有独立状态）；`import_muse_playlist` / `import_muse_playlist_payload` 两个公开 API + `_import_muse_playlist_data` / `_resolve_muse_track_path` / `_generate_muse_playlist_id` / `_find_existing_muse_playlist` / `_find_track_by_source_fields` 共 7 个方法原样搬移到 `MusePlaylistImporter`；`LibraryService` 保留两个公开 API 作为委托门面（`return self._muse_importer.<method>(...)`），公开 API 零变化；`ALL_SONGS_ID`/`FAVORITES_ID`/`AUDIO_EXTENSIONS` 通过方法内延迟导入避免循环依赖；`LibraryService.__init__` 末尾构造 `self._muse_importer`；顺带清理 `library_service.py` 中因搬移而未用的 `json` 导入。**注意（向编排者报告）**：批次原文描述"`import_folder` 的 Muse 分支"在代码中**不存在**——`import_folder` 只处理音频文件夹扫描，无 Muse 分支；Muse 导入的实际公开入口是 `import_muse_playlist` 与 `import_muse_playlist_payload` 两个方法。本批按修正范围（其余 5 个方法 + 2 个公开入口）执行，未触碰 `import_folder`。B1 测试未覆盖 Muse 导入路径，列为未测试区域；三命令全过（168 passed） |

---

## 3. 批次总览与依赖

### 泳道（同一泳道内必须串行，泳道间可并行）

```
泳道C（全局散点，最先做）：B3 → 之后才允许开其他泳道
泳道A（曲库，串行）：      B1 → B6 → B7 → B8
泳道B（UI，串行）：        B2 → B4 → B5 → B10 → B11 → B12 → B13
强模型任务：               S1、S2（在 B3 之后任意时刻，与泳道A/B并行；只碰 core/core.py）
```

### 冲突矩阵

| 批次 | 触碰文件 | 与谁冲突 |
|---|---|---|
| B3 | app_controller.py、core/core.py、library_service.py、player_service.py(仅加日志) | 所有批次（故最先做） |
| B1 | tests/ 新增文件 | 无 |
| B2 | playback_mixin、windowing_mixin、playlist_dialog、settings_dialog、main_window_impl(调用点)、app_controller(加方法) | 泳道B后续批次 |
| B4 | main_window_helpers、新增 app/services/lyrics_parser.py | 泳道B |
| B5 | playback_mixin、playback_stats_service.py | 泳道B |
| B6/B7/B8 | library_service.py、新增 services 模块、tests/ | 泳道A |
| B9 | app_controller.py | 泳道C之外无冲突（B3 完成后可随时做） |
| B10/B11 | playback_mixin、windowing_mixin | 泳道B |
| B12 | main_window_impl、两个 mixin | 泳道B（最后做） |
| B13 | main_window_helpers | 与 B4 同文件，须在 B4 后 |
| S1/S2 | core/core.py（S2 可能碰 core/types.py） | 仅与 B3 冲突 |

### 强模型核查结论（2026-09-13，B1–B13 全部执行后）

逐项独立核查（非复述执行 agent 的自报）：

- `LibraryService` 公共方法集合与基线 diff 仅差已删的死方法——B6/B7/B8 拆分未改变对外契约；
- B5 导出逻辑与 HEAD 原版逐行等价（SHA256 计算式、payload 结构、`indent=2` 均未变）；
- `lyrics_parser.py` 零 Qt 导入；`app/ui/` 无 core 直连、无 `0x0100` 残留；
- 两个 mixin 防御性 `hasattr/getattr` 清零；B9 协议测试未被放水（fixture 改动是注册表化的合理适配）；
- **168 测试全绿、ruff 零告警**；离屏 UI 实例化冒烟 14 项全过（真实 QApplication + MainWindow 构建、
  跨混入的简洁模式往返、B9 协议 ping、干净关停）。

**核查中发现并修复**：`.gitignore` 的 `test*` 通配连 `tests/` 目录一起匹配，B1 产出的
`tests/test_library_service.py` 与此前的 `test_player_core.py` 一直被静默忽略、无法入库。
已追加 `!tests/`、`!tests/**` 再包含与 `tests/**/__pycache__/` 定向再忽略，`git status` 现可正确列出。

**遗留人工验收（合入前必须完成）**：

1. B13 图标目检：启动应用核对 15 个图标与改造前无肉眼差异（执行 agent 已做 30 个变体的实例化渲染，但视觉等价只能人工确认）；
2. 真机播放冒烟：真实音频设备上连续快速切歌、拖动进度条，验证 S1 关流竞态修复（自动化用假后端，覆盖不了真实 sounddevice 路径）。

---

## 4. 批次详情

### B1｜曲库服务特征测试（安全网，泳道A第一步）

- **目标**：为 `LibraryService` 的纯逻辑补"特征测试"（锁定当前行为，为 B6-B8 拆分提供回归保护）。
- **触碰文件**：仅新增 `tests/test_library_service.py`（如需 fixture 可改 `tests/conftest.py`，保持向后兼容）。
- **现状**：`library_service.py` 约 1815 行、55 个方法，零测试覆盖。
- **测试范围**（只测纯逻辑与 JSON 往返，**不要求**覆盖音频解码/真实音频文件解析）：
  1. `_normalize_relpath`：正斜杠归一化、绝对路径、空值。
  2. `_deduplicate_tracks`：构造 path / source_sha256 重复的 Track，断言保留策略与返回值 `bool`。
  3. `_drop_missing_tracks`：用 `tmp_path` 创建真实文件 + 引用不存在路径，断言只删缺失项。
  4. `_normalize_playlist_tracks`：playlist 引用不存在曲目时被剔除、`ALL_SONGS_ID` 重建。
  5. 歌单 CRUD：`create_playlist` / `rename_playlist` / `remove_playlist` / 收藏（`FAVORITES_ID`）行为。
  6. `export_playlist_file`：用真实 `PlaybackStatsService(tmp_path)` + `tmp_path` 下临时曲库，断言导出 JSON 的 schema/track_count/stats 字段结构（**锁定现状**，不判断"应该"是什么样）。
- **方法**：参考 `tests/test_stats_service.py`、`tests/test_stores.py` 的 tmp_path 用法与断言风格。`LibraryService(LibraryStore(tmp_path / "library.json"), MetadataService())` 构造；若 MetadataService 构造需要参数，按 `app_controller.initialize_services` 中的实际调用方式照抄。
- **验收**：`pytest tests/ -q` 全绿且新文件用例数 ≥ 15；`ruff check .` 通过。
- **禁止**：为"让测试通过"而修改任何 `app/` 源码。若发现疑似 bug，记录报告，不修。

---

### B2｜UI 微清理：魔法数、常量去重、分层违规

- **目标**：三件独立小事，一次做完。
- **触碰文件**：`playback_mixin.py`、`windowing_mixin.py`、`playlist_dialog.py`、`settings_dialog.py`、`main_window_impl.py`（仅 `_open_settings_dialog` 调用点）、`app_controller.py`（仅新增方法）。
- **步骤**：
  1. **`Qt.UserRole` 替换**：6 处 `0x0100` → `Qt.ItemDataRole.UserRole`。位置（grep `0x0100` 定位）：
     - `playlist_dialog.py`：2 处
     - `playback_mixin.py`：2 处
     - `windowing_mixin.py`：2 处
  2. **音频扩展名常量统一**：新建 `app/ui/file_types.py`：
     ```python
     """音频文件类型常量（UI 层共用）。"""

     AUDIO_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".wma"})
     AUDIO_FILE_FILTER: str = "音频文件 (*.mp3 *.flac *.wav *.m4a *.aac *.ogg *.opus *.wma)"
     ```
     替换 4 处：`windowing_mixin.py` 两个 `audio_exts = {...}` 局部集合、`playback_mixin.py` 与 `playlist_dialog.py` 各一处过滤器字符串（grep `*.mp3` 与 `audio_exts` 定位）。
  3. **分层违规修复**：`settings_dialog.py` 顶部 `from core.output import list_output_devices` 违反"UI 不直接操作 core"约束。改法：
     - `AppController` 新增薄方法：`def list_output_devices(self) -> list: from core.output import list_output_devices as _f; return _f()`（函数内延迟导入，保持启动轻）。
     - `SettingsDialog.__init__` 增加可选参数 `list_output_devices_fn: Callable[[], list] | None = None`，默认 `None` 时设备下拉框显示"默认设备"一项即可（与现在无设备时的行为保持一致）。
     - 调用点（`playback_mixin.py` `_open_settings_dialog`）传入 `self.controller.list_output_devices`。
- **验收**：三命令全过 + `grep -rn "from core" app/ui/` 零命中 + `grep -rn "0x0100" app/` 零命中。
- **回滚**：`git checkout -- <文件>`。

---

### B3｜静默吞异常统一加日志（全局，最先做）

- **目标**：消灭"无痕迹降级"。所有 `except Exception: pass` / `except Exception: continue` 至少补一条日志。
- **触碰文件**：`library_service.py`、`app_controller.py`、`core/core.py`、`player_service.py`（如有命中）、`metadata_service.py`（如有命中）。
- **规则**（逐点套用，先 grep `except Exception` 全库列清单再动手）：
  1. 服务层/控制器：`logger.warning("<动作>失败 %s: %s", <关键参数>, exc)`。若是循环内高频预期路径（如逐 packet 解码容错），用 `logger.debug`。
  2. **音频实时回调路径（`core.py` `_audio_callback` 内）禁止加日志**——实时线程不允许 IO。该路径下的 `pass` 保持原样并在报告标注"实时路径，不加日志"。
  3. `core.py` 目前**没有 logger**：模块顶部加 `import logging` + `logger = logging.getLogger("museplayer.core")`（与 `player_service.py` 的命名风格一致）。
  4. 保留原有控制流（continue/return/pass），只增加日志语句，不改变任何分支逻辑。
- **参考基线**（grep 时应命中的典型位置，以实际 grep 结果为准）：
  - `library_service.py`：清理日志写失败、`_dedupe_key` 静默降级、`_remove_track_globally` 索引删除失败等约 4-5 处。
  - `app_controller.py`：歌词 GBK 回退后仍失败处。
  - `core/core.py`：后台关流、seek 退化、损坏 packet 跳过等约 3-4 处。
- **验收**：三命令全过；`grep -n "except Exception" <文件>` 逐处核对：每处要么有日志、要么属于实时回调白名单（报告列出白名单清单）。

---

### B4｜歌词解析下沉服务层（泳道B）

- **目标**：把 `main_window_helpers.py` 中无 Qt 依赖的纯解析逻辑移到 `app/services/lyrics_parser.py`，UI 文件从 2359 行瘦身约 600 行。
- **触碰文件**：`main_window_helpers.py`、新增 `app/services/lyrics_parser.py`。
- **移动内容**（在 helpers 中 grep 定位）：
  - 数据类：`FuriganaAnnotation`、`LyricWord`、`LyricEntry`（约 142-232 行区域）
  - 函数：`_parse_lrc_entries`、`_parse_qrc_words`、`_parse_qrc_entries`、`_parse_qrc_structured`、`build_structured_lyrics` 及同区域的其余 LRC/QRC/假名注音纯函数、时间格式化函数（约 1299-1860 行区域）。逐个函数判断：**函数体不 import 任何 Qt 类型才可移动**；依赖 Qt 的留在 helpers 并在报告中说明。
- **兼容策略（最低风险）**：函数/类名保持原样（含下划线前缀），移动后在 `main_window_helpers.py` 顶部加：
  ```python
  from app.services.lyrics_parser import (  # 兼容再导出：现有 UI 导入路径不变
      FuriganaAnnotation, LyricEntry, LyricWord, _parse_lrc_entries, ...,
  )
  ```
  现有 `playback_mixin.py` 等从 helpers 导入这些符号的代码**无需改动**。
- **验收**：三命令全过 + `python -c "import app.ui.main_window_helpers, app.services.lyrics_parser"` 成功 + `grep -n "PySide6" app/services/lyrics_parser.py` 零命中。
- **禁止**：修改任何函数体逻辑；重命名符号。

---

### B5｜统计导出下沉服务层（泳道B）

- **目标**：`playback_mixin.py` `_export_stats`（约 134-197 行）中的 JSON 序列化 + SHA256 + 写盘逻辑移入 `PlaybackStatsService`，UI 只留对话框与提示。
- **触碰文件**：`playback_mixin.py`、`playback_stats_service.py`。
- **步骤**：
  1. 通读 `_export_stats` 全文，识别"业务"（序列化结构、哈希、写文件）与"UI"（getSaveFileName、statusBar 提示、异常弹窗）。
  2. 在 `PlaybackStatsService` 新增公开方法 `export_to_file(self, dest: Path) -> dict`（返回导出摘要如曲目数/路径），**业务逻辑原样搬移**，内部对 `self._entries` 的访问合法（同类内）。
  3. `_export_stats` 改为：选路径 → 调 `export_to_file` → statusBar 提示；原 `stats_svc._entries` 私有越界访问随之消失（grep `_entries` 在 app/ui/ 下应零命中，`_lyrics_*` 除外）。
- **验收**：三命令全过 + B1 若已合入则其导出往返测试仍绿。
- **禁止**：改变导出 JSON 的任何字段结构（有外部格式规范 `docs/musearc_stats_import_format_spec.md`）。

---

### B6｜曲库拆分之一：LibraryCleaner（泳道A）

- **目标**：把数据清理职责拆出 `library_service.py`（约 199-468 行区域的 4 个方法 + 相关辅助）。
- **触碰文件**：`library_service.py`、新增 `app/services/library_cleaner.py`。
- **拆分模式（B7/B8 同款，务必一致）**：组合而非继承。
  ```python
  class LibraryCleaner:
      """曲库数据清理（缺失文件/失效歌词/去重/归一化）。组合持有 LibraryService，只调用其公开状态。"""

      def __init__(self, library: "LibraryService") -> None:
          self._library = library
  ```
  - 原 `LibraryService._drop_missing_tracks` / `_cleanup_missing_lyrics_paths` / `_deduplicate_tracks` / `_normalize_playlist_tracks` 四个方法体**原样搬移**到 Cleaner，体内对 `self.tracks` / `self.playlists` / `self._path_index` 等 library 状态的引用改为 `self._library.tracks` 等。
  - `LibraryService` 保留原方法名作为**委托门面**（`return self._cleaner._drop_missing_tracks()` 或直接内联调用），公开 API 零变化；`load_preloaded` / `deferred_cleanup` 两个调用入口不改。
  - `LibraryService.__init__` 末尾构造 `self._cleaner = LibraryCleaner(self)`。
- **验收**：三命令全过 + **B1 的曲库测试全绿**（这是本批的核心验收）。
- **禁止**：改方法逻辑；改 `LibraryService` 公开签名。

---

### B7｜曲库拆分之二：MusePlaylistImporter（泳道A）

- **目标**：拆出 Muse 歌单导入职责（`_import_muse_playlist_data` 约 190 行 + `import_folder` 的 Muse 分支 + `_resolve_muse_track_path` / `_generate_muse_playlist_id` / `_find_existing_muse_playlist` / `_find_track_by_source_fields` 辅助函数）。
- **触碰文件**：`library_service.py`、新增 `app/services/muse_playlist_importer.py`。
- **模式**：同 B6（组合持有 LibraryService + 委托门面）。`import_folder` 本身留在 `LibraryService`（公开 API），其中 Muse 分支委托给 `Importer.import_muse_playlist(...)`；注意 `import_folder` 两个分支近乎复制（约 850-880 行区域），**只做搬移不做合并**，合并留给未来人工批次。
- **验收**：三命令全过 + B1 测试全绿。若 B1 未覆盖 Muse 导入路径，在报告中列出"未测试区域"。

---

### B8｜曲库拆分之三：PlaylistExporter（泳道A）

- **目标**：拆出导出职责（`export_playlist_file` 约 130 行 + `_sanitize_export_name` / `_export_track_lyrics` / `_export_relpath`）。
- **触碰文件**：`library_service.py`、新增 `app/services/playlist_exporter.py`。
- **模式**：同 B6/B7。注意 `export_playlist_file(playlist_id, out_dir, playback_stats_service)` 把 stats 服务当参数传——搬移时保持参数不变（改成构造注入留给未来批次）。
- **验收**：三命令全过 + B1 导出往返测试全绿。

---

### B9｜dispatch_command 注册表化

- **目标**：`AppController.dispatch_command`（约 759 行起，23 个 `if cmd ==` 分支）改为注册表分发。
- **触碰文件**：仅 `app_controller.py`。
- **步骤**：
  1. 每个分支体原样提取为独立方法 `_cmd_<name>(self, payload: dict) -> dict`（如 `_cmd_ping`、`_cmd_play`、`_cmd_set_volume`）。分支体内的参数校验与错误返回**逐字保留**。
  2. `__init__`（或 `initialize_services` 前）构建 `self._command_handlers: dict[str, Callable[[dict], dict]] = {"ping": self._cmd_ping, ...}`，23 个命令键与现在的 `cmd == "<name>"` 完全一致（全小写）。
  3. `dispatch_command` 改为：归一化 cmd（保持现有 `.strip().lower()`）→ 查表 → 未命中返回与现在**完全相同**的 unknown 错误结构 → 命中则 `return handler(payload)`。
- **验收**：三命令全过，其中 `pytest tests/test_control_server.py -q`（30 个用例）是本批核心验收——它们直接覆盖协议分发。逐条 diff 确认 23 个命令的响应结构无变化。
- **禁止**：改变任何命令的响应 JSON 结构；合并相似分支。

---

### B10｜mixin 边界整理：窗口职责移入 windowing_mixin

- **目标**：`playback_mixin.py` 中的窗口行为方法（`_on_opacity_changed`、`_toggle_compact_lock`、`_toggle_always_on_top`，约 1269-1330 行区域，以 grep 为准）移到 `windowing_mixin.py`。
- **触碰文件**：两个 mixin。
- **步骤**：方法体原样剪切 → 粘贴到 `windowing_mixin` 类内（放在 `_toggle_compact_mode` 附近）→ grep 三个方法名确认无引用断裂（两个 mixin 都组合进 `MainWindow`，外部引用不受影响）→ 检查 `windowing_mixin.py` 顶部 import 是否缺 `QPoint` 等符号，缺则补。
- **验收**：三命令全过 + 人工启动冒烟：切换透明度/简洁锁定/置顶功能正常。
- **禁止**：修改方法体；调整 MRO（类声明顺序不动）。

---

### B11｜mixin 边界整理：导入/歌词职责移入 playback_mixin

- **目标**：`windowing_mixin.py` 中的业务方法 `_import_folder_as_playlist`、`_attach_lyrics_to_current_track`（约 1176-1235 行区域）移到 `playback_mixin.py`。`dropEvent` 是 Qt 事件重载，**留在** windowing_mixin，继续调用这两个方法（组合后仍在同一对象上）。
- **触碰文件**：两个 mixin。
- **步骤**：同 B10（剪切粘贴、补 import、grep 验证）。注意方法体里调用的 `controller.library_service.create_playlist / import_file` 引用随之移动，无需改动。
- **验收**：三命令全过 + 人工启动冒烟：拖拽音频文件入窗口可播放、拖拽歌词文件可附加。

---

### B12｜懒初始化属性显式化（泳道B收尾）

- **目标**：消灭 mixin 里的防御性 `hasattr(self, ...)` / `getattr(self, "...", ...)`——它们的根因是属性在别的类里"有时才被赋值"。
- **触碰文件**：`main_window_impl.py`、两个 mixin。
- **步骤**：
  1. grep 两个 mixin 中全部 `hasattr(self,` / `getattr(self, "`（当前约 24 处 + 8 处），列成清单。
  2. **白名单不动**：`getattr(self.controller.settings, ...)` / `getattr(self.settings, ...)`（对用户设置文件的防御是合法的）、`getattr(self.player, ...)`。
  3. 其余每一处：在 `MainWindow.__init__` 的状态区（"窗口交互状态"等注释块）补显式默认值声明（如 `self._last_random_seed: int | None = None`、`self.rich_title_bar: QWidget | None = None`），然后把 mixin 中的防御写法简化为直接属性访问。
  4. 逐处确认：显式默认值与原 getattr 的 default **完全一致**（False/None/0）。
- **验收**：三命令全过 + grep 复查：非白名单的 `hasattr(self,` / `getattr(self, "` 清零；人工启动冒烟（触发 eventFilter/模式切换/随机播放路径）。

---

### B13｜图标绘制样板收敛（需人工目检）

- **目标**：`main_window_helpers.py` 尾部 15 个 `_make_*_icon` 函数（约 1863 行至文件尾）共用同一套"QPixmap → 填充透明 → QPainter + Antialiasing → RoundCap 画笔"样板，收敛为一个高阶辅助。
- **触碰文件**：仅 `main_window_helpers.py`（若 B4 已合入，注意文件已变化）。
- **步骤**：新增私有辅助：
  ```python
  def _render_icon(size: int, draw: Callable[[QPainter], None]) -> QIcon:
      """按统一画布规格渲染矢量图标。draw 中只写具体的绘图指令。"""
  ```
  15 个函数各自保留，但函数体缩为 `return _render_icon(24, lambda p: <原绘图指令>)`。**绘图指令逐行保留**（坐标、画笔宽度、颜色全部不动）。
- **验收**：三命令全过 + **人工目检**：启动应用，核对播放模式图标、主题按钮、菜单图标与改造前肉眼无差异（此批次必须人工验收后才能合入）。
- **预期收益**：约 500 行 → 约 250 行。

---

## 5. 强模型专属任务（并发修复，弱模型禁做）

> **状态（2026-09-12）：S1、S2 已完成**（见"已完成项"表，勿重复实施）。S3 维持"无实际卡顿反馈不启动"。

### ~~S1｜core.py `_async_close_stream` 竞态修复~~ ✅ 已完成

- **问题**：`core/core.py` 中 `_async_close_stream`（约 705-746 行区域）保存 `old_output = self._output` 后起后台线程，`time.sleep(0.05)` 后调用 `backend.close()`。但 `_output` 实例从不被替换（始终同一个 `SoundDeviceOutputBackend`），若这 0.05s 内主线程 `play()` 已重新 `_ensure_stream_started().open()` 了新流，后台 close 会把**新流关掉**，表现为切歌后无声或流泄漏。
- **必读**：`core/core.py` 全文，重点 `_async_close_stream`、`_close_output_in_background`、`play`、`_ensure_stream_started`、`load`；`core/output.py` 的 `open/close`。
- **推荐方向**（可论证后偏离，需在报告说明理由）：close 不再作用于"后端对象"，而是**捕获具体要关的 stream 句柄**，并在锁内核对"当前 `_stream` 是否仍是被关的那个"，不是则跳过关闭；或改为同步 close（评估 sounddevice `close()` 的耗时上限后再定）。
- **验收**：代码评审通过 + 手工压测：脚本内连续快速 `load()`/`play()`/`stop()` 数十轮，每轮后断言 `is_playing` 与输出状态一致、无异常。写一个 `tests/` 下的可重复回归脚本（若无法稳定复现竞态，写成压力测试亦可）。

### ~~S2｜core.py seek TOCTOU + start_sec 丢失~~ ✅ 已完成

- **问题 1**：`seek()` 锁内决策 → 释放锁 → `load_streaming` 重新加锁，两段之间若发生 `load()` 换曲会用旧决策加载错对象（单线程 UI 不触发，但内核宣称可复用）。
- **问题 2**：`load_streaming` 时长未知时的回退 `return self.load(source, start_sec=0.0)` 把调用方传入的 `start_sec` 静默丢弃。
- **修复方向**：问题 2 是一行修复（透传 `start_sec`），先做；问题 1 把"决策 + 执行"收敛到同一次持锁区间，或锁内重验 `_source_path` 未变。
- **验收**：代码评审 + 现有测试全绿；为问题 2 补一个能构造的单元测试（若需要真实音频文件，放 `testFile/`（已 gitignore）并在测试中条件跳过）。

### S3（可选）｜音频回调持锁插值优化

`_audio_callback` 在 RLock 内做逐声道 `np.interp`（变速率路径）。方向：锁内只拷贝缓冲视图，锁外插值。仅在收到实际卡顿反馈后做，无问题不启动。

---

## 6. 暂不排期清单（禁止任何批次顺手处理）

以下为已知但**未排期**的问题，留给人工决策，agent 不得触碰：

- `QCoreApplication.processEvents()` 反模式（playback_mixin 2 处）——涉及事件时序，需人工评估。
- `nativeEvent` 在 `main_window_impl.py` 与 `windowing_mixin.py` 双定义（MRO 透传）——涉及无边框窗口行为。
- 状态栏提示 `statusBar().showMessage` 约 70 处、对话框打开模板 4 处、`blockSignals` 手工模板——收益低、噪音大。
- `import_folder` 两个复制粘贴分支的合并——应与 B7 之后的语义审阅一起做。
- `app_controller` 对 `player_service._current_playlist_id` 等私有成员的越界写入——需先设计 PlayerService 公开接口。
- `app/ui` 与 `app/services` 中公开可变字典 `tracks`/`playlists` 的封装收紧——依赖 B6-B8 完成后的接口梳理。

---

## 7. 编排者备忘

- 每批合入后在本文件"已完成项"表登记（批次号 + 日期 + 一句话结果），供后续 agent 感知进度。
- 行号会随批次推进漂移：**给 agent 派单时提醒"以符号名定位"**。
- B1 完成前不得启动 B6/B7/B8；B13 合入前必须完成人工目检；S1/S2 建议排在 B3 之后、与泳道并行。
- 全部批次完成后建议跑一次完整人工冒烟（播放/切歌/导入/导出/协议 ping），并考虑删除本文件或归档到 `docs/` 外。
