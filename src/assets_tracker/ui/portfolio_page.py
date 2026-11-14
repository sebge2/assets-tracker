"""Page « Portefeuille » : indicateurs, évolution sur une période au choix, répartition."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.bond_repository import BondRepository
from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.db.commodity_repository import CommodityRepository
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.fund_repository import FundRepository
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.db.stock_repository import StockRepository
from assets_tracker.domain.exposure import ExposedAsset, ExposureKind
from assets_tracker.domain.fx import rate_for, transactions_in_euros
from assets_tracker.domain.models import InstrumentType
from assets_tracker.domain.portfolio import (
    BASE_CURRENCY,
    CashSource,
    Portfolio,
    PortfolioInput,
    PricedSource,
    build_portfolio,
    change,
    window,
)
from assets_tracker.ui import theme
from assets_tracker.ui.allocation_donut import AllocationDonut, DonutSlice
from assets_tracker.ui.evolution_chart import EvolutionChart
from assets_tracker.ui.formatting import (
    ASSET_LABELS,
    MINUS,
    format_number,
    money,
    signed_money,
)
from assets_tracker.ui.portfolio_exposure import PortfolioExposureCard
from assets_tracker.ui.widgets import Card, PageContent

_PERIODS = (("3 mois", 3), ("6 mois", 6), ("12 mois", 12), ("Tout", 0))


def _signed_percent(value: Decimal) -> str:
    prefix = "+" if value > 0 else ""
    return f"{prefix}{format_number(value)} %"


class _LegendRow(QFrame):
    """Une ligne de légende : le survol ou le focus clavier met la part en avant."""

    entered = Signal()
    left = Signal()

    def __init__(self, item: DonutSlice) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(f"{item.label} : {format_number(item.percent, 1)} %")
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {item.color};")
        name = QLabel(item.label)
        share = QLabel(f"{format_number(item.percent, 1)} %")
        share.setFont(theme.mono_font(13))
        share.setProperty("role", "muted")
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(10)
        row.addWidget(dot)
        row.addWidget(name, 1)
        row.addWidget(share)

    def enterEvent(self, event: object) -> None:  # noqa: N802 (API Qt)
        self.entered.emit()

    def leaveEvent(self, event: object) -> None:  # noqa: N802 (API Qt)
        self.left.emit()

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 (API Qt)
        super().focusInEvent(event)
        self.entered.emit()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 (API Qt)
        super().focusOutEvent(event)
        self.left.emit()


class PortfolioPage(QWidget):
    """Vue d'ensemble, lecture seule : tout est recalculé depuis les comptes et les positions."""

    asset_requested = Signal(object, int)  # (InstrumentType, identifiant) : ouvrir sa fiche

    def __init__(
        self,
        cash: CashRepository,
        commodities: CommodityRepository,
        funds: FundRepository,
        bonds: BondRepository,
        stocks: StockRepository,
        fx: FxRepository,
        *,
        exposure: ExposureRepository | None = None,
        today: Callable[[], date] = date.today,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cash = cash
        self._commodities = commodities
        self._funds = funds
        self._bonds = bonds
        self._stocks = stocks
        self._fx = fx
        self._exposure = exposure
        self._foreign_funds: tuple[str, ...] = ()
        self._foreign_bonds: tuple[str, ...] = ()
        self._foreign_stocks: tuple[str, ...] = ()
        self._today = today
        self._period = 12
        self._portfolio: Portfolio | None = None
        self._rows: list[_LegendRow] = []

        title = QLabel("Portefeuille")
        title.setProperty("role", "title")
        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "subtitle")
        header = QVBoxLayout()
        header.setSpacing(4)
        header.addWidget(title)
        header.addWidget(self._subtitle)

        self._value_card = Card("Valeur totale")
        self._gain_card = Card("Plus-value latente")
        self._realized_card = Card("Plus-value réalisée")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._value_card, self._gain_card, self._realized_card):
            cards.addWidget(card, 1)

        self._chart = EvolutionChart()
        self._delta = QLabel()
        self._delta.setFont(theme.mono_font(14))
        chips = QHBoxLayout()
        chips.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self._chips: dict[int, QPushButton] = {}
        for label, months in _PERIODS:
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setProperty("variant", "chip")
            chip.setChecked(months == self._period)
            chip.clicked.connect(lambda _=False, m=months: self._set_period(m))
            group.addButton(chip)
            self._chips[months] = chip
            chips.addWidget(chip)
        chips.addStretch(1)
        evolution_caption = QLabel("ÉVOLUTION DE LA VALEUR")
        evolution_caption.setProperty("role", "caption")
        self._evolution = QFrame()
        self._evolution.setProperty("role", "card")
        evolution_layout = QVBoxLayout(self._evolution)
        evolution_layout.setContentsMargins(20, 16, 20, 16)
        evolution_layout.setSpacing(12)
        evolution_layout.addWidget(evolution_caption)
        evolution_layout.addLayout(chips)
        evolution_layout.addWidget(self._delta)
        evolution_layout.addWidget(self._chart, 1)

        allocation_caption = QLabel("RÉPARTITION")
        allocation_caption.setProperty("role", "caption")
        self._donut = AllocationDonut()
        self._donut.highlighted.connect(self._on_donut_highlight)
        self._legend = QVBoxLayout()
        self._legend.setSpacing(2)
        self._note = QLabel()
        self._note.setProperty("role", "muted")
        self._note.setWordWrap(True)
        self._allocation = QFrame()
        self._allocation.setProperty("role", "card")
        self._allocation.setMinimumWidth(240)
        allocation_layout = QVBoxLayout(self._allocation)
        allocation_layout.setContentsMargins(20, 16, 20, 16)
        allocation_layout.setSpacing(12)
        allocation_layout.addWidget(allocation_caption)
        allocation_layout.addWidget(self._donut, 0, Qt.AlignmentFlag.AlignHCenter)
        allocation_layout.addLayout(self._legend)
        allocation_layout.addWidget(self._note)
        allocation_layout.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._evolution, 2)
        body.addWidget(self._allocation, 1)

        self._exposure_card = PortfolioExposureCard()
        self._exposure_card.asset_requested.connect(self.asset_requested)

        self._empty = QLabel(
            "Rien à afficher pour l'instant : ajoutez un compte cash, un fonds, "
            "une obligation, une action ou une matière première."
        )
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)

        content = PageContent()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(24)
        layout.addLayout(header)
        layout.addLayout(cards)
        layout.addLayout(body)
        layout.addWidget(self._exposure_card)
        layout.addWidget(self._empty)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.refresh()

    # -- données --

    def load(self) -> PortfolioInput:
        cash = [
            CashSource(account, self._cash.movements(account.id))
            for account in self._cash.list_accounts()
            if account.id is not None
        ]
        priced = [
            PricedSource(
                InstrumentType.COMMODITY,
                holding.transactions,
                [(record.valuation_date, record.price) for record in holding.history],
            )
            for holding in self._commodities.list_holdings()
        ]
        rates = self._fx.latest_rates()
        funds = self._funds.list_holdings()
        # Devise étrangère : convertie au dernier taux connu, exclue et signalée sans taux.
        self._foreign_funds = tuple(
            sorted({h.asset.currency for h in funds if rate_for(h.asset.currency, rates) is None})
        )
        for holding in funds:
            asset_id = holding.asset.id
            rate = rate_for(holding.asset.currency, rates)
            if asset_id is None or rate is None:
                continue
            history = [
                (v.valuation_date, v.value * rate) for v in self._funds.nav_history(asset_id)
            ]
            priced.append(
                PricedSource(
                    InstrumentType.FUND, transactions_in_euros(holding.transactions, rate), history
                )
            )
        bonds = self._bonds.list_holdings()
        self._foreign_bonds = tuple(
            sorted({h.asset.currency for h in bonds if rate_for(h.asset.currency, rates) is None})
        )
        for bond in bonds:
            asset_id = bond.asset.id
            rate = rate_for(bond.asset.currency, rates)
            if asset_id is None or rate is None:
                continue
            # Valeur d'un titre (cours % × nominal), comme le prix des transactions, en euros.
            history = [
                (v.valuation_date, v.value * rate) for v in self._bonds.quote_history(asset_id)
            ]
            priced.append(
                PricedSource(
                    InstrumentType.BOND, transactions_in_euros(bond.transactions, rate), history
                )
            )
        stocks = self._stocks.list_holdings()
        self._foreign_stocks = tuple(
            sorted({h.asset.currency for h in stocks if rate_for(h.asset.currency, rates) is None})
        )
        for stock in stocks:
            asset_id = stock.asset.id
            rate = rate_for(stock.asset.currency, rates)
            if asset_id is None or rate is None:
                continue
            history = [
                (v.valuation_date, v.value * rate) for v in self._stocks.quote_history(asset_id)
            ]
            priced.append(
                PricedSource(
                    InstrumentType.STOCK, transactions_in_euros(stock.transactions, rate), history
                )
            )
        return PortfolioInput(cash, priced)

    def refresh(self) -> None:
        self._portfolio = build_portfolio(self.load(), self._today())
        summary = self._portfolio.summary
        quoted = [h.price.fetched_at for h in self._commodities.list_holdings() if h.price]
        self._subtitle.setText(
            f"Cours mis à jour à {max(quoted):%H:%M}" if quoted else "Aucun cours actualisé"
        )
        self._value_card.set(
            money(summary.total, "EUR"), "Cash, fonds, obligations, actions et matières premières"
        )
        gain_color = theme.variation_color(summary.unrealized)
        arrow = "▲ " if summary.unrealized > 0 else "▼ " if summary.unrealized < 0 else ""
        percent = (
            "Aucune position à valoriser"
            if summary.unrealized_percent is None
            else _signed_percent(summary.unrealized_percent)
        )
        self._gain_card.set(
            f"{arrow}{signed_money(summary.unrealized, 'EUR')}", percent, gain_color
        )
        self._realized_card.set(
            signed_money(summary.realized, "EUR"),
            "Ventes déjà encaissées",
            theme.variation_color(summary.realized),
        )
        self._fill_allocation(self._portfolio)
        self._render_evolution()
        has_data = bool(self._portfolio.months)
        self._evolution.setVisible(has_data)
        self._allocation.setVisible(has_data)
        self._exposure_card.set_assets(self._exposed_assets(self._portfolio))
        self._exposure_card.setVisible(has_data and self._exposure is not None)
        self._empty.setVisible(not has_data)

    # -- secteurs et zones --

    def _exposed_assets(self, portfolio: Portfolio) -> list[ExposedAsset]:
        """Valeur en EUR de chaque position ouverte (dernier taux connu) et ses répartitions.

        Les devises sans taux sont exclues, comme dans la valeur totale ; le cash et les matières
        premières entrent en un seul bloc chacun (leur valeur vient de la répartition par type).
        """
        if self._exposure is None:
            return []
        rates = self._fx.latest_rates()
        assets: list[ExposedAsset] = []
        groups = (
            (InstrumentType.FUND, self._funds.list_holdings()),
            (InstrumentType.BOND, self._bonds.list_holdings()),
            (InstrumentType.STOCK, self._stocks.list_holdings()),
        )
        for instrument, holdings in groups:
            for holding in holdings:
                asset_id = holding.asset.id
                rate = rate_for(holding.asset.currency, rates)
                quantity = holding.position.quantity
                if asset_id is None or rate is None or holding.valuation is None or quantity <= 0:
                    continue
                assets.append(
                    ExposedAsset(
                        holding.asset.name,
                        instrument,
                        quantity * holding.valuation.value * rate,
                        tuple(self._exposure.get(asset_id, ExposureKind.SECTOR)),
                        tuple(self._exposure.get(asset_id, ExposureKind.ZONE)),
                        asset_id,
                    )
                )
        blocks = {
            InstrumentType.CASH: "Comptes cash",
            InstrumentType.COMMODITY: "Matières premières",
        }
        for part in portfolio.slices:
            if part.asset_type in blocks:
                name = blocks[part.asset_type]
                assets.append(ExposedAsset(name, part.asset_type, part.value, (), ()))
        return assets

    # -- évolution --

    def _set_period(self, months: int) -> None:
        self._period = months
        self._render_evolution()

    def _render_evolution(self) -> None:
        if self._portfolio is None:
            return
        months, totals = window(self._portfolio.months, self._portfolio.totals, self._period)
        self._chart.set_data(months, [], totals, "EUR")
        delta, percent = change(totals)
        if len(totals) < 2:
            self._delta.setText("")
            return
        arrow = "▲ " if delta > 0 else "▼ " if delta < 0 else ""
        amount = f"{MINUS}{money(-delta, 'EUR')}" if delta < 0 else signed_money(delta, "EUR")
        text = f"{arrow}{amount}"
        if percent is not None:
            text += f" ({_signed_percent(percent)})"
        period = next(label for label, months_ in _PERIODS if months_ == self._period)
        self._delta.setText(f"{text} · {period if self._period else 'depuis le début'}")
        self._delta.setStyleSheet(f"color: {theme.variation_color(delta)};")

    # -- répartition --

    def _fill_allocation(self, portfolio: Portfolio) -> None:
        slices = [
            DonutSlice(
                ASSET_LABELS[s.asset_type],
                theme.ASSET_COLORS[s.asset_type.value],
                s.value,
                s.percent,
            )
            for s in portfolio.slices
        ]
        self._donut.set_slices(slices, portfolio.summary.total)
        for row in self._rows:
            self._legend.removeWidget(row)
            row.deleteLater()
        self._rows = []
        for index, item in enumerate(slices):
            row = _LegendRow(item)
            row.entered.connect(lambda i=index: self._donut.set_highlight(i))
            row.left.connect(lambda: self._donut.set_highlight(-1))
            self._legend.addWidget(row)
            self._rows.append(row)
        notes = []
        if portfolio.foreign_cash:
            currencies = ", ".join(portfolio.foreign_cash)
            notes.append(f"Comptes en {currencies} non inclus (pas de taux de change).")
        if self._foreign_funds:
            currencies = ", ".join(self._foreign_funds)
            notes.append(f"Fonds en {currencies} non inclus (pas de taux de change).")
        if self._foreign_bonds:
            currencies = ", ".join(self._foreign_bonds)
            notes.append(f"Obligations en {currencies} non incluses (pas de taux de change).")
        if self._foreign_stocks:
            currencies = ", ".join(self._foreign_stocks)
            notes.append(f"Actions en {currencies} non incluses (pas de taux de change).")
        self._note.setText(" ".join(notes))
        self._note.setVisible(bool(notes))

    def _on_donut_highlight(self, index: int) -> None:
        for position, row in enumerate(self._rows):
            row.setStyleSheet(
                f"background: {theme.HOVER_ROW}; border-radius: 6px;" if position == index else ""
            )
