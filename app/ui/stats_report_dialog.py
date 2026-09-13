"""年度报告生成与查看对话框。

两页式（QStackedWidget）：
1. 向导页：选择主数据（年份/检查点）与副数据（不对比/年份/检查点）。
2. 报告页：只读报告视图（KPI、月度图、榜单、趣味发现），可重选主/副、导出。

导出格式：HTML（自包含）/ PNG（窗口截图）/ TXT（纯文本摘要）。
图表使用 QPainter 自绘，配色取自 theme.py，与主界面美术一致。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.services.playback_stats_service import PlaybackStatsService
from app.services.stats_checkpoint_service import StatsCheckpointService
from app.services.stats_report_builder import ReportData, ReportSource, StatsReportBuilder, apply_comparison
from app.ui.stats_report_exporter import write_report_export
from app.ui.theme import ACCENT, ACCENT_STRONG

_MONTH_NAMES = [f"{m}月" for m in range(1, 13)]


class MonthlyBarChart(QWidget):
    """月度播放时长柱状图（QPainter 自绘，悬停显示明细）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = [0.0] * 12
        self._plays: list[int] = [0] * 12
        self._hover_index: int = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_series(self, monthly_seconds: list[float], monthly_play: list[int]) -> None:
        """更新月度序列（12 个元素，缺省补 0）。"""
        secs = [float(v) for v in (monthly_seconds or [])[:12]]
        plays = [int(v) for v in (monthly_play or [])[:12]]
        self._values = secs + [0.0] * (12 - len(secs))
        self._plays = plays + [0] * (12 - len(plays))
        self.update()

    def _bar_rect(self, index: int) -> QRectF:
        """计算第 index 根柱子的绘制区域。"""
        margin = 8.0
        label_h = 18.0
        width = self.width()
        height = self.height() - label_h
        slot = (width - margin * 2) / 12.0
        bar_w = slot * 0.62
        x = margin + slot * index + (slot - bar_w) / 2
        max_val = max(self._values) or 1.0
        bar_h = (height - margin) * (self._values[index] / max_val)
        return QRectF(x, height - bar_h - margin, bar_w, bar_h)

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if max(self._values) <= 0:
            # 空态：该数据源没有月度聚合（存量数据无时间分桶，自本版本起积累）
            painter.setPen(QPen(QColor("#6f6f6f")))
            painter.drawText(
                self.rect(),
                int(Qt.AlignmentFlag.AlignCenter),
                "暂无月度数据 — 时间分桶统计自本版本起积累",
            )
            painter.end()
            return
        for index in range(12):
            rect = self._bar_rect(index)
            # 底部月份标签
            painter.setPen(QPen(QColor("#9a9a9a")))
            painter.drawText(
                QRectF(rect.left(), self.height() - 18.0, rect.width(), 16.0),
                int(Qt.AlignmentFlag.AlignCenter),
                _MONTH_NAMES[index],
            )
            if self._values[index] <= 0:
                continue
            color = QColor(ACCENT_STRONG if index == self._hover_index else ACCENT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 4, 4)
            # 柱顶数值（仅悬停柱）
            if index == self._hover_index:
                painter.setPen(QPen(QColor("#f1f1f1")))
                hours_text = f"{self._values[index] / 3600.0:.1f}h"
                painter.drawText(
                    QRectF(rect.left(), rect.top() - 20, rect.width(), 18),
                    int(Qt.AlignmentFlag.AlignCenter),
                    hours_text,
                )
        # 峰值参考线
        peak_line_color = QColor(ACCENT)
        peak_line_color.setAlpha(60)
        painter.setPen(QPen(peak_line_color, 1, Qt.PenStyle.DotLine))
        painter.drawLine(8.0, 8.0, self.width() - 8.0, 8.0)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — Qt 命名
        pos = event.position()
        new_hover = -1
        for index in range(12):
            if self._bar_rect(index).adjusted(-2, -6, 2, 6).contains(pos):
                new_hover = index
                break
        if new_hover != self._hover_index:
            self._hover_index = new_hover
            self.update()
        if 0 <= new_hover < 12 and self._values[new_hover] > 0:
            tip = f"{_MONTH_NAMES[new_hover]} · {self._values[new_hover] / 3600.0:.1f} 小时 · {self._plays[new_hover]} 次播放"
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()


class StatsReportDialog(QDialog):
    """生成年度报告对话框（向导 + 报告视图）。"""

    def __init__(
        self,
        parent: QWidget,
        stats_service: PlaybackStatsService,
        checkpoint_service: StatsCheckpointService,
    ):
        """初始化。

        Args:
            parent: 父窗口
            stats_service: 播放统计服务（实时分桶数据源）
            checkpoint_service: 检查点服务（快照数据源）
        """
        super().__init__(parent)
        self._stats = stats_service
        self._checkpoints = checkpoint_service
        self._primary: ReportSource | None = None
        self._secondary: ReportSource | None = None
        self._primary_report: ReportData | None = None
        self._secondary_report: ReportData | None = None

        self.setWindowTitle("生成年度报告 — MusePlayer")
        self.setModal(True)
        self.setMinimumSize(820, 620)

        self._stack = QStackedWidget(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)
        self._stack.addWidget(self._build_wizard_page())
        self._stack.addWidget(self._build_report_page())
        year_sources, checkpoint_sources = self._collect_sources()
        if not year_sources and not checkpoint_sources:
            self._show_empty_notice()

    # ===== 数据源收集 =====

    def _collect_sources(self) -> tuple[list[ReportSource], list[ReportSource]]:
        """收集可选主/副数据源：(年份源, 检查点源)。"""
        years = self._stats.available_years()
        current_year = time.localtime().tm_year
        year_sources = [
            ReportSource(
                kind="year",
                year=year,
                label=f"{year} 年（至今）" if year == current_year else f"{year} 年",
            )
            for year in years
        ]
        checkpoint_sources = [
            ReportSource(kind="checkpoint", checkpoint_id=meta.id, label=meta.name)
            for meta in self._checkpoints.list_checkpoints()
        ]
        return year_sources, checkpoint_sources

    def _show_empty_notice(self) -> None:
        """无任何统计数据时的空态提示。"""
        self.setWindowTitle("生成年度报告 — 暂无数据")
        QMessageBox.information(
            self,
            "生成年度报告",
            "暂无可用的统计数据。\n\n开始播放歌曲后，本功能将按年份自动积累统计；\n也可先在「统计 → 检查点管理」手动保存一个快照。",
        )

    # ===== 向导页 =====

    def _build_wizard_page(self) -> QWidget:
        """构建主/副选择向导页。"""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)

        title = QLabel("生成年度报告")
        title.setStyleSheet(f"font-size: 20px; font-weight: 600; color: {ACCENT_STRONG};")
        layout.addWidget(title)

        subtitle = QLabel("先选择主数据（查看谁的数据）和副数据（和谁对比，可跳过），再点「生成报告」。")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #9aa69b; font-size: 12px;")
        layout.addWidget(subtitle)

        self._primary_group = QButtonGroup(self)
        self._secondary_group = QButtonGroup(self)

        self._primary_list, self._primary_hint = self._build_pick_box(layout, "① 主数据（查看谁的数据）")
        self._secondary_list, self._secondary_hint = self._build_pick_box(layout, "② 副数据（和谁对比，可不选）")

        self._reload_wizard_choices()

        foot = QHBoxLayout()
        self._wizard_hint = QLabel("提示：主数据为「检查点」时按快照累计口径展示；副数据可不选。")
        self._wizard_hint.setStyleSheet("color: #9aa69b; font-size: 11.5px;")
        self._wizard_hint.setWordWrap(True)
        foot.addWidget(self._wizard_hint, 1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        foot.addWidget(cancel_btn)
        generate_btn = QPushButton("生成报告 →")
        generate_btn.setDefault(True)
        generate_btn.clicked.connect(self._on_generate)
        foot.addWidget(generate_btn)
        layout.addLayout(foot)
        return page

    def _build_pick_box(self, parent_layout: QVBoxLayout, heading: str) -> tuple[QWidget, QLabel]:
        """构建一个单选卡片容器（可滚动列表）。返回 (容器, 空态提示标签)。"""
        frame = QFrame(self)
        frame.setObjectName("Card")
        frame.setStyleSheet("QFrame#Card { border: 1px solid #3b3b3b; border-radius: 10px; }")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(6)
        label = QLabel(heading)
        label.setStyleSheet(f"color: {ACCENT}; font-size: 13px; font-weight: 600; background: transparent;")
        inner.addWidget(label)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(150)
        holder = QWidget(self)
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(4)
        holder.setLayout(holder_layout)
        scroll.setWidget(holder)
        inner.addWidget(scroll, 1)

        empty_hint = QLabel("")
        empty_hint.setStyleSheet("color: #6f6f6f; font-size: 11.5px; background: transparent;")
        inner.addWidget(empty_hint)
        parent_layout.addWidget(frame, 1)
        return holder, empty_hint

    def _reload_wizard_choices(self) -> None:
        """重新填充主/副数据单选列表。"""
        year_sources, checkpoint_sources = self._collect_sources()
        self._fill_pick_box(
            self._primary_list,
            self._primary_hint,
            self._primary_group,
            year_sources,
            checkpoint_sources,
            allow_none=False,
        )
        self._fill_pick_box(
            self._secondary_list,
            self._secondary_hint,
            self._secondary_group,
            year_sources,
            checkpoint_sources,
            allow_none=True,
        )
        # 副数据默认不选（= 不对比）
        self._secondary_group.setExclusive(False)

    def _fill_pick_box(
        self,
        holder: QWidget,
        empty_hint: QLabel,
        group: QButtonGroup,
        year_sources: list[ReportSource],
        checkpoint_sources: list[ReportSource],
        *,
        allow_none: bool,
    ) -> None:
        """填充一个单选容器。"""
        layout: QVBoxLayout = holder.layout()
        # 清空旧选项
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not year_sources and not checkpoint_sources and not allow_none:
            empty_hint.setText("暂无可选数据：播放歌曲或先保存检查点后这里会出现选项。")
            return
        empty_hint.setText("")

        def add_option(text: str, source: ReportSource | None, *, checked: bool) -> None:
            radio = QRadioButton(text, holder)
            radio.setChecked(checked)
            group.addButton(radio)
            radio.setProperty("report_source", source)
            layout.addWidget(radio)

        if allow_none:
            add_option("不对比（仅展示主数据）", None, checked=True)
        for source in year_sources:
            add_option(f"{source.label}", source, checked=False)
        for source in checkpoint_sources:
            add_option(f"检查点「{source.label}」", source, checked=False)
        layout.addStretch(1)

    def _selected_source(self, group: QButtonGroup) -> ReportSource | None:
        """读取单选组当前选中的数据源。"""
        radio = group.checkedButton()
        if radio is None:
            return None
        return radio.property("report_source")

    def _on_generate(self) -> None:
        """生成报告：校验主数据 → 构建 → 切换到报告页。"""
        primary = self._selected_source(self._primary_group)
        if primary is None:
            QMessageBox.information(self, "生成年度报告", "请先选择主数据。")
            return
        secondary = self._selected_source(self._secondary_group)
        if secondary is not None and (
            primary.kind == secondary.kind
            and (primary.kind != "year" or primary.year == secondary.year)
            and (primary.kind != "checkpoint" or primary.checkpoint_id == secondary.checkpoint_id)
        ):
            QMessageBox.information(self, "生成年度报告", "主数据和副数据不能是同一条目。")
            return
        builder = StatsReportBuilder(self._stats, self._checkpoints, self._track_lookup())
        try:
            primary_report = builder.build(primary)
            secondary_report = builder.build(secondary) if secondary is not None else None
            if secondary_report is not None:
                apply_comparison(primary_report, secondary_report)
        except Exception as exc:  # 构建失败兜底，避免报告窗口崩溃
            QMessageBox.warning(self, "生成年度报告", f"生成报告失败：{exc}")
            return
        self._primary = primary
        self._secondary = secondary
        self._primary_report = primary_report
        self._secondary_report = secondary_report
        self._render_report()
        self._stack.setCurrentIndex(1)

    def _track_lookup(self):
        """构建 track_id -> (title, artist, album) 查询函数（经曲库公开访问器）。"""
        library = self._resolve_library()
        if library is None:
            return lambda track_id: None

        def lookup(track_id: str) -> tuple[str, str, str] | None:
            track = library.get_track(track_id)
            if track is None:
                return None
            return (track.title, track.artist, track.album)

        return lookup

    def _resolve_library(self):
        """从父窗口解析曲库服务（主窗口上是 controller.library_service）。"""
        parent = self.parent()
        if parent is None:
            return None
        controller = getattr(parent, "controller", parent)
        return getattr(controller, "library_service", None)

    # ===== 报告页 =====

    def _build_report_page(self) -> QWidget:
        """构建报告查看页。"""
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(20, 14, 20, 12)
        outer.setSpacing(10)

        # 顶部工具行
        head = QHBoxLayout()
        self._report_title = QLabel("")
        self._report_title.setStyleSheet(f"font-size: 20px; font-weight: 600; color: {ACCENT_STRONG};")
        head.addWidget(self._report_title, 1)
        back_btn = QPushButton("↺ 重选主/副")
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        head.addWidget(back_btn)
        export_html_btn = QPushButton("导出 HTML")
        export_html_btn.clicked.connect(lambda: self._on_export("html"))
        head.addWidget(export_html_btn)
        export_png_btn = QPushButton("导出 PNG")
        export_png_btn.clicked.connect(lambda: self._on_export("png"))
        head.addWidget(export_png_btn)
        export_txt_btn = QPushButton("导出 TXT")
        export_txt_btn.clicked.connect(lambda: self._on_export("txt"))
        head.addWidget(export_txt_btn)
        outer.addLayout(head)

        self._report_subtitle = QLabel("")
        self._report_subtitle.setStyleSheet("color: #9aa69b; font-size: 12px;")
        self._report_subtitle.setWordWrap(True)
        outer.addWidget(self._report_subtitle)

        # 可滚动报告内容（右侧留出与滚动条的间距）
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 20, 10)
        content_layout.setSpacing(14)
        content.setLayout(content_layout)

        self._compare_banner = QLabel("")
        self._compare_banner.setStyleSheet(
            "background: rgba(167,192,128,0.14); border: 1px solid rgba(167,192,128,0.4);"
            "border-radius: 8px; padding: 6px 12px; font-size: 12px;"
        )
        self._compare_banner.setWordWrap(True)
        content_layout.addWidget(self._compare_banner)

        # KPI 四卡
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self._kpi_labels: list[QLabel] = []
        for _ in range(4):
            card = QLabel("")
            card.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.setStyleSheet(
                "background: #1f1f1f; border: 1px solid #3b3b3b; border-radius: 10px; padding: 14px 12px;"
            )
            kpi_row.addWidget(card, 1)
            self._kpi_labels.append(card)
        content_layout.addLayout(kpi_row)

        # 月度图
        self._chart = MonthlyBarChart(content)
        content_layout.addWidget(self._chart)

        # 双栏：Top10 + 歌手专辑（富文本标签统一左上对齐，QLabel 默认垂直居中会让
        # 内容少的卡片整体下沉，观感怪异）
        columns = QHBoxLayout()
        columns.setSpacing(14)
        top_align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        self._top_label = QLabel("")
        self._top_label.setWordWrap(True)
        self._top_label.setTextFormat(Qt.TextFormat.RichText)
        self._top_label.setAlignment(top_align)
        self._top_label.setStyleSheet(
            "background: #1f1f1f; border: 1px solid #3b3b3b; border-radius: 10px; padding: 16px 18px;"
        )
        columns.addWidget(self._top_label, 3)
        self._artist_label = QLabel("")
        self._artist_label.setWordWrap(True)
        self._artist_label.setTextFormat(Qt.TextFormat.RichText)
        self._artist_label.setAlignment(top_align)
        self._artist_label.setStyleSheet(
            "background: #1f1f1f; border: 1px solid #3b3b3b; border-radius: 10px; padding: 16px 18px;"
        )
        columns.addWidget(self._artist_label, 2)
        content_layout.addLayout(columns)

        # 趣味发现宫格
        self._fun_grid = QGridLayout()
        self._fun_grid.setSpacing(12)
        content_layout.addLayout(self._fun_grid)
        content_layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return page

    def _render_report(self) -> None:
        """将当前 ReportData 渲染到报告页控件。"""
        report = self._primary_report
        if report is None:
            return
        self._report_title.setText(report.title)
        self._report_subtitle.setText(f"{report.subtitle} · 主数据：{report.source_label}")
        if self._secondary_report is not None:
            self._compare_banner.setText(f"⚖️ 正在与「{self._secondary_report.source_label}」对比 — ▲/▼ 为相对变化")
            self._compare_banner.show()
        else:
            self._compare_banner.hide()

        # KPI
        delta_cls_up = "#8fd977"
        delta_cls_down = "#ef6b6b"
        for idx, card in enumerate(self._kpi_labels):
            if idx < len(report.kpis):
                kpi = report.kpis[idx]
                text = f"<div style='color:#9a9a9a;font-size:11px;'>{kpi['label']}</div>"
                text += f"<div style='font-size:22px;font-weight:700;'>{kpi['display']}</div>"
                if kpi.get("delta_pct") is not None:
                    color = delta_cls_up if kpi["delta_pct"] >= 0 else delta_cls_down
                    text += f"<div style='font-size:11px;color:{color};'>{kpi['delta_text']}（对比）</div>"
                card.setText(text)
                card.show()
            else:
                card.hide()

        self._chart.set_series(report.monthly_seconds, report.monthly_play)

        # Top10（Qt 富文本对 float:inline-block 支持不可靠，统一用表格布局：
        # 左单元格序号+歌名+歌手，右单元格次数右对齐）
        def _count_cell(count: int) -> str:
            return f"<td style='text-align:right;vertical-align:top;color:#c9c9c9;white-space:nowrap;padding-left:12px;'>{count} 次</td>"

        lines = ["<div style='font-weight:600;font-size:14px;margin-bottom:8px;'>最常听 · Top 10</div>"]
        if not report.top_tracks:
            lines.append("<div style='color:#9a9a9a;'>暂无数据</div>")
        for idx, row in enumerate(report.top_tracks):
            mark = ""
            if self._secondary_report is not None:
                d = row.get("delta")
                if d == "new":
                    mark = " <span style='color:#e0af68;font-weight:700;'>NEW</span>"
                elif d == "up":
                    mark = f" <span style='color:{delta_cls_up};font-weight:700;'>▲{row['delta_rank']}</span>"
                elif d == "down":
                    mark = f" <span style='color:{delta_cls_down};font-weight:700;'>▼{-row['delta_rank']}</span>"
            gold = "#e0af68" if idx < 3 else "#9a9a9a"
            lines.append(
                "<table width='100%' cellspacing='0' cellpadding='0'>"
                "<tr>"
                f"<td style='vertical-align:top;padding:5px 0;'>"
                f"<span style='color:{gold};font-weight:700;'>&nbsp;{idx + 1}&nbsp;&nbsp;</span>"
                f"<span style='font-weight:600;'>{row['title']}</span><br/>"
                f"<span style='font-size:11px;color:#9a9a9a;'>&nbsp;&nbsp;&nbsp;&nbsp;{row['artist']}{mark}</span>"
                f"</td>"
                f"{_count_cell(row['count'])}"
                "</tr>"
                "</table>"
            )
        self._top_label.setText("".join(lines))

        artist_lines = ["<div style='font-weight:600;font-size:14px;margin-bottom:6px;'>最常听的歌手</div>"]
        if not report.top_artists:
            artist_lines.append("<div style='color:#9a9a9a;'>暂无数据</div>")
        for idx, row in enumerate(report.top_artists):
            artist_lines.append(
                "<table width='100%' cellspacing='0' cellpadding='0'>"
                f"<tr><td style='padding:4px 0;'>{idx + 1}. {row['name']}</td>{_count_cell(row['count'])}</tr>"
                "</table>"
            )
        artist_lines.append("<div style='font-weight:600;font-size:14px;margin:12px 0 8px;'>最常听的专辑</div>")
        for idx, row in enumerate(report.top_albums):
            artist_lines.append(
                "<table width='100%' cellspacing='0' cellpadding='0'>"
                f"<tr><td style='padding:4px 0;'>{idx + 1}. {row['name']}</td>{_count_cell(row['count'])}</tr>"
                "</table>"
            )
        self._artist_label.setText("".join(artist_lines))

        # 趣味发现宫格（3 列）
        while self._fun_grid.count():
            item = self._fun_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for idx, fact in enumerate(report.fun_facts):
            text = (
                f"<div style='font-size:18px;'>{fact['emoji']}</div>"
                f"<div style='font-weight:700;margin-top:2px;'>{fact['title']}</div>"
                f"<div style='color:#9a9a9a;font-size:11.5px;margin-top:3px;line-height:150%;'>{fact['text']}</div>"
            )
            card = QLabel(text)
            card.setWordWrap(True)
            card.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            card.setStyleSheet("background: #1f1f1f; border: 1px solid #3b3b3b; border-radius: 10px; padding: 14px 16px;")
            self._fun_grid.addWidget(card, idx // 3, idx % 3)
        # 换行标签的高度依赖实际宽度；此刻报告页尚未完成布局，先占位，
        # 等事件循环里布局给出真实宽度后再修正最小高度（否则首次打开卡片被压矮）
        QTimer.singleShot(0, self._update_report_label_heights)

    def _update_report_label_heights(self) -> None:
        """按标签当前宽度重算换行文本所需高度（width='100%' 表格在窄宽度下算错高度）。"""
        for label in (self._top_label, self._artist_label):
            width = label.width()
            if width <= 50:
                continue
            height = label.heightForWidth(width)
            if height is not None and height > 0:
                label.setMinimumHeight(min(height, 4000))

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 命名
        """窗口尺寸变化后重算富文本标签高度。"""
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_report_label_heights)

    # ===== 导出 =====

    def _on_export(self, fmt: str) -> None:
        """导出报告（HTML/TXT 由导出器生成，PNG 为报告页截图）。"""
        if self._primary_report is None:
            return
        filters = {
            "html": "HTML 报告 (*.html)",
            "png": "PNG 图片 (*.png)",
            "txt": "文本摘要 (*.txt)",
        }
        suffix = "html" if fmt == "html" else fmt
        base_name = f"MusePlayer_年度报告_{self._primary_report.source_label}".replace("/", "-")
        dest, _ = QFileDialog.getSaveFileName(self, "导出年度报告", base_name, filters[fmt])
        if not dest:
            return
        if fmt == "png":
            dest = dest if dest.lower().endswith(".png") else f"{dest}.png"
            pixmap = self._stack.widget(1).grab()
            if not pixmap.save(dest, "PNG"):
                QMessageBox.warning(self, "导出", "PNG 保存失败。")
                return
        else:
            try:
                final_path = write_report_export(dest, self._primary_report, self._secondary_report, suffix)
            except Exception as exc:
                QMessageBox.warning(self, "导出", f"导出失败：{exc}")
                return
            dest = str(final_path)
        QMessageBox.information(self, "导出", f"已导出：{dest}")
