from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.services.app_controller import AppController
from app.ui.main_window import MainWindow
from app.ui.theme import APP_STYLE


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MusePlayer")
    app.setStyle("Fusion")
    if sys.platform.startswith("win"):
        app.setFont(QFont("Microsoft YaHei", 9))
    app.setStyleSheet(APP_STYLE)

    project_root = Path(__file__).resolve().parent
    controller = AppController(project_root=project_root)

    win = MainWindow(controller)
    win.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
