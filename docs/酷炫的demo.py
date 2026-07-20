
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
        """初始化任务栏进度条演示窗口。参数：无。返回值：无。"""
        super().__init__()  # 调用父类的构造函数
        self.setWindowTitle("任务栏进度条 Demo")  # 设置窗口标题为“任务栏进度条 Demo”
        self.setFixedSize(440, 560)  # 设置窗口固定大小为440像素宽、560像素高

        # 获取 COM 接口
        self.taskbar = create_taskbar()  # 创建Windows任务栏COM接口实例

        # 当前状态
        self.current_state = TBPF_NORMAL  # 初始化进度条状态为正常模式
        self.current_value = 0  # 初始化进度条值为0
        self.auto_running = False  # 初始化自动运行为False

        self._build_ui()  # 构建用户界面
        self._apply_base_style()  # 应用基础样式
        self._sync_colors()  # 同步颜色设置
        self._push_to_taskbar()  # 将状态推送到任务栏进度条

    # ─────────────── UI 构建 ───────────────

    def _build_ui(self):
        """构建用户界面

        初始化并组装主窗口的所有UI组件，包括标题、进度显示、控制控件和提示信息。
        本方法不接收参数，也不返回任何值。
        """
        root = QWidget()  # 创建主容器部件
        self.setCentralWidget(root)  # 将主容器设置为窗口的中心部件
        vbox = QVBoxLayout(root)  # 在主容器上创建垂直布局管理器
        vbox.setContentsMargins(28, 24, 28, 20)  # 设置布局的边距（左、上、右、下）
        vbox.setSpacing(14)  # 设置布局内各部件之间的间距

        # 标题
        title = QLabel("任务栏进度条演示")  # 创建标题标签
        title.setFont(QFont("Microsoft YaHei", 17, QFont.Bold))  # 设置标题字体、大小和加粗
        title.setAlignment(Qt.AlignCenter)  # 设置标题文本居中对齐
        vbox.addWidget(title)  # 将标题添加到垂直布局

        # ── 进度数值 + 进度条 ──
        card1 = self._card()  # 创建第一个卡片容器
        c1 = QVBoxLayout(card1)  # 在卡片上创建垂直布局
        self.lbl_value = QLabel("0 %")  # 创建显示百分比的标签
        self.lbl_value.setFont(QFont("Consolas", 36, QFont.Bold))  # 设置百分比标签的等宽字体、大小和加粗
        self.lbl_value.setAlignment(Qt.AlignCenter)  # 设置百分比文本居中对齐
        c1.addWidget(self.lbl_value)  # 将百分比标签添加到卡片布局

        self.bar = QProgressBar()  # 创建进度条控件
        self.bar.setRange(0, 100)  # 设置进度条范围从0到100
        self.bar.setValue(0)  # 设置进度条初始值为0
        self.bar.setTextVisible(False)  # 隐藏进度条上的文本显示
        self.bar.setFixedHeight(14)  # 设置进度条固定高度为14像素
        c1.addWidget(self.bar)  # 将进度条添加到卡片布局

        self.lbl_desc = QLabel()  # 创建描述信息标签
        self.lbl_desc.setAlignment(Qt.AlignCenter)  # 设置描述文本居中对齐
        self.lbl_desc.setObjectName("descLabel")  # 设置标签的 objectName 以便在样式表中定位
        c1.addWidget(self.lbl_desc)  # 将描述标签添加到卡片布局
        vbox.addWidget(card1)  # 将第一个卡片添加到主垂直布局

        # ── 手动滑动条 ──
        card2 = self._card()  # 创建第二个卡片容器
        c2 = QVBoxLayout(card2)  # 在卡片上创建垂直布局
        c2.addWidget(self._heading("手动控制"))  # 添加“手动控制”标题
        self.slider = QSlider(Qt.Horizontal)  # 创建一个水平滑动条
        self.slider.setRange(0, 100)  # 设置滑动条范围从0到100
        self.slider.setValue(0)  # 设置滑动条初始值为0
        self.slider.valueChanged.connect(self._on_slider)  # 将滑动条的值改变信号连接到处理函数
        c2.addWidget(self.slider)  # 将滑动条添加到卡片布局
        vbox.addWidget(card2)  # 将第二个卡片添加到主垂直布局

        # ── 状态单选 ──
        card3 = self._card()  # 创建第三个卡片容器
        c3 = QVBoxLayout(card3)  # 在卡片上创建垂直布局
        c3.addWidget(self._heading("进度状态"))  # 添加“进度状态”标题
        row1, row2 = QHBoxLayout(), QHBoxLayout()  # 创建两个水平布局用于容纳单选按钮行
        self.radio_group = QButtonGroup(self)  # 创建一个按钮组来管理单选按钮
        self.radios = {}  # 创建一个字典来按标志存储单选按钮的引用
        # 遍历状态顺序列表，为每个状态创建单选按钮
        for idx, flag in enumerate(STATE_ORDER):
            meta = STATE_META[flag]  # 获取当前状态的元数据
            rb = QRadioButton(meta["label"])  # 创建单选按钮，并设置标签文本
            rb.setCursor(Qt.PointingHandCursor)  # 设置鼠标悬停时为手形指针
            rb.setProperty("color", meta["color"])  # 将颜色信息存储为部件的自定义属性
            self.radio_group.addButton(rb, idx)  # 将单选按钮添加到组，并分配唯一ID（索引）
            self.radios[flag] = rb  # 将按钮按状态标志存入字典
            # 根据索引将按钮分配到第一行（前3个）或第二行（后2个）
            (row1 if idx < 3 else row2).addWidget(rb)
        # 设置默认选中的状态为 TBPF_NORMAL
        self.radio_group.button(STATE_TO_INDEX[TBPF_NORMAL]).setChecked(True)
        # 将按钮组中任何按钮被点击的信号连接到处理函数
        self.radio_group.idClicked.connect(self._on_state_radio)
        c3.addLayout(row1)  # 将第一行布局添加到卡片
        c3.addLayout(row2)  # 将第二行布局添加到卡片
        vbox.addWidget(card3)  # 将第三个卡片添加到主垂直布局

        # ── 按钮行 ──
        hbox = QHBoxLayout()  # 创建水平布局用于放置操作按钮
        self.btn_auto = QPushButton("▶  自动演示")  # 创建“自动演示”按钮
        self.btn_auto.setCursor(Qt.PointingHandCursor)  # 设置鼠标悬停时为手形指针
        self.btn_auto.setObjectName("btnPrimary")  # 设置按钮的 objectName 以便在样式表中定位
        self.btn_auto.clicked.connect(self._toggle_auto)  # 将按钮的点击信号连接到切换自动演示的函数
        hbox.addWidget(self.btn_auto)  # 将按钮添加到水平布局

        self.btn_reset = QPushButton("重  置")  # 创建“重置”按钮
        self.btn_reset.setCursor(Qt.PointingHandCursor)  # 设置鼠标悬停时为手形指针
        self.btn_reset.setObjectName("btnSecondary")  # 设置按钮的 objectName 以便在样式表中定位
        self.btn_reset.clicked.connect(self._reset)  # 将按钮的点击信号连接到重置函数
        hbox.addWidget(self.btn_reset)  # 将按钮添加到水平布局
        vbox.addLayout(hbox)  # 将按钮行布局添加到主垂直布局

        # ── 底部提示 ──
        tip = QLabel("最小化窗口后，观察任务栏图标上的进度效果")  # 创建底部提示标签
        tip.setObjectName("tipLabel")  # 设置标签的 objectName 以便在样式表中定位
        tip.setAlignment(Qt.AlignCenter)  # 设置提示文本居中对齐
        tip.setWordWrap(True)  # 启用自动换行
        vbox.addWidget(tip)  # 将提示标签添加到主垂直布局
        vbox.addStretch()  # 添加一个弹性空间，将上方的部件推向顶部

        # 自动演示定时器
        self.timer = QTimer(self)  # 创建一个定时器对象，父级为当前窗口
        self.timer.timeout.connect(self._auto_tick)  # 将定时器的超时信号连接到自动演示的步进函数

    # ─────────────── 辅助方法 ───────────────

    @staticmethod
    def _card() -> QFrame:
        """
        创建一个QFrame对象，并设置其对象名为"card"，然后返回该对象。

        参数：无
        返回值：QFrame对象
        """
        f = QFrame()  # 创建一个新的QFrame实例
        f.setObjectName("card")  # 设置对象名为"card"
        return f  # 返回创建的QFrame对象

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
        """
        切换自动运行状态。
        如果当前处于自动运行状态，则停止自动运行；否则启动自动运行。
        参数：
            无。
        返回值：
            无。该方法通过调用内部的 _stop_auto 或 _start_auto 方法来改变状态，不返回任何值。
        """
        if self.auto_running:  # 检查当前是否处于自动运行状态
            self._stop_auto()  # 如果是，则停止自动运行
        else:
            self._start_auto()  # 如果不是，则启动自动运行

    def _start_auto(self):
        """启动自动演示循环。

        该方法开始一个自动播放演示，更新界面控件状态，并同步任务栏进度。
        注意：无参数传入，无返回值。
        """
        # 设置自动运行标志为真
        self.auto_running = True
        # 更新按钮文本以指示停止操作
        self.btn_auto.setText("⏸  停止演示")
        # 禁用滑块，防止用户在演示期间交互
        self.slider.setEnabled(False)
        # 重置到正常 0%
        self.current_state = TBPF_NORMAL
        self.current_value = 0
        # 将滑块值重置为0
        self.slider.setValue(0)
        # 根据当前状态设置对应的单选按钮为选中状态
        self.radio_group.button(STATE_TO_INDEX[TBPF_NORMAL]).setChecked(True)
        # 同步界面颜色设置
        self._sync_colors()
        # 将进度状态推送到操作系统任务栏
        self._push_to_taskbar()
        # 启动定时器，间隔40毫秒（约25fps），实现约4秒的演示循环
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
        """重置任务栏进度条状态到初始值。
    
        该方法会停止自动运行、清空进度值、更新UI控件状态，
        并同步任务栏显示。
    
        Args:
            无
        
        Returns:
            无
        """
        if self.auto_running:  # 检查是否正在自动运行
            self._stop_auto()  # 若正在运行则停止自动模式
    
        self.current_state = TBPF_NORMAL  # 将状态重置为正常模式
        self.current_value = 0  # 将进度值清零
    
        self.slider.setValue(0)  # 更新滑块控件到0位置
        self.radio_group.button(STATE_TO_INDEX[TBPF_NORMAL]).setChecked(True)  # 选中正常状态对应的单选按钮
    
        self._sync_colors()  # 同步颜色主题到界面
        self._push_to_taskbar()  # 将当前状态推送到任务栏显示

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
