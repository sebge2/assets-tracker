from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
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
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.fund_repository import FundRepository
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.db.insurance_repository import InsuranceRepository
from assets_tracker.db.stock_repository import StockRepository
from assets_tracker.db.term_note_repository import TermNoteRepository
from assets_tracker.db.valuation_repository import ValuationRepository
from assets_tracker.domain.models import InstrumentType
from assets_tracker.market.exposure import fetch_exposure
from assets_tracker.ui import icons, theme
from assets_tracker.ui.bonds_page import BondsPage
from assets_tracker.ui.cash_page import CashPage
from assets_tracker.ui.commodities_page import CommoditiesPage
from assets_tracker.ui.funds_page import FundsPage
from assets_tracker.ui.analytics_page import AnalyticsPage
from assets_tracker.ui.insurance_page import InsurancePage
from assets_tracker.ui.portfolio_page import PortfolioPage
from assets_tracker.ui.projection_page import ProjectionPage
from assets_tracker.ui.stocks_page import StocksPage
from assets_tracker.ui.term_notes_page import TermNotesPage

AUTO_REFRESH_MS = 4 * 60 * 60 * 1000  # actualisation automatique de tous les cours : 4 h


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
        term_note_repository = TermNoteRepository(database)
        insurance_repository = InsuranceRepository(database)
        fx_repository = FxRepository(database)
        exposure_repository = ExposureRepository(database)
        self.portfolio_page = PortfolioPage(
            cash_repository,
            commodity_repository,
            fund_repository,
            bond_repository,
            stock_repository,
            fx_repository,
            exposure=exposure_repository,
            term_notes=term_note_repository,
        )
        self.cash_page = CashPage(cash_repository)
        self._stack.addWidget(self.portfolio_page)
        self.commodities_page = CommoditiesPage(commodity_repository)
        self.funds_page = FundsPage(
            fund_repository, exposure=exposure_repository, exposure_fetcher=fetch_exposure
        )
        self.bonds_page = BondsPage(bond_repository, fx_repository, exposure=exposure_repository)
        self.term_notes_page = TermNotesPage(term_note_repository, exposure=exposure_repository)
        self.insurance_page = InsurancePage(insurance_repository)  # hors portefeuille
        self.stocks_page = StocksPage(
            stock_repository,
            fx_repository,
            exposure=exposure_repository,
            exposure_fetcher=fetch_exposure,
        )
        self.analytics_page = AnalyticsPage(self.portfolio_page.load)  # hors assurances
        self.projection_page = ProjectionPage()
        self.portfolio_page.asset_requested.connect(self._open_asset)
        self._valuations = ValuationRepository(database)
        self._refresh_pending: set[str] = set()
        self._refresh_starting = False
        self.portfolio_page.refresh_requested.connect(self.refresh_all_quotes)
        self.portfolio_page.set_last_refresh(self._valuations.last_fetched_at())
        for name, page in (
            ("stocks", self.stocks_page),
            ("bonds", self.bonds_page),
            ("commodities", self.commodities_page),
        ):
            page.quotes_refreshed.connect(lambda name=name: self._quotes_refreshed(name))
        self._auto_refresh = QTimer(self)
        self._auto_refresh.setInterval(AUTO_REFRESH_MS)
        self._auto_refresh.timeout.connect(self.refresh_all_quotes)
        self._auto_refresh.start()
        self._stack.addWidget(self.cash_page)
        self._stack.addWidget(self.commodities_page)
        self._stack.addWidget(self.funds_page)
        self._stack.addWidget(self.bonds_page)
        self._stack.addWidget(self.term_notes_page)
        self._stack.addWidget(self.insurance_page)
        self._stack.addWidget(self.stocks_page)
        self._stack.addWidget(self.analytics_page)
        self._stack.addWidget(self.projection_page)

        root = QWidget()
        root.setObjectName("root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(root)

    def refresh_all_quotes(self) -> None:
        """Redemande les cours de toutes les positions (actions, obligations, matières premières).

        Appelé par le bouton du Portefeuille et, toutes les 4 h, par le minuteur. Les appels
        tournent hors du thread Qt ; le spinner s'arrête quand toutes les pages ont terminé.
        """
        if self._refresh_pending or self._refresh_starting:
            return
        pages = {
            "stocks": self.stocks_page.refresh_quotes,
            "bonds": self.bonds_page.refresh_quotes,
            "commodities": self.commodities_page.refresh_prices,
        }
        self._refresh_pending = set(pages)
        self._refresh_starting = True
        self.portfolio_page.set_refreshing(True)
        for name, refresh in pages.items():
            if not refresh(force=True):
                self._refresh_pending.discard(name)
        self._refresh_starting = False
        self._finish_refresh_if_done()

    def _quotes_refreshed(self, name: str) -> None:
        self._refresh_pending.discard(name)
        if not self._refresh_starting:
            self._finish_refresh_if_done()

    def _finish_refresh_if_done(self) -> None:
        if self._refresh_pending:
            return
        self.portfolio_page.set_refreshing(False)
        self.portfolio_page.set_last_refresh(self._valuations.last_fetched_at())
        self.portfolio_page.refresh()

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
                ("Bons de caisse", "term_note"),
                ("Assurances et pensions", "insurance"),
                ("Actions", "stock"),
                ("Analyses", "analytics"),
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

    def _open_asset(self, instrument: InstrumentType, asset_id: int) -> None:
        """Ouvre la fiche d'un actif depuis l'écran Portefeuille (pour compléter ses données)."""
        pages = {
            InstrumentType.FUND: (3, self.funds_page),
            InstrumentType.BOND: (4, self.bonds_page),
            InstrumentType.TERM_NOTE: (5, self.term_notes_page),
            InstrumentType.STOCK: (7, self.stocks_page),
        }
        target = pages.get(instrument)
        if target is None:
            return
        index, page = target
        page.select(asset_id)
        self._navigate(index)

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
            self.term_notes_page.refresh()
        elif index == 6:
            self.insurance_page.refresh()
        elif index == 7:
            self.stocks_page.refresh()
            self.stocks_page.refresh_quotes()
        elif index == 8:
            self.analytics_page.refresh()
