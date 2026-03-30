from __future__ import annotations

ACCENT = "#A7C080"
ACCENT_STRONG = "#81e98b"

APP_STYLE_LIGHT = f"""
QWidget {{
    background: #f6f7f6;
    color: #1d221f;
    font-family: "Noto Sans SC", "Segoe UI", "Microsoft YaHei";
    font-size: 13px;
}}

QMainWindow {{
    background: #f3f4f3;
}}

QFrame#RichTitleBar {{
    background: transparent;
    border: none;
}}

QLabel#RichTitleLabel {{
    color: #1c211e;
    font-size: 13px;
    font-weight: 600;
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
    color: #748276;
    background: transparent;
    padding: 0px 6px;
    font-size: 12px;
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
    border: 1px solid #505050;
    background: #2a2a2a;
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
    border: 1px solid #505050;
    background: #2a2a2a;
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
    border: 1px solid #505050;
    background: #2a2a2a;
    color: #ffffff;
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

QSplitter::handle:horizontal {{
    width: 6px;
    background: transparent;
}}

QSplitter::handle:horizontal:hover {{
    background: rgba(167, 192, 128, 0.30);
}}
"""

APP_STYLE_DARK = f"""
QWidget {{
    background: #1a1a1a;
    color: #f1f1f1;
    font-family: "Noto Sans SC", "Segoe UI", "Microsoft YaHei";
    font-size: 13px;
}}

QMainWindow {{
    background: #161616;
}}

QFrame#RichTitleBar {{
    background: transparent;
    border: none;
}}

QLabel#RichTitleLabel {{
    color: #f1f1f1;
    font-size: 13px;
    font-weight: 600;
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
    color: #9fae9f;
    background: transparent;
    padding: 0px 6px;
    font-size: 12px;
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

QSplitter::handle:horizontal {{
    width: 6px;
    background: transparent;
}}

QSplitter::handle:horizontal:hover {{
    background: rgba(167, 192, 128, 0.35);
}}
"""

APP_STYLE = APP_STYLE_DARK
