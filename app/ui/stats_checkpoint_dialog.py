"""统计检查点管理对话框。

检查点 = 播放统计的只读快照。本对话框提供"增删查改名"：
- 保存当前为检查点（默认名"年份+月份"，可自定义）
- 查看（只读预览元信息与汇总）、改名（仅显示名）、删除（二次确认）
快照数据本身无编辑入口。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.playback_stats_service import PlaybackStatsService
from app.services.stats_checkpoint_service import CheckpointMeta, StatsCheckpointService, default_checkpoint_name


class StatsCheckpointDialog(QDialog):
    """检查点管理对话框（模态）。"""

    def __init__(
        self, parent: QWidget, checkpoint_service: StatsCheckpointService, stats_service: PlaybackStatsService
    ):
        """初始化。

        Args:
            parent: 父窗口
            checkpoint_service: 检查点服务
            stats_service: 播放统计服务（用于"保存当前为检查点"）
        """
        super().__init__(parent)
        self._checkpoints = checkpoint_service
        self._stats = stats_service
        self.setWindowTitle("检查点管理 — MusePlayer")
        self.setModal(True)
        self.setMinimumSize(640, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(12)

        # 顶部工具行
        top = QHBoxLayout()
        self._save_btn = QPushButton("＋ 保存当前为检查点")
        self._save_btn.clicked.connect(self._on_save_clicked)
        top.addWidget(self._save_btn)
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #9aa69b; font-size: 12px;")
        top.addWidget(self._count_label, 1)
        root.addLayout(top)

        # 检查点表格：名称 / 保存时间 / 曲目数 / 类型 / 操作
        self._table = QTableWidget(0, 5, self)
        self._table.setHorizontalHeaderLabels(["名称", "保存时间", "曲目数", "类型", "操作"])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(42)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._table, 1)

        hint = QLabel("检查点是统计数据的只读快照：保存后数据不可修改，仅支持改名、查看与删除。", self)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa69b; font-size: 11.5px;")
        root.addWidget(hint)

        # 底部按钮
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._reload()

    def _reload(self) -> None:
        """重新加载检查点列表。"""
        metas = self._checkpoints.list_checkpoints()
        self._table.setRowCount(len(metas))
        for row, meta in enumerate(metas):
            name_item = QTableWidgetItem(meta.name)
            name_item.setData(Qt.ItemDataRole.UserRole, meta.id)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(
                row, 1, QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.created_at)))
            )
            self._table.setItem(row, 2, QTableWidgetItem(str(meta.track_count)))
            self._table.setItem(row, 3, QTableWidgetItem("跨年自动保存" if meta.auto else "手动保存"))
            self._table.setCellWidget(row, 4, self._build_action_row(meta))
        self._count_label.setText(f"共 {len(metas)} 个 · 存放于 data/checkpoints/")

    def _build_action_row(self, meta: CheckpointMeta) -> QWidget:
        """构建一行的操作按钮组（查看/改名/删除）。"""
        holder = QWidget(self)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)
        view_btn = QPushButton("查看")
        view_btn.clicked.connect(lambda _=False, m=meta: self._on_view(m))
        rename_btn = QPushButton("改名")
        rename_btn.clicked.connect(lambda _=False, m=meta: self._on_rename(m))
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(lambda _=False, m=meta: self._on_delete(m))
        for btn in (view_btn, rename_btn, delete_btn):
            btn.setFixedHeight(28)
            btn.setFixedWidth(52)
            layout.addWidget(btn)
        return holder

    def _selected_meta(self) -> CheckpointMeta | None:
        """获取当前选中行的检查点元信息。"""
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        checkpoint_id = item.data(Qt.ItemDataRole.UserRole)
        return self._checkpoints.get(checkpoint_id)

    def _on_save_clicked(self) -> None:
        """保存当前统计为检查点：弹命名框，默认"年份+月份"。"""
        if not self._stats.has_entries:
            QMessageBox.information(self, "保存检查点", "当前没有统计数据可保存。")
            return
        default_name = default_checkpoint_name()
        dialog = QInputDialog(self)
        dialog.setWindowTitle("保存检查点")
        dialog.setLabelText("检查点名称（默认为年份+月份，可自定义纪念名）：")
        dialog.setTextValue(default_name)
        ok = dialog.exec()
        if not ok:
            return
        name = dialog.textValue().strip() or default_name
        meta = self._checkpoints.save(self._stats.snapshot_payload(), name)
        self._reload()
        QMessageBox.information(self, "保存检查点", f"已保存检查点「{meta.name}」（{meta.track_count} 首曲目）。")

    def _on_view(self, meta: CheckpointMeta) -> None:
        """只读预览检查点汇总信息。"""
        payload = self._checkpoints.load(meta.id)
        if payload is None:
            QMessageBox.warning(self, "查看检查点", "检查点文件不存在或已损坏。")
            self._reload()
            return
        play_total = 0
        seconds_total = 0.0
        for row in (payload.get("tracks") or {}).values():
            if isinstance(row, dict):
                play_total += int(row.get("play_count", 0))
                seconds_total += float(row.get("played_seconds_total", 0.0))
        created_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.created_at))
        QMessageBox.information(
            self,
            "检查点详情",
            f"名称：{meta.name}\n"
            f"保存时间：{created_text}\n"
            f"曲目数：{meta.track_count}\n"
            f"累计播放：{play_total:,} 次\n"
            f"累计时长：{seconds_total / 3600.0:.1f} 小时\n"
            f"类型：{'跨年自动保存' if meta.auto else '手动保存'}",
        )

    def _on_rename(self, meta: CheckpointMeta) -> None:
        """修改检查点显示名称（快照数据不变）。"""
        dialog = QInputDialog(self)
        dialog.setWindowTitle("检查点改名")
        dialog.setLabelText("新名称：")
        dialog.setTextValue(meta.name)
        ok = dialog.exec()
        if not ok:
            return
        new_name = dialog.textValue().strip()
        if not new_name or new_name == meta.name:
            return
        self._checkpoints.rename(meta.id, new_name)
        self._reload()

    def _on_delete(self, meta: CheckpointMeta) -> None:
        """删除检查点（二次确认）。"""
        confirm = QMessageBox.question(
            self,
            "删除检查点",
            f"确定删除检查点「{meta.name}」吗？\n该操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._checkpoints.delete(meta.id)
        self._reload()
