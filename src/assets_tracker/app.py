from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from assets_tracker.db.connection import DatabaseSecurityError, connect, default_db_path
from assets_tracker.db.keychain import KeychainError
from assets_tracker.ui.main_window import MainWindow
from assets_tracker.ui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AssetsTracker")
    apply_theme(app)
    try:
        database = connect(default_db_path())
    except (DatabaseSecurityError, KeychainError) as error:
        QMessageBox.critical(None, "Portefeuille verrouillé", str(error))
        return 1
    window = MainWindow()
    window.show()
    exit_code = app.exec()
    database.close()
    return exit_code
