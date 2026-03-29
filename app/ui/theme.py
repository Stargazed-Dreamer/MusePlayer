from __future__ import annotations

APP_STYLE = """
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
    border-radius: 10px;
    padding: 8px;
}

QMenuBar::item {
    padding: 6px 12px;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.0);
    border: 1px solid transparent;
    margin: 1px;
}

QMenuBar::item:selected {
    background: rgba(30, 88, 153, 0.24);
    border: 1px solid #b8d0ea;
}

QMenuBar::item:pressed {
    background: rgba(30, 88, 153, 0.32);
    border: 1px solid #9dbde0;
}

QMenu {
    background: #ffffff;
    border: 1px solid #dbe5f2;
    padding: 6px;
    border-radius: 10px;
}

QMenu::item {
    padding: 8px 14px;
    border-radius: 8px;
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
    border-radius: 12px;
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
    border-radius: 10px;
    padding: 6px 10px;
}

QListWidget {
    padding: 6px;
}

QListWidget::item {
    border-radius: 8px;
    padding: 8px;
}

QListWidget::item:selected {
    background: rgba(30, 88, 153, 0.16);
    color: #0e1f36;
}

QSlider::groove:horizontal {
    background: #d8e4f2;
    height: 6px;
    border-radius: 3px;
}

QSlider::sub-page:horizontal {
    background: #1e5899;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background: #ffffff;
    border: 2px solid #1e5899;
    width: 14px;
    margin: -6px 0;
    border-radius: 7px;
}

QFrame#Card {
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid #dce6f3;
    border-radius: 16px;
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
    border-radius: 15px;
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
    border-radius: 11px;
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
    border-radius: 10px;
}

QToolButton#CompactTopButton {
    min-width: 24px;
    min-height: 24px;
    max-width: 24px;
    max-height: 24px;
    border-radius: 12px;
}

QToolButton#TrackDeleteButton {
    min-width: 20px;
    min-height: 20px;
    max-width: 20px;
    max-height: 20px;
    border-radius: 10px;
    border: 1px solid #e4c6cc;
    background: #fff6f7;
}

QToolButton#TrackDeleteButton:hover {
    border-color: #d896a0;
    background: #ffecee;
}

QLabel#TrackTitleLabel {
    color: #132033;
}

QLabel#CompactTitleLabel {
    font-size: 15px;
    font-weight: 700;
    color: #15385f;
}

QLabel#CompactLyricLineLabel {
    font-size: 12px;
    color: #4f6a85;
}

QSplitter::handle:horizontal {
    width: 6px;
    background: transparent;
}

QSplitter::handle:horizontal:hover {
    background: rgba(30, 88, 153, 0.20);
}
"""
