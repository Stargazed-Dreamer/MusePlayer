from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.entities import Settings
from app.ui.shortcut_settings import (
    SHORTCUT_ACTIONS,
    default_global_shortcuts,
    default_interface_shortcuts,
    merge_shortcuts,
)
from app.ui.theme import error_severity_color

CONFLICT_HINT_TEXT = "【全局按键冲突】"


class ShortcutSettingsDialog(QDialog):
    """按键设置对话框。

    Args:
        settings: 当前设置对象（就地修改，见 `apply_to_settings`）。
        parent: 父窗口；从它身上读 `_dark_theme` 决定错误红取浅色还是深色那套。
        global_conflicts: 上一次全局注册失败的结果 `action_id -> 失败原因`。
            命中的行会在【操作】下方标出红色小字，并把该行的全局快捷键输入框
            描红，表示这一项当前**未生效**；用户一旦改动该行就撤掉标记，
            因为要不要得等到保存后重新注册才知道。
    """

    def __init__(
        self,
        settings: Settings,
        parent=None,
        global_conflicts: dict[str, str] | None = None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._interface_edits: dict[str, QKeySequenceEdit] = {}
        self._global_edits: dict[str, QKeySequenceEdit] = {}
        self._conflict_hints: dict[str, QLabel] = {}
        self._error_color = error_severity_color(bool(getattr(parent, "_dark_theme", True)))
        # 全局快捷键被整体关闭时，上一轮的失败结果已失效，不再标红
        self._global_conflicts = (
            dict(global_conflicts or {}) if bool(getattr(settings, "global_shortcuts_enabled", True)) else {}
        )
        self.setWindowTitle("按键设置")
        self.setMinimumWidth(640)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.global_enabled_check = QCheckBox("启用全局快捷键")
        self.global_enabled_check.setChecked(bool(self._settings.global_shortcuts_enabled))
        self.global_enabled_check.toggled.connect(self._set_global_editors_enabled)
        layout.addWidget(self.global_enabled_check)

        hint = QLabel("界面快捷键仅在播放器窗口内生效；全局快捷键在播放器位于后台时仍可使用。留空可禁用单项快捷键。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        if self._global_conflicts:
            conflict_summary = QLabel(
                f"下列 {len(self._global_conflicts)} 项全局快捷键注册失败（多为被其他程序占用），"
                "当前未生效，请改用其他组合："
            )
            conflict_summary.setWordWrap(True)
            conflict_summary.setStyleSheet(f"QLabel {{ color: {self._error_color}; }}")
            layout.addWidget(conflict_summary)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.addWidget(QLabel("操作"), 0, 0)
        grid.addWidget(QLabel("界面快捷键"), 0, 1)
        grid.addWidget(QLabel("全局快捷键"), 0, 2)

        interface_values = merge_shortcuts(
            self._settings.interface_shortcuts,
            default_interface_shortcuts(),
        )
        global_values = merge_shortcuts(
            self._settings.global_shortcuts,
            default_global_shortcuts(),
        )
        for row, item in enumerate(SHORTCUT_ACTIONS, start=1):
            grid.addWidget(self._build_action_cell(item.action_id, item.label), row, 0)
            interface_edit = QKeySequenceEdit(QKeySequence(interface_values[item.action_id]))
            global_edit = QKeySequenceEdit(QKeySequence(global_values[item.action_id]))
            interface_edit.setMaximumSequenceLength(1)
            global_edit.setMaximumSequenceLength(1)
            grid.addWidget(interface_edit, row, 1)
            grid.addWidget(global_edit, row, 2)
            self._interface_edits[item.action_id] = interface_edit
            self._global_edits[item.action_id] = global_edit
            # 改动即视为"待重新验证"，先撤掉这一行的冲突标记
            global_edit.keySequenceChanged.connect(
                lambda _seq, action_id=item.action_id: self._clear_action_conflict(action_id)
            )
        layout.addLayout(grid)

        reset_button = QPushButton("恢复默认")
        reset_button.clicked.connect(self._restore_defaults)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(reset_button)
        footer.addStretch(1)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self._set_global_editors_enabled(self.global_enabled_check.isChecked())
        for action_id in list(self._global_edits):
            self._apply_conflict_style(action_id)

    def _build_action_cell(self, action_id: str, label: str) -> QWidget:
        """构造【操作】那一格：操作名 + 冲突时的红色小字（默认隐藏）。"""
        cell = QWidget(self)
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(2)
        name_label = QLabel(label)
        name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        conflict_label = QLabel(CONFLICT_HINT_TEXT)
        conflict_label.setStyleSheet(
            f"QLabel {{ color: {self._error_color}; font-size: 12px; background: transparent; }}"
        )
        conflict_label.setVisible(action_id in self._global_conflicts)
        cell_layout.addWidget(name_label)
        cell_layout.addWidget(conflict_label)
        self._conflict_hints[action_id] = conflict_label
        return cell

    def _apply_conflict_style(self, action_id: str) -> None:
        """冲突行：全局输入框描错误红 + 小字可见；非冲突：清掉自定义样式回归主题。"""
        editor = self._global_edits.get(action_id)
        hint = self._conflict_hints.get(action_id)
        conflicted = action_id in self._global_conflicts
        reason = self._global_conflicts.get(action_id, "该全局快捷键注册失败，当前未生效")
        if editor is not None:
            if conflicted:
                editor.setStyleSheet(
                    f"QKeySequenceEdit {{ color: {self._error_color}; "
                    f"border: 1px solid {self._error_color}; border-radius: 6px; padding: 6px 10px; }}"
                )
                editor.setToolTip(reason)
            else:
                editor.setStyleSheet("")
                editor.setToolTip("")
        if hint is not None:
            hint.setVisible(conflicted)
            hint.setToolTip(reason)

    def _clear_action_conflict(self, action_id: str) -> None:
        """用户改了这一项组合：撤掉冲突标记，等保存后重新注册再判定。"""
        if action_id not in self._global_conflicts:
            return
        self._global_conflicts.pop(action_id, None)
        self._apply_conflict_style(action_id)

    def _set_global_editors_enabled(self, enabled: bool) -> None:
        for editor in self._global_edits.values():
            editor.setEnabled(enabled)

    def _restore_defaults(self) -> None:
        for action_id, value in default_interface_shortcuts().items():
            self._interface_edits[action_id].setKeySequence(QKeySequence(value))
        for action_id, value in default_global_shortcuts().items():
            self._global_edits[action_id].setKeySequence(QKeySequence(value))
        self.global_enabled_check.setChecked(True)

    @staticmethod
    def _portable_text(editor: QKeySequenceEdit) -> str:
        return editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)

    def _find_duplicate(self, editors: dict[str, QKeySequenceEdit]) -> str | None:
        labels = {item.action_id: item.label for item in SHORTCUT_ACTIONS}
        used: dict[str, str] = {}
        for action_id, editor in editors.items():
            shortcut = self._portable_text(editor)
            if not shortcut:
                continue
            normalized = shortcut.casefold()
            previous = used.get(normalized)
            if previous is not None:
                return f"“{labels[previous]}”与“{labels[action_id]}”使用了相同快捷键：{shortcut}"
            used[normalized] = action_id
        return None

    def _accept_if_valid(self) -> None:
        duplicate = self._find_duplicate(self._interface_edits)
        if duplicate is None:
            duplicate = self._find_duplicate(self._global_edits)
        if duplicate is not None:
            QMessageBox.warning(self, "快捷键冲突", duplicate)
            return
        self.accept()

    def apply_to_settings(self) -> Settings:
        self._settings.global_shortcuts_enabled = self.global_enabled_check.isChecked()
        self._settings.interface_shortcuts = {
            action_id: self._portable_text(editor) for action_id, editor in self._interface_edits.items()
        }
        self._settings.global_shortcuts = {
            action_id: self._portable_text(editor) for action_id, editor in self._global_edits.items()
        }
        return self._settings
