from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models.entities import Settings
from core.output import list_output_devices


class SettingsDialog(QDialog):

    def __init__(self, settings: Settings, parent=None):
        """
        初始化设置窗口。
    
        参数:
            settings (Settings): 一个Settings对象，用于管理设置。
            parent (QWidget, optional): 父窗口，默认为None。
    
        返回值:
            无
        """
        super().__init__(parent)  # 调用父类的构造方法，设置父窗口
        self.setWindowTitle("设置")  # 设置窗口标题为"设置"
        self.resize(520, 560)  # 调整窗口大小为520x560像素
        self._settings = settings  # 存储传入的设置对象，供后续使用
        self._build_ui()  # 调用方法构建用户界面

    def _build_ui(self) -> None:
        """构建设置对话框的用户界面。创建滚动区域、各个设置组和按钮行，并连接按钮信号到相应处理方法。"""
        root = QVBoxLayout(self)  # 初始化主垂直布局
        scroll = QScrollArea(self)  # 创建滚动区域以容纳设置内容
        scroll.setWidgetResizable(True)  # 允许滚动区域内的小部件可调整大小
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)  # 移除滚动区域的边框
        container = QWidget()  # 创建容器小部件
        layout = QVBoxLayout(container)  # 为容器设置垂直布局
        layout.setSpacing(10)  # 设置布局内小部件之间的间距为10像素

        self._build_playback_group(layout)  # 构建播放设置组
        self._build_lyrics_group(layout)  # 构建歌词设置组
        self._build_audio_group(layout)  # 构建音频设置组
        self._build_interface_group(layout)  # 构建界面设置组
        self._build_network_group(layout)  # 构建网络设置组
        self._build_data_group(layout)  # 构建数据设置组

        layout.addStretch(1)  # 添加弹性空间，将设置组推到顶部
        scroll.setWidget(container)  # 将容器设置为滚动区域的小部件
        root.addWidget(scroll, 1)  # 将滚动区域添加到根布局，并设置拉伸因子为1

        button_row = QHBoxLayout()  # 创建水平布局用于按钮行
        self.btn_cancel = QPushButton("取消")  # 创建取消按钮
        self.btn_cancel.setObjectName("GhostButton")  # 设置按钮对象名称以便样式表定位
        self.btn_ok = QPushButton("保存")  # 创建保存按钮
        button_row.addStretch(1)  # 添加弹性空间，将按钮推到右侧
        button_row.addWidget(self.btn_cancel)  # 将取消按钮添加到按钮行
        button_row.addWidget(self.btn_ok)  # 将保存按钮添加到按钮行
        root.addLayout(button_row)  # 将按钮行添加到根布局

        self.btn_cancel.clicked.connect(self.reject)  # 连接取消按钮点击信号到对话框的reject方法
        self.btn_ok.clicked.connect(self._on_accept_clicked)  # 连接保存按钮点击信号到_on_accept_clicked方法

        self._on_control_interface_toggled(self.control_interface_check.isChecked())  # 初始化控制接口切换状态
        self._on_timed_save_toggled(self.timed_save_check.isChecked())  # 初始化定时保存切换状态
        self._validate_window_limits()  # 验证并设置窗口限制

    def _build_playback_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("播放")
        form = QFormLayout(group)
        form.setSpacing(6)

        self.auto_restore_check = QCheckBox("启动时恢复上次歌曲与进度")
        self.auto_restore_check.setChecked(bool(self._settings.auto_restore_session))

        self.single_loop_mode_check = QCheckBox("增加单曲循环到播放模式里")
        self.single_loop_mode_check.setChecked(bool(getattr(self._settings, "enable_single_loop_mode", True)))

        self.playlist_loop_mode_check = QCheckBox("增加歌单循环到播放模式里")
        self.playlist_loop_mode_check.setChecked(bool(self._settings.enable_playlist_loop_mode))

        self.playlist_loop_sort_combo = QComboBox()
        self.playlist_loop_sort_combo.addItem("歌单导入默认顺序", "default")
        self.playlist_loop_sort_combo.addItem("歌名顺序", "title")
        self.playlist_loop_sort_combo.addItem("歌手顺序", "artist")
        sort_val = str(getattr(self._settings, "playlist_loop_sort", "default")).strip().lower()
        sort_idx = self.playlist_loop_sort_combo.findData(sort_val)
        self.playlist_loop_sort_combo.setCurrentIndex(0 if sort_idx < 0 else sort_idx)

        self.prefer_playlist_order_check = QCheckBox("优先使用歌单指定的顺序")
        self.prefer_playlist_order_check.setChecked(bool(getattr(self._settings, "prefer_playlist_order", False)))

        self.random_display_order_combo = QComboBox()
        self.random_display_order_combo.addItem("随机前顺序（默认）", "original")
        self.random_display_order_combo.addItem("随机后顺序", "random")
        rdo_val = str(getattr(self._settings, "random_display_order", "original")).strip().lower()
        rdo_idx = self.random_display_order_combo.findData(rdo_val)
        self.random_display_order_combo.setCurrentIndex(0 if rdo_idx < 0 else rdo_idx)

        self.collect_playback_data_check = QCheckBox("收集播放数据")
        self.collect_playback_data_check.setChecked(bool(self._settings.collect_playback_data))

        form.addRow(self.auto_restore_check)
        form.addRow(self.single_loop_mode_check)
        form.addRow(self.playlist_loop_mode_check)
        form.addRow("歌单循环排序", self.playlist_loop_sort_combo)
        form.addRow(self.prefer_playlist_order_check)
        form.addRow("随机模式显示顺序", self.random_display_order_combo)
        form.addRow(self.collect_playback_data_check)

        parent_layout.addWidget(group)

    def _build_lyrics_group(self, parent_layout: QVBoxLayout) -> None:
        """构建歌词设置组。

        功能：在父布局中添加歌词设置组，包含显示日语歌词和显示罗马音的复选框。
        参数：
            parent_layout (QVBoxLayout): 用于添加歌词组的垂直布局。
        返回值：无。
        """
        group = QGroupBox("歌词")  # 创建歌词设置组
        form = QFormLayout(group)  # 为组创建表单布局
        form.setSpacing(6)  # 设置表单布局的间距

        self.show_japanese_lyrics_check = QCheckBox("显示日语歌词")  # 创建显示日语歌词复选框
        # 从设置中安全获取显示日语歌词的值，并设置复选框状态
        self.show_japanese_lyrics_check.setChecked(bool(getattr(self._settings, "show_japanese_lyrics", True)))

        self.show_romaji_check = QCheckBox("日语歌词显示罗马音")  # 创建显示罗马音复选框
        # 从设置中安全获取显示罗马音的值，并设置复选框状态
        self.show_romaji_check.setChecked(bool(getattr(self._settings, "show_romaji", True)))

        form.addRow(self.show_japanese_lyrics_check)  # 将日语歌词复选框添加到表单布局
        form.addRow(self.show_romaji_check)  # 将罗马音复选框添加到表单布局

        parent_layout.addWidget(group)  # 将歌词组添加到父布局

    def _build_audio_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("音频")
        form = QFormLayout(group)
        form.setSpacing(6)

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

        form.addRow("全局音量放大倍数", self.gain_boost_spin)
        form.addRow("读取策略", self.read_strategy_combo)
        form.addRow("输出硬件", self.output_device_combo)

        parent_layout.addWidget(group)

    def _build_interface_group(self, parent_layout: QVBoxLayout) -> None:
        """
        构建界面设置组，包括主题、窗口记忆和最大窗口尺寸的配置。
        参数：
            parent_layout (QVBoxLayout): 父布局，界面组将被添加到此布局中。
        返回值：
            无
        """
        # 创建一个标题为“界面”的分组框
        group = QGroupBox("界面")
        # 创建表单布局并设置间距
        form = QFormLayout(group)
        form.setSpacing(6)

        # 创建“默认夜间主题”复选框，并从设置中读取初始值
        self.dark_theme_check = QCheckBox("默认夜间主题")
        self.dark_theme_check.setChecked(bool(getattr(self._settings, "dark_theme", True)))

        # 创建“记住上次窗口大小和位置”复选框，并从设置中读取初始值
        self.remember_window_geometry_check = QCheckBox("记住上次窗口大小和位置")
        self.remember_window_geometry_check.setChecked(
            bool(getattr(self._settings, "remember_window_geometry", True))
        )

        # 创建最大窗口宽度和高度的输入框
        self.max_window_width_edit = QLineEdit()
        self.max_window_height_edit = QLineEdit()
        # 设置占位符文本，提示输入规则
        self.max_window_width_edit.setPlaceholderText("0 表示不限制，最小 600")
        self.max_window_height_edit.setPlaceholderText("0 表示不限制，最小 800")
        # 从设置中获取最大窗口尺寸值，如果为0或负数则显示空字符串
        max_w = int(getattr(self._settings, "max_window_width", 0))
        max_h = int(getattr(self._settings, "max_window_height", 0))
        self.max_window_width_edit.setText("" if max_w <= 0 else str(max_w))
        self.max_window_height_edit.setText("" if max_h <= 0 else str(max_h))
        # 创建警告标签，用于显示输入验证的警告信息
        self.max_window_warning_label = QLabel("")
        self.max_window_warning_label.setObjectName("InputWarningLabel")
        self.max_window_warning_label.setStyleSheet("color: #d34545;")
        self.max_window_warning_label.setVisible(False)

        # 将控件添加到表单布局中
        form.addRow(self.dark_theme_check)
        form.addRow(self.remember_window_geometry_check)
        form.addRow("最大窗口宽度", self.max_window_width_edit)
        form.addRow("最大窗口高度", self.max_window_height_edit)
        form.addRow("", self.max_window_warning_label)

        # 连接输入框的文本改变信号到验证函数
        self.max_window_width_edit.textChanged.connect(self._validate_window_limits)
        self.max_window_height_edit.textChanged.connect(self._validate_window_limits)

        # 将构建的界面组添加到父布局
        parent_layout.addWidget(group)

    def _build_network_group(self, parent_layout: QVBoxLayout) -> None:
        """构建网络控制接口设置组。
    
        功能：在父布局中创建网络控制相关的设置界面，包括控制接口开关、
             主机地址输入框和端口号输入框。
    
        参数：
            parent_layout (QVBoxLayout): 父级垂直布局，用于承载网络设置组
        
        返回值：
            None: 该方法无返回值，直接将控件添加到父布局中
        """
        group = QGroupBox("网络控制接口")  # 创建分组框，标题为"网络控制接口"
        form = QFormLayout(group)  # 为分组框创建表单布局
        form.setSpacing(6)  # 设置表单行间距为6像素

        # 创建控制接口启用/禁用的复选框
        self.control_interface_check = QCheckBox("启用控制接口")
        # 根据配置文件中的设置初始化复选框状态
        self.control_interface_check.setChecked(bool(self._settings.control_interface_enabled))
        # 绑定复选框状态变化的信号到回调函数
        self.control_interface_check.toggled.connect(self._on_control_interface_toggled)

        # 创建主机地址输入框，使用配置中的默认主机地址初始化
        self.host_edit = QLineEdit(self._settings.control_host)
        # 创建端口号选择框
        self.port_spin = QSpinBox()
        # 设置端口号的有效范围：1-65535（TCP/UDP端口范围）
        self.port_spin.setRange(1, 65535)
        # 使用配置中的默认端口号初始化，转换为整数类型
        self.port_spin.setValue(int(self._settings.control_port))

        # 将各控件添加到表单布局中
        form.addRow(self.control_interface_check)  # 第一行：控制接口复选框
        form.addRow("主机", self.host_edit)  # 第二行：主机地址输入
        form.addRow("端口", self.port_spin)  # 第三行：端口号输入

        # 将整个网络设置组添加到父布局中
        parent_layout.addWidget(group)

    def _build_data_group(self, parent_layout: QVBoxLayout) -> None:
        group = QGroupBox("数据与日志")
        form = QFormLayout(group)
        form.setSpacing(6)

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

        self.startup_file_check = QCheckBox("启动时检查歌曲文件是否存在（关闭可加速启动）")
        self.startup_file_check.setChecked(bool(getattr(self._settings, "startup_file_check", True)))

        form.addRow(self.timed_save_check)
        form.addRow("定时保存间隔", self.timed_save_spin)
        form.addRow(self.logging_check)
        form.addRow(self.crash_logging_check)
        form.addRow(self.data_maintenance_logging_check)
        form.addRow(self.startup_file_check)

        parent_layout.addWidget(group)

    def _parse_max_window_value(self, text: str, minimum: int) -> tuple[int | None, str]:
        """
        解析最大窗口尺寸值。

        功能：
            从输入的文本字符串中解析出表示最大窗口尺寸的整数值，并根据最小值要求进行验证。

        参数：
            text (str): 需要解析的原始文本字符串，期望包含一个整数。
            minimum (int): 最小允许值，解析出的整数必须大于或等于此值。

        返回值：
            tuple[int | None, str]: 一个元组。
                - 第一个元素：解析成功且符合条件的整数值；如果输入为空则返回0；如果解析失败或不符合条件则返回None。
                - 第二个元素：错误信息字符串；如果解析成功或输入为空则返回空字符串。
        """
        # 去除文本首尾的空白字符
        raw = text.strip()
        # 如果去除空白后为空，则返回0和空错误信息
        if not raw:
            return 0, ""
        # 尝试将文本转换为整数
        try:
            value = int(raw)
        # 如果转换过程中发生任何异常（如文本不是数字），则捕获并返回None和固定的错误提示
        except Exception:
            return None, "最大窗口尺寸必须是整数。"
        # 如果解析出的整数值小于或等于0，则返回0和空错误信息（这可能表示无效或未设置）
        if value <= 0:
            return 0, ""
        # 如果解析出的整数值小于传入的最小允许值，则返回None和包含具体最小值要求的错误信息
        if value < minimum:
            # 注意：错误信息中的具体数值（如600, 800）是硬编码的，可能不适用于所有情况。这里保持原代码逻辑。
            return None, f"最大窗口尺寸过小：宽度至少 600，高度至少 800。"
        # 所有条件检查通过，返回解析成功的整数值和空错误信息
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
            prefer_playlist_order=bool(self.prefer_playlist_order_check.isChecked()),
            playlist_loop_sort=str(self.playlist_loop_sort_combo.currentData() or "default"),
            random_display_order=str(self.random_display_order_combo.currentData() or "original"),
            show_romaji=bool(self.show_romaji_check.isChecked()),
            show_japanese_lyrics=bool(self.show_japanese_lyrics_check.isChecked()),
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
            startup_file_check=bool(self.startup_file_check.isChecked()),
        )

    def _on_control_interface_toggled(self, enabled: bool) -> None:
        self.host_edit.setEnabled(bool(enabled))
        self.port_spin.setEnabled(bool(enabled))

    def _on_timed_save_toggled(self, enabled: bool) -> None:
        self.timed_save_spin.setEnabled(bool(enabled))

    def _warn_before_disable(self, title: str, body: str) -> bool:
        """在禁用功能前弹出警告对话框，要求用户确认高风险操作。

        创建一个警告对话框，向用户提示即将进行的操作的风险，并等待用户确认。
        如果用户点击“仍然关闭”（Yes），则方法返回 True；如果点击“取消”（No），则返回 False。

        Args:
            title (str): 对话框的标题。
            body (str): 对话框中显示的具体警告信息正文。

        Returns:
            bool: 返回用户的选择。True 表示确认禁用，False 表示取消操作。
        """
        # 创建一个模态消息框，父窗口为当前实例 (self)
        box = QMessageBox(self)
        # 设置对话框图标为警告样式
        box.setIcon(QMessageBox.Icon.Warning)
        # 设置对话框窗口标题
        box.setWindowTitle(title)
        # 设置对话框正文文本，使用 HTML 格式进行高亮和排版
        box.setText(
            f"<span style='color:#d34545; font-weight:700;'>高风险操作</span><br/>{body}<br/>"
            "除非你非常确定自己在做什么，否则不建议关闭。"
        )
        # 设置对话框的标准按钮，包含“是”（Yes）和“否”（No）两个选项
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        # 将默认焦点按钮设置为“否”（No），引导用户更谨慎地选择
        box.setDefaultButton(QMessageBox.StandardButton.No)
        # 自定义“是”按钮的显示文本为“仍然关闭”
        box.button(QMessageBox.StandardButton.Yes).setText("仍然关闭")
        # 自定义“否”按钮的显示文本为“取消”
        box.button(QMessageBox.StandardButton.No).setText("取消")
        # 执行对话框，并检查用户点击的按钮是否为“是”（Yes），返回对应的布尔值
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_crash_logging_toggled(self, enabled: bool) -> None:
        """处理崩溃日志开关切换事件。当禁用崩溃日志时，显示警告提示用户可能的问题；如果用户确认禁用，则返回；否则，恢复勾选状态。参数：enabled (bool): 崩溃日志的启用状态。返回值：None。"""
        if enabled:  # 如果启用崩溃日志，直接返回，无需进一步处理
            return
        ok = self._warn_before_disable(
            "关闭崩溃日志",
            "关闭后发生崩溃时将缺少追踪信息，可能无法定位问题。",
        )  # 调用警告函数，询问用户是否确认禁用崩溃日志；如果用户确认，ok 为 True
        if ok:  # 如果用户确认禁用，直接返回
            return
        # 如果用户取消禁用，恢复崩溃日志勾选状态
        self.crash_logging_check.blockSignals(True)  # 阻止信号以避免在设置勾选时触发事件
        self.crash_logging_check.setChecked(True)    # 恢复崩溃日志为勾选状态
        self.crash_logging_check.blockSignals(False) # 解除信号阻止

    def _on_data_maintenance_logging_toggled(self, enabled: bool) -> None:
        """
        处理数据维护日志切换事件。

        当启用时，直接返回；否则，如果用户确认警告，则不执行操作，否则将复选框设置为选中状态，以防止禁用日志记录。

        参数:
            enabled (bool): 是否启用日志记录。

        返回:
            None
        """
        if enabled:  # 如果启用日志记录，直接返回
            return
        # 显示警告对话框，询问用户是否确认关闭日志记录
        ok = self._warn_before_disable(
            "关闭数据维护日志",
            "关闭后自动数据清理将不再留下核查记录，排障难度会明显增加。",
        )
        if ok:  # 如果用户确认关闭，则返回，不执行后续操作
            return
        # 用户未确认，所以阻止日志记录被禁用
        self.data_maintenance_logging_check.blockSignals(True)  # 阻止信号发送，避免触发事件
        self.data_maintenance_logging_check.setChecked(True)  # 强制设置复选框为选中状态，防止禁用
        self.data_maintenance_logging_check.blockSignals(False)  # 恢复信号发送
