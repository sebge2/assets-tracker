"""Écran des actions : synthèse en euros, secteurs et zones, liste, fiche, achat et vente."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from html import escape

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.db.stock_repository import StockHolding, StockRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind, exposure_breakdown
from assets_tracker.domain.funds import FundValue, ValuationSource
from assets_tracker.domain.fx import currencies_to_refresh, rate_for
from assets_tracker.domain.models import Position, Transaction, TransactionType
from assets_tracker.domain.positions import compute_position
from assets_tracker.domain.stocks import (
    StockDetails,
    StockQuote,
    annual_dividends,
    dividend_yield,
    is_quote_stale,
    price_change,
    price_series,
    stock_in_euros,
    stock_totals,
    stock_trade_quote,
    stock_value,
    validate_shares,
)
from assets_tracker.domain.valuation import Valuation, assets_to_refresh
from assets_tracker.market.fx import fetch_rate_to_eur
from assets_tracker.market.stocks import fetch_stock_price
from assets_tracker.ui import theme
from assets_tracker.ui.bonds_page import (
    DETAIL_WIDTH,
    LAST_OPERATIONS,
    SOURCE_LABELS,
    WIDE_LAYOUT,
    QuoteFetcher,
    RateFetcher,
    _buttons,
    _clear,
    _QuoteTask,
    _rich,
    _signed_pct,
    _subtle,
    _ValidatedDialog,
    fetch_quotes,
    fetch_rates,
)
from assets_tracker.ui.commodity_chart import PositionChart
from assets_tracker.ui.exposure_widgets import (
    ExposureSection,
    ExposureSummary,
    edit_exposure,
    main_label,
)
from assets_tracker.ui.form_helpers import date_edit, labeled, row, to_date
from assets_tracker.ui.formatting import (
    MINUS,
    NBSP,
    format_number,
    format_quantity,
    money,
    parse_decimal,
    signed_money,
)
from assets_tracker.ui.widgets import RIGHT, Card, fit_height, prepare_table

CURRENCY = "EUR"


# --- Données affichées ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StockView:
    """Une action valorisée : tout est recalculé depuis ses opérations et son dernier cours."""

    holding: StockHolding
    position: Position
    quote: Valuation | None  # valeur d'UNE action, dans sa devise ; None sans cours connu
    value: FundValue | None
    stale: bool  # cours plus ancien que le seuil de `domain.stocks`
    change: Decimal | None  # variation entre les deux derniers cours enregistrés

    @property
    def asset_id(self) -> int:
        assert self.holding.asset.id is not None
        return self.holding.asset.id

    @property
    def details(self) -> StockDetails:
        return self.holding.details

    @property
    def currency(self) -> str:
        return self.holding.asset.currency


def build_view(
    holding: StockHolding, today: date, history: list[tuple[date, Decimal]] | None = None
) -> StockView:
    position = holding.position
    quote = holding.valuation
    value = stock_value(position, quote.value) if quote is not None else None
    stale = quote is not None and is_quote_stale(quote.valuation_date, today)
    return StockView(holding, position, quote, value, stale, price_change(history or []))


def outdated(views: list[StockView]) -> list[StockView]:
    """Positions ouvertes sans cours ou au cours ancien, la plus urgente d'abord."""
    pending = [v for v in views if v.position.quantity > 0 and (v.quote is None or v.stale)]
    return sorted(pending, key=lambda v: v.quote.valuation_date if v.quote else date.min)


def alert_message(pending: list[StockView], today: date, failed: set[int]) -> str:
    first = pending[0]
    label = escape(first.holding.asset.name)
    name = f"<span style='color:{theme.WARNING};font-weight:600'>{label}</span>"
    yahoo = first.asset_id in failed
    if first.quote is None:
        text = f"{name} n'a pas encore de cours. "
        text += "Yahoo n'en fournit pas : saisissez-le." if yahoo else "Saisissez-le."
    else:
        age = (today - first.quote.valuation_date).days
        origin = "Yahoo ne fournit pas de cours pour " if yahoo else "Le cours de "
        text = (
            f"{origin}{name}"
            f"{', et le dernier cours' if yahoo else ''} date du "
            f"{first.quote.valuation_date:%d/%m/%Y} ({age} jours). "
            "Saisissez le dernier cours pour une valorisation fiable."
        )
    others = len(pending) - 1
    if others:
        text += f" {others} autre{'s' if others > 1 else ''} action{'s' if others > 1 else ''}"
        text += " à mettre à jour."
    return text


def _pct(value: Decimal | None, places: int = 2) -> str:
    return "—" if value is None else f"{format_number(value, places)}{NBSP}%"


# --- Dialogues --------------------------------------------------------------


class StockDialog(_ValidatedDialog):
    """Nouvelle action : le cours est demandé à Yahoo, saisi à la main s'il est absent."""

    def __init__(self, parent: QWidget | None, holding: StockHolding | None = None) -> None:
        super().__init__(parent)
        self._source = holding.details.valuation_source if holding else ValuationSource.YAHOO
        self.setWindowTitle("Modifier l'action" if holding else "Nouvelle action")
        self.setMinimumWidth(560)
        self._name, self._ticker = QLineEdit(), QLineEdit()
        self._currency, self._place = QLineEdit("EUR"), QLineEdit()
        self._dividend = QLineEdit()
        hint = QLabel(
            "Le cours est récupéré automatiquement via Yahoo à partir du ticker. S'il est "
            "introuvable, vous serez invité à le saisir à la main, avec une alerte s'il date de "
            "plus de 7 jours."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        title = QLabel("Modifier l'action" if holding else "Nouvelle action")
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        layout.addWidget(title)
        layout.addWidget(labeled("Nom", self._name))
        layout.addWidget(
            row(
                labeled("Ticker Yahoo", self._ticker, "Avec le suffixe de la place (.PA, .AS…)"),
                labeled("Devise", self._currency, "Convertie en EUR dans les synthèses"),
            )
        )
        layout.addWidget(labeled("Place de cotation", self._place))
        layout.addWidget(
            labeled("Dividende annuel par action", self._dividend, "Optionnel, dans la devise")
        )
        layout.addWidget(hint)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer" if holding else "Ajouter l'action"))
        if holding is not None:
            self._load(holding)

    def _load(self, holding: StockHolding) -> None:
        details = holding.details
        self._name.setText(holding.asset.name)
        self._ticker.setText(holding.asset.symbol)
        self._currency.setText(holding.asset.currency)
        self._place.setText(details.place)
        dividend = details.dividend
        self._dividend.setText("" if dividend is None else format_quantity(dividend))

    def validate(self) -> None:
        self.form()

    def form(self) -> tuple[str, str, str, StockDetails]:
        dividend_text = self._dividend.text().strip()
        dividend = parse_decimal(dividend_text, "Dividende") if dividend_text else None
        return (
            self._name.text(),
            self._ticker.text(),
            self._currency.text(),
            StockDetails(self._place.text(), dividend, self._source),
        )


class QuoteDialog(_ValidatedDialog):
    """Saisie manuelle du cours, sans passer par une opération."""

    def __init__(self, parent: QWidget | None, holding: StockHolding, today: date) -> None:
        super().__init__(parent)
        self._today = today
        self.setWindowTitle("Mettre à jour le cours")
        self.setMinimumWidth(460)
        self._price = QLineEdit()
        self._date = date_edit(today)
        latest, currency = holding.valuation, holding.asset.currency
        hint = (
            f"Dernier cours connu : {money(latest.value, currency)} "
            f"({latest.valuation_date:%d/%m/%Y})"
            if latest is not None
            else "Aucun cours connu pour l'instant."
        )
        title = QLabel(holding.asset.name)
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(
            row(labeled(f"Cours ({currency})", self._price, hint), labeled("Date", self._date))
        )
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer"))

    def validate(self) -> None:
        self.form()

    def form(self) -> tuple[date, Decimal]:
        price = parse_decimal(self._price.text(), "Cours")
        if price <= 0:
            raise ValueError("Le cours doit être strictement positif.")
        when = to_date(self._date)
        if when > self._today:
            raise ValueError("La date du cours ne peut pas être dans le futur.")
        return when, price


@dataclass(frozen=True, slots=True)
class TradeForm:
    kind: TransactionType
    when: date
    quantity: Decimal
    price: Decimal
    fees: Decimal
    record_quote: bool  # le cours saisi devient le cours daté de l'opération


@dataclass(frozen=True, slots=True)
class _Preview:
    quote: StockQuote
    after: Position


class TradeDialog(QDialog):
    """Achat ou vente, avec récapitulatif recalculé à chaque frappe."""

    def __init__(
        self, parent: QWidget | None, holding: StockHolding, kind: TransactionType, today: date
    ) -> None:
        super().__init__(parent)
        self._holding, self._today, self._kind = holding, today, kind
        self._position = holding.position
        self._currency = holding.asset.currency
        self.setWindowTitle("Acheter ou vendre")
        self.setMinimumWidth(560)

        self._title = QLabel()
        self._title.setProperty("role", "title")
        self._title.setStyleSheet("font-size: 20px;")
        held = format_quantity(self._position.quantity)
        subtitle = QLabel(f"{holding.asset.name} · détention {held} actions")
        subtitle.setProperty("role", "muted")

        kinds = QHBoxLayout()
        kinds.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self._kind_buttons: dict[TransactionType, QPushButton] = {}
        for choice, label in ((TransactionType.BUY, "Achat"), (TransactionType.SELL, "Vente")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("variant", "chip")
            button.setChecked(choice is kind)
            button.clicked.connect(lambda _=False, k=choice: self._set_kind(k))
            group.addButton(button)
            self._kind_buttons[choice] = button
            kinds.addWidget(button)
        kinds.addStretch(1)
        self._kind_buttons[TransactionType.SELL].setEnabled(self._position.quantity > 0)

        latest = holding.valuation
        self._quantity = QLineEdit()
        self._price = QLineEdit(format_number(latest.value) if latest is not None else "")
        self._fees = QLineEdit("0")
        self._date = date_edit(today)
        self._quantity_hint = QLabel()
        self._quantity_hint.setProperty("role", "hint")
        price_hint = (
            f"Prérempli : dernier cours ({latest.valuation_date:%d/%m/%Y})"
            if latest is not None
            else "Aucun cours connu : saisissez-le."
        )
        quantity_box = QWidget()
        quantity_layout = QVBoxLayout(quantity_box)
        quantity_layout.setContentsMargins(0, 0, 0, 0)
        quantity_layout.setSpacing(6)
        quantity_caption = QLabel("Actions")
        quantity_caption.setProperty("role", "muted")
        quantity_layout.addWidget(quantity_caption)
        quantity_layout.addWidget(self._quantity)
        quantity_layout.addWidget(self._quantity_hint)

        summary = QFrame()
        summary.setProperty("role", "card")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(18, 16, 18, 16)
        summary_layout.setSpacing(10)
        self._summary_rows: list[tuple[QWidget, QLabel, QLabel]] = []
        for _ in range(4):
            line = QWidget()
            line_layout = QHBoxLayout(line)
            line_layout.setContentsMargins(0, 0, 0, 0)
            caption, value = QLabel(), QLabel()
            value.setFont(theme.mono_font(14))
            line_layout.addWidget(caption, 1)
            line_layout.addWidget(value)
            summary_layout.addWidget(line)
            self._summary_rows.append((line, caption, value))
        self._after = QLabel()
        self._after.setProperty("role", "hint")
        summary_layout.addWidget(self._after)

        self._error = QLabel()
        self._error.setProperty("role", "error")
        self._error.setWordWrap(True)
        self._record = QCheckBox("Enregistrer ce cours comme cours à cette date")
        self._record.setChecked(
            holding.details.valuation_source is ValuationSource.MANUAL or latest is None
        )
        self._buttons = _buttons(self, "Enregistrer")
        self._save = self._buttons.button(QDialogButtonBox.StandardButton.Save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(self._title)
        layout.addWidget(subtitle)
        layout.addLayout(kinds)
        layout.addWidget(
            row(quantity_box, labeled(f"Cours ({self._currency})", self._price, price_hint))
        )
        layout.addWidget(
            row(
                labeled(
                    f"Frais de courtage ({self._currency})",
                    self._fees,
                    "Inclus dans le PRU à l'achat, déduits à la vente",
                ),
                labeled("Date", self._date),
            )
        )
        layout.addWidget(summary)
        layout.addWidget(self._error)
        layout.addWidget(self._record)
        layout.addWidget(self._buttons)

        self._quantity.textChanged.connect(self._update)
        self._price.textChanged.connect(self._update)
        self._fees.textChanged.connect(self._update)
        self._date.dateChanged.connect(self._update)
        self._apply_kind()
        self._update()

    # -- saisie --

    def _set_kind(self, kind: TransactionType) -> None:
        self._kind = kind
        self._apply_kind()
        self._update()

    def _apply_kind(self) -> None:
        buy = self._kind is TransactionType.BUY
        self._title.setText("Acheter des actions" if buy else "Vendre des actions")
        self._save.setText("Enregistrer l'achat" if buy else "Enregistrer la vente")
        captions = (
            ("Montant", "Frais de courtage", "Total débité")
            if buy
            else ("Montant brut", "Frais de courtage", "Total crédité", "Plus-value réalisée")
        )
        for index, (line, caption, value) in enumerate(self._summary_rows):
            line.setVisible(index < len(captions))
            caption.setText(captions[index] if index < len(captions) else "")
            caption.setStyleSheet(f"color: {theme.TEXT if index == 2 else theme.TEXT_MUTED};")
            value.setFont(theme.mono_font(17 if index == 2 else 14, bold=index == 2))

    def _preview(self) -> _Preview | None:
        try:
            quantity = validate_shares(parse_decimal(self._quantity.text(), "Nombre d'actions"))
            price = parse_decimal(self._price.text(), "Cours")
            fees = parse_decimal(self._fees.text() or "0", "Frais")
            quote = stock_trade_quote(self._kind, quantity, price, fees)
        except ValueError:
            return None  # saisie incomplète : pas de récapitulatif, pas de message
        asset_id = self._position.asset_id
        candidate = Transaction(
            None, asset_id, self._kind, to_date(self._date), quantity, price, fees
        )
        try:
            after = compute_position(asset_id, [*self._holding.transactions, candidate])
        except ValueError as error:
            self._error.setText(str(error))
            return None
        return _Preview(quote, after)

    def _update(self, *_: object) -> None:
        self._error.setText("")
        buy = self._kind is TransactionType.BUY
        preview = self._preview()
        self._save.setEnabled(preview is not None)
        held = format_quantity(self._position.quantity)
        self._quantity_hint.setText("" if buy else f"Maximum : {held} actions")
        currency = self._currency
        if preview is None:
            values = ["—", "—", "—", "—"]
            colors = [theme.TEXT] * 4
            self._after.setText("")
        else:
            quote, after = preview.quote, preview.after
            realized = after.realized_pnl - self._position.realized_pnl
            values = [
                money(quote.gross, currency),
                signed_money(quote.fees if buy else -quote.fees, currency),
                money(quote.total, currency),
                "—",
            ]
            colors = [theme.TEXT] * 4
            if not buy:
                values[3] = signed_money(realized, currency)
                colors[3] = theme.variation_color(realized)
            self._after.setText(
                "Après opération : position soldée"
                if after.quantity == 0
                else f"Après opération : {format_quantity(after.quantity)} actions · "
                f"PRU {money(after.pru, currency)}"
            )
        for entry, text, color in zip(self._summary_rows, values, colors, strict=True):
            entry[2].setText(text)
            entry[2].setStyleSheet(f"color: {color};")

    # -- résultat --

    def summary_rows(self) -> list[tuple[str, str]]:
        """Lignes visibles du récapitulatif (libellé, valeur), pour les tests."""
        return [
            (caption.text(), value.text())
            for line, caption, value in self._summary_rows
            if not line.isHidden()
        ]

    def form(self) -> TradeForm:
        quantity = validate_shares(parse_decimal(self._quantity.text(), "Nombre d'actions"))
        price = parse_decimal(self._price.text(), "Cours")
        fees = parse_decimal(self._fees.text() or "0", "Frais")
        stock_trade_quote(self._kind, quantity, price, fees)  # valide les montants
        when = to_date(self._date)
        record = self._record.isChecked() and when <= self._today
        return TradeForm(self._kind, when, quantity, price, fees, record)


# --- Page -------------------------------------------------------------------


class StocksPage(QWidget):
    """Écran « Actions » : synthèse en euros, secteurs, liste et fiche de l'action choisie."""

    _COLUMNS = ["Action", "Cours", "Valeur", "Dividende", "+/− latente"]

    def __init__(
        self,
        repository: StockRepository,
        fx: FxRepository,
        *,
        exposure: ExposureRepository | None = None,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = datetime.now,
        quote_fetcher: QuoteFetcher = fetch_stock_price,
        rate_fetcher: RateFetcher = fetch_rate_to_eur,
        background: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._fx = fx
        self._exposure = exposure
        self._today = today
        self._now = now
        self._quote_fetcher = quote_fetcher
        self._rate_fetcher = rate_fetcher
        self._background = background
        self._selected: int | None = None
        self._views: list[StockView] = []
        self._pending: list[StockView] = []
        self._failed: set[int] = set()  # Yahoo n'a pas répondu pour ces actions
        self._attempted: set[int] = set()  # déjà interrogées depuis le lancement
        self._attempted_rates: set[str] = set()  # devises déjà interrogées depuis le lancement
        self._refreshing = False
        self._tasks: list[_QuoteTask] = []

        title = QLabel("Actions")
        title.setProperty("role", "title")
        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "subtitle")
        add = QPushButton("Nouvelle action")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._new_stock)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(self._subtitle)
        header.addLayout(heading, 1)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)

        self._value_card = Card("Valeur de marché")
        self._gain_card = Card("Plus-value latente")
        self._dividend_card = Card("Dividendes annuels")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._value_card, self._gain_card, self._dividend_card):
            cards.addWidget(card, 1)

        self._alert = QFrame()
        self._alert.setObjectName("stockAlert")
        self._alert.setStyleSheet(
            f"QFrame#stockAlert {{ background: {theme.WARNING_SOFT}; "
            f"border: 1px solid {theme.WARNING_BORDER}; border-radius: 12px; }}"
        )
        self._alert_text = QLabel()
        self._alert_text.setTextFormat(Qt.TextFormat.RichText)
        self._alert_text.setWordWrap(True)
        alert_button = QPushButton("Saisir le cours")
        alert_button.clicked.connect(lambda _=False: self._update_quote(self._first_pending()))
        alert_layout = QHBoxLayout(self._alert)
        alert_layout.setContentsMargins(16, 12, 16, 12)
        alert_layout.setSpacing(12)
        alert_layout.addWidget(self._alert_text, 1)
        alert_layout.addWidget(alert_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._exposure_card = ExposureSummary("LA VALEUR DE MARCHÉ")

        self._table = QTableWidget()
        self._table.setAccessibleName("Actions détenues")
        prepare_table(self._table, self._COLUMNS, row_height=64)
        self._table.setWordWrap(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        columns = self._table.horizontalHeader()
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((150, 150, 140, 130), start=1):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            columns.resizeSection(column, width)
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.currentCellChanged.connect(self._on_row_changed)

        self._empty = QLabel("Aucune action. Ajoutez-en une avec « Nouvelle action ».")
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._detail = self._build_detail()
        self._body = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._body.setSpacing(16)
        self._body.addWidget(self._table, 1, Qt.AlignmentFlag.AlignTop)
        self._body.addWidget(self._detail, 0, Qt.AlignmentFlag.AlignTop)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(24)
        layout.addLayout(header)
        layout.addLayout(cards)
        layout.addWidget(self._alert)
        layout.addWidget(self._exposure_card)
        layout.addLayout(self._body)
        layout.addWidget(self._empty)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.refresh()

    # -- fiche --

    def _build_detail(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        frame.setMinimumWidth(340)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        self._d_source = QLabel()
        badge = QHBoxLayout()
        badge.addWidget(self._d_source)
        badge.addStretch(1)
        self._d_name = QLabel()
        self._d_name.setProperty("role", "title")
        self._d_name.setStyleSheet("font-size: 22px;")
        self._d_name.setWordWrap(True)
        self._d_sub = QLabel()
        self._d_sub.setProperty("role", "subtitle")
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addLayout(badge)
        names.addWidget(self._d_name)
        names.addWidget(self._d_sub)
        layout.addLayout(names)

        self._d_value = QLabel()
        self._d_value.setFont(theme.mono_font(26))
        self._d_gain = QLabel()
        self._d_gain.setFont(theme.mono_font(14))
        self._d_euros = QLabel()
        self._d_euros.setProperty("role", "hint")
        figures = QVBoxLayout()
        figures.setSpacing(4)
        figures.addWidget(self._d_value)
        figures.addWidget(self._d_gain)
        figures.addWidget(self._d_euros)
        layout.addLayout(figures)

        chart_caption = QLabel("ÉVOLUTION DU COURS · 12 MOIS")
        chart_caption.setProperty("role", "caption")
        self._chart = PositionChart()
        self._chart.setAccessibleName("Cours de l'action et prix de revient")
        legend = QLabel(
            f"<span style='color:{theme.ACCENT}'>■</span> Cours &nbsp;&nbsp; "
            f"<span style='color:{theme.WARNING}'>■</span> Prix de revient"
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setProperty("role", "muted")
        layout.addWidget(chart_caption)
        layout.addWidget(self._chart)
        layout.addWidget(legend)

        self._tiles: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setSpacing(10)
        captions = (
            "Actions",
            "PRU",
            "Cours",
            "Variation",
            "Dividende par action",
            "Rendement",
            "Dividendes annuels",
            "Devise",
            "Secteur principal",
            "Zone principale",
            "Place",
        )
        for index, caption in enumerate(captions):
            grid.addWidget(self._tile(caption), index // 2, index % 2)
        layout.addLayout(grid)

        self._exposure_section = ExposureSection()
        self._exposure_section.edit_requested.connect(
            lambda: self._edit_exposure(self._selected_view())
        )
        layout.addWidget(self._exposure_section)

        self._buy_button = QPushButton("Acheter")
        self._buy_button.setProperty("variant", "primary")
        self._buy_button.clicked.connect(lambda _=False: self._trade(TransactionType.BUY))
        self._sell_button = QPushButton("Vendre")
        self._sell_button.clicked.connect(lambda _=False: self._trade(TransactionType.SELL))
        self._quote_button = QPushButton("Mettre à jour le cours…")
        self._quote_button.clicked.connect(
            lambda _=False: self._update_quote(self._selected_view())
        )
        self._edit_button = QPushButton("Modifier l'action…")
        self._edit_button.clicked.connect(lambda _=False: self._edit_stock(self._selected_view()))
        trades = QHBoxLayout()
        trades.setSpacing(10)
        trades.addWidget(self._buy_button, 1)
        trades.addWidget(self._sell_button, 1)
        layout.addLayout(trades)
        layout.addWidget(self._quote_button)
        layout.addWidget(self._edit_button)

        operations_caption = QLabel("DERNIÈRES OPÉRATIONS")
        operations_caption.setProperty("role", "caption")
        self._operations = QVBoxLayout()
        self._operations.setSpacing(0)
        layout.addWidget(operations_caption)
        layout.addLayout(self._operations)
        return frame

    def _tile(self, caption: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("stockTile")
        tile.setStyleSheet(
            f"QFrame#stockTile {{ background: {theme.RAISED}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 10px; }}"
        )
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        label = QLabel(caption)
        label.setProperty("role", "hint")
        value = QLabel()
        value.setFont(theme.mono_font(14))
        value.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value)
        self._tiles[caption] = value
        return tile

    # -- données --

    def refresh(self) -> None:
        today = self._today()
        self._views = [
            build_view(
                holding,
                today,
                [(v.valuation_date, v.value) for v in self._repository.quote_history(asset_id)],
            )
            for holding in self._repository.list_holdings()
            if (asset_id := holding.asset.id) is not None
        ]
        if self._selected not in {v.asset_id for v in self._views}:
            self._selected = self._views[0].asset_id if self._views else None
        count = len(self._views)
        dated = [v.quote.valuation_date for v in self._views if v.quote is not None]
        self._subtitle.setText(
            f"{count} action{'s' if count > 1 else ''}"
            + (f" · cours au {max(dated):%d/%m/%Y}" if dated else "")
        )
        self._summarize()
        self._pending = outdated(self._views)
        self._alert.setVisible(bool(self._pending))
        if self._pending:
            self._alert_text.setText(alert_message(self._pending, today, self._failed))
        present = bool(self._views)
        self._table.setVisible(present)
        self._detail.setVisible(present)
        self._empty.setVisible(not present)
        self._fill_table()
        self._show_selected()

    def _summarize(self) -> None:
        """Synthèse en euros : chaque action est convertie au dernier taux de change connu."""
        rates = self._fx.latest_rates()
        converted = []
        weighted: list[tuple[int, Decimal]] = []
        without_rate = 0
        for v in self._views:
            rate = rate_for(v.currency, rates)
            if rate is None:
                without_rate += 1
                continue
            price = v.quote.value if v.quote else None
            converted.append(stock_in_euros(v.position, v.details, price, rate))
            if price is not None and v.position.quantity > 0:
                weighted.append((v.asset_id, v.position.quantity * price * rate))
        totals = stock_totals(converted)
        notes = ["Cours × actions"]
        if totals.unvalued:
            notes.append(f"{totals.unvalued} sans cours")
        if any(v.currency != CURRENCY for v in self._views):
            notes.append("converti en EUR")
        if without_rate:
            notes.append(f"{without_rate} sans taux de change exclue(s)")
        self._value_card.set(money(totals.value, CURRENCY), " · ".join(notes))
        self._gain_card.set(
            signed_money(totals.gain, CURRENCY),
            _signed_pct(totals.gain_percent),
            theme.variation_color(totals.gain),
        )
        self._dividend_card.set(
            money(totals.dividends, CURRENCY), f"Rendement {_pct(totals.dividend_yield)}"
        )
        self._show_exposure(weighted)

    def _show_exposure(self, weighted: list[tuple[int, Decimal]]) -> None:
        if self._exposure is None:
            self._exposure_card.setVisible(False)
            return
        ids = [asset_id for asset_id, _ in weighted]
        by_kind = {
            kind: self._exposure.all_for(ids, kind)
            for kind in (ExposureKind.SECTOR, ExposureKind.ZONE)
        }
        sectors, zones = (
            exposure_breakdown((value, by_kind[kind][asset_id]) for asset_id, value in weighted)
            for kind in (ExposureKind.SECTOR, ExposureKind.ZONE)
        )
        self._exposure_card.set_data(sectors, zones)

    def _fill_table(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._views))
        for table_row, view in enumerate(self._views):
            self._fill_row(table_row, view)
            if view.asset_id == self._selected:
                self._table.selectRow(table_row)
        self._table.blockSignals(False)
        fit_height(self._table)

    def _fill_row(self, table_row: int, view: StockView) -> None:
        asset, details, currency = view.holding.asset, view.details, view.currency
        color = theme.SERIES[view.asset_id % len(theme.SERIES)]
        meta = f"{asset.symbol} · {details.place}" if details.place else asset.symbol
        self._table.setCellWidget(
            table_row,
            0,
            _rich(
                f"<span style='color:{color}'>■</span>&nbsp; <b>{escape(asset.name)}</b><br>"
                f"{_subtle(escape(meta), 12)}"
            ),
        )
        if view.quote is None:
            quote_html = f"—<br>{_subtle('Pas de cours')}"
        else:
            source = SOURCE_LABELS[details.valuation_source]
            note = f"{source} · {view.quote.valuation_date:%d/%m/%Y}"
            if view.stale:
                note = f"<span style='color:{theme.WARNING}'>{note} · ancien</span>"
            quote_html = f"{money(view.quote.value, currency)}<br>{_subtle(note)}"
        self._table.setCellWidget(table_row, 1, _rich(quote_html, RIGHT, mono=True))
        shares = f"{format_quantity(view.position.quantity)} actions"
        value_text = "—" if view.value is None else money(view.value.value, currency)
        self._table.setCellWidget(
            table_row, 2, _rich(f"{value_text}<br>{_subtle(shares, 12)}", RIGHT, mono=True)
        )
        if details.dividend is None:
            dividend_html = "—"
        else:
            annual = annual_dividends(details, view.position.quantity)
            yield_text = (
                "—"
                if view.quote is None
                else _pct(dividend_yield(details, view.quote.value))
            )
            dividend_html = f"{money(annual, currency)}<br>{_subtle(yield_text, 12)}"
        self._table.setCellWidget(table_row, 3, _rich(dividend_html, RIGHT, mono=True))
        if view.value is None:
            gain_html = "—"
        else:
            gain_html = (
                f"<span style='color:{theme.variation_color(view.value.gain)}'>"
                f"{signed_money(view.value.gain, currency)}<br>"
                f"<span style='font-size:12px'>{_signed_pct(view.value.gain_percent)}</span>"
                "</span>"
            )
        self._table.setCellWidget(table_row, 4, _rich(gain_html, RIGHT, mono=True))

    def _show_selected(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        holding, position, details = view.holding, view.position, view.details
        asset, currency = holding.asset, view.currency
        today = self._today()
        self._d_name.setText(asset.name)
        place = f" · {details.place}" if details.place else ""
        self._d_sub.setText(f"{asset.symbol}{place}")

        source = SOURCE_LABELS[details.valuation_source]
        if view.quote is None:
            badge, warn = "Pas de cours", True
        else:
            badge = f"Cours {source} · {view.quote.valuation_date:%d/%m/%Y}"
            warn = view.stale
            if warn:
                badge += " · à mettre à jour"
        background, foreground = (
            (theme.WARNING_SOFT, theme.WARNING) if warn else (theme.OVERLAY, theme.TEXT_MUTED)
        )
        self._d_source.setText(badge)
        self._d_source.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 10px; "
            "padding: 2px 10px; font-size: 12px; font-weight: 600;"
        )

        valued = view.value
        rate = rate_for(currency, self._fx.latest_rates())
        if valued is None:
            self._d_value.setText("—")
            self._d_gain.setText("")
            self._d_euros.setText("")
        else:
            self._d_value.setText(money(valued.value, currency))
            self._d_gain.setText(
                f"{signed_money(valued.gain, currency)} · {_signed_pct(valued.gain_percent)}"
            )
            self._d_gain.setStyleSheet(f"color: {theme.variation_color(valued.gain)};")
            if currency == CURRENCY:
                self._d_euros.setText("")
            elif rate is None:
                self._d_euros.setText("Taux de change inconnu : non converti en euros.")
            else:
                self._d_euros.setText(
                    f"≈ {money(valued.value * rate, CURRENCY)} · "
                    f"1 {currency} = {money(rate, CURRENCY)}"
                )

        points = price_series(
            [(v.valuation_date, v.value) for v in self._repository.quote_history(view.asset_id)],
            holding.transactions,
            today,
        )
        pru = position.pru if position.quantity > 0 else None
        self._chart.set_data(points, pru)

        quote = view.quote.value if view.quote is not None else None
        dividend = details.dividend
        self._tiles["Actions"].setText(format_quantity(position.quantity))
        self._tiles["PRU"].setText("—" if pru is None else money(pru, currency))
        self._tiles["Cours"].setText("—" if quote is None else money(quote, currency))
        self._tiles["Variation"].setText(_signed_pct(view.change))
        self._tiles["Dividende par action"].setText(
            "—" if dividend is None else money(dividend, currency)
        )
        self._tiles["Rendement"].setText(
            "—" if quote is None else _pct(dividend_yield(details, quote))
        )
        annual = None if dividend is None else annual_dividends(details, position.quantity)
        self._tiles["Dividendes annuels"].setText(
            "—" if annual is None else money(annual, currency)
        )
        self._tiles["Devise"].setText(currency)
        sectors: list[ExposureEntry] = []
        zones: list[ExposureEntry] = []
        if self._exposure is not None:
            sectors = self._exposure.get(view.asset_id, ExposureKind.SECTOR)
            zones = self._exposure.get(view.asset_id, ExposureKind.ZONE)
        self._exposure_section.setVisible(self._exposure is not None)
        self._exposure_section.set_data(sectors, zones)
        self._tiles["Secteur principal"].setText(main_label(sectors))
        self._tiles["Zone principale"].setText(main_label(zones))
        self._tiles["Place"].setText(details.place or "—")
        self._sell_button.setEnabled(position.quantity > 0)

        _clear(self._operations)
        newest_first = sorted(
            holding.transactions, key=lambda t: (t.trade_date, t.id or 0), reverse=True
        )
        for tx in newest_first[:LAST_OPERATIONS]:
            self._operations.addWidget(self._operation_row(holding, tx))

    def _operation_row(self, holding: StockHolding, tx: Transaction) -> QWidget:
        buy = tx.type is TransactionType.BUY
        currency = holding.asset.currency
        gross = tx.quantity * tx.unit_price
        amount = -(gross + tx.fees) if buy else gross - tx.fees
        badge = QLabel("Achat" if buy else "Vente")
        badge.setFixedWidth(64)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {theme.ACCENT_SOFT if buy else theme.WARNING_SOFT}; "
            f"color: {theme.ACCENT_HOVER if buy else theme.WARNING}; "
            "border-radius: 6px; padding: 3px 0; font-size: 11px; font-weight: 600;"
        )
        quantity = QLabel(
            f"{'+' if buy else MINUS}{format_quantity(tx.quantity)} actions · "
            f"{money(tx.unit_price, currency)}"
        )
        quantity.setFont(theme.mono_font(13))
        day = QLabel(f"{tx.trade_date:%d/%m/%Y}")
        day.setProperty("role", "hint")
        figures = QVBoxLayout()
        figures.setSpacing(2)
        figures.addWidget(quantity)
        figures.addWidget(day)
        total = QLabel(signed_money(amount, currency))
        total.setFont(theme.mono_font(13))
        line = QWidget()
        line_layout = QHBoxLayout(line)
        line_layout.setContentsMargins(0, 10, 0, 10)
        line_layout.setSpacing(12)
        line_layout.addWidget(badge)
        line_layout.addLayout(figures, 1)
        line_layout.addWidget(total)
        return line

    def _selected_view(self) -> StockView | None:
        for view in self._views:
            if view.asset_id == self._selected:
                return view
        return None

    def _first_pending(self) -> StockView | None:
        return self._pending[0] if self._pending else None

    # -- cours Yahoo --

    def refresh_quotes(self, force: bool = False) -> None:
        """Demande à Yahoo le cours des positions ouvertes, une fois par jour et par action.

        Les taux de change des devises étrangères sont actualisés dans la même tâche. Un échec
        garde le dernier cours (ou taux) connu : l'alerte demande alors une saisie manuelle.
        """
        if self._refreshing:
            return
        open_stocks = [
            h
            for h in self._repository.list_holdings()
            if h.asset.id is not None and h.position.quantity > 0
        ]
        due = set(
            assets_to_refresh(
                [h.asset.id for h in open_stocks if h.asset.id is not None],
                self._repository.valued_on(self._today()),
                force=force,
            )
        )
        if not force:
            due -= self._attempted
        currencies = currencies_to_refresh(
            (h.asset.currency for h in open_stocks),
            self._fx.latest_rates(),
            self._today(),
            force=force,
        )
        if not force:
            currencies = [c for c in currencies if c not in self._attempted_rates]
        requests = [
            (h.asset.id, h.asset.symbol)
            for h in open_stocks
            if h.asset.id is not None and h.asset.id in due
        ]
        if not requests and not currencies:
            return
        self._refreshing = True
        self._attempted.update(asset_id for asset_id, _ in requests)
        self._attempted_rates.update(currencies)
        if not self._background:
            self._finish_refresh(
                (
                    fetch_quotes(requests, self._quote_fetcher),
                    fetch_rates(currencies, self._rate_fetcher),
                )
            )
            return
        task = _QuoteTask(requests, self._quote_fetcher, currencies, self._rate_fetcher)
        task.signals.done.connect(self._finish_refresh)
        self._tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _finish_refresh(self, results: object) -> None:
        assert isinstance(results, tuple)
        quotes, rates = results
        stamp, today = self._now(), self._today()
        for currency, rate in rates.items():
            if rate is None:
                continue  # le dernier taux connu reste utilisé
            try:
                self._fx.record(currency, today, rate, stamp)
            except ValueError:
                continue
        for asset_id, price in quotes.items():
            if price is None:
                self._failed.add(asset_id)
                continue
            try:
                self._repository.record_quote(
                    asset_id, today, price, stamp, ValuationSource.YAHOO
                )
            except ValueError:
                self._failed.add(asset_id)
            else:
                self._failed.discard(asset_id)
        self._refreshing = False
        self._tasks.clear()
        self.refresh()

    # -- interactions --

    def _on_row_changed(self, current_row: int, *_: int) -> None:
        if 0 <= current_row < len(self._views):
            self._selected = self._views[current_row].asset_id
            self._show_selected()

    def select(self, asset_id: int) -> None:
        """Choisit la ligne affichée dans la fiche (appliquée à la prochaine actualisation)."""
        self._selected = asset_id

    def _edit_exposure(self, view: StockView | None) -> None:
        if view is None or self._exposure is None:
            return
        if edit_exposure(self, self._exposure, view.asset_id, view.holding.asset.name):
            self._selected = view.asset_id
            self.refresh()

    def _new_stock(self) -> None:
        dialog = StockDialog(self)
        # La fenêtre reste ouverte (saisie conservée) tant que l'action est refusée.
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                name, ticker, currency, details = dialog.form()
                created = self._repository.create(name, ticker, currency, details)
            except ValueError as error:
                dialog.show_error(str(error))
                continue
            self._selected = created.asset.id
            break
        self.refresh()
        self.refresh_quotes()

    def _edit_stock(self, view: StockView | None) -> None:
        if view is None:
            return
        dialog = StockDialog(self, view.holding)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                name, ticker, currency, details = dialog.form()
                self._repository.update(view.asset_id, name, ticker, currency, details)
            except ValueError as error:
                dialog.show_error(str(error))
                continue
            break
        self._selected = view.asset_id
        self.refresh()

    def _trade(self, kind: TransactionType) -> None:
        view = self._selected_view()
        if view is None:
            return
        dialog = TradeDialog(self, view.holding, kind, self._today())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            form = dialog.form()
            self._repository.add_transaction(
                view.asset_id, form.kind, form.when, form.quantity, form.price, form.fees
            )
            if form.record_quote:
                self._repository.record_quote(
                    view.asset_id, form.when, form.price, self._now(), ValuationSource.MANUAL
                )
        except ValueError as error:
            QMessageBox.warning(self, "Opération refusée", str(error))
        self.refresh()

    def _update_quote(self, view: StockView | None) -> None:
        if view is None:
            return
        dialog = QuoteDialog(self, view.holding, self._today())
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                when, price = dialog.form()
                self._repository.record_quote(
                    view.asset_id, when, price, self._now(), ValuationSource.MANUAL
                )
            except ValueError as error:
                dialog.show_error(str(error))
                continue
            break
        self._failed.discard(view.asset_id)
        self._selected = view.asset_id
        self.refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._apply_direction()

    def _apply_direction(self) -> None:
        """Fiche à droite du tableau si la fenêtre est assez large, sinon dessous."""
        wide = self.width() >= WIDE_LAYOUT
        direction = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self._body.direction() == direction:
            return
        self._body.setDirection(direction)
        if wide:
            self._detail.setFixedWidth(DETAIL_WIDTH)
        else:
            self._detail.setMinimumWidth(340)
            self._detail.setMaximumWidth(16777215)
