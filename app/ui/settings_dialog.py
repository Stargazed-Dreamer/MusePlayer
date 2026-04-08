from __future__ import annotations

"""设置对话框。

提供应用的全面配置选项：
- 网络控制接口（IP地址、端口、启用状态）
- 播放行为（自动恢复、播放模式、数据收集）
- 音频处理（全局增益增强、读取策略）
- 界面偏好（主题、窗口记忆）
- 数据管理（定时保存、日志记录）

配置分类：
1. 控制接口设置：运行时TCP控制协议配置
2. 播放设置：会话恢复、播放模式偏好
3. 音频设置：增益增强、解码策略
4. 界面设置：主题、透明度、窗口行为
5. 数据设置：自动保存、日志记录

数据绑定：
- 所有控件直接绑定到Settings实体属性
- 实时验证输入的有效性（如端口号范围）
- 部分设置需要重启相关服务生效
"""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.models.entities import Settings
from core.output import list_output_devices


class SettingsDialog(QDialog):
    """应用设置对话框。
    
    功能特性：
    - 网络设置：配置运行时控制接口的监听地址和端口
    - 播放设置：控制会话恢复、播放模式扩展、统计收集等行为
    - 音频设置：调整全局增益增强系数和文件读取策略
    - 界面设置：主题选择、窗口记忆、透明度控制
    - 数据设置：定时保存间隔、日志记录等持久化选项
    
    设计特点：
    - 表单式布局，清晰的分组和相关性
    - 实时输入验证，防止无效配置
    - 条件启用/禁用相关选项组
    - 支持取消操作，不保存未确认的修改
    
    与Settings实体关系：
    - 对话框持有Settings实例的引用
    - 修改直接作用于实体属性
    - 需要外部代码调用SettingsStore保存到文件
    """
    
    def __init__(self, settings: Settings, parent=None):
        """初始化设置对话框。
        
        Args:
            settings: Settings实体实例，包含当前配置值
            parent: 父级窗口，用于模态显示
        """
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(480, 420)
        self._settings = settings  # 持有Settings实体引用

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        form = QFormLayout()
        self.host_edit = QLineEdit(self._settings.control_host)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self._settings.control_port))
        self.control_interface_check = QCheckBox("启用控制接口")
        self.control_interface_check.setChecked(bool(self._settings.control_interface_enabled))
        self.control_interface_check.toggled.connect(self._on_control_interface_toggled)

        self.auto_restore_check = QCheckBox("启动时恢复上次歌曲与进度")
        self.auto_restore_check.setChecked(bool(self._settings.auto_restore_session))

        self.playlist_loop_mode_check = QCheckBox("增加歌单循环到播放模式里")
        self.playlist_loop_mode_check.setChecked(bool(self._settings.enable_playlist_loop_mode))
        self.single_loop_mode_check = QCheckBox("增加单曲循环到播放模式里")
        self.single_loop_mode_check.setChecked(bool(getattr(self._settings, "enable_single_loop_mode", True)))

        self.collect_playback_data_check = QCheckBox("收集播放数据")
        self.collect_playback_data_check.setChecked(bool(self._settings.collect_playback_data))

        self.gain_boost_spin = QDoubleSpinBox()
        self.gain_boost_spin.setRange(0.5, 5.0)
        self.gain_boost_spin.setDecimals(2)
        self.gain_boost_spin.setSingleStep(0.05)
        self.gain_boost_spin.setValue(float(self._settings.global_gain_boost))
        self.gain_boost_spin.setSuffix("x")

        self.read_strategy_combo = QComboBox()
        self.read_strategy_combo.addItem("窗口读取（默认）", "window")
        self.read_strategy_combo.addItem("一次性读取全文件", "full")
        strategy = (self._settings.read_strategy or "window").strip().lower()
        idx = self.read_strategy_combo.findData(strategy)
        self.read_strategy_combo.setCurrentIndex(0 if idx < 0 else idx)

        self.output_device_combo = QComboBox()
        self.output_device_combo.addItem("跟随系统", "")
        current_device = str(getattr(self._settings, "output_device", "")).strip()
        for dev_info in list_output_devices():
            self.output_device_combo.addItem(dev_info["name"], dev_info["name"])
        dev_idx = self.output_device_combo.findData(current_device)
        self.output_device_combo.setCurrentIndex(0 if dev_idx < 0 else dev_idx)

        self.timed_save_check = QCheckBox("启用定时保存")
        self.timed_save_check.setChecked(bool(self._settings.timed_save_enabled))
        self.timed_save_spin = QSpinBox()
        self.timed_save_spin.setRange(1, 1440)
        self.timed_save_spin.setValue(int(self._settings.timed_save_minutes))
        self.timed_save_spin.setSuffix(" 分钟")
        self.timed_save_check.toggled.connect(self._on_timed_save_toggled)

        self.logging_check = QCheckBox("启用日志（每次启动新建文件，保留最近10个）")
        self.logging_check.setChecked(bool(self._settings.logging_enabled))
        self.crash_logging_check = QCheckBox("记录崩溃日志（不建议关闭）")
        self.crash_logging_check.setChecked(bool(getattr(self._settings, "crash_logging_enabled", True)))
        self.crash_logging_check.toggled.connect(self._on_crash_logging_toggled)
        self.data_maintenance_logging_check = QCheckBox("记录数据维护日志（不建议关闭）")
        self.data_maintenance_logging_check.setChecked(
            bool(getattr(self._settings, "data_maintenance_logging_enabled", True))
        )
        self.data_maintenance_logging_check.toggled.connect(self._on_data_maintenance_logging_toggled)

        self.dark_theme_check = QCheckBox("默认夜间主题")
        self.dark_theme_check.setChecked(bool(getattr(self._settings, "dark_theme", True)))

        self.remember_window_geometry_check = QCheckBox("记住上次窗口大小和位置")
        self.remember_window_geometry_check.setChecked(
            bool(getattr(self._settings, "remember_window_geometry", True))
        )
        self.max_window_width_edit = QLineEdit()
        self.max_window_height_edit = QLineEdit()
        self.max_window_width_edit.setPlaceholderText("0 表示不限制，最小 600")
        self.max_window_height_edit.setPlaceholderText("0 表示不限制，最小 800")
        max_w = int(getattr(self._settings, "max_window_width", 0))
        max_h = int(getattr(self._settings, "max_window_height", 0))
        self.max_window_width_edit.setText("" if max_w <= 0 else str(max_w))
        self.max_window_height_edit.setText("" if max_h <= 0 else str(max_h))
        self.max_window_warning_label = QLabel("")
        self.max_window_warning_label.setObjectName("InputWarningLabel")
        self.max_window_warning_label.setStyleSheet("color: #d34545;")
        self.max_window_warning_label.setVisible(False)

        form.addRow("控制接口主机", self.host_edit)
        form.addRow("控制接口端口", self.port_spin)
        form.addRow("全局音量放大倍数", self.gain_boost_spin)
        form.addRow("读取策略", self.read_strategy_combo)
        form.addRow("输出硬件", self.output_device_combo)
        form.addRow("定时保存间隔", self.timed_save_spin)
        form.addRow("最大窗口宽度", self.max_window_width_edit)
        form.addRow("最大窗口高度", self.max_window_height_edit)
        form.addRow("", self.max_window_warning_label)
        root.addLayout(form)
        root.addWidget(self.control_interface_check)
        root.addWidget(self.auto_restore_check)
        root.addWidget(self.playlist_loop_mode_check)
        root.addWidget(self.single_loop_mode_check)
        root.addWidget(self.collect_playback_data_check)
        root.addWidget(self.timed_save_check)
        root.addWidget(self.logging_check)
        root.addWidget(self.crash_logging_check)
        root.addWidget(self.data_maintenance_logging_check)
        root.addWidget(self.dark_theme_check)
        root.addWidget(self.remember_window_geometry_check)
        self._on_control_interface_toggled(self.control_interface_check.isChecked())
        self._on_timed_save_toggled(self.timed_save_check.isChecked())

        button_row = QHBoxLayout()
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("GhostButton")
        self.btn_ok = QPushButton("保存")
        button_row.addStretch(1)
        button_row.addWidget(self.btn_cancel)
        button_row.addWidget(self.btn_ok)
        root.addStretch(1)
        root.addLayout(button_row)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._on_accept_clicked)

        self.max_window_width_edit.textChanged.connect(self._validate_window_limits)
        self.max_window_height_edit.textChanged.connect(self._validate_window_limits)
        self._validate_window_limits()

    def _parse_max_window_value(self, text: str, minimum: int) -> tuple[int | None, str]:
        raw = text.strip()
        if not raw:
            return 0, ""
        try:
            value = int(raw)
        except Exception:
            return None, "最大窗口尺寸必须是整数。"
        if value <= 0:
            return 0, ""
        if value < minimum:
            return None, f"最大窗口尺寸过小：宽度至少 600，高度至少 800。"
        return value, ""

    def _validate_window_limits(self) -> bool:
        width, width_error = self._parse_max_window_value(self.max_window_width_edit.text(), 600)
        height, height_error = self._parse_max_window_value(self.max_window_height_edit.text(), 800)
        error = width_error or height_error
        valid = error == "" and width is not None and height is not None
        self.max_window_warning_label.setVisible(not valid)
        self.max_window_warning_label.setText(error)
        self.btn_ok.setEnabled(valid)
        return valid

    def _on_accept_clicked(self) -> None:
        if not self._validate_window_limits():
            return
        self.accept()

    def output_settings(self) -> Settings:
        return Settings(
            control_host=self.host_edit.text().strip() or "127.0.0.1",
            control_port=int(self.port_spin.value()),
            control_interface_enabled=bool(self.control_interface_check.isChecked()),
            auto_restore_session=bool(self.auto_restore_check.isChecked()),
            logging_enabled=bool(self.logging_check.isChecked()),
            crash_logging_enabled=bool(self.crash_logging_check.isChecked()),
            data_maintenance_logging_enabled=bool(self.data_maintenance_logging_check.isChecked()),
            enable_single_loop_mode=bool(self.single_loop_mode_check.isChecked()),
            enable_playlist_loop_mode=bool(self.playlist_loop_mode_check.isChecked()),
            collect_playback_data=bool(self.collect_playback_data_check.isChecked()),
            global_gain_boost=float(self.gain_boost_spin.value()),
            read_strategy=str(self.read_strategy_combo.currentData() or "window"),
            output_device=str(self.output_device_combo.currentData() or ""),
            timed_save_enabled=bool(self.timed_save_check.isChecked()),
            timed_save_minutes=int(self.timed_save_spin.value()),
            dark_theme=bool(self.dark_theme_check.isChecked()),
            remember_window_geometry=bool(self.remember_window_geometry_check.isChecked()),
            window_x=int(getattr(self._settings, "window_x", -1)),
            window_y=int(getattr(self._settings, "window_y", -1)),
            window_width=int(getattr(self._settings, "window_width", 0)),
            window_height=int(getattr(self._settings, "window_height", 0)),
            max_window_width=int(self._parse_max_window_value(self.max_window_width_edit.text(), 600)[0] or 0),
            max_window_height=int(self._parse_max_window_value(self.max_window_height_edit.text(), 800)[0] or 0),
        )

    def _on_control_interface_toggled(self, enabled: bool) -> None:
        self.host_edit.setEnabled(bool(enabled))
        self.port_spin.setEnabled(bool(enabled))

    def _on_timed_save_toggled(self, enabled: bool) -> None:
        self.timed_save_spin.setEnabled(bool(enabled))

    def _warn_before_disable(self, title: str, body: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(
            f"<span style='color:#d34545; font-weight:700;'>高风险操作</span><br/>{body}<br/>"
            "除非你非常确定自己在做什么，否则不建议关闭。"
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("仍然关闭")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_crash_logging_toggled(self, enabled: bool) -> None:
        if enabled:
            return
        ok = self._warn_before_disable(
            "关闭崩溃日志",
            "关闭后发生崩溃时将缺少追踪信息，可能无法定位问题。",
        )
        if ok:
            return
        self.crash_logging_check.blockSignals(True)
        self.crash_logging_check.setChecked(True)
        self.crash_logging_check.blockSignals(False)

    def _on_data_maintenance_logging_toggled(self, enabled: bool) -> None:
        if enabled:
            return
        ok = self._warn_before_disable(
            "关闭数据维护日志",
            "关闭后自动数据清理将不再留下核查记录，排障难度会明显增加。",
        )
        if ok:
            return
        self.data_maintenance_logging_check.blockSignals(True)
        self.data_maintenance_logging_check.setChecked(True)
        self.data_maintenance_logging_check.blockSignals(False)
