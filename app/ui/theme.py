from __future__ import annotations

# MusePlayer 主题配色和样式定义

# 主题色彩配置
ACCENT = "#A7C080"  # 主要强调色（青柠绿），用于按钮高亮、进度条等
ACCENT_STRONG = "#81e98b"  # 强调整色（亮绿色），用于特殊高亮状态

# 浅色主题样式表
#
# 设计理念：
# - 以柔和的浅色调为主，减少视觉疲劳
# - 强调色用于关键交互元素，提供清晰的视觉反馈
# - 层次分明的信息架构，标题>元信息>辅助信息
# - 圆角和阴影营造现代感的界面体验
APP_STYLE_LIGHT = f"""
QWidget {{
    background: #f6f7f6;
    color: #1d221f;
    font-family: "Microsoft YaHei", "Segoe UI", "Noto Sans SC";
    font-size: 13px;
}}

QMainWindow {{
    background: #f3f4f3;
}}

QFrame#RichTitleBar {{
    background: rgba(167, 192, 128, 0.22);
    border: 1px solid rgba(128, 146, 103, 0.45);
}}

QLabel#RichTitleLabel {{
    color: #1c211e;
    font-size: 14px;
    font-weight: 600;
    padding-left: 4px;
}}

QToolButton#RichTitleButton {{
    min-width: 26px;
    max-width: 26px;
    min-height: 22px;
    max-height: 22px;
    border-radius: 4px;
    border: 1px solid #cdd4ce;
    background: #f8f9f8;
    color: #1f2521;
    font-weight: 700;
}}

QToolButton#RichTitleButton:hover {{
    border-color: {ACCENT};
}}

QFrame#Card,
QWidget#VolumePanel,
QWidget#CompactTopBar,
QWidget#TrackRowWidget,
QWidget#LyricRowWidget {{
    background: transparent;
    border: none;
}}

QLabel {{
    background: transparent;
}}

QLabel#TitleLabel {{
    font-size: 24px;
    font-weight: 700;
    color: #161b18;
}}

QLabel#MetaLabel {{
    font-size: 13px;
    color: #3a443e;
    background: transparent;
}}

QLabel#CaptionLabel {{
    font-size: 12px;
    color: #59655d;
}}

QLabel#RandomStateHintLabel {{
    color: #5f6c5f;
    background: transparent;
    padding: 0px 6px;
    font-size: 12px;
}}

QLabel#VersionHintLabel {{
    color: #4f6442;
    background: transparent;
    padding: 0px 4px;
    font-size: 12px;
    font-weight: 600;
}}

QMenuBar {{
    background: #f3f4f3;
    border: none;
    padding: 2px 4px;
}}

QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
    border: 1px solid transparent;
}}

QMenuBar::item:selected {{
    background: rgba(167, 192, 128, 0.30);
    border: 1px solid rgba(128, 146, 103, 0.55);
}}

QMenuBar::item:pressed {{
    background: rgba(167, 192, 128, 0.45);
    border: 1px solid rgba(128, 146, 103, 0.70);
}}

QMenu {{
    background: #f8f9f8;
    border: 1px solid #d7ddd8;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 12px;
    color: #1f2521;
}}

QMenu::item:selected {{
    background: rgba(167, 192, 128, 0.28);
    color: #10140f;
}}

QMenu::separator {{
    height: 1px;
    background: #d7ddd8;
    margin: 4px 10px;
}}

QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: #f8f9f8;
    color: #1f2521;
    border: 1px solid #d2d8d3;
    border-radius: 6px;
    padding: 6px 10px;
}}

QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: #ecefed;
    color: #9aa39c;
    border: 1px solid #dde2de;
}}

QToolButton#SearchClearButton {{
    border: none;
    background: transparent;
    color: #d94f4f;
    font-size: 13px;
    font-weight: 700;
    padding: 0px;
}}

QToolButton#SearchClearButton:hover {{
    color: #b23838;
}}

QListWidget#lyrics_list, QListWidget#track_list, QListWidget {{
    background: transparent;
    border: none;
    outline: none;
    padding: 4px;
}}

QListWidget::item {{
    padding: 6px;
    border: none;
    background: transparent;
}}

QListWidget::item:selected {{
    background: rgba(167, 192, 128, 0.40);
    color: #000000;
}}

QPushButton {{
    background: #f8f9f8;
    color: #1f2521;
    border: 1px solid #d2d8d3;
    border-radius: 6px;
    padding: 7px 12px;
    font-weight: 600;
}}

QPushButton:hover {{
    border-color: {ACCENT};
}}

QPushButton#GhostButton {{
    background: transparent;
}}

QToolButton#ControlIconButton,
QToolButton#ModeButton,
QToolButton#CompactButton,
QToolButton#CompactTopButton,
QToolButton#VolumeIconButton {{
    min-width: 30px;
    min-height: 30px;
    max-width: 30px;
    max-height: 30px;
    border-radius: 8px;
    border: 1px solid #d2d8d3;
    background: #ffffff;
    color: #1f2521;
}}

QToolButton#ControlIconButton:hover,
QToolButton#ModeButton:hover,
QToolButton#CompactButton:hover,
QToolButton#CompactTopButton:hover,
QToolButton#VolumeIconButton:hover {{
    border-color: {ACCENT};
}}

QToolButton#SidebarToggle {{
    min-width: 22px;
    max-width: 22px;
    min-height: 54px;
    max-height: 54px;
    border-radius: 10px;
    border: 1px solid #d2d8d3;
    background: #ffffff;
    color: #1f2521;
}}

QToolButton#SidebarToggle:hover {{
    border-color: {ACCENT};
}}

QToolButton#LocateCurrentButton {{
    min-width: 20px;
    min-height: 20px;
    max-width: 20px;
    max-height: 20px;
    border-radius: 10px;
    border: 1px solid #d2d8d3;
    background: #ffffff;
    color: #1f2521;
}}

QToolButton#LocateCurrentButton:hover {{
    border-color: {ACCENT};
}}

QLabel#VolumeValueLabel {{
    color: #2b332e;
    background: transparent;
    border: none;
    padding: 0px 2px;
}}

QSlider {{
    min-height: 18px;
    background: transparent;
}}

QSlider::groove:horizontal {{
    background: transparent;
    height: 6px;
    border: 1px solid rgba(167, 192, 128, 0.65);
    border-radius: 3px;
}}

QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}

QSlider::add-page:horizontal {{
    background: transparent;
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    width: 14px;
    margin: -5px 0;
    border: 1px solid {ACCENT};
    border-radius: 7px;
    background: #ffffff;
}}

QCheckBox {{
    spacing: 6px;
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid #9faea2;
    background: transparent;
}}

QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: #7f9870;
}}

QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
    background: rgba(167, 192, 128, 0.35);
}}

QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: rgba(167, 192, 128, 0.55);
}}

QCheckBox:disabled {{
    color: #9aa39c;
}}

QCheckBox::indicator:disabled {{
    border-color: #c8cfca;
    background: #ecefed;
}}

QSplitter::handle:horizontal {{
    width: 6px;
    background: transparent;
}}

QSplitter::handle:horizontal:hover {{
    background: rgba(167, 192, 128, 0.30);
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 0px 2px 0px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: rgba(167, 192, 128, 0.68);
    border-radius: 5px;
    min-height: 26px;
}}

QScrollBar::handle:vertical:hover {{
    background: rgba(129, 233, 139, 0.85);
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px 2px 0px 2px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: rgba(167, 192, 128, 0.68);
    border-radius: 5px;
    min-width: 26px;
}}

QScrollBar::handle:horizontal:hover {{
    background: rgba(129, 233, 139, 0.85);
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
    border: none;
}}
"""

# 深色主题样式表
#
# 设计理念：
# - 深灰背景降低屏幕亮度，适合夜间使用
# - 调整强调色透明度，保持视觉层次的同时减少刺眼感
# - 提高文本对比度，确保在深色背景上的可读性
# - 适度使用边框和背景区分不同功能区域
APP_STYLE_DARK = f"""
QWidget {{
    background: #1a1a1a;
    color: #f1f1f1;
    font-family: "Microsoft YaHei", "Segoe UI", "Noto Sans SC";
    font-size: 13px;
}}

QMainWindow {{
    background: #161616;
}}

QFrame#RichTitleBar {{
    background: rgba(129, 233, 139, 0.12);
    border: 1px solid rgba(129, 233, 139, 0.30);
}}

QLabel#RichTitleLabel {{
    color: #f1f1f1;
    font-size: 14px;
    font-weight: 600;
    padding-left: 4px;
}}

QToolButton#RichTitleButton {{
    min-width: 26px;
    max-width: 26px;
    min-height: 22px;
    max-height: 22px;
    border-radius: 4px;
    border: 1px solid #3a3a3a;
    background: #1f1f1f;
    color: #f4f4f4;
    font-weight: 700;
}}

QToolButton#RichTitleButton:hover {{
    border-color: {ACCENT};
}}

QFrame#Card,
QWidget#VolumePanel,
QWidget#CompactTopBar,
QWidget#TrackRowWidget,
QWidget#LyricRowWidget {{
    background: transparent;
    border: none;
}}

QLabel {{
    background: transparent;
}}

QLabel#TitleLabel {{
    font-size: 24px;
    font-weight: 700;
    color: #f4f4f4;
}}

QLabel#MetaLabel {{
    font-size: 13px;
    color: #c3c3c3;
    background: transparent;
}}

QLabel#CaptionLabel {{
    font-size: 12px;
    color: #9a9a9a;
}}

QLabel#RandomStateHintLabel {{
    color: #b7c4b7;
    background: transparent;
    padding: 0px 6px;
    font-size: 12px;
}}

QLabel#VersionHintLabel {{
    color: #d0ddc6;
    background: transparent;
    padding: 0px 4px;
    font-size: 12px;
    font-weight: 600;
}}

QMenuBar {{
    background: #161616;
    border: none;
    padding: 2px 4px;
}}

QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
    border: 1px solid transparent;
    color: #f1f1f1;
}}

QMenuBar::item:selected {{
    background: rgba(167, 192, 128, 0.26);
    border: 1px solid rgba(129, 233, 139, 0.45);
}}

QMenuBar::item:pressed {{
    background: rgba(167, 192, 128, 0.42);
    border: 1px solid rgba(129, 233, 139, 0.65);
}}

QMenu {{
    background: #1d1d1d;
    border: 1px solid #333333;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 12px;
    color: #f1f1f1;
}}

QMenu::item:selected {{
    background: rgba(167, 192, 128, 0.26);
    color: #ffffff;
}}

QMenu::separator {{
    height: 1px;
    background: #333333;
    margin: 4px 10px;
}}

QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: #1f1f1f;
    color: #f1f1f1;
    border: 1px solid #3b3b3b;
    border-radius: 6px;
    padding: 6px 10px;
}}

QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: #292929;
    color: #858585;
    border: 1px solid #3a3a3a;
}}

QToolButton#SearchClearButton {{
    border: none;
    background: transparent;
    color: #ef6b6b;
    font-size: 13px;
    font-weight: 700;
    padding: 0px;
}}

QToolButton#SearchClearButton:hover {{
    color: #ff8f8f;
}}

QListWidget#lyrics_list, QListWidget#track_list, QListWidget {{
    background: transparent;
    border: none;
    outline: none;
    padding: 4px;
}}

QListWidget::item {{
    padding: 6px;
    border: none;
    background: transparent;
    color: #f1f1f1;
}}

QListWidget::item:selected {{
    background: rgba(167, 192, 128, 0.28);
    color: #ffffff;
}}

QPushButton {{
    background: #1f1f1f;
    color: #f1f1f1;
    border: 1px solid #3b3b3b;
    border-radius: 6px;
    padding: 7px 12px;
    font-weight: 600;
}}

QPushButton:hover {{
    border-color: {ACCENT};
}}

QPushButton#GhostButton {{
    background: transparent;
}}

QToolButton#ControlIconButton,
QToolButton#ModeButton,
QToolButton#CompactButton,
QToolButton#CompactTopButton,
QToolButton#VolumeIconButton {{
    min-width: 30px;
    min-height: 30px;
    max-width: 30px;
    max-height: 30px;
    border-radius: 8px;
    border: 1px solid #3b3b3b;
    background: #1f1f1f;
    color: #ffffff;
}}

QToolButton#ControlIconButton:hover,
QToolButton#ModeButton:hover,
QToolButton#CompactButton:hover,
QToolButton#CompactTopButton:hover,
QToolButton#VolumeIconButton:hover {{
    border-color: {ACCENT};
}}

QToolButton#SidebarToggle {{
    min-width: 22px;
    max-width: 22px;
    min-height: 54px;
    max-height: 54px;
    border-radius: 10px;
    border: 1px solid #3b3b3b;
    background: #1f1f1f;
    color: #ffffff;
}}

QToolButton#SidebarToggle:hover {{
    border-color: {ACCENT};
}}

QToolButton#LocateCurrentButton {{
    min-width: 20px;
    min-height: 20px;
    max-width: 20px;
    max-height: 20px;
    border-radius: 10px;
    border: 1px solid #3b3b3b;
    background: #1f1f1f;
    color: #ffffff;
}}

QToolButton#LocateCurrentButton:hover {{
    border-color: {ACCENT};
}}

QLabel#VolumeValueLabel {{
    color: #e5e5e5;
    background: transparent;
    border: none;
    padding: 0px 2px;
}}

QSlider {{
    min-height: 18px;
    background: transparent;
}}

QSlider::groove:horizontal {{
    background: transparent;
    height: 6px;
    border: 1px solid rgba(167, 192, 128, 0.70);
    border-radius: 3px;
}}

QSlider::sub-page:horizontal {{
    background: {ACCENT_STRONG};
    border-radius: 3px;
}}

QSlider::add-page:horizontal {{
    background: transparent;
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    width: 14px;
    margin: -5px 0;
    border: 1px solid {ACCENT_STRONG};
    border-radius: 7px;
    background: #f5f5f5;
}}

QCheckBox {{
    spacing: 6px;
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid #8d8d8d;
    background: transparent;
}}

QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT_STRONG};
}}

QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
    background: rgba(167, 192, 128, 0.28);
}}

QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: rgba(129, 233, 139, 0.35);
}}

QCheckBox:disabled {{
    color: #888888;
}}

QCheckBox::indicator:disabled {{
    border-color: #5e5e5e;
    background: #2c2c2c;
}}

QSplitter::handle:horizontal {{
    width: 6px;
    background: transparent;
}}

QSplitter::handle:horizontal:hover {{
    background: rgba(167, 192, 128, 0.35);
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 0px 2px 0px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: rgba(167, 192, 128, 0.68);
    border-radius: 5px;
    min-height: 26px;
}}

QScrollBar::handle:vertical:hover {{
    background: rgba(129, 233, 139, 0.82);
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px 2px 0px 2px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: rgba(167, 192, 128, 0.68);
    border-radius: 5px;
    min-width: 26px;
}}

QScrollBar::handle:horizontal:hover {{
    background: rgba(129, 233, 139, 0.82);
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
    border: none;
}}
"""

APP_STYLE = APP_STYLE_DARK
