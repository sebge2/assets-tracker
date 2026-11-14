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
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.bond_repository import BondRepository
from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.db.commodity_repository import CommodityRepository
from assets_tracker.db.fund_repository import FundRepository
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.db.stock_repository import StockRepository
from assets_tracker.ui import icons, theme
from assets_tracker.ui.bonds_page import BondsPage
from assets_tracker.ui.cash_page import CashPage
from assets_tracker.ui.commodities_page import CommoditiesPage
from assets_tracker.ui.funds_page import FundsPage
from assets_tracker.ui.portfolio_page import PortfolioPage
from assets_tracker.ui.projection_page import ProjectionPage
from assets_tracker.ui.stocks_page import StocksPage


class MainWindow(QMainWindow):
    def __init__(self, database: Any) -> None:
        super().__init__()
        self.setWindowTitle("Assets Tracker")
        self.resize(1360, 820)

        self._stack = QStackedWidget()
        cash_repository = CashRepository(database)
        commodity_repository = CommodityRepository(database)
        fund_repository = FundRepository(database)
        bond_repository = BondRepository(database)
        stock_repository = StockRepository(database)
        fx_repository = FxRepository(database)
        self.portfolio_page = PortfolioPage(
            cash_repository,
            commodity_repository,
            fund_repository,
            bond_repository,
            stock_repository,
            fx_repository,
        )
        self.cash_page = CashPage(cash_repository)
        self._stack.addWidget(self.portfolio_page)
        self.commodities_page = CommoditiesPage(commodity_repository)
        self.funds_page = FundsPage(fund_repository)
        self.bonds_page = BondsPage(bond_repository, fx_repository)
        self.stocks_page = StocksPage(stock_repository, fx_repository)
        self.projection_page = ProjectionPage()
        self._stack.addWidget(self.cash_page)
        self._stack.addWidget(self.commodities_page)
        self._stack.addWidget(self.funds_page)
        self._stack.addWidget(self.bonds_page)
        self._stack.addWidget(self.stocks_page)
        self._stack.addWidget(self.projection_page)

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
            (
                ("Portefeuille", "dashboard"),
                ("Comptes cash", "cash"),
                ("Matières premières", "commodities"),
                ("Fonds", "fund"),
                ("Obligations", "bond"),
                ("Actions", "stock"),
                ("Simulation", "simulation"),
            )
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

    def _navigate(self, index: int) -> None:
        self._nav_buttons[index].setChecked(True)
        self._stack.setCurrentIndex(index)
        if index == 0:
            self.portfolio_page.refresh()
        elif index == 1:
            self.cash_page.show_list()
        elif index == 2:
            self.commodities_page.refresh()
        elif index == 3:
            self.funds_page.refresh()
        elif index == 4:
            self.bonds_page.refresh()
            self.bonds_page.refresh_quotes()
        elif index == 5:
            self.stocks_page.refresh()
            self.stocks_page.refresh_quotes()
