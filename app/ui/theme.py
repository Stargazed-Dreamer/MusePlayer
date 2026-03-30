from __future__ import annotations

APP_STYLE_LIGHT = """
QWidget {
    background: #f4f7fb;
    color: #132033;
    font-family: "Noto Sans SC", "Segoe UI", "Microsoft YaHei";
    font-size: 13px;
}

QMainWindow {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #f8fbff, stop:1 #edf3ff);
}

QMenuBar {
    background: rgba(255, 255, 255, 0.7);
    border: 1px solid #d6e3f1;
    border-radius: 0px;
    padding: 2px 4px;
}

QMenuBar::item {
    padding: 4px 8px;
    border-radius: 0px;
    background: rgba(255, 255, 255, 0.0);
    border: 1px solid transparent;
    margin: 0px;
}

QMenuBar::item:selected {
    background: rgba(30, 88, 153, 0.24);
    border: 1px solid #b8d0ea;
}

QMenuBar::item:pressed {
    background: rgba(30, 88, 153, 0.32);
    border: 1px solid #9dbde0;
}

QLabel#RandomStateHintLabel {
    color: #8fa3bc;
    background: transparent;
    padding: 0px 6px;
    font-size: 12px;
}

QMenu {
    background: #ffffff;
    border: 1px solid #dbe5f2;
    padding: 4px;
    border-radius: 0px;
}

QMenu::item {
    padding: 6px 12px;
    border-radius: 0px;
    background: transparent;
    color: #132033;
}

QMenu::item:selected,
QMenu::item:selected:active {
    background: rgba(30, 88, 153, 0.22);
    color: #0f2440;
}

QMenu::item:pressed,
QMenu::item:pressed:selected {
    background: rgba(30, 88, 153, 0.34);
    color: #0d1d34;
}

QMenu::separator {
    height: 1px;
    background: #e2eaf4;
    margin: 5px 10px;
}

QPushButton {
    border: none;
    border-radius: 0px;
    background: #1e5899;
    color: #ffffff;
    padding: 8px 14px;
    font-weight: 600;
}

QPushButton:hover {
    background: #2d69ad;
}

QPushButton:pressed {
    background: #1a4f89;
}

QPushButton#GhostButton {
    background: #ffffff;
    color: #1e5899;
    border: 1px solid #bfd1e8;
}

QLineEdit, QTextEdit, QListWidget, QComboBox, QSpinBox {
    background: #ffffff;
    border: 1px solid #d5e1ee;
    border-radius: 0px;
    padding: 6px 10px;
}

QListWidget {
    padding: 6px;
}

QListWidget::item {
    border-radius: 0px;
    padding: 8px;
}

QSlider {
    min-height: 16px;
    background: transparent;
}

QListWidget::item:selected {
    background: rgba(30, 88, 153, 0.16);
    color: #0e1f36;
}

QSlider::groove:horizontal {
    background: #d8e4f2;
    height: 4px;
    border-radius: 0px;
}

QSlider::sub-page:horizontal {
    background: #1e5899;
    border-radius: 0px;
}

QSlider::handle:horizontal {
    background: #ffffff;
    border: 1px solid #1e5899;
    width: 12px;
    margin: -4px 0;
    border-radius: 6px;
}

QFrame#Card {
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid #dce6f3;
    border-radius: 0px;
}

QLabel#TitleLabel {
    font-size: 24px;
    font-weight: 700;
}

QLabel#MetaLabel {
    font-size: 13px;
    color: #42566f;
}

QLabel#CaptionLabel {
    font-size: 12px;
    color: #65819d;
}

QToolButton#ControlIconButton,
QToolButton#ModeButton,
QToolButton#CompactButton,
QToolButton#CompactTopButton {
    min-width: 30px;
    min-height: 30px;
    max-width: 30px;
    max-height: 30px;
    border-radius: 0px;
    border: 1px solid #c8d8eb;
    background: #ffffff;
}

QToolButton#ControlIconButton:hover,
QToolButton#ModeButton:hover,
QToolButton#CompactButton:hover,
QToolButton#CompactTopButton:hover {
    background: #eff5fb;
    border-color: #9abbe0;
}

QToolButton#VolumeIconButton {
    min-width: 30px;
    min-height: 30px;
    max-width: 30px;
    max-height: 30px;
    border-radius: 0px;
    border: 1px solid #c8d8eb;
    background: #ffffff;
}

QToolButton#VolumeIconButton:hover {
    background: #eff5fb;
    border-color: #9abbe0;
}

QToolButton#ModeButton {
    border-color: #9dbce0;
    background: #f1f7ff;
}

QToolButton#SidebarToggle {
    min-width: 22px;
    max-width: 22px;
    min-height: 54px;
    max-height: 54px;
    border-radius: 0px;
    border: 1px solid #c7d9ec;
    background: #ffffff;
}

QToolButton#SidebarToggle:hover {
    background: #ecf3fb;
}

QWidget#VolumePanel {
    background: transparent;
}

QLabel#VolumeValueLabel {
    font-size: 12px;
    color: #4f6a85;
    background: #ffffff;
    border: 1px solid #c8d8eb;
    border-radius: 0px;
    padding: 0px 2px;
}

QWidget#CompactTopBar {
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid #d5e2f1;
    border-radius: 0px;
}

QToolButton#CompactTopButton {
    min-width: 24px;
    min-height: 24px;
    max-width: 24px;
    max-height: 24px;
    border-radius: 0px;
}

QToolButton#TrackDeleteButton {
    min-width: 12px;
    min-height: 12px;
    max-width: 12px;
    max-height: 12px;
    border: none;
    background: transparent;
    color: #c62f2f;
    font-size: 12px;
    font-weight: 700;
    padding: 0px;
}

QToolButton#TrackDeleteButton:hover {
    color: #991f1f;
    background: transparent;
}

QToolButton#LocateCurrentButton {
    min-width: 20px;
    min-height: 20px;
    max-width: 20px;
    max-height: 20px;
    border: 1px solid #9dbce0;
    border-radius: 10px;
    background: #ffffff;
}

QToolButton#LocateCurrentButton:hover {
    background: #eff5fb;
    border-color: #7fa7d4;
}

QLabel#TrackTitleLabel {
    color: #132033;
    background: transparent;
}

QLabel#CompactTitleLabel {
    font-size: 14px;
    font-weight: 700;
    color: #15385f;
}

QLabel#CompactLyricLineLabel {
    font-size: 12px;
    color: #4f6a85;
}

QLabel#LyricTimeLabel {
    font-size: 11px;
    color: #5f7892;
    background: transparent;
}

QLabel#LyricTextLabel {
    font-size: 13px;
    color: #14273f;
    background: transparent;
}

QWidget#TrackRowWidget,
QWidget#LyricRowWidget {
    background: transparent;
}

QListWidget#lyrics_list,
QListWidget#track_list {
    background: #ffffff;
}

QLabel#CompactTopTitle {
    font-size: 14px;
    font-weight: 700;
    color: #15385f;
    background: transparent;
}

QSplitter::handle:horizontal {
    width: 6px;
    background: transparent;
}

QSplitter::handle:horizontal:hover {
    background: rgba(30, 88, 153, 0.20);
}
"""

APP_STYLE_DARK = """
QWidget {
    background: #121620;
    color: #dbe6f4;
    font-family: "Noto Sans SC", "Segoe UI", "Microsoft YaHei";
    font-size: 13px;
}

QMainWindow {
    background: #0e1420;
}

QMenuBar {
    background: #151d2a;
    border: 1px solid #2b3b52;
    border-radius: 0px;
    padding: 2px 4px;
}

QMenuBar::item {
    padding: 4px 8px;
    border-radius: 0px;
    background: transparent;
    border: 1px solid transparent;
    margin: 0px;
    color: #d7e2f2;
}

QMenuBar::item:selected {
    background: rgba(73, 125, 193, 0.28);
    border: 1px solid #4d6790;
}

QMenuBar::item:pressed {
    background: rgba(73, 125, 193, 0.40);
    border: 1px solid #5f7fae;
}

QLabel#RandomStateHintLabel {
    color: #7f97b8;
    background: transparent;
    padding: 0px 6px;
    font-size: 12px;
}

QMenu {
    background: #1a2434;
    border: 1px solid #30445f;
    padding: 4px;
    border-radius: 0px;
}

QMenu::item {
    padding: 6px 12px;
    border-radius: 0px;
    background: transparent;
    color: #d8e2f1;
}

QMenu::item:selected,
QMenu::item:selected:active {
    background: rgba(73, 125, 193, 0.30);
    color: #f0f6ff;
}

QMenu::item:pressed,
QMenu::item:pressed:selected {
    background: rgba(73, 125, 193, 0.44);
    color: #f7fbff;
}

QMenu::separator {
    height: 1px;
    background: #30445f;
    margin: 5px 10px;
}

QPushButton {
    border: none;
    border-radius: 0px;
    background: #3e6da8;
    color: #f3f7ff;
    padding: 8px 14px;
    font-weight: 600;
}

QPushButton:hover {
    background: #4b7dbc;
}

QPushButton:pressed {
    background: #365f93;
}

QPushButton#GhostButton {
    background: #1a2434;
    color: #9fc0e8;
    border: 1px solid #3b5575;
}

QLineEdit, QTextEdit, QListWidget, QComboBox, QSpinBox {
    background: #182233;
    border: 1px solid #344961;
    border-radius: 0px;
    padding: 6px 10px;
    color: #d9e5f5;
}

QListWidget {
    padding: 6px;
}

QListWidget::item {
    border-radius: 0px;
    padding: 8px;
}

QSlider {
    min-height: 16px;
    background: transparent;
}

QListWidget::item:selected {
    background: rgba(73, 125, 193, 0.24);
    color: #eef5ff;
}

QSlider::groove:horizontal {
    background: #30445f;
    height: 4px;
    border-radius: 0px;
}

QSlider::sub-page:horizontal {
    background: #5a88c5;
    border-radius: 0px;
}

QSlider::handle:horizontal {
    background: #e7f0ff;
    border: 1px solid #6d9ad5;
    width: 12px;
    margin: -4px 0;
    border-radius: 6px;
}

QFrame#Card {
    background: #141f2f;
    border: 1px solid #2e415a;
    border-radius: 0px;
}

QLabel#TitleLabel {
    font-size: 24px;
    font-weight: 700;
    color: #eaf3ff;
}

QLabel#MetaLabel {
    font-size: 13px;
    color: #97adcb;
}

QLabel#CaptionLabel {
    font-size: 12px;
    color: #7d94b3;
}

QToolButton#ControlIconButton,
QToolButton#ModeButton,
QToolButton#CompactButton,
QToolButton#CompactTopButton {
    min-width: 30px;
    min-height: 30px;
    max-width: 30px;
    max-height: 30px;
    border-radius: 0px;
    border: 1px solid #3a5475;
    background: #1a2434;
}

QToolButton#ControlIconButton:hover,
QToolButton#ModeButton:hover,
QToolButton#CompactButton:hover,
QToolButton#CompactTopButton:hover {
    background: #223149;
    border-color: #5c7fae;
}

QToolButton#VolumeIconButton {
    min-width: 30px;
    min-height: 30px;
    max-width: 30px;
    max-height: 30px;
    border-radius: 0px;
    border: 1px solid #3a5475;
    background: #1a2434;
}

QToolButton#VolumeIconButton:hover {
    background: #223149;
    border-color: #5c7fae;
}

QToolButton#ModeButton {
    border-color: #5578a8;
    background: #20324c;
}

QToolButton#SidebarToggle {
    min-width: 22px;
    max-width: 22px;
    min-height: 54px;
    max-height: 54px;
    border-radius: 0px;
    border: 1px solid #3b5474;
    background: #1a2434;
}

QToolButton#SidebarToggle:hover {
    background: #223149;
}

QWidget#VolumePanel {
    background: transparent;
}

QLabel#VolumeValueLabel {
    font-size: 12px;
    color: #c9d8ee;
    background: #1a2434;
    border: 1px solid #3a5475;
    border-radius: 0px;
    padding: 0px 2px;
}

QWidget#CompactTopBar {
    background: #1a2434;
    border: 1px solid #314660;
    border-radius: 0px;
}

QToolButton#CompactTopButton {
    min-width: 24px;
    min-height: 24px;
    max-width: 24px;
    max-height: 24px;
    border-radius: 0px;
}

QToolButton#TrackDeleteButton {
    min-width: 12px;
    min-height: 12px;
    max-width: 12px;
    max-height: 12px;
    border: none;
    background: transparent;
    color: #ff6b6b;
    font-size: 12px;
    font-weight: 700;
    padding: 0px;
}

QToolButton#TrackDeleteButton:hover {
    color: #ff4d4d;
    background: transparent;
}

QToolButton#LocateCurrentButton {
    min-width: 20px;
    min-height: 20px;
    max-width: 20px;
    max-height: 20px;
    border: 1px solid #5c7fae;
    border-radius: 10px;
    background: #1a2434;
}

QToolButton#LocateCurrentButton:hover {
    background: #223149;
    border-color: #7aa2d7;
}

QLabel#TrackTitleLabel {
    color: #dbe7f7;
    background: transparent;
}

QLabel#CompactTitleLabel {
    font-size: 14px;
    font-weight: 700;
    color: #d9e8ff;
}

QLabel#CompactLyricLineLabel {
    font-size: 12px;
    color: #a9bedc;
}

QLabel#LyricTimeLabel {
    font-size: 11px;
    color: #8da7ca;
    background: transparent;
}

QLabel#LyricTextLabel {
    font-size: 13px;
    color: #dce9fb;
    background: transparent;
}

QWidget#TrackRowWidget,
QWidget#LyricRowWidget {
    background: transparent;
}

QListWidget#lyrics_list,
QListWidget#track_list {
    background: #182233;
}

QLabel#CompactTopTitle {
    font-size: 14px;
    font-weight: 700;
    color: #d9e8ff;
    background: transparent;
}

QSplitter::handle:horizontal {
    width: 6px;
    background: transparent;
}

QSplitter::handle:horizontal:hover {
    background: rgba(120, 163, 220, 0.34);
}
"""

APP_STYLE = APP_STYLE_DARK
