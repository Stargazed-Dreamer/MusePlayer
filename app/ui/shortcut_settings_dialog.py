from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QVBoxLayout,
)

from app.models.entities import Settings
from app.ui.shortcut_settings import (
    SHORTCUT_ACTIONS,
    default_global_shortcuts,
    default_interface_shortcuts,
    merge_shortcuts,
)


class ShortcutSettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._interface_edits: dict[str, QKeySequenceEdit] = {}
        self._global_edits: dict[str, QKeySequenceEdit] = {}
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
            grid.addWidget(QLabel(item.label), row, 0)
            interface_edit = QKeySequenceEdit(QKeySequence(interface_values[item.action_id]))
            global_edit = QKeySequenceEdit(QKeySequence(global_values[item.action_id]))
            interface_edit.setMaximumSequenceLength(1)
            global_edit.setMaximumSequenceLength(1)
            grid.addWidget(interface_edit, row, 1)
            grid.addWidget(global_edit, row, 2)
            self._interface_edits[item.action_id] = interface_edit
            self._global_edits[item.action_id] = global_edit
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
            action_id: self._portable_text(editor)
            for action_id, editor in self._interface_edits.items()
        }
        self._settings.global_shortcuts = {
            action_id: self._portable_text(editor)
            for action_id, editor in self._global_edits.items()
        }
        return self._settings
