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


class PlaylistDialog(QDialog):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("歌单管理")
        self.resize(520, 460)

        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        desc = QLabel("新建、重命名、删除歌单，并可向歌单导入文件夹。")
        desc.setObjectName("CaptionLabel")
        root.addWidget(desc)

        self.list_widget = QListWidget(self)
        root.addWidget(self.list_widget, 1)

        row1 = QHBoxLayout()
        self.btn_new = QPushButton("新建")
        self.btn_rename = QPushButton("重命名")
        self.btn_delete = QPushButton("删除")
        self.btn_copy = QPushButton("复制歌单")
        self.btn_merge = QPushButton("合并歌单")
        row1.addWidget(self.btn_new)
        row1.addWidget(self.btn_rename)
        row1.addWidget(self.btn_delete)
        row1.addWidget(self.btn_copy)
        row1.addWidget(self.btn_merge)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_set_active = QPushButton("设为当前")
        self.btn_import_folder = QPushButton("导入文件夹")
        self.btn_import_playlist = QPushButton("导入歌单文件")
        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("GhostButton")
        row2.addWidget(self.btn_set_active)
        row2.addWidget(self.btn_import_folder)
        row2.addWidget(self.btn_import_playlist)
        row2.addStretch(1)
        row2.addWidget(self.btn_close)
        root.addLayout(row2)

        self.btn_new.clicked.connect(self._create_playlist)
        self.btn_rename.clicked.connect(self._rename_playlist)
        self.btn_delete.clicked.connect(self._delete_playlist)
        self.btn_copy.clicked.connect(self._copy_playlist)
        self.btn_merge.clicked.connect(self._merge_playlist)
        self.btn_set_active.clicked.connect(self._set_active)
        self.btn_import_folder.clicked.connect(self._import_folder)
        self.btn_import_playlist.clicked.connect(self._import_playlist_file)
        self.btn_close.clicked.connect(self.accept)

    def reload(self) -> None:
        self.list_widget.clear()
        active_id = self.controller.library_service.active_playlist_id
        for playlist in self.controller.library_service.list_playlists():
            display_name = "全部歌曲" if playlist.id == "all_songs" else playlist.name
            label = f"{display_name} ({len(playlist.track_ids)})"
            if playlist.id == active_id:
                label = f"{label}  [当前]"
            item = QListWidgetItem(label)
            item.setData(0x0100, playlist.id)
            self.list_widget.addItem(item)

    def _selected_playlist_id(self) -> str | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(0x0100)

    def _create_playlist(self) -> None:
        text, ok = QInputDialog.getText(self, "新建歌单", "歌单名称：")
        if not ok:
            return
        self.controller.create_playlist(text)
        self.reload()

    def _rename_playlist(self) -> None:
        playlist_id = self._selected_playlist_id()
        if not playlist_id:
            return
        if playlist_id == "all_songs":
            QMessageBox.information(self, "提示", "“全部歌曲”不可重命名。")
            return

        text, ok = QInputDialog.getText(self, "重命名歌单", "新名称：")
        if not ok:
            return
        self.controller.rename_playlist(playlist_id, text)
        self.reload()

    def _delete_playlist(self) -> None:
        playlist_id = self._selected_playlist_id()
        if not playlist_id:
            return
        if playlist_id == "all_songs":
            QMessageBox.information(self, "提示", "“全部歌曲”不可删除。")
            return

        answer = QMessageBox.question(self, "确认", "确定删除该歌单？")
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.controller.delete_playlist(playlist_id)
        self.reload()

    def _copy_playlist(self) -> None:
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
        playlist_id = self._selected_playlist_id()
        if not playlist_id:
            return
        self.controller.player_service.set_playlist(playlist_id)
        self.controller.library_changed.emit()
        self.reload()

    def _import_folder(self) -> None:
        playlist_id = self._selected_playlist_id() or self.controller.library_service.active_playlist_id
        if playlist_id == "all_songs":
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

    def _import_playlist_file(self) -> None:
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
