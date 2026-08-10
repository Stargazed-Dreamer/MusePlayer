# 功能变更检查清单

> 每次新增功能、修改模块、重构代码后，按此清单逐项检查。
> 确保代码和文档始终同步。

## 代码变更

- [ ] 如果新增了模块/文件，是否在 `AGENTS.md` 项目结构中更新？
- [ ] 如果修改了数据模型（`app/models/entities.py`），是否同步更新了所有使用该模型的代码？
- [ ] 如果修改了运行时控制命令（`dispatch_command`），是否同步更新了对应的 Skill 文件？
- [ ] 如果新增了错误处理，是否在对应 Skill 的关键规则中记录？
- [ ] 是否有硬编码的路径、端口、魔法数字需要提取为配置？
- [ ] 代码中是否出现硬编码的本机绝对路径（`C:\Users\`、`/home/<user>`、`F:\codex\` 等）？必须改为基于 `Path(__file__)` 或 `Settings` 的动态路径

## 文档更新

- [ ] 如果新增/修改了 Skill，是否在 `_index.md` 中注册/更新？
- [ ] 如果发现了新的坑或陷阱，是否写入 `AGENTS.md` 关键约束？
- [ ] 如果修改了 TCP 控制协议（新增/修改命令），是否更新 `player-control.md` 或 `library-manage.md`？
- [ ] 如果修改了配置项，是否同步更新 `app/models/entities.py` 的 `Settings` 模型与 `app/ui/settings_dialog.py`？（项目使用 `data/settings.json`，无 TOML 配置模板）

## 配置变更

- [ ] 如果新增了配置项，是否在 `Settings` 模型（`app/models/entities.py`）和设置对话框（`app/ui/settings_dialog.py`）中同步？
- [ ] 配置默认值是否合理？（参考 `docs/architecture_design_cn.md` 的 Settings 字段一览表）

## 隐私检查（每次提交前必看）

> 项目已公开。任何进入仓库的内容都会被外界看到。

- [ ] 本次提交是否包含运行时数据文件（`data/*.json`、`*.log`、`crash.txt`、`*.muse_stats.json`）？这些必须留在 `.gitignore` 覆盖范围内
- [ ] 本次提交是否包含开发笔记/对话存档（`todo.txt`、`对话存档.txt`、`*存档*.txt`）？
- [ ] 本次提交是否包含测试素材（`testFile/`、`test*`、`_library_test_*`）？
- [ ] 本次提交是否包含构建产物（`.build/`、`dist/`、`__pycache__/`、`*.pyc`）？
- [ ] 新增的日志/转储文件是否使用 `.log` 后缀或已加入 `.gitignore`？
- [ ] 新增的运行时数据文件是否落在 `data/` 目录下（而非项目根目录或其他位置）？
- [ ] 错误堆栈/日志中是否包含本机路径？这些文件必须可被 gitignore
- [ ] 代码/文档/提交信息中是否泄露了个人邮箱、手机号、QQ/微信号、真实姓名？
- [ ] 代码中是否硬编码了密钥、Token、API key、数据库密码？
- [ ] 新增依赖是否会在用户主目录（`~/.config/`、`%APPDATA%`）写文件？如是，文档中是否说明

## 跨平台兼容性检查

> 目标：Windows 一等公民，macOS / Linux 实验性支持。新代码避免不必要的平台绑定。

- [ ] 新增的 Windows 专有 API 调用（`ctypes.windll`、`winreg`、`comtypes`、`RegisterHotKey` 等）是否包裹在 `if sys.platform.startswith("win"):` 守卫内，或用 `try/except ImportError` 软导入？
- [ ] 新增依赖若仅 Windows 可用，是否在 `requirements.txt` 和 `pyproject.toml` 中加了 `; sys_platform == 'win32'` 环境标记？
- [ ] 非 Windows 平台启动是否会因 `ImportError` 崩溃？必须能正常启动并降级
- [ ] 路径处理是否使用 `pathlib.Path` 或 `os.path.join`？是否硬编码了 `\\` 分隔符？
- [ ] 调用外部程序（`subprocess.Popen(["explorer.exe", ...])` 等）是否按 `sys.platform` 分发（Windows `explorer.exe` / macOS `open -R` / Linux `xdg-open`）？
- [ ] 新增 `.bat` 启动脚本时是否同步提供了 `.sh` 版本？
- [ ] `pyproject.toml` classifiers 是否仍列出 Windows / Linux / macOS 三平台？
- [ ] 是否留下了未使用的 Windows 常量/死代码？应及时删除避免误导

## Git

- [ ] `.gitignore` 是否排除了新增的敏感/临时文件？
- [ ] 提交信息是否准确描述了变更内容？
- [ ] 提交前是否运行 `git status` 确认无意外文件（尤其根目录的 `.json` / `.log` / `.txt`）？

## 测试

- [ ] 应用能否正常启动（`python main.py`）？
- [ ] 运行时控制接口能否正常连接（`ping` 命令）？
- [ ] 新增功能是否可验证？
- [ ] 已有功能是否仍然正常？

## MusePlayer 专项

- [ ] 如果修改了 `core/` 模块，是否确认音频播放不受影响？
- [ ] 如果修改了 UI 组件，是否确认日间/夜间主题都正常？
- [ ] 如果修改了数据持久化格式，是否兼容已有数据文件？
- [ ] 如果修改了播放器服务，是否确认会话恢复功能正常？

## 公开发布前清单（每次 release 前执行）

- [ ] `data/` 目录下所有运行时文件（session/library/settings/playback_stats/logs/crashlogs/runtime_errors.log）已物理删除或清空
- [ ] `.build/` 目录已删除（可由 `build.bat` 重新生成）
- [ ] `testFile/` 已删除（测试素材不入库）
- [ ] 根目录下的 `*.muse_stats.json`、`crash.txt`、`todo.txt`、`对话存档.txt` 已删除
- [ ] 全项目 `__pycache__/` 已清理
- [ ] `git status` 确认工作区无意外文件将被提交
- [ ] README / CHANGELOG / AGENTS.md 中无本机路径或个人信息泄露
