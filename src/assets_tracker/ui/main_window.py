from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTableWidget


class MainWindow(QMainWindow):
    COLUMNS = ("Actif", "Quantité", "PRU", "Cours", "Valeur", "+/- value")

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Assets Tracker")
        self.resize(900, 560)
        table = QTableWidget(0, len(self.COLUMNS))
        table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.setCentralWidget(table)
