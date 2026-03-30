
"""
Windows 任务栏进度条 Demo
依赖: PySide6, comtypes
原理: 通过 ITaskbarList3 COM 接口控制任务栏图标上的进度条
"""

import sys
import ctypes
from ctypes import c_void_p, c_int, c_uint, c_ulonglong, HRESULT

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSlider, QPushButton, QLabel, QFrame, QButtonGroup, QRadioButton,
    QProgressBar,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

import comtypes
from comtypes import IUnknown, GUID, COMMETHOD

# ═══════════════════════ COM 接口定义 ═══════════════════════

CLSID_TaskbarList = GUID("{56FDF344-FD6D-11d0-958A-006097C9A090}")
IID_ITaskbarList3  = GUID("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")

# 进度状态标志
TBPF_NOPROGRESS    = 0x00000000  # 无进度条
TBPF_INDETERMINATE = 0x00000001  # 不确定（滚动绿色）
TBPF_NORMAL        = 0x00000002  # 正常（绿色）
TBPF_ERROR         = 0x00000003  # 错误（红色）
TBPF_PAUSED        = 0x00000004  # 暂停（黄色）


class ITaskbarList3(IUnknown):
    """
    ITaskbarList3 COM 接口的 Python 声明。
    继承链: IUnknown -> ITaskbarList(5方法) -> ITaskbarList2(+1方法) -> ITaskbarList3(+12方法)
    所有方法必须按 vtable 顺序完整声明，否则偏移量会错位。
    """
    _iid_ = IID_ITaskbarList3
    _methods_ = [
        # ── ITaskbarList ──
        COMMETHOD([], HRESULT, "HrInit"),
        COMMETHOD([], HRESULT, "AddTab",       (["in"], c_void_p, "hwnd")),
        COMMETHOD([], HRESULT, "DeleteTab",    (["in"], c_void_p, "hwnd")),
        COMMETHOD([], HRESULT, "ActivateTab",  (["in"], c_void_p, "hwnd")),
        COMMETHOD([], HRESULT, "SetActiveAlt", (["in"], c_void_p, "hwnd")),
        # ── ITaskbarList2 ──
        COMMETHOD([], HRESULT, "MarkFullscreenWindow",
                  (["in"], c_void_p, "hwnd"), (["in"], c_int, "fFullscreen")),
        # ── ITaskbarList3（本 demo 仅使用前两个） ──
        COMMETHOD([], HRESULT, "SetProgressValue",
                  (["in"], c_void_p, "hwnd"),
                  (["in"], c_ulonglong, "ullCompleted"),
                  (["in"], c_ulonglong, "ullTotal")),
        COMMETHOD([], HRESULT, "SetProgressState",
                  (["in"], c_void_p, "hwnd"),
                  (["in"], c_int, "tbpFlags")),
        # ── 以下方法保持 vtable 对齐，demo 中不调用 ──
        COMMETHOD([], HRESULT, "RegisterTab",
                  (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2")),
        COMMETHOD([], HRESULT, "UnregisterTab",       (["in"], c_void_p, "h")),
        COMMETHOD([], HRESULT, "SetTabOrder",
                  (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2")),
        COMMETHOD([], HRESULT, "SetTabActive",
                  (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2"), (["in"], c_int, "f")),
        COMMETHOD([], HRESULT, "ThumbBarAddButtons",
                  (["in"], c_void_p, "h"), (["in"], c_uint, "n"), (["in"], c_void_p, "p")),
        COMMETHOD([], HRESULT, "ThumbBarUpdateButtons",
                  (["in"], c_void_p, "h"), (["in"], c_uint, "n"), (["in"], c_void_p, "p")),
        COMMETHOD([], HRESULT, "ThumbBarSetImageList",
                  (["in"], c_void_p, "h"), (["in"], c_void_p, "p")),
        COMMETHOD([], HRESULT, "SetOverlayIcon",
                  (["in"], c_void_p, "h"), (["in"], c_void_p, "p1"), (["in"], c_void_p, "p2")),
        COMMETHOD([], HRESULT, "SetThumbnailTooltip",
                  (["in"], c_void_p, "h"), (["in"], c_void_p, "p")),
        COMMETHOD([], HRESULT, "SetThumbnailClip",
                  (["in"], c_void_p, "h"), (["in"], c_void_p, "p")),
    ]


def create_taskbar() -> ITaskbarList3:
    """创建并初始化 ITaskbarList3 COM 实例"""
    tb = comtypes.CoCreateInstance(
        CLSID_TaskbarList, ITaskbarList3, comtypes.CLSCTX_INPROC_SERVER
    )
    tb.HrInit()
    return tb


# ═══════════════════════ 状态元数据 ═══════════════════════

STATE_META = {
    TBPF_NOPROGRESS:    {"label": "无进度",   "color": "#8e8e93", "desc": "任务栏不显示进度条"},
    TBPF_INDETERMINATE: {"label": "不确定",   "color": "#5dade2", "desc": "滚动动画，进度未知"},
    TBPF_NORMAL:        {"label": "正常",     "color": "#00d4aa", "desc": "绿色进度条"},
    TBPF_ERROR:         {"label": "错误",     "color": "#ff4757", "desc": "红色进度条"},
    TBPF_PAUSED:        {"label": "暂停",     "color": "#ffa502", "desc": "黄色进度条"},
}

STATE_ORDER = [TBPF_NOPROGRESS, TBPF_INDETERMINATE, TBPF_NORMAL, TBPF_ERROR, TBPF_PAUSED]
STATE_TO_INDEX = {s: i for i, s in enumerate(STATE_ORDER)}


# ═══════════════════════ 主窗口 ═══════════════════════

class TaskbarProgressDemo(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("任务栏进度条 Demo")
        self.setFixedSize(440, 560)

        # 获取 COM 接口
        self.taskbar = create_taskbar()

        # 当前状态
        self.current_state = TBPF_NORMAL
        self.current_value = 0
        self.auto_running = False

        self._build_ui()
        self._apply_base_style()
        self._sync_colors()
        self._push_to_taskbar()

    # ─────────────── UI 构建 ───────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(28, 24, 28, 20)
        vbox.setSpacing(14)

        # 标题
        title = QLabel("任务栏进度条演示")
        title.setFont(QFont("Microsoft YaHei", 17, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        vbox.addWidget(title)

        # ── 进度数值 + 进度条 ──
        card1 = self._card()
        c1 = QVBoxLayout(card1)
        self.lbl_value = QLabel("0 %")
        self.lbl_value.setFont(QFont("Consolas", 36, QFont.Bold))
        self.lbl_value.setAlignment(Qt.AlignCenter)
        c1.addWidget(self.lbl_value)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        c1.addWidget(self.bar)

        self.lbl_desc = QLabel()
        self.lbl_desc.setAlignment(Qt.AlignCenter)
        self.lbl_desc.setObjectName("descLabel")
        c1.addWidget(self.lbl_desc)
        vbox.addWidget(card1)

        # ── 手动滑动条 ──
        card2 = self._card()
        c2 = QVBoxLayout(card2)
        c2.addWidget(self._heading("手动控制"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(0)
        self.slider.valueChanged.connect(self._on_slider)
        c2.addWidget(self.slider)
        vbox.addWidget(card2)

        # ── 状态单选 ──
        card3 = self._card()
        c3 = QVBoxLayout(card3)
        c3.addWidget(self._heading("进度状态"))
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        self.radio_group = QButtonGroup(self)
        self.radios = {}
        for idx, flag in enumerate(STATE_ORDER):
            meta = STATE_META[flag]
            rb = QRadioButton(meta["label"])
            rb.setCursor(Qt.PointingHandCursor)
            rb.setProperty("color", meta["color"])
            self.radio_group.addButton(rb, idx)
            self.radios[flag] = rb
            (row1 if idx < 3 else row2).addWidget(rb)
        self.radio_group.button(STATE_TO_INDEX[TBPF_NORMAL]).setChecked(True)
        self.radio_group.idClicked.connect(self._on_state_radio)
        c3.addLayout(row1)
        c3.addLayout(row2)
        vbox.addWidget(card3)

        # ── 按钮行 ──
        hbox = QHBoxLayout()
        self.btn_auto = QPushButton("▶  自动演示")
        self.btn_auto.setCursor(Qt.PointingHandCursor)
        self.btn_auto.setObjectName("btnPrimary")
        self.btn_auto.clicked.connect(self._toggle_auto)
        hbox.addWidget(self.btn_auto)

        self.btn_reset = QPushButton("重  置")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.setObjectName("btnSecondary")
        self.btn_reset.clicked.connect(self._reset)
        hbox.addWidget(self.btn_reset)
        vbox.addLayout(hbox)

        # ── 底部提示 ──
        tip = QLabel("最小化窗口后，观察任务栏图标上的进度效果")
        tip.setObjectName("tipLabel")
        tip.setAlignment(Qt.AlignCenter)
        tip.setWordWrap(True)
        vbox.addWidget(tip)
        vbox.addStretch()

        # 自动演示定时器
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._auto_tick)

    # ─────────────── 辅助方法 ───────────────

    @staticmethod
    def _card() -> QFrame:
        f = QFrame()
        f.setObjectName("card")
        return f

    @staticmethod
    def _heading(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        return lbl

    def _hwnd(self) -> int:
        return int(self.winId())

    # ─────────────── 样式 ───────────────

    def _apply_base_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #0d0d1a;
                color: #e4e4f0;
            }
            #card {
                background-color: #161628;
                border: 1px solid #252545;
                border-radius: 12px;
            }
            #card > * {          /* 让 QVBox 内部内容有 padding */
                /* 无法直接给 layout 设 padding，改用 setContentsMargins */
            }
            #descLabel {
                color: #7a7a9a;
                font-size: 12px;
                margin-top: 2px;
            }
            #tipLabel {
                color: #55557a;
                font-size: 12px;
                padding: 4px 8px;
            }
            QRadioButton {
                spacing: 6px;
                font-size: 13px;
                color: #c8c8e0;
            }
            QRadioButton::indicator {
                width: 16px; height: 16px;
                border-radius: 8px;
                border: 2px solid #3a3a60;
                background: transparent;
            }
            #btnPrimary {
                background-color: #00d4aa;
                color: #0d0d1a;
                border: none;
                border-radius: 8px;
                padding: 10px 0;
                font-size: 14px;
                font-weight: bold;
            }
            #btnPrimary:hover  { background-color: #00eebb; }
            #btnPrimary:pressed{ background-color: #00b894; }
            #btnSecondary {
                background-color: #252545;
                color: #c8c8e0;
                border: 1px solid #35355a;
                border-radius: 8px;
                padding: 10px 0;
                font-size: 14px;
                font-weight: bold;
            }
            #btnSecondary:hover  { background-color: #30305a; }
            #btnSecondary:pressed{ background-color: #1e1e3a; }
        """)
        # 给 card 内部加 padding
        for child in self.findChildren(QFrame):
            if child.objectName() == "card":
                lay = child.layout()
                if lay:
                    lay.setContentsMargins(16, 14, 16, 14)
                    lay.setSpacing(8)

    def _sync_colors(self):
        """根据 current_state 同步所有控件颜色"""
        color = STATE_META[self.current_state]["color"]
        desc  = STATE_META[self.current_state]["desc"]

        # 数值标签
        self.lbl_value.setText(f"{self.current_value} %")
        self.lbl_value.setStyleSheet(f"color:{color};")

        # 描述
        self.lbl_desc.setText(desc)

        # 进度条
        if self.current_state == TBPF_INDETERMINATE:
            self.bar.setRange(0, 0)       # Qt 不确定模式
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(self.current_value)
        self.bar.setStyleSheet(f"""
            QProgressBar {{
                background: #252545; border-radius: 7px; border: none;
            }}
            QProgressBar::chunk {{
                background: {color}; border-radius: 7px;
            }}
        """)

        # 滑动条
        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: #252545; height: 8px; border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {color}; width: 20px; height: 20px;
                margin: -6px 0; border-radius: 10px;
            }}
            QSlider::sub-page:horizontal {{
                background: {color}; border-radius: 4px;
            }}
        """)

        # 单选按钮 indicator 颜色
        for flag, rb in self.radios.items():
            c = STATE_META[flag]["color"]
            rb.setStyleSheet(f"""
                QRadioButton {{
                    spacing: 6px; font-size: 13px; color: #c8c8e0;
                }}
                QRadioButton::indicator {{
                    width: 16px; height: 16px; border-radius: 8px;
                    border: 2px solid #3a3a60; background: transparent;
                }}
                QRadioButton::indicator:checked {{
                    border-color: {c}; background: {c};
                }}
            """)

        # 主按钮跟随主色
        self.btn_auto.setStyleSheet(f"""
            #btnPrimary {{
                background-color: {color}; color: #0d0d1a;
                border: none; border-radius: 8px;
                padding: 10px 0; font-size: 14px; font-weight: bold;
            }}
            #btnPrimary:hover  {{ background-color: {color}cc; }}
            #btnPrimary:pressed{{ background-color: {color}99; }}
        """)

    # ─────────────── 任务栏通信 ───────────────

    def _push_to_taskbar(self):
        hwnd = self._hwnd()
        self.taskbar.SetProgressState(hwnd, self.current_state)
        if self.current_state in (TBPF_NORMAL, TBPF_ERROR, TBPF_PAUSED):
            self.taskbar.SetProgressValue(hwnd, self.current_value, 100)

    # ─────────────── 事件处理 ───────────────

    def _on_slider(self, val: int):
        self.current_value = val
        self.lbl_value.setText(f"{val} %")
        if self.current_state != TBPF_INDETERMINATE:
            self.bar.setValue(val)
        self._push_to_taskbar()

    def _on_state_radio(self, btn_id: int):
        self.current_state = STATE_ORDER[btn_id]
        self._sync_colors()
        self._push_to_taskbar()

    def _toggle_auto(self):
        if self.auto_running:
            self._stop_auto()
        else:
            self._start_auto()

    def _start_auto(self):
        self.auto_running = True
        self.btn_auto.setText("⏸  停止演示")
        self.slider.setEnabled(False)
        # 重置到正常 0%
        self.current_state = TBPF_NORMAL
        self.current_value = 0
        self.slider.setValue(0)
        self.radio_group.button(STATE_TO_INDEX[TBPF_NORMAL]).setChecked(True)
        self._sync_colors()
        self._push_to_taskbar()
        self.timer.start(40)  # ~25fps，约 4 秒走完

    def _stop_auto(self):
        self.timer.stop()
        self.auto_running = False
        self.btn_auto.setText("▶  自动演示")
        self.slider.setEnabled(True)

    def _auto_tick(self):
        """自动演示：0→100，然后依次展示 错误→暂停→不确定→循环"""
        if self.current_value < 100:
            self.current_value += 1
            self.slider.setValue(self.current_value)
            self.lbl_value.setText(f"{self.current_value} %")
            self.bar.setValue(self.current_value)
            self._push_to_taskbar()
        else:
            self.timer.stop()
            self._auto_show_states(0)

    def _auto_show_states(self, step: int):
        """依次展示各状态"""
        if not self.auto_running:
            return
        demo_seq = [
            (TBPF_ERROR,         100, 1200),
            (TBPF_PAUSED,         72, 1200),
            (TBPF_INDETERMINATE,   0, 2000),
        ]
        if step >= len(demo_seq):
            # 一轮结束，重新开始
            self.current_state = TBPF_NORMAL
            self.current_value = 0
            self.slider.setValue(0)
            self.radio_group.button(STATE_TO_INDEX[TBPF_NORMAL]).setChecked(True)
            self._sync_colors()
            self._push_to_taskbar()
            self.timer.start(40)
            return
        state, val, delay = demo_seq[step]
        self.current_state = state
        self.current_value = val
        self.slider.setValue(val)
        self.radio_group.button(STATE_TO_INDEX[state]).setChecked(True)
        self._sync_colors()
        self._push_to_taskbar()
        QTimer.singleShot(delay, lambda: self._auto_show_states(step + 1))

    def _reset(self):
        if self.auto_running:
            self._stop_auto()
        self.current_state = TBPF_NORMAL
        self.current_value = 0
        self.slider.setValue(0)
        self.radio_group.button(STATE_TO_INDEX[TBPF_NORMAL]).setChecked(True)
        self._sync_colors()
        self._push_to_taskbar()

    def closeEvent(self, event):
        """关闭时清除任务栏进度"""
        try:
            self.taskbar.SetProgressState(self._hwnd(), TBPF_NOPROGRESS)
        except Exception:
            pass
        event.accept()


# ═══════════════════════ 入口 ═══════════════════════

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")          # 使用 Fusion 风格确保跨平台一致性
    win = TaskbarProgressDemo()
    win.show()
    sys.exit(app.exec())
