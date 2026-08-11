from __future__ import annotations

import importlib
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import ACCENT, ACCENT_STRONG


def _load_version_info() -> dict[str, str]:
    """从磁盘实时读取版本信息。

    每次调用都通过 importlib.reload 重新加载 app.version 模块，
    确保即使运行时磁盘上的 version.py 被修改，下次打开关于对话框也能读到最新值。

    Returns:
        包含所有版本字段的字典。
    """
    import app.version as _version_mod

    importlib.reload(_version_mod)
    return {
        "app_version": _version_mod.APP_VERSION,
        "data_format_version": _version_mod.DATA_FORMAT_VERSION,
        "protocol_version": _version_mod.PROTOCOL_VERSION,
        "author": _version_mod.AUTHOR,
        "copyright": _version_mod.COPYRIGHT,
        "license_name": _version_mod.LICENSE_NAME,
        "license_url": _version_mod.LICENSE_URL,
        "repo_url": _version_mod.REPO_URL,
        "repo_issues_url": _version_mod.REPO_ISSUES_URL,
    }


def _resolve_icon_path() -> Path:
    """定位项目根目录的 icon.ico。

    基于当前文件路径动态计算，避免硬编码绝对路径。
    """
    # app/ui/about_dialog.py → 上两级即项目根
    return Path(__file__).resolve().parent.parent.parent / "icon.ico"


class AboutDialog(QDialog):
    """关于对话框。

    模态显示应用元信息：图标、版本、数据格式版本、通信协议版本、作者、仓库链接。
    每次打开时通过 _load_version_info() 实时从磁盘读取版本信息。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于 MusePlayer")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        info = _load_version_info()
        self._info = info

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(28, 24, 28, 20)
        root_layout.setSpacing(14)

        # 顶部：图标 + 应用名 + 版本
        top_layout = QHBoxLayout()
        top_layout.setSpacing(16)

        icon_label = QLabel(self)
        # 逻辑显示尺寸
        icon_logical = 64
        # 直接加载 icon.ico，取其中最高分辨率（256×256）的内嵌版本，自行平滑缩放
        ico_path = _resolve_icon_path()
        if ico_path.exists():
            # 用 QIcon + 实际尺寸获取最高分辨率的内嵌 pixmap
            icon = QIcon(str(ico_path))
            # QIcon.availableSizes() 返回所有内嵌尺寸，取面积最大的
            avail_sizes = icon.availableSizes()
            if avail_sizes:
                best_size = max(avail_sizes, key=lambda s: s.width() * s.height())
                source_pixmap = icon.pixmap(best_size)
            else:
                # 回退：直接用 QPixmap 加载整个 ico
                source_pixmap = QPixmap(str(ico_path))
            if not source_pixmap.isNull():
                # 平滑缩放到逻辑尺寸，保持长宽比
                scaled = source_pixmap.scaled(
                    icon_logical,
                    icon_logical,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                icon_label.setPixmap(scaled)
        icon_label.setFixedSize(icon_logical, icon_logical)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title = QLabel("MusePlayer", self)
        title.setObjectName("AboutTitle")
        title.setStyleSheet(f"font-size: 22px; font-weight: 600; color: {ACCENT_STRONG};")
        subtitle = QLabel("本地音乐播放器 · PySide6 + PyAV", self)
        subtitle.setStyleSheet("font-size: 12px; color: #9aa69b;")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        top_layout.addLayout(title_block, 1)
        root_layout.addLayout(top_layout)

        # 分隔线
        sep = QLabel(self)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {ACCENT}; opacity: 0.4;")
        sep.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root_layout.addWidget(sep)

        # 信息条目
        rows = [
            ("应用版本", info["app_version"]),
            ("数据格式版本", info["data_format_version"]),
            ("通信协议版本", info["protocol_version"]),
            ("作者", info["author"]),
            ("版权", info["copyright"]),
        ]
        for label_text, value_text in rows:
            row = QHBoxLayout()
            row.setSpacing(12)
            key_label = QLabel(label_text, self)
            key_label.setStyleSheet("font-size: 12px; color: #9aa69b;")
            key_label.setFixedWidth(96)
            key_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value_label = QLabel(value_text, self)
            value_label.setStyleSheet("font-size: 13px;")
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(key_label)
            row.addWidget(value_label, 1)
            root_layout.addLayout(row)

        # 仓库链接（可点击超链接）
        repo_row = QHBoxLayout()
        repo_row.setSpacing(12)
        repo_key = QLabel("仓库", self)
        repo_key.setStyleSheet("font-size: 12px; color: #9aa69b;")
        repo_key.setFixedWidth(96)
        repo_key.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        repo_link = QLabel(self)
        repo_link.setText(
            f'<a href="{info["repo_url"]}" style="color: {ACCENT_STRONG}; text-decoration: none;">{info["repo_url"]}</a>'
        )
        repo_link.setTextFormat(Qt.TextFormat.RichText)
        repo_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        repo_link.setOpenExternalLinks(False)
        repo_link.linkActivated.connect(self._open_repo_url)
        repo_row.addWidget(repo_key)
        repo_row.addWidget(repo_link, 1)
        root_layout.addLayout(repo_row)

        # 许可证链接（可点击超链接）
        license_row = QHBoxLayout()
        license_row.setSpacing(12)
        license_key = QLabel("许可证", self)
        license_key.setStyleSheet("font-size: 12px; color: #9aa69b;")
        license_key.setFixedWidth(96)
        license_key.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        license_link = QLabel(self)
        license_link.setText(
            f'<a href="{info["license_url"]}" style="color: {ACCENT_STRONG}; text-decoration: none;">{info["license_name"]}</a>'
        )
        license_link.setTextFormat(Qt.TextFormat.RichText)
        license_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        license_link.setOpenExternalLinks(False)
        license_link.linkActivated.connect(self._open_license_url)
        license_row.addWidget(license_key)
        license_row.addWidget(license_link, 1)
        root_layout.addLayout(license_row)

        # 底部按钮
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        copy_btn = QPushButton("复制仓库链接", self)
        copy_btn.clicked.connect(self._copy_repo_url)
        button_row.addWidget(copy_btn)
        close_btn = QPushButton("关闭", self)
        close_btn.setObjectName("AboutCloseBtn")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        root_layout.addLayout(button_row)

    def _open_repo_url(self, _url: str) -> None:
        QDesktopServices.openUrl(QUrl(self._info["repo_url"]))

    def _open_license_url(self, _url: str) -> None:
        QDesktopServices.openUrl(QUrl(self._info["license_url"]))

    def _copy_repo_url(self) -> None:
        from PySide6.QtGui import QClipboard

        clip: QClipboard = QGuiApplication.clipboard()
        if clip is not None:
            clip.setText(self._info["repo_url"])
