from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.ui import icons, theme
from assets_tracker.ui.cash_page import CashPage


class MainWindow(QMainWindow):
    COLUMNS = ("Actif", "Quantité", "PRU", "Cours", "Valeur", "+/- value")

    def __init__(self, database: Any) -> None:
        super().__init__()
        self.setWindowTitle("Assets Tracker")
        self.resize(1360, 820)

        self._stack = QStackedWidget()
        self._portfolio = self._build_portfolio_page()
        self.cash_page = CashPage(CashRepository(database))
        self._stack.addWidget(self._portfolio)
        self._stack.addWidget(self.cash_page)

        root = QWidget()
        root.setObjectName("root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(root)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(8)

        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(32))
        logo.setAccessibleName("Logo Assets Tracker")
        name = QLabel(f"Assets <span style='color:{theme.ACCENT}'>Tracker</span>")
        name.setTextFormat(Qt.TextFormat.RichText)
        name.setStyleSheet("font-size: 20px; font-weight: 700;")
        brand = QHBoxLayout()
        brand.setContentsMargins(12, 0, 12, 20)
        brand.setSpacing(10)
        brand.addWidget(logo)
        brand.addWidget(name, 1)
        layout.addLayout(brand)

        group = QButtonGroup(self)
        group.setExclusive(True)
        self._nav_buttons: list[QPushButton] = []
        for index, (label, icon_name) in enumerate(
            (("Portefeuille", "dashboard"), ("Comptes cash", "cash"))
        ):
            button = QPushButton(label)
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setIcon(icons.icon(icon_name, theme.TEXT_MUTED, theme.ACCENT))
            button.setIconSize(QSize(18, 18))
            button.clicked.connect(lambda _=False, i=index: self._navigate(i))
            group.addButton(button)
            self._nav_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        return sidebar

    def _build_portfolio_page(self) -> QWidget:
        table = QTableWidget(0, len(self.COLUMNS))
        table.setHorizontalHeaderLabels(list(self.COLUMNS))
        return table

    def _navigate(self, index: int) -> None:
        self._nav_buttons[index].setChecked(True)
        self._stack.setCurrentIndex(index)
        if index == 1:
            self.cash_page.show_list()
