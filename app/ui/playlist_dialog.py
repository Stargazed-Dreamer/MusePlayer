"""歌单管理对话框。

提供完整的歌单管理功能：
- 创建、重命名、删除歌单
- 歌单复制和合并操作
- 导入音频文件夹和歌单文件
- 设置当前播放歌单
- 导出列表中任意选中的歌单（包括“全部歌曲”、“我喜欢”或用户自建歌单）

界面特点：
- 左侧列表显示所有歌单（包括“全部歌曲”）
- 右侧操作按钮提供各类管理功能
- 支持文件夹批量导入和单个文件导入
- 提供歌单数据导入导出功能
- 打开对话框时默认选中当前活跃歌单，便于直接操作或导出

与AppController的交互：
- 所有歌单操作都委托给AppController处理
- 对话框仅负责UI交互，业务逻辑在控制器层
- 支持实时更新歌单列表和状态反馈
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.services.app_controller import AppController
from app.services.library_service import ALL_SONGS_ID, FAVORITES_ID


class PlaylistDialog(QDialog):
    """歌单管理对话框。

    核心功能：
    - 显示所有歌单列表，标识当前活跃歌单
    - 创建新文件夹名称的空歌单
    - 重命名现有歌单（不可重命名“全部歌曲”）
    - 删除用户创建的歌单
    - 复制歌单结构和内容
    - 合并多个歌单内容
    - 导入本地文件夹中的音频文件
    - 导入外部歌单文件（.muse_playlist.json格式）
    - 快速设置当前播放歌单
    - 导出列表中任意选中的歌单（不依赖主窗口当前播放歌单或搜索状态）
    - 打开对话框时默认选中当前活跃歌单

    架构设计：
    - 纯UI层，所有业务逻辑委托给AppController
    - 支持与主窗口的模态/非模态交互
    - 自动刷新机制保持数据同步
    """

    def __init__(self, controller: AppController, parent=None):
        """初始化歌单管理对话框。

        Args:
            controller: AppController实例，用于歌单数据操作
            parent: 父级窗口，用于模态对话框显示
        """
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("歌单管理")
        self.resize(520, 460)

        self._build_ui()
        self.reload()  # 初始化歌单列表

    def _build_ui(self) -> None:
        """构建对话框用户界面。

        创建的界面包含以下组件：
        - 顶部：功能描述标签，说明对话框用途
        - 中间：歌单列表，显示所有可用歌单及其曲目数量
        - 下部第一行：歌单管理按钮（新建、导入歌单文件、重命名、删除）
        - 下部第二行：歌单操作按钮（设为当前、复制当前歌单、将当前添加到歌单）
        - 下部第三行：导入与导出按钮（向当前导入文件夹、向当前导入歌曲文件、导出选中歌单、关闭）

        所有按钮都连接到相应的事件处理方法。
        """
        root = QVBoxLayout(self)

        # 功能描述标签
        desc = QLabel("管理歌单、导入音频与歌单文件，并支持导出列表中选中的歌单。")
        desc.setObjectName("CaptionLabel")
        root.addWidget(desc)

        # 歌单列表（占据主要空间）
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)  # 单选模式
        root.addWidget(self.list_widget, 1)  # 占据1的伸缩因子

        # 第一行操作按钮
        row1 = QHBoxLayout()
        self.btn_new = QPushButton("新建")
        self.btn_import_playlist = QPushButton("导入歌单文件")
        self.btn_rename = QPushButton("重命名")
        self.btn_delete = QPushButton("删除")
        row1.addWidget(self.btn_new)
        row1.addWidget(self.btn_import_playlist)
        row1.addWidget(self.btn_rename)
        row1.addWidget(self.btn_delete)
        root.addLayout(row1)

        # 第二行操作按钮
        row2 = QHBoxLayout()
        self.btn_set_active = QPushButton("设为当前")
        self.btn_copy = QPushButton("复制当前歌单")
        self.btn_merge = QPushButton("将当前添加到歌单")
        row2.addWidget(self.btn_set_active)
        row2.addWidget(self.btn_copy)
        row2.addWidget(self.btn_merge)
        row2.addStretch(1)
        root.addLayout(row2)

        # 第三行操作按钮
        row3 = QHBoxLayout()
        self.btn_import_folder = QPushButton("向当前导入文件夹")
        self.btn_import_files = QPushButton("向当前导入歌曲文件")
        self.btn_export = QPushButton("导出选中歌单")
        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("GhostButton")
        row3.addWidget(self.btn_import_folder)
        row3.addWidget(self.btn_import_files)
        row3.addWidget(self.btn_export)
        row3.addStretch(1)  # 弹性空间，将关闭按钮推到右侧
        row3.addWidget(self.btn_close)
        root.addLayout(row3)

        self.btn_new.clicked.connect(self._create_playlist)
        self.btn_rename.clicked.connect(self._rename_playlist)
        self.btn_delete.clicked.connect(self._delete_playlist)
        self.btn_copy.clicked.connect(self._copy_playlist)
        self.btn_merge.clicked.connect(self._merge_playlist)
        self.btn_set_active.clicked.connect(self._set_active)
        self.btn_import_folder.clicked.connect(self._import_folder)
        self.btn_import_files.clicked.connect(self._import_files)
        self.btn_import_playlist.clicked.connect(self._import_playlist_file)
        self.btn_export.clicked.connect(self._export_playlist_file)
        self.btn_close.clicked.connect(self.accept)

    def reload(self) -> None:
        """刷新歌单列表显示。

        重新加载所有歌单并更新列表显示，包括：
        - 清空当前列表
        - 获取最新的歌单数据
        - 显示歌单名称和曲目数量
        - 标识当前活跃的歌单
        - 为每个列表项设置歌单ID数据
        - 默认选中当前活跃的歌单，便于用户直接对其进行操作或导出
        """
        self.list_widget.clear()
        active_id = self.controller.library_service.active_playlist_id
        active_row = -1
        for idx, playlist in enumerate(self.controller.library_service.list_playlists()):
            if playlist.id == ALL_SONGS_ID:
                display_name = "全部歌曲"
            elif playlist.id == FAVORITES_ID:
                display_name = "我喜欢"
            else:
                display_name = playlist.name
            label = f"{display_name} ({len(playlist.track_ids)})"
            if playlist.id == active_id:
                label = f"{label}  [当前]"
                active_row = idx
            item = QListWidgetItem(label)
            item.setData(0x0100, playlist.id)
            self.list_widget.addItem(item)
        # 默认选中当前活跃的歌单，避免用户打开对话框后点击按钮无反馈
        if active_row >= 0:
            self.list_widget.setCurrentRow(active_row)

    def _selected_playlist_id(self) -> str | None:
        """获取当前选中的歌单ID。

        Returns:
            str | None: 当前选中歌单的ID，如果没有选中则返回None
        """
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(0x0100)

    def _create_playlist(self) -> None:
        """创建新歌单。

        显示输入对话框获取用户输入的歌单名称，然后调用控制器创建新歌单。
        创建成功后刷新列表显示。
        """
        text, ok = QInputDialog.getText(self, "新建歌单", "歌单名称：")
        if not ok:
            return
        self.controller.create_playlist(text)
        self.reload()

    def _rename_playlist(self) -> None:
        """重命名当前选中的歌单。

        检查选中的歌单是否可重命名（"全部歌曲"不可重命名），
        然后显示输入对话框获取新名称并调用控制器进行重命名。
        """
        playlist_id = self._selected_playlist_id()
        if not playlist_id:
            return
        if playlist_id in {ALL_SONGS_ID, FAVORITES_ID}:
            QMessageBox.information(self, "提示", "系统歌单不可重命名。")
            return

        text, ok = QInputDialog.getText(self, "重命名歌单", "新名称：")
        if not ok:
            return
        self.controller.rename_playlist(playlist_id, text)
        self.reload()

    def _delete_playlist(self) -> None:
        """删除当前选中的歌单。

        检查选中的歌单是否可删除（"全部歌曲"不可删除），
        显示确认对话框获取用户确认，然后调用控制器删除歌单。
        """
        playlist_id = self._selected_playlist_id()
        if not playlist_id:
            return
        if playlist_id in {ALL_SONGS_ID, FAVORITES_ID}:
            QMessageBox.information(self, "提示", "系统歌单不可删除。")
            return

        answer = QMessageBox.question(self, "确认", "确定删除该歌单？")
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.controller.delete_playlist(playlist_id)
        self.reload()

    def _copy_playlist(self) -> None:
        """复制当前选中的歌单。

        获取选中歌单信息并设置为默认名称（歌单名 - 副本），
        显示输入对话框获取新名称，调用控制器复制歌单。
        复制失败时显示错误信息。
        """
        playlist_id = self._selected_playlist_id()
        if not playlist_id:
            return
        default_name = ""
        selected = self.controller.library_service.get_playlist(playlist_id)
        if selected is not None:
            default_name = f"{selected.name} - 副本"
        text, ok = QInputDialog.getText(self, "复制歌单", "新歌单名称（可选）：", text=default_name)
        if not ok:
            return
        new_id = self.controller.copy_playlist(playlist_id, new_name=text.strip() or None)
        if not new_id:
            QMessageBox.warning(self, "提示", "复制失败：歌单不存在。")
            return
        self.reload()

    def _merge_playlist(self) -> None:
        """合并歌单操作。

        将当前选中的歌单合并到其他歌单中，显示可选目标歌单列表，
        选择合适的歌单后进行合并操作并显示合并结果。
        """
        source_id = self._selected_playlist_id()
        if not source_id:
            return

        choices: list[tuple[str, str]] = []
        for playlist in self.controller.library_service.list_playlists():
            if playlist.id == source_id:
                continue
            choices.append((playlist.id, playlist.name))
        if not choices:
            QMessageBox.information(self, "提示", "没有可合并的目标歌单。")
            return

        labels = [f"{name} ({pid})" for pid, name in choices]
        selected_label, ok = QInputDialog.getItem(self, "合并歌单", "选择目标歌单：", labels, 0, False)
        if not ok or not selected_label:
            return

        target_id = None
        for idx, label in enumerate(labels):
            if label == selected_label:
                target_id = choices[idx][0]
                break
        if not target_id:
            return

        merged = self.controller.merge_playlist(source_id, target_id)
        QMessageBox.information(self, "合并完成", f"已新增 {merged} 首歌曲到目标歌单。")
        self.reload()

    def _set_active(self) -> None:
        """将选中的歌单设置为当前播放歌单。

        调用播放器服务设置当前歌单，并触发库更改信号，
        然后刷新显示以更新当前歌单标识。
        """
        playlist_id = self._selected_playlist_id()
        if not playlist_id:
            return
        self.controller.player_service.set_playlist(playlist_id)
        self.controller.library_changed.emit()
        self.reload()

    def _import_folder(self) -> None:
        """从文件夹导入音乐文件。

        显示文件夹选择对话框，将选定文件夹中的所有音频文件
        导入到当前选中的歌单中（或活跃歌单，如果在"全部歌曲"）。
        导入过程异常时会显示错误信息。
        """
        playlist_id = self._selected_playlist_id() or self.controller.library_service.active_playlist_id
        if playlist_id == ALL_SONGS_ID:
            playlist_id = None

        folder = QFileDialog.getExistingDirectory(self, "选择音乐文件夹")
        if not folder:
            return

        try:
            count = self.controller.import_folder(Path(folder), playlist_id=playlist_id)
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return

        QMessageBox.information(self, "导入完成", f"已导入 {count} 首歌曲")
        self.reload()

    def _import_files(self) -> None:
        """从本地导入音频文件到播放列表。

        功能：
            打开文件对话框让用户选择音频文件，并将它们导入到当前选中的播放列表中。

        参数：
            无。

        返回值：
            无。
        """
        # 获取当前选中的播放列表ID，如果没有则使用默认的活跃播放列表
        playlist_id = self._selected_playlist_id() or self.controller.library_service.active_playlist_id
        # 如果选中的是“所有歌曲”这个特殊ID，则将播放列表设为None，表示不限制播放列表
        if playlist_id == ALL_SONGS_ID:
            playlist_id = None
        # 弹出文件选择对话框，允许用户选择多种格式的音频文件
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择歌曲文件",
            "",
            "音频文件 (*.mp3 *.flac *.wav *.m4a *.aac *.ogg *.opus *.wma)",
        )
        # 如果用户没有选择任何文件，则直接返回，不执行导入
        if not file_paths:
            return
        # 调用控制器的import_files方法执行导入，将文件路径字符串转为Path对象
        count = self.controller.import_files([Path(p) for p in file_paths], playlist_id=playlist_id)
        # 导入完成后弹出信息提示框，告知用户导入的歌曲数量
        QMessageBox.information(self, "导入完成", f"已导入 {count} 首歌曲")
        # 刷新当前视图以反映导入后的变化
        self.reload()

    def _import_playlist_file(self) -> None:
        """导入外部歌单文件。

        显示文件选择对话框选择.muse_playlist.json或.json格式的歌单文件，
        调用控制器导入歌单数据，导入异常时显示错误信息。
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入歌单文件",
            "",
            "MuseArc 歌单 (*.muse_playlist.json);;JSON 文件 (*.json)",
        )
        if not file_path:
            return
        try:
            self.controller.import_muse_playlist(Path(file_path))
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        QMessageBox.information(self, "导入完成", "歌单文件已导入。")
        self.reload()

    def _export_playlist_file(self) -> None:
        """将列表中选中的歌单导出为一个文件。

        基于列表当前选中项导出对应歌单（包括“全部歌曲”、“我喜欢”或任意用户歌单），
        不依赖主窗口的“当前播放歌单”或搜索状态。若未选中任何歌单，会弹出提示引导用户先选择。

        Args:
            self: 实例自身。

        Returns:
            None: 此方法不返回任何值。
        """
        # 获取列表中当前选中的歌单ID
        playlist_id = self._selected_playlist_id()
        # 如果没有选中任何歌单，提示用户先选择，避免按钮点击无反馈
        if not playlist_id:
            QMessageBox.information(self, "提示", "请先在列表中选择要导出的歌单。")
            return
        # 弹出一个文件夹选择对话框，让用户选择导出目标目录
        out_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        # 如果用户取消了目录选择，则直接返回
        if not out_dir:
            return
        try:
            # 调用控制器执行实际的歌单导出逻辑，并获取生成的文件路径
            file_path = self.controller.export_playlist(playlist_id, Path(out_dir))
        except Exception as exc:
            # 如果在导出过程中发生任何异常（如文件写入权限问题），则弹出错误消息框
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        # 导出成功，弹出信息消息框，显示导出文件的完整路径
        QMessageBox.information(self, "导出完成", f"歌单已导出：\n{file_path}")
