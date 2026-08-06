---
name: build-release
description: >
  使用 tools/export_build.py 构建 MusePlayer 便携式运行时包（嵌入式 Python + PySide6 裁剪）。
  触发词：打包、构建、发版、发布新版本、build、release
---

# 构建与发版 (build-release)

## 触发词
打包、构建、发版、发布新版本、build、release

## 概述
使用 `tools/export_build.py` 构建 MusePlayer 便携式运行时包。构建方式是 **嵌入式 Python + PySide6 模块裁剪**（非 PyInstaller），产物输出到 `.build/` 目录，可直接分发。

## 前置条件
- Python 3.12+ 开发环境（用于运行构建脚本本身）
- 项目根目录下存在 `requirements.txt`、`app/version.py`、`tools/export_build.py`
- 网络可访问 python.org（首次构建需下载嵌入式 Python 3.11.9）
- `app/version.py` 中的 `APP_VERSION` 已更新为目标发版版本号

## 工作流

### 1. 构建前检查

按 `docs/release_checklist.md` 的"构建前"清单逐项确认：

- [ ] `app/version.py` 中的 `APP_VERSION` 已更新（如 `1.2.0`）。**版本号决定产物目录名**（`portable_runtime_v{APP_VERSION}`），发版前必须改对
- [ ] `requirements.txt` 中的依赖版本正确。**requirements.txt 内容变化会使运行时缓存失效**（以 SHA256 为缓存键），需要重新下载/安装依赖
- [ ] 所有新功能在 `docs/` 目录有对应文档
- [ ] 所有代码变更已同步更新到相关文档（接口文档、架构文档、格式文档）
- [ ] `AGENTS.md` 中的项目结构、关键约束与当前代码一致
- [ ] `.trae/rules/project_rules.md` 中的检查清单仍适用

### 2. 执行构建

构建命令：

```bash
python tools/export_build.py --mode all
```

**命令参数**：

| 参数 | 取值 | 默认 | 说明 |
|------|------|------|------|
| `--mode` | `all` / `code` / `runtime` | `all` | `code` 仅构建代码包；`runtime` 仅构建运行时；`all` 两者都构建 |
| `--runtime-python` | 语义化版本号 | `3.11.9` | 嵌入式 Python 版本（**注意：这是运行时 Python 版本，与开发环境 Python 3.12+ 不同**） |
| `--force-download` | 标志 | 未设置 | 设置时忽略缓存强制重新下载嵌入式 Python 与依赖 |

**典型场景**：

```bash
# 完整构建（首次或版本号变更后）
python tools/export_build.py --mode all

# 仅构建代码包（快速验证代码裁剪）
python tools/export_build.py --mode code

# 强制刷新运行时缓存（requirements.txt 变更或怀疑缓存损坏时）
python tools/export_build.py --mode runtime --force-download

# 指定其他嵌入式 Python 版本
python tools/export_build.py --mode all --runtime-python 3.12.1
```

### 3. 构建产物路径

| 模式 | 产物路径 | 说明 |
|------|---------|------|
| `code` | `.build/minimal_code_v{APP_VERSION}/` | 最小代码包（仅 `app/`、`core/`、`main.py`、`requirements.txt`、`icon.ico`） |
| `runtime` | `.build/portable_runtime_v{APP_VERSION}/` | 便携式运行时包（嵌入式 Python + 依赖 + 代码，可直接分发） |
| 缓存 | `.build/_cache/configured_python-{ver}-reqs{hash}/` | 运行时缓存，下次构建命中可跳过下载 |
| 包内目录 | `{output_dir}/MusePlayer_v{APP_VERSION}/` | 便携包内代码 bundle 目录名 |

### 4. 监控构建日志

构建过程中关注以下关键日志：

- `[RUN]` — 正在执行的子命令
- `[CACHE HIT] configured runtime: {cache_key}` — 命中缓存，跳过下载
- `[CACHE SAVE] configured runtime -> {cache_key}` — 保存新缓存
- `[TRIM] PySide6 modules detected: [...]` — 自动扫描到的 PySide6 模块
- `[TRIM] Keeping modules: [...]` / `[TRIM] Keeping DLLs: [...]` — 保留的模块与 DLL
- `[TRIM] Removed N items, saved X.X MB` — 裁剪节省的空间，通常应 **70%+**

### 5. 构建后验证

按 `docs/release_checklist.md` 的"构建后验证"清单逐项确认（关键项）：

- [ ] 在 `.build/portable_runtime_v{APP_VERSION}/` 目录运行 `start.bat`，确认应用正常启动
- [ ] 播放一首歌曲，确认音频输出正常
- [ ] 打开设置对话框，确认所有设置项可正常修改和保存
- [ ] 切换播放模式（单曲循环/歌单循环/随机），确认功能正常
- [ ] 拖入歌词文件，确认歌词显示正常
- [ ] 如有日语歌词，确认注音/罗马音/翻译分行显示
- [ ] **关闭应用后重新打开，确认会话恢复正常**（进度正确，不是回到几秒）
- [ ] **切换日间/夜间主题，确认歌曲列表不会多余刷新**
- [ ] **在设置中切换输出设备，确认播放不中断**
- [ ] 检查 `data/crashlogs/` 目录，确认无异常崩溃日志
- [ ] 检查 `data/logs/` 目录，确认同一次运行只生成一个日志文件

### 6. 发版

- [ ] 将 `.build/portable_runtime_v{APP_VERSION}/` 目录打包为 zip
- [ ] 在 Release 页面附上 zip 和更新说明
- [ ] 更新 `CHANGELOG.md`（如存在）
- [ ] 打 git tag（如 `v1.2.0`），与 `APP_VERSION` 一致

## 关键规则

- **构建工具是嵌入式 Python + PySide6 裁剪，非 PyInstaller**：`tools/export_build.py` 下载 python.org 官方嵌入式 Python 包，pip install 依赖后扫描 import 裁剪 PySide6，不打包字节码
- **运行时缓存键**：`configured_python-{runtime_python}-reqs{requirements_hash}`。`requirements.txt` 改变 → SHA256 改变 → 缓存失效 → 自动重新下载安装。缓存位于 `.build/_cache/`
- **`--force-download` 会清空并重建运行时缓存**：仅在 requirements 变更、Python 版本切换或缓存损坏时使用，正常发版无需
- **PySide6 模块裁剪**：`_scan_pyside6_imports` 扫描 `app/` 与 `core/` 源码中的 `import PySide6.xxx`，自动保留实际用到的模块。新增 PySide6 模块依赖（如 QtWebSockets、QtSql）会被自动检测
- **隐式 DLL 依赖需手动维护**：如有运行时通过非 import 方式加载的 PySide6 DLL，必须在 `_PYSIDE6_TRIM_RUNTIME_DEPS` 中手动添加对应 DLL 名，否则裁剪后运行时崩溃
- **Qt 插件目录需手动维护**：`_PYSIDE6_TRIM_KEEP_PLUGIN_DIRS` 控制保留哪些插件目录，新增 Qt 插件依赖时需添加对应目录名
- **裁剪节省空间应达 70%+**：若 `[TRIM] saved X.X MB` 远低于此比例，检查 `_scan_pyside6_imports` 是否漏检，或新引入了大体积依赖
- **`APP_VERSION` 是产物路径的一部分**：版本号未改会导致旧产物被覆盖。发版前必须先改 `app/version.py`
- **嵌入式 Python 默认 3.11.9，与开发环境 Python 3.12+ 不同**：如需切换嵌入式版本，用 `--runtime-python` 指定，并验证依赖在该版本下可用
- **`CODE_ITEMS` 白名单**：代码包仅包含 `app`、`core`、`main.py`、`requirements.txt`、`icon.ico`。新增需分发的顶层文件/目录时，必须同步修改 `tools/export_build.py` 中的 `CODE_ITEMS` 列表
- **构建后必须验证会话恢复与输出设备切换**：这两项是音频内核相关的高风险功能，裁剪后可能因缺失 DLL 失效

## 依赖

| 依赖 | 路径/接口 | 说明 |
|------|----------|------|
| Python 3.12+ | 开发环境 | 运行构建脚本本身 |
| 构建脚本 | `tools/export_build.py` | 核心构建工具 |
| requirements.txt | 项目根目录 | 依赖清单，SHA256 作为缓存键 |
| 版本号 | `app/version.py::APP_VERSION` | 决定产物路径名 |
| 检查清单 | `docs/release_checklist.md` | 完整发版验证清单 |
| 网络访问 | python.org | 首次构建下载嵌入式 Python |

## 输入/输出
- 输入：`--mode`、`--runtime-python`、`--force-download` 参数，当前 `APP_VERSION`
- 输出：`.build/portable_runtime_v{APP_VERSION}/` 便携式运行时包（可直接 zip 分发）
