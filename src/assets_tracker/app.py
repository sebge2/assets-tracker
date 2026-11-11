from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from assets_tracker.ui.main_window import MainWindow
from assets_tracker.ui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AssetsTracker")
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()
