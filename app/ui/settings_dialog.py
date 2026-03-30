from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.models.entities import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(460, 340)
        self._settings = settings

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

        self.timed_save_check = QCheckBox("启用定时保存")
        self.timed_save_check.setChecked(bool(self._settings.timed_save_enabled))
        self.timed_save_spin = QSpinBox()
        self.timed_save_spin.setRange(1, 1440)
        self.timed_save_spin.setValue(int(self._settings.timed_save_minutes))
        self.timed_save_spin.setSuffix(" 分钟")
        self.timed_save_check.toggled.connect(self._on_timed_save_toggled)

        self.logging_check = QCheckBox("启用日志（每次启动新建文件，保留最近10个）")
        self.logging_check.setChecked(bool(self._settings.logging_enabled))

        self.dark_theme_check = QCheckBox("默认夜间主题")
        self.dark_theme_check.setChecked(bool(getattr(self._settings, "dark_theme", True)))

        form.addRow("控制接口主机", self.host_edit)
        form.addRow("控制接口端口", self.port_spin)
        form.addRow("全局音量放大倍数", self.gain_boost_spin)
        form.addRow("读取策略", self.read_strategy_combo)
        form.addRow("定时保存间隔", self.timed_save_spin)
        root.addLayout(form)
        root.addWidget(self.control_interface_check)
        root.addWidget(self.auto_restore_check)
        root.addWidget(self.playlist_loop_mode_check)
        root.addWidget(self.collect_playback_data_check)
        root.addWidget(self.timed_save_check)
        root.addWidget(self.logging_check)
        root.addWidget(self.dark_theme_check)
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
        self.btn_ok.clicked.connect(self.accept)

    def output_settings(self) -> Settings:
        return Settings(
            control_host=self.host_edit.text().strip() or "127.0.0.1",
            control_port=int(self.port_spin.value()),
            control_interface_enabled=bool(self.control_interface_check.isChecked()),
            auto_restore_session=bool(self.auto_restore_check.isChecked()),
            logging_enabled=bool(self.logging_check.isChecked()),
            enable_playlist_loop_mode=bool(self.playlist_loop_mode_check.isChecked()),
            collect_playback_data=bool(self.collect_playback_data_check.isChecked()),
            global_gain_boost=float(self.gain_boost_spin.value()),
            read_strategy=str(self.read_strategy_combo.currentData() or "window"),
            timed_save_enabled=bool(self.timed_save_check.isChecked()),
            timed_save_minutes=int(self.timed_save_spin.value()),
            dark_theme=bool(self.dark_theme_check.isChecked()),
        )

    def _on_control_interface_toggled(self, enabled: bool) -> None:
        self.host_edit.setEnabled(bool(enabled))
        self.port_spin.setEnabled(bool(enabled))

    def _on_timed_save_toggled(self, enabled: bool) -> None:
        self.timed_save_spin.setEnabled(bool(enabled))
