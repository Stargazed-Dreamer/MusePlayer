from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.version import APP_VERSION  # noqa: E402

PROJECT_NAME = "MusePlayer"
BUILD_ROOT = PROJECT_ROOT / ".build"
CACHE_DIR = BUILD_ROOT / "_cache"
CODE_EXPORT_DIR = BUILD_ROOT / f"minimal_code_v{APP_VERSION}"
RUNTIME_EXPORT_DIR = BUILD_ROOT / f"portable_runtime_v{APP_VERSION}"
BUNDLE_DIRNAME = f"{PROJECT_NAME}_v{APP_VERSION}"

CODE_ITEMS = [
    "app",
    "core",
    "main.py",
    "requirements.txt",
    "icon.ico",
]

COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".pytest_cache",
    ".mypy_cache",
)


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    """执行系统命令。

    参数：
        cmd (list[str]): 要执行的命令列表。
        cwd (Path | None, optional): 工作目录路径，默认为None。

    返回：
        None
    """
    # 打印运行命令的提示
    print(f"[RUN] {' '.join(cmd)}")
    # 使用subprocess.run执行命令，设置工作目录并检查错误
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def clean_dir(path: Path) -> None:
    """清理指定目录：如果目录存在则删除，然后重新创建。

    Args:
        path (Path): 需要清理的目录路径。

    Returns:
        None
    """
    if path.exists():
        shutil.rmtree(path)  # 检查目录是否存在，如果存在则递归删除
    path.mkdir(parents=True, exist_ok=True)  # 创建目录，parents=True表示创建多层目录，exist_ok=True表示目录已存在时不报错


def copy_code_bundle(dst_case_dir: Path) -> Path:
    """将指定代码包复制到目标案例目录中。

    此函数用于将项目中的代码包（由CODE_ITEMS定义）复制到目标案例目录的子目录中。
    复制过程会处理目录和文件，忽略指定的文件（COPY_IGNORE），并保留文件元数据。

    Args:
        dst_case_dir (Path): 目标案例目录的路径，复制后的代码包将存放在该目录下的BUNDLE_DIRNAME子目录中。

    Returns:
        Path: 返回代码包复制后的根目录路径（即目标案例目录下的BUNDLE_DIRNAME目录路径）。
    """
    # 构建代码包的根目录路径（目标案例目录下的BUNDLE_DIRNAME子目录）
    bundle_root = dst_case_dir / BUNDLE_DIRNAME
    # 创建代码包根目录，如果父目录不存在则一并创建，如果目录已存在则忽略
    bundle_root.mkdir(parents=True, exist_ok=True)
    # 遍历CODE_ITEMS中定义的每个项目（文件或目录）
    for item in CODE_ITEMS:
        # 构建源项目的完整路径
        src = PROJECT_ROOT / item
        # 构建目标项目的完整路径
        dst = bundle_root / item
        # 如果源项目不存在，则跳过本次循环，继续处理下一个项目
        if not src.exists():
            continue
        # 如果源项目是目录
        if src.is_dir():
            # 复制整个目录树，使用COPY_IGNORE指定忽略的文件，如果目标目录已存在则合并内容
            shutil.copytree(src, dst, ignore=COPY_IGNORE, dirs_exist_ok=True)
        else:
            # 如果源项目是文件
            # 确保目标文件的父目录存在，如果不存在则创建
            dst.parent.mkdir(parents=True, exist_ok=True)
            # 复制文件并保留文件元数据（如修改时间、权限等）
            shutil.copy2(src, dst)
    # 返回代码包复制后的根目录路径
    return bundle_root


def _ansi_encoding() -> str:
    """根据操作系统返回合适的ANSI编码名称。

    功能：检测当前操作系统，Windows系统返回"mbcs"编码，其他系统返回"cp1252"编码。
    参数：无。
    返回值：str - 表示ANSI编码名称的字符串。
    """
    if os.name == "nt":  # 检查操作系统是否为Windows（nt代表Windows NT系列）
        return "mbcs"  # Windows系统下返回多字节字符集（mbcs）编码，用于处理ANSI字符
    return "cp1252"  # 其他系统（如Linux/macOS）下返回西欧Windows编码cp1252作为默认ANSI编码


def write_bat_ansi(path: Path, content: str) -> None:
    """将字符串内容写入到指定路径的文件中，使用ANSI编码，并确保换行符为Windows格式（\r\n）。

    参数:
    path (Path): 文件路径。
    content (str): 要写入的内容。

    返回值:
    None: 无返回值。
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")  # 标准化换行符为Windows格式
    path.write_bytes(normalized.encode(_ansi_encoding(), errors="replace"))  # 使用ANSI编码写入文件，遇到无法编码的字符时替换


def write_source_launcher(bundle_root: Path) -> None:
    """这个函数用于生成并写入一个Windows批处理启动器文件。

    参数:
    bundle_root (Path): 包含main.py的根目录路径。

    返回:
    None: 无返回值，但会在bundle_root目录下创建start.bat文件。
    """
    # 使用textwrap.dedent去除字符串的额外缩进，创建批处理文件内容
    launcher = textwrap.dedent("""\
        @echo off
        setlocal
        cd /d "%~dp0"
        where pythonw >nul 2>nul
        if %errorlevel% neq 0 (
            echo pythonw not found in PATH.
            exit /b 1
        )
        start "" /B pythonw ".\\main.py"
        exit /b 0
    """)
    # 调用write_bat_ansi函数将launcher内容写入bundle_root目录下的start.bat文件
    write_bat_ansi(bundle_root / "start.bat", launcher)


def write_runtime_launcher(bundle_root: Path) -> None:
    """
    生成Windows批处理启动器脚本，用于启动Python应用程序
    
    功能：在指定目录下创建两个批处理文件
        1. start.bat - 常规启动器（静默运行）
        2. start_debug.bat - 调试启动器（显示控制台）
    
    参数：
        bundle_root (Path): 应用程序包的根目录路径
    
    返回值：
        None: 该函数不返回任何值，但会在文件系统中创建两个批处理文件
    """
    
    # 定义常规启动器脚本内容（静默模式，无控制台窗口）
    launcher = textwrap.dedent("""\
        @echo off
        setlocal
        cd /d "%~dp0"  # 切换到脚本所在目录
        # 检查是否存在pythonw.exe（无控制台窗口的Python解释器）
        if exist ".\\python\\pythonw.exe" (
            start "" /B ".\\python\\pythonw.exe" ".\\main.py"  # 后台启动Python程序
            exit /b 0  # 成功退出
        )
        # 如果没有pythonw.exe，尝试使用python.exe
        if exist ".\\python\\python.exe" (
            start "" /B ".\\python\\python.exe" ".\\main.py"  # 后台启动Python程序
            exit /b 0  # 成功退出
        )
        echo Embedded python executable not found.  # 提示未找到Python解释器
        exit /b 1  # 错误退出码
    """)
    
    # 写入常规启动器批处理文件
    write_bat_ansi(bundle_root / "start.bat", launcher)
    
    # 定义调试启动器脚本内容（显示控制台窗口，便于查看输出）
    debug_launcher = textwrap.dedent("""\
        @echo off
        setlocal
        cd /d "%~dp0"  # 切换到脚本所在目录
        # 检查是否存在python.exe
        if exist ".\\python\\python.exe" (
            ".\\python\\python.exe" ".\\main.py"  # 直接运行Python程序（显示控制台）
            pause  # 暂停等待用户按键
            exit /b %errorlevel%  # 以Python程序的退出码退出
        )
        echo Embedded python executable not found.  # 提示未找到Python解释器
        pause  # 暂停等待用户按键
        exit /b 1  # 错误退出码
    """)
    
    # 写入调试启动器批处理文件
    write_bat_ansi(bundle_root / "start_debug.bat", debug_launcher)


def write_environments_file(
    bundle_root: Path,
    *,
    artifact_type: str,
    runtime_python_version: str | None = None,
    runtime_freeze: str | None = None,
) -> None:
    """将环境信息写入指定目录下的 environments.txt 文件。

    功能：
        收集项目元数据、主机环境信息以及可选的运行时信息，生成一个文本文件，用于记录构建和部署环境。

    参数：
        bundle_root (Path): 捆绑根目录路径，输出文件将存放在此目录下。
        artifact_type (str): 工件类型，例如 'wheel' 或 'sdist'，用于标识构建产物。
        runtime_python_version (str | None, 可选): 运行时Python版本，如果提供则添加到环境中。默认为 None。
        runtime_freeze (str | None, 可选): 运行时pip冻结信息，通常通过 pip freeze 生成。默认为 None。

    返回值：
        None: 无返回值。
    """
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").strip()
    lines = [
        f"project={PROJECT_NAME}",
        f"version={APP_VERSION}",
        f"artifact_type={artifact_type}",
        f"generated_at={datetime.now().isoformat(timespec='seconds')}",
        f"build_host_python={sys.version.replace(chr(10), ' ')}",
        f"build_host_platform={platform.platform()}",
        "",
        "[requirements.txt]",
        requirements,
    ]
    if runtime_python_version:
        lines.extend(["", "[runtime_python]", runtime_python_version])
    if runtime_freeze is not None:
        lines.extend(["", "[runtime_pip_freeze]", runtime_freeze.strip()])
    (bundle_root / "environments.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def download(url: str, target_file: Path, *, force: bool = False) -> Path:
    """下载文件到指定路径，支持缓存跳过和强制重新下载。

    Args:
        url (str): 需要下载的文件的网络地址。
        target_file (Path): 文件要保存到的本地目标路径。
        force (bool, optional): 是否强制重新下载，即使文件已存在。默认为 False。

    Returns:
        Path: 返回下载完成后（或缓存命中的）文件路径。
    """
    # 创建目标文件的父目录，如果父目录不存在则递归创建
    target_file.parent.mkdir(parents=True, exist_ok=True)
    # 检查文件是否已存在且未设置强制下载标志，如果是则直接返回缓存路径
    if target_file.exists() and not force:
        print(f"[CACHE HIT] {target_file.name} (already downloaded)")
        return target_file
    # 文件不存在或需要强制下载，开始执行下载
    print(f"[DL] {url}")
    # 使用 urllib 库从给定的 URL 下载文件到目标路径
    urllib.request.urlretrieve(url, target_file)
    # 下载完成，返回保存的文件路径
    return target_file


# ======================== NEW: runtime cache helpers ========================

def _requirements_hash() -> str:
    """Return a short hash of requirements.txt content for cache key."""
    req_path = PROJECT_ROOT / "requirements.txt"
    if not req_path.exists():
        return "none"
    return hashlib.sha256(req_path.read_bytes()).hexdigest()[:12]


def _configured_runtime_cache_key(runtime_python: str) -> str:
    """Build a unique cache dir name: Python version + requirements hash.

    When requirements.txt changes the hash changes, so a stale cache is
    automatically invalidated and a fresh runtime is built.
    """
    return f"configured_python-{runtime_python}-reqs{_requirements_hash()}"


def _prepare_configured_runtime(
    runtime_python_dir: Path,
    runtime_python: str,
    *,
    force: bool = False,
) -> None:
    """Prepare a fully configured embedded Python in *runtime_python_dir*.

    Strategy
    --------
    1. Compute a cache key from the Python version + requirements.txt hash.
    2. If ``.build/_cache/<cache_key>`` already exists and *force* is false,
       **directly copy** the cached tree into *runtime_python_dir* and return
       immediately — no download, no pip install.
    3. Otherwise perform the full bootstrap (download embed zip, get-pip,
       install pip, install requirements), then **copy the result into the
       cache** so the next run can reuse it.
    """
    cache_key = _configured_runtime_cache_key(runtime_python)
    cached_runtime = CACHE_DIR / cache_key

    # ---- cache hit: just copy ----
    if cached_runtime.exists() and not force:
        print(f"[CACHE HIT] configured runtime: {cache_key}")
        print("[CACHE HIT] skipping embed download, get-pip, and pip install")
        if runtime_python_dir.exists():
            shutil.rmtree(runtime_python_dir)
        ignore_fn = _make_pyside6_trim_ignore()
        shutil.copytree(cached_runtime, runtime_python_dir, ignore=ignore_fn)
        return

    # ---- cache miss: full bootstrap ----
    embed_zip_name = f"python-{runtime_python}-embed-amd64.zip"
    embed_url = (
        f"https://www.python.org/ftp/python/{runtime_python}/{embed_zip_name}"
    )
    embed_zip_path = download(
        embed_url, CACHE_DIR / embed_zip_name, force=force
    )

    with zipfile.ZipFile(embed_zip_path, "r") as zf:
        zf.extractall(runtime_python_dir)

    configure_embedded_python(runtime_python_dir)

    python_exe = runtime_python_dir / "python.exe"
    if not python_exe.exists():
        raise RuntimeError("python.exe not found in embedded runtime")

    get_pip_path = download(
        "https://bootstrap.pypa.io/get-pip.py",
        CACHE_DIR / "get-pip.py",
        force=force,
    )
    run(
        [str(python_exe), str(get_pip_path), "--no-warn-script-location"],
        cwd=runtime_python_dir,
    )
    run(
        [str(python_exe), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=runtime_python_dir,
    )
    run(
        [
            str(python_exe), "-m", "pip", "install",
            "--prefer-binary", "--only-binary", ":all:",
            "-r", str(PROJECT_ROOT / "requirements.txt"),
        ],
        cwd=runtime_python_dir,
    )

    # ---- save to cache for next build ----
    print(f"[CACHE SAVE] configured runtime -> {cache_key}")
    if cached_runtime.exists():
        shutil.rmtree(cached_runtime)
    shutil.copytree(runtime_python_dir, cached_runtime)


# ======================== END NEW ========================


def configure_embedded_python(python_dir: Path) -> None:
    """配置嵌入式Python运行时的路径文件（._pth）。

    此函数确保嵌入式Python环境的路径文件包含必要的配置项：
    1. 指向父目录的路径 `..`
    2. 标准库 site-packages 路径 `Lib\site-packages`
    3. `import site` 指令
    如果这些配置项缺失，函数会自动添加它们。最后，函数还会确保 `Lib/site-packages` 目录存在。

    参数:
        python_dir (Path): 嵌入式Python运行时的目录路径。

    返回值:
        None: 此函数没有返回值，仅用于执行配置操作。
    """
    # 使用 glob 查找名为 python*._pth 的路径配置文件。
    pth_files = list(python_dir.glob("python*._pth"))
    if not pth_files:
        raise RuntimeError("python*._pth not found in embedded runtime")
    # 取第一个找到的 ._pth 文件进行操作。
    pth_file = pth_files[0]
    # 读取文件内容，并按行分割，同时去除每行末尾的换行符。
    rows = [
        line.rstrip("\r\n")
        for line in pth_file.read_text(encoding="utf-8").splitlines()
    ]
    # 创建一个列表，用于存储处理后的新行。
    new_rows: list[str] = []
    # 以下三个布尔标志用于记录是否已存在关键的配置项。
    has_parent_path = False
    has_site_packages = False
    has_import_site = False
    # 遍历原始文件的每一行。
    for row in rows:
        stripped = row.strip()
        # 检查并处理指向父目录的路径 `..`。
        if stripped == "..":
            has_parent_path = True
            new_rows.append("..")
            continue
        # 检查并规范化 `lib\site-packages` 路径为 `Lib\site-packages`。
        if stripped.lower() == "lib\\site-packages":
            has_site_packages = True
            new_rows.append("Lib\\site-packages")
            continue
        # 检查被注释的 `import site` 指令，并将其激活。
        if stripped.startswith("#") and stripped.replace("#", "", 1).strip() == "import site":
            has_import_site = True
            new_rows.append("import site")
            continue
        # 检查并记录已存在的 `import site` 指令。
        if stripped == "import site":
            has_import_site = True
            new_rows.append(row)
            continue
        # 其他行保持原样。
        new_rows.append(row)
    # 如果处理完所有行后，父目录路径 `..` 仍然缺失，则将其插入到 `.` 之后。
    if not has_parent_path:
        insert_at = 0
        for i, row in enumerate(new_rows):
            if row.strip() == ".":
                insert_at = i + 1
                break
        new_rows.insert(insert_at, "..")
    # 如果 site-packages 路径缺失，则将其插入到 `import site` 之前。
    if not has_site_packages:
        insert_at = len(new_rows)
        for i, row in enumerate(new_rows):
            if row.strip() == "import site":
                insert_at = i
                break
        new_rows.insert(insert_at, "Lib\\site-packages")
    # 如果 `import site` 指令缺失，则将其追加到文件末尾。
    if not has_import_site:
        new_rows.append("import site")
    # 将处理后的新行列表重新组合为字符串，并写入原文件。
    pth_file.write_text("\n".join(new_rows) + "\n", encoding="utf-8")
    # 确保 `Lib/site-packages` 目录存在，如果不存在则递归创建。
    (python_dir / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)


def build_minimal_code() -> Path:
    """构建最小代码包并返回打包路径。
    此函数执行一系列步骤来生成包含最小可运行代码的包。
    
    Args:
        无参数
    
    Returns:
        Path: 生成的代码包根目录路径
    """
    print("[STEP] build minimal code bundle")  # 打印构建步骤开始信息
    clean_dir(CODE_EXPORT_DIR)  # 清理代码导出目录，确保为全新构建环境
    bundle_root = copy_code_bundle(CODE_EXPORT_DIR)  # 将代码包复制到导出目录并获取根路径
    write_source_launcher(bundle_root)  # 在代码包中写入源代码启动器脚本
    write_environments_file(bundle_root, artifact_type="minimal_code")  # 写入环境配置文件，标记为最小代码类型
    return bundle_root  # 返回构建完成的代码包路径


def build_portable_runtime(runtime_python: str, *, force_download: bool = False) -> Path:
    """构建便携式运行时包。
    
    此函数创建一个自包含的便携式运行时包，主要用于Windows平台。
    它会清理目标目录，复制代码，准备Python运行时环境，并生成必要的启动文件和配置文件。
    
    Args:
        runtime_python (str): 用于构建便携式运行时的Python解释器路径或标识。
        force_download (bool, optional): 是否强制重新下载运行时。默认为False。
    
    Returns:
        Path: 生成的便携式运行时包的根目录路径。
    
    Raises:
        RuntimeError: 如果在非Windows平台调用，或运行时目录中找不到python.exe时抛出。
    """
    # 检查当前平台是否为Windows，因为便携式运行时导出目前仅支持Windows主机
    if not sys.platform.startswith("win"):
        raise RuntimeError("portable runtime export currently supports Windows host only")
    
    print("[STEP] build portable runtime bundle")
    
    # 清理运行时导出目录，为新的构建做准备
    clean_dir(RUNTIME_EXPORT_DIR)
    
    # 复制代码包到导出目录，并获取包的根路径
    bundle_root = copy_code_bundle(RUNTIME_EXPORT_DIR)

    # 创建运行时Python目录，用于存放Python运行时环境
    runtime_python_dir = bundle_root / "python"
    runtime_python_dir.mkdir(parents=True, exist_ok=True)

    # 准备配置好的运行时环境，如果指定则强制重新下载
    _prepare_configured_runtime(
        runtime_python_dir, runtime_python, force=force_download,
    )

    # 定义Python可执行文件路径，并验证其存在性
    python_exe = runtime_python_dir / "python.exe"
    if not python_exe.exists():
        raise RuntimeError("python.exe not found in embedded runtime")

    # 裁剪PySide6库，移除不必要的文件以减小包体积
    _trim_pyside6(runtime_python_dir)

    # 获取运行时Python版本信息
    runtime_version = subprocess.check_output(
        [str(python_exe), "-c", "import sys; print(sys.version.replace('\\n', ' '))"],
        text=True, cwd=str(bundle_root),
    ).strip()
    
    # 获取运行时已安装包列表
    runtime_freeze = subprocess.check_output(
        [str(python_exe), "-m", "pip", "freeze"],
        text=True, cwd=str(bundle_root),
    ).strip()

    # 生成运行时启动器脚本
    write_runtime_launcher(bundle_root)
    
    # 写入环境配置文件，包含运行时类型、版本和依赖信息
    write_environments_file(
        bundle_root,
        artifact_type="portable_runtime",
        runtime_python_version=runtime_version,
        runtime_freeze=runtime_freeze,
    )
    
    # 返回构建完成的便携式运行时包的根目录路径
    return bundle_root


_PYSIDE6_TRIM_SKIP_DIRS = frozenset({
    "__pycache__", "include", "typesystems", "scripts", "glue",
    "resources", "metatypes", "QtAsyncio", "qml", "translations",
})

_PYSIDE6_TRIM_KEEP_PLUGIN_DIRS = frozenset({
    "platforms", "styles", "imageformats", "iconengines",
    "multimedia", "networkinformation", "tls", "generic", "sqldrivers",
})

_PYSIDE6_TRIM_RUNTIME_DEPS = frozenset({
    "Qt6OpenGL", "Qt6OpenGLWidgets", "Qt6Svg", "Qt6SvgWidgets",
    "Qt6Sql", "Qt6MultimediaWidgets",
})


def _make_pyside6_trim_ignore() -> Callable[[str, list[str]], set[str]]:
    """生成一个用于 PySide6 裁剪的忽略规则函数。
    
    该函数创建一个闭包，用于在构建过程中根据扫描到的导入模块和运行时依赖，
    决定哪些文件和目录应该被忽略。
    
    Returns:
        Callable[[str, list[str]], set[str]]: 一个接受目录路径和内容列表，返回需要忽略的项的集合的函数。
    """
    # 定义要扫描的源代码目录
    source_dirs = [PROJECT_ROOT / "app", PROJECT_ROOT / "core", PROJECT_ROOT]
    # 扫描源码中的 PySide6 模块导入
    used_modules = _scan_pyside6_imports(source_dirs)
    # 保留扫描到的模块以及核心的 Qt 模块
    keep_modules = used_modules | {"QtCore", "QtGui", "QtWidgets"}
    # 初始化需要保留的 DLL 文件集合
    keep_dlls: set[str] = set()
    # 遍历需要保留的模块，收集它们对应的 DLL 文件名
    for mod in keep_modules:
        keep_dlls.update(_pyside6_module_dll_names(mod))
    # 添加 PySide6 运行时的直接依赖
    keep_dlls.update(_PYSIDE6_TRIM_RUNTIME_DEPS)

    # 构造 PySide6 安装目录的路径前缀（用于判断当前目录是否位于 PySide6 安装目录下）
    pyside6_dir_prefix = os.path.join("Lib", "site-packages", "PySide6")

    def _ignore(directory: str, contents: list[str]) -> set[str]:
        """决定在给定目录下忽略哪些文件和目录。
        
        Args:
            directory: 当前正在处理的目录路径。
            contents: 该目录下的文件和子目录列表。
        
        Returns:
            set[str]: 需要被忽略的文件和目录名的集合。
        """
        # 将目录路径中的反斜杠替换为正斜杠，以便统一比较
        norm = directory.replace("\\", "/")
        # 如果当前目录不在 PySide6 安装目录下，则不忽略任何内容
        if pyside6_dir_prefix.replace("\\", "/") not in norm:
            return set()

        # 初始化忽略集合
        ignored: set[str] = set()
        # 遍历目录下的每一项
        for name in contents:
            # 如果是预定义的跳过目录，则直接忽略
            if name in _PYSIDE6_TRIM_SKIP_DIRS:
                ignored.add(name)
                continue
            # 如果是插件目录，则需要进一步检查其子目录
            if name == "plugins":
                plugin_path = os.path.join(directory, name)
                # 遍历插件目录下的子目录（确保插件路径存在且是一个目录）
                for sub in os.listdir(plugin_path) if os.path.isdir(plugin_path) else []:
                    # 如果子目录不在保留的插件目录列表中，则忽略
                    if sub not in _PYSIDE6_TRIM_KEEP_PLUGIN_DIRS:
                        ignored.add(sub)
                continue
            # 如果是以 "Qt" 开头的项（通常是 Qt 模块目录），且不在需要保留的模块中，则忽略整个目录
            if name.startswith("Qt") and name not in keep_modules:
                full = os.path.join(directory, name)
                # 确保它是一个目录才忽略（避免误忽略同名的文件）
                if os.path.isdir(full):
                    ignored.add(name)
                continue
            # 处理 DLL 和 PYD 文件：提取不带扩展名的文件主干名
            name_lower = name.lower()
            if name_lower.endswith((".pyd", ".dll")):
                stem = name
                # 移除文件扩展名，得到主干名
                for ext in (".pyd", ".dll"):
                    if stem.endswith(ext):
                        stem = stem[: -len(ext)]
                        break
                # 检查主干名是否在保留的 DLL 集合或模块名中（也考虑带前缀的变体，如 "QtCore_..."）
                is_keep = any(stem == k or stem.startswith(k + "_") for k in keep_dlls | keep_modules)
                # 如果不在保留列表中，且不是 PySide6 或 shiboken 的核心文件，并且以 Qt 相关前缀开头，则忽略
                if not is_keep and not stem.startswith("pyside6") and not stem.startswith("shiboken"):
                    if stem.startswith("Qt3D") or stem.startswith("Qt6") or stem.startswith("Qt"):
                        ignored.add(name)

        # 返回需要忽略的项的集合
        return ignored

    # 返回配置好的忽略函数
    return _ignore


def _scan_pyside6_imports(source_dirs: list[Path]) -> set[str]:
    """
    扫描指定目录下的Python源文件，找出所有PySide6模块的导入。

    功能：
        遍历给定的源代码目录（及其子目录），分析Python文件中的import语句，
        提取并返回所有被导入的PySide6模块名称。

    参数：
        source_dirs (list[Path]): 需要扫描的源码根目录路径列表。

    返回：
        set[str]: 一个包含所有被导入的PySide6模块名称的集合。
                  例如 {'QtCore', 'QtWidgets', 'QtGui'}。
    """
    # 编译正则表达式，用于匹配两种PySide6导入模式：
    # 1. from PySide6.<模块> import ... (捕获组1: 模块名)
    # 2. import PySide6.<模块> (捕获组2: 模块名)
    import_re = re.compile(r"from\s+PySide6\.(\w+)\s+import|import\s+PySide6\.(\w+)")
    # 定义需要跳过的非源码目录集合，例如构建目录、虚拟环境等
    skip_dirs = {".build", ".venv", "__pycache__", "node_modules"}
    # 用于存储发现的所有PySide6模块名的集合
    modules: set[str] = set()
    # 遍历所有给定的源码根目录
    for src_dir in source_dirs:
        # 使用rglob递归查找所有.py文件
        for py_file in src_dir.rglob("*.py"):
            # 检查文件路径的任何组成部分是否在跳过目录列表中
            if any(part in skip_dirs for part in py_file.parts):
                continue
            # 尝试读取文件内容，使用UTF-8编码，忽略编码错误
            try:
                text = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                # 如果读取文件时发生操作系统错误（如权限问题），则跳过该文件
                continue
            # 使用正则表达式在文件文本中查找所有匹配的导入模式
            for m in import_re.finditer(text):
                # 获取匹配到的模块名（来自两个捕获组中的一个）
                mod = m.group(1) or m.group(2)
                # 如果模块名有效且首字母大写（PySide6模块命名规范），则添加到集合中
                if mod and mod[0].isupper():
                    modules.add(mod)
    # 返回所有找到的PySide6模块名的集合
    return modules


def _pyside6_module_dll_names(module_name: str) -> set[str]:
    """根据传入的PySide6模块名，返回该模块及其相关依赖模块的DLL文件名集合。

    参数:
        module_name (str): PySide6模块名，例如 "QtWidgets", "QtGui" 等。

    返回值:
        set[str]: 包含主模块名和相关依赖模块DLL文件名的集合。
    """
    # 初始化集合，包含传入的模块名本身和基于它生成的Qt6版本DLL名（去掉前两个字符并加上"Qt6"前缀）
    names = {module_name, f"Qt6{module_name[2:]}"}
    # 如果模块是QtWidgets，则额外添加多媒体、OpenGL和SVG相关的widgets模块
    if module_name == "QtWidgets":
        names.update({"Qt6MultimediaWidgets", "Qt6OpenGLWidgets", "Qt6SvgWidgets"})
    # 如果模块是QtGui，则额外添加OpenGL和SVG模块
    if module_name == "QtGui":
        names.update({"Qt6OpenGL", "Qt6Svg"})
    # 如果模块是QtMultimedia，则额外添加多媒体widgets模块
    if module_name == "QtMultimedia":
        names.add("Qt6MultimediaWidgets")
    return names


def _trim_pyside6(runtime_python_dir: Path, *, source_dirs: list[Path] | None = None) -> None:
    """精简PySide6库，删除未使用的模块和动态链接库，以减小运行时目录的大小。
    
    Args:
        runtime_python_dir: Path - 运行时Python目录，包含site-packages等。
        source_dirs: list[Path] | None - 源代码目录列表，用于扫描PySide6的导入。如果为None，则使用默认的项目目录。
    
    Returns:
        None
    """
    site_packages = runtime_python_dir / "Lib" / "site-packages"
    pyside6_dir = site_packages / "PySide6"
    if not pyside6_dir.is_dir():
        print("[TRIM] PySide6 not found, skipping trim")
        return

    if source_dirs is None:
        source_dirs = [PROJECT_ROOT / "app", PROJECT_ROOT / "core", PROJECT_ROOT]

    # 扫描源代码目录中的PySide6导入，获取使用的模块列表
    used_modules = _scan_pyside6_imports(source_dirs)
    # 核心模块必须保留，将它们加入保留集合
    keep_modules = used_modules | {"QtCore", "QtGui", "QtWidgets"}
    # 收集需要保留的动态链接库名称
    keep_dlls: set[str] = set()
    for mod in keep_modules:
        keep_dlls.update(_pyside6_module_dll_names(mod))
    # 将PySide6运行时的必要依赖也加入保留集合
    keep_dlls.update(_PYSIDE6_TRIM_RUNTIME_DEPS)

    print(f"[TRIM] PySide6 modules detected: {sorted(used_modules)}")
    print(f"[TRIM] Keeping modules: {sorted(keep_modules)}")
    print(f"[TRIM] Keeping DLLs: {sorted(keep_dlls)}")

    removed_count = 0
    removed_bytes = 0

    # 遍历PySide6目录下的所有条目
    for item in list(pyside6_dir.iterdir()):
        name = item.name
        name_lower = name.lower()

        # 处理子目录
        if item.is_dir():
            # 跳过明确标记为需要跳过的目录（如文档、示例等）
            if name in _PYSIDE6_TRIM_SKIP_DIRS:
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                shutil.rmtree(item, ignore_errors=True)
                removed_count += 1
                removed_bytes += size
                continue
            # 对plugins目录进行选择性精简
            if name == "plugins":
                for sub in list(item.iterdir()):
                    if sub.is_dir() and sub.name not in _PYSIDE6_TRIM_KEEP_PLUGIN_DIRS:
                        size = sum(f.stat().st_size for f in sub.rglob("*") if f.is_file())
                        shutil.rmtree(sub, ignore_errors=True)
                        removed_count += 1
                        removed_bytes += size
                continue
            # 删除以"Qt"开头但未在保留列表中的模块目录
            if name.startswith("Qt") and name not in keep_modules:
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                shutil.rmtree(item, ignore_errors=True)
                removed_count += 1
                removed_bytes += size
                continue

        # 处理动态链接库文件（.pyd和.dll）
        if item.is_file() and name_lower.endswith((".pyd", ".dll")):
            # 提取文件名的主要部分（去掉扩展名）
            stem = name
            for ext in (".pyd", ".dll"):
                if stem.endswith(ext):
                    stem = stem[: -len(ext)]
                    break
            # 检查是否需要保留该文件
            is_keep = False
            for keep in keep_dlls | keep_modules:
                # 如果文件名与保留项完全匹配，或者是保留项加上特定后缀（如"_amd64"）的变体，则保留
                if stem == keep or stem.startswith(keep + "_"):
                    is_keep = True
                    break
            # 如果不需要保留，且不是PySide6或Shiboken的核心文件，则尝试删除
            if not is_keep and not stem.startswith("pyside6") and not stem.startswith("shiboken"):
                # 仅删除以Qt开头的特定模块的动态库
                if stem.startswith("Qt3D") or stem.startswith("Qt6") or stem.startswith("Qt"):
                    try:
                        size = item.stat().st_size
                        item.unlink()
                        removed_count += 1
                        removed_bytes += size
                    except OSError:
                        pass

    # 如果删除了文件，打印节省的空间信息
    if removed_bytes > 0:
        mb = removed_bytes / (1024 * 1024)
        print(f"[TRIM] Removed {removed_count} items, saved {mb:.1f} MB")


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数，用于导出MusePlayer可分发产物。

    功能：
        该函数使用argparse模块解析命令行参数，返回一个命名空间对象。

    参数：
        无显式函数参数；命令行参数包括：
        --mode: 选择导出模式，可选值为"all", "code", "runtime"，默认"all"。
        --runtime-python: 指定运行时导出的嵌入式Python版本，默认"3.11.9"。
        --force-download: 标志，如果设置则忽略缓存并重新下载。

    返回值：
        argparse.Namespace对象，包含解析后的参数值。
    """
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(
        description="Export MusePlayer distributable artifacts"
    )
    # 添加--mode参数，指定导出模式
    parser.add_argument(
        "--mode", choices=["all", "code", "runtime"], default="all",
        help="code=minimal code set, runtime=portable runnable package, all=both",
    )
    # 添加--runtime-python参数，指定嵌入式Python版本
    parser.add_argument(
        "--runtime-python", default="3.11.9",
        help="embedded Python version for runtime export (e.g. 3.11.9)",
    )
    # 添加--force-download参数，强制重新下载
    parser.add_argument(
        "--force-download", action="store_true",
        help="ignore cache and re-download embedded Python/get-pip",
    )
    # 解析命令行参数并返回Namespace对象
    return parser.parse_args()


def main() -> int:
    """
    功能：根据命令行参数构建代码或运行时环境，并打印输出路径。

    参数：无显式参数，但通过parse_args()获取配置。

    返回值：始终返回0，表示执行成功。
    """
    args = parse_args()  # 解析命令行参数
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)  # 创建构建根目录，如果不存在则递归创建
    built: list[Path] = []  # 初始化一个列表来存储构建后的路径
    if args.mode in {"all", "code"}:  # 如果模式包含"all"或"code"，则构建代码
        built.append(build_minimal_code())
    if args.mode in {"all", "runtime"}:  # 如果模式包含"all"或"runtime"，则构建运行时环境
        built.append(
            build_portable_runtime(args.runtime_python, force_download=args.force_download)
        )
    print("\n[DONE] exported:")  # 打印完成信息
    for path in built:  # 遍历构建的路径并打印
        print(f" - {path}")
    return 0  # 返回0表示成功执行


if __name__ == "__main__":
    raise SystemExit(main())
