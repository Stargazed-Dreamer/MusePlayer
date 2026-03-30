from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import urllib.request
import zipfile
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
    print(f"[RUN] {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_code_bundle(dst_case_dir: Path) -> Path:
    bundle_root = dst_case_dir / BUNDLE_DIRNAME
    bundle_root.mkdir(parents=True, exist_ok=True)

    for item in CODE_ITEMS:
        src = PROJECT_ROOT / item
        dst = bundle_root / item
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst, ignore=COPY_IGNORE, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return bundle_root


def _ansi_encoding() -> str:
    if os.name == "nt":
        return "mbcs"
    return "cp1252"


def write_bat_ansi(path: Path, content: str) -> None:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    path.write_bytes(normalized.encode(_ansi_encoding(), errors="replace"))


def write_source_launcher(bundle_root: Path) -> None:
    launcher = textwrap.dedent(
        """\
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
        """
    )
    write_bat_ansi(bundle_root / "start.bat", launcher)


def write_runtime_launcher(bundle_root: Path) -> None:
    launcher = textwrap.dedent(
        """\
        @echo off
        setlocal
        cd /d "%~dp0"
        if exist ".\\python\\pythonw.exe" (
          start "" /B ".\\python\\pythonw.exe" ".\\main.py"
          exit /b 0
        )
        if exist ".\\python\\python.exe" (
          start "" /B ".\\python\\python.exe" ".\\main.py"
          exit /b 0
        )
        echo Embedded python executable not found.
        exit /b 1
        """
    )
    write_bat_ansi(bundle_root / "start.bat", launcher)


def write_environments_file(
    bundle_root: Path,
    *,
    artifact_type: str,
    runtime_python_version: str | None = None,
    runtime_freeze: str | None = None,
) -> None:
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
    target_file.parent.mkdir(parents=True, exist_ok=True)
    if target_file.exists() and not force:
        print(f"[CACHE] {target_file.name}")
        return target_file
    print(f"[DL] {url}")
    urllib.request.urlretrieve(url, target_file)
    return target_file


def configure_embedded_python(python_dir: Path) -> None:
    pth_files = list(python_dir.glob("python*._pth"))
    if not pth_files:
        raise RuntimeError("python*._pth not found in embedded runtime")
    pth_file = pth_files[0]
    rows = [line.rstrip("\r\n") for line in pth_file.read_text(encoding="utf-8").splitlines()]

    new_rows: list[str] = []
    has_parent_path = False
    has_site_packages = False
    has_import_site = False

    for row in rows:
        stripped = row.strip()
        if stripped == "..":
            has_parent_path = True
            new_rows.append("..")
            continue
        if stripped.lower() == "lib\\site-packages":
            has_site_packages = True
            new_rows.append("Lib\\site-packages")
            continue
        if stripped.startswith("#") and stripped.replace("#", "", 1).strip() == "import site":
            has_import_site = True
            new_rows.append("import site")
            continue
        if stripped == "import site":
            has_import_site = True
        new_rows.append(row)

    if not has_parent_path:
        insert_at = 0
        for i, row in enumerate(new_rows):
            if row.strip() == ".":
                insert_at = i + 1
                break
        new_rows.insert(insert_at, "..")

    if not has_site_packages:
        insert_at = len(new_rows)
        for i, row in enumerate(new_rows):
            if row.strip() == "import site":
                insert_at = i
                break
        new_rows.insert(insert_at, "Lib\\site-packages")

    if not has_import_site:
        new_rows.append("import site")

    pth_file.write_text("\n".join(new_rows) + "\n", encoding="utf-8")
    (python_dir / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)


def build_minimal_code() -> Path:
    print("[STEP] build minimal code bundle")
    clean_dir(CODE_EXPORT_DIR)
    bundle_root = copy_code_bundle(CODE_EXPORT_DIR)
    write_source_launcher(bundle_root)
    write_environments_file(bundle_root, artifact_type="minimal_code")
    return bundle_root


def build_portable_runtime(runtime_python: str, *, force_download: bool = False) -> Path:
    if not sys.platform.startswith("win"):
        raise RuntimeError("portable runtime export currently supports Windows host only")

    print("[STEP] build portable runtime bundle")
    clean_dir(RUNTIME_EXPORT_DIR)
    bundle_root = copy_code_bundle(RUNTIME_EXPORT_DIR)

    runtime_python_dir = bundle_root / "python"
    runtime_python_dir.mkdir(parents=True, exist_ok=True)

    embed_zip_name = f"python-{runtime_python}-embed-amd64.zip"
    embed_url = f"https://www.python.org/ftp/python/{runtime_python}/{embed_zip_name}"
    embed_zip_path = download(embed_url, CACHE_DIR / embed_zip_name, force=force_download)

    with zipfile.ZipFile(embed_zip_path, "r") as zf:
        zf.extractall(runtime_python_dir)

    configure_embedded_python(runtime_python_dir)

    python_exe = runtime_python_dir / "python.exe"
    if not python_exe.exists():
        raise RuntimeError("python.exe not found in embedded runtime")

    get_pip_path = download(
        "https://bootstrap.pypa.io/get-pip.py",
        CACHE_DIR / "get-pip.py",
        force=force_download,
    )
    run([str(python_exe), str(get_pip_path), "--no-warn-script-location"], cwd=runtime_python_dir)
    run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], cwd=bundle_root)
    run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--prefer-binary",
            "--only-binary",
            ":all:",
            "-r",
            str(bundle_root / "requirements.txt"),
        ],
        cwd=bundle_root,
    )

    runtime_version = subprocess.check_output(
        [str(python_exe), "-c", "import sys; print(sys.version.replace('\\n', ' '))"],
        text=True,
        cwd=str(bundle_root),
    ).strip()
    runtime_freeze = subprocess.check_output(
        [str(python_exe), "-m", "pip", "freeze"],
        text=True,
        cwd=str(bundle_root),
    ).strip()

    write_runtime_launcher(bundle_root)
    write_environments_file(
        bundle_root,
        artifact_type="portable_runtime",
        runtime_python_version=runtime_version,
        runtime_freeze=runtime_freeze,
    )
    return bundle_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export MusePlayer distributable artifacts")
    parser.add_argument(
        "--mode",
        choices=["all", "code", "runtime"],
        default="all",
        help="code=minimal code set, runtime=portable runnable package, all=both",
    )
    parser.add_argument(
        "--runtime-python",
        default="3.11.9",
        help="embedded Python version for runtime export (e.g. 3.11.9)",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="ignore cache and re-download embedded Python/get-pip",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)

    built: list[Path] = []
    if args.mode in {"all", "code"}:
        built.append(build_minimal_code())
    if args.mode in {"all", "runtime"}:
        built.append(build_portable_runtime(args.runtime_python, force_download=args.force_download))

    print("\n[DONE] exported:")
    for path in built:
        print(f" - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

