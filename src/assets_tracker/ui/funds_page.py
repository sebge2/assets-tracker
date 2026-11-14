"""Écran des fonds : synthèse, liste, fiche du fonds sélectionné, souscription et rachat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.fund_repository import FundHolding, FundRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind, exposure_breakdown
from assets_tracker.domain.funds import (
    FundDetails,
    FundValue,
    TradeQuote,
    ValuationSource,
    fund_totals,
    fund_value,
    is_nav_stale,
    nav_series,
    quantity_from_amount,
    trade_quote,
)
from assets_tracker.domain.models import Position, Transaction, TransactionType
from assets_tracker.domain.positions import compute_position
from assets_tracker.domain.valuation import Valuation
from assets_tracker.ui import theme
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
    percent,
    signed_money,
)
from assets_tracker.ui.widgets import RIGHT, Card, PageContent, fit_height, prepare_table

CURRENCY = "EUR"
WIDE_LAYOUT = 1340  # largeur de page à partir de laquelle la fiche passe à droite du tableau
DETAIL_WIDTH = 380
LAST_OPERATIONS = 3
SOURCE_LABELS = {ValuationSource.YAHOO: "yahoo", ValuationSource.MANUAL: "manuelle"}


# --- Données affichées ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundView:
    """Un fonds valorisé : tout est recalculé depuis ses opérations et sa dernière VL."""

    holding: FundHolding
    position: Position
    nav: Valuation | None  # None sans VL connue
    value: FundValue | None
    stale: bool  # VL plus ancienne que le seuil de `domain.funds`

    @property
    def asset_id(self) -> int:
        assert self.holding.asset.id is not None
        return self.holding.asset.id


def build_view(holding: FundHolding, today: date) -> FundView:
    position = holding.position
    nav = holding.valuation
    value = fund_value(position, nav.value) if nav is not None else None
    stale = nav is not None and is_nav_stale(nav.valuation_date, today)
    return FundView(holding, position, nav, value, stale)


def outdated(views: list[FundView]) -> list[FundView]:
    """Positions ouvertes sans VL ou à la VL ancienne, la plus urgente d'abord."""
    pending = [v for v in views if v.position.quantity > 0 and (v.nav is None or v.stale)]
    return sorted(pending, key=lambda v: v.nav.valuation_date if v.nav else date.min)


def alert_message(pending: list[FundView], today: date) -> str:
    first = pending[0]
    label = escape(first.holding.asset.name)
    name = f"<span style='color:{theme.WARNING};font-weight:600'>{label}</span>"
    if first.nav is None:
        text = (
            f"{name} n'a pas encore de valeur liquidative. "
            "Saisissez-la pour valoriser ce fonds."
        )
    else:
        age = (today - first.nav.valuation_date).days
        text = (
            f"La VL de {name} date du {first.nav.valuation_date:%d/%m/%Y} ({age} jours). "
            "Saisissez la dernière valeur pour une valorisation fiable."
        )
    others = len(pending) - 1
    if others:
        text += f" {others} autre{'s' if others > 1 else ''} fonds à mettre à jour."
    return text


def _signed_percent(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{'+' if value > 0 else ''}{format_number(value, 1)}{NBSP}%"


def _subtle(text: str, size: int = 11) -> str:
    return f"<span style='color:{theme.TEXT_SUBTLE};font-size:{size}px'>{text}</span>"


def _rich(
    html: str, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft, *, mono: bool = False
) -> QLabel:
    label = QLabel(html)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    label.setContentsMargins(12, 0, 12, 0)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    if mono:
        label.setFont(theme.mono_font(13))
    return label


def _clear(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.deleteLater()


def _buttons(dialog: QDialog, save_label: str) -> QDialogButtonBox:
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
    )
    box.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
    save = box.button(QDialogButtonBox.StandardButton.Save)
    save.setText(save_label)
    save.setProperty("variant", "primary")
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


# --- Dialogues --------------------------------------------------------------


class FundDialog(QDialog):
    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nouveau fonds")
        self._name, self._identifier = QLineEdit(), QLineEdit()
        self._broker, self._ter = QLineEdit(), QLineEdit()
        self._currency = QLineEdit("EUR")
        self._source = QComboBox()
        self._source.addItem("Valeur liquidative manuelle", ValuationSource.MANUAL)
        self._source.addItem("Yahoo Finance", ValuationSource.YAHOO)
        self._ticker = QLineEdit()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.addWidget(labeled("Nom", self._name))
        layout.addWidget(labeled("ISIN ou ticker", self._identifier))
        layout.addWidget(
            row(labeled("Courtier / enveloppe", self._broker), labeled("Devise", self._currency))
        )
        layout.addWidget(
            row(
                labeled("Frais courants annuels (TER, %)", self._ter),
                labeled("Origine de la VL", self._source),
            )
        )
        layout.addWidget(labeled("Ticker Yahoo (si applicable)", self._ticker))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def form(self) -> tuple[str, str, str, FundDetails]:
        ter = parse_decimal(self._ter.text(), "TER") if self._ter.text().strip() else None
        source = ValuationSource(self._source.currentData())
        return (
            self._name.text(),
            self._identifier.text(),
            self._currency.text(),
            FundDetails(self._broker.text(), ter, source, self._ticker.text() or None),
        )


class NavDialog(QDialog):
    """Saisie manuelle de la valeur liquidative d'un fonds, sans passer par une opération."""

    def __init__(self, parent: QWidget | None, holding: FundHolding, today: date) -> None:
        super().__init__(parent)
        self._today = today
        self.setWindowTitle("Mettre à jour la VL")
        self.setMinimumWidth(460)
        self._nav = QLineEdit()
        self._date = date_edit(today)
        latest = holding.valuation
        hint = (
            f"Dernière VL connue : {money(latest.value, holding.asset.currency)} "
            f"({latest.valuation_date:%d/%m/%Y})"
            if latest is not None
            else "Aucune VL connue pour l'instant."
        )
        title = QLabel(holding.asset.name)
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(
            row(labeled("Valeur liquidative", self._nav, hint), labeled("Date", self._date))
        )
        layout.addWidget(_buttons(self, "Enregistrer"))

    def form(self) -> tuple[date, Decimal]:
        nav = parse_decimal(self._nav.text(), "Valeur liquidative")
        when = to_date(self._date)
        if when > self._today:
            raise ValueError("La date de la VL ne peut pas être dans le futur.")
        return when, nav


@dataclass(frozen=True, slots=True)
class TradeForm:
    kind: TransactionType
    when: date
    quantity: Decimal
    nav: Decimal
    fees: Decimal  # montant, converti depuis le pourcentage saisi
    record_nav: bool  # la VL saisie devient une valorisation datée de l'opération


@dataclass(frozen=True, slots=True)
class _Preview:
    quantity: Decimal
    quote: TradeQuote
    after: Position


class TradeDialog(QDialog):
    """Souscription ou rachat, avec récapitulatif recalculé à chaque frappe."""

    def __init__(
        self, parent: QWidget | None, holding: FundHolding, kind: TransactionType, today: date
    ) -> None:
        super().__init__(parent)
        self._holding, self._today, self._kind = holding, today, kind
        self._position = holding.position
        self._currency = holding.asset.currency
        self._by_amount = False
        self.setWindowTitle("Souscrire ou racheter")
        self.setMinimumWidth(560)

        self._title = QLabel()
        self._title.setProperty("role", "title")
        self._title.setStyleSheet("font-size: 20px;")
        held = format_quantity(self._position.quantity)
        subtitle = QLabel(f"{holding.asset.name} · détention {held} parts")
        subtitle.setProperty("role", "muted")

        kinds = QHBoxLayout()
        kinds.setSpacing(8)
        kind_group = QButtonGroup(self)
        kind_group.setExclusive(True)
        self._kind_buttons: dict[TransactionType, QPushButton] = {}
        choices = ((TransactionType.BUY, "Souscrire"), (TransactionType.SELL, "Racheter"))
        for choice, label in choices:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("variant", "chip")
            button.setChecked(choice is kind)
            button.clicked.connect(lambda _=False, k=choice: self._set_kind(k))
            kind_group.addButton(button)
            self._kind_buttons[choice] = button
            kinds.addWidget(button)
        kinds.addStretch(1)
        self._kind_buttons[TransactionType.SELL].setEnabled(self._position.quantity > 0)

        modes = QHBoxLayout()
        modes.setSpacing(8)
        modes_caption = QLabel("Saisir en")
        modes_caption.setProperty("role", "muted")
        modes.addWidget(modes_caption)
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        self._mode_buttons: dict[bool, QPushButton] = {}
        for by_amount, label in ((False, "Parts"), (True, "Montant (€)")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("variant", "chip")
            button.setChecked(by_amount is False)
            button.clicked.connect(lambda _=False, m=by_amount: self._set_mode(m))
            mode_group.addButton(button)
            self._mode_buttons[by_amount] = button
            modes.addWidget(button)
        modes.addStretch(1)

        latest = holding.valuation
        self._quantity = QLineEdit()
        self._nav = QLineEdit(format_number(latest.value) if latest is not None else "")
        self._fee = QLineEdit("0")
        self._date = date_edit(today)
        self._quantity_caption = QLabel()
        self._quantity_caption.setProperty("role", "muted")
        self._quantity_hint = QLabel()
        self._quantity_hint.setProperty("role", "hint")
        self._fee_caption = QLabel()
        self._fee_caption.setProperty("role", "muted")
        nav_hint = (
            f"Préremplie : dernière VL ({latest.valuation_date:%d/%m/%Y})"
            if latest is not None
            else "Aucune VL connue : saisissez-la."
        )

        quantity_box = QWidget()
        quantity_layout = QVBoxLayout(quantity_box)
        quantity_layout.setContentsMargins(0, 0, 0, 0)
        quantity_layout.setSpacing(6)
        quantity_layout.addWidget(self._quantity_caption)
        quantity_layout.addWidget(self._quantity)
        quantity_layout.addWidget(self._quantity_hint)
        fee_box = QWidget()
        fee_layout = QVBoxLayout(fee_box)
        fee_layout.setContentsMargins(0, 0, 0, 0)
        fee_layout.setSpacing(6)
        fee_layout.addWidget(self._fee_caption)
        fee_layout.addWidget(self._fee)

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
        self._record = QCheckBox("Enregistrer cette VL comme valorisation à cette date")
        self._record.setChecked(holding.details.valuation_source is ValuationSource.MANUAL)
        self._buttons = _buttons(self, "Enregistrer")
        self._save = self._buttons.button(QDialogButtonBox.StandardButton.Save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(self._title)
        layout.addWidget(subtitle)
        layout.addLayout(kinds)
        layout.addLayout(modes)
        layout.addWidget(row(quantity_box, labeled("Valeur liquidative", self._nav, nav_hint)))
        layout.addWidget(row(fee_box, labeled("Date", self._date)))
        layout.addWidget(summary)
        layout.addWidget(self._error)
        layout.addWidget(self._record)
        layout.addWidget(self._buttons)

        self._quantity.textChanged.connect(self._update)
        self._nav.textChanged.connect(self._update)
        self._fee.textChanged.connect(self._update)
        self._date.dateChanged.connect(self._update)
        self._apply_kind()
        self._update()

    # -- saisie --

    def _set_kind(self, kind: TransactionType) -> None:
        self._kind = kind
        self._apply_kind()
        self._update()

    def _set_mode(self, by_amount: bool) -> None:
        self._by_amount = by_amount
        self._quantity.clear()  # la valeur saisie n'aurait plus le même sens
        self._update()

    def _apply_kind(self) -> None:
        buy = self._kind is TransactionType.BUY
        self._title.setText("Souscrire des parts" if buy else "Racheter des parts")
        self._fee_caption.setText("Frais d'entrée (%)" if buy else "Frais de sortie (%)")
        self._save.setText("Enregistrer la souscription" if buy else "Enregistrer le rachat")
        captions = (
            ("Montant brut", "Frais d'entrée", "Total débité")
            if buy
            else ("Montant brut", "Frais de sortie", "Montant crédité", "Plus-value réalisée")
        )
        for index, (line, caption, value) in enumerate(self._summary_rows):
            line.setVisible(index < len(captions))
            caption.setText(captions[index] if index < len(captions) else "")
            caption.setStyleSheet(f"color: {theme.TEXT if index == 2 else theme.TEXT_MUTED};")
            value.setFont(theme.mono_font(17 if index == 2 else 14, bold=index == 2))

    def _quantity_value(self, nav: Decimal) -> Decimal:
        text = self._quantity.text()
        if self._by_amount:
            return quantity_from_amount(parse_decimal(text, "Montant"), nav)
        return parse_decimal(text, "Nombre de parts")

    def _preview(self) -> _Preview | None:
        try:
            nav = parse_decimal(self._nav.text(), "Valeur liquidative")
            rate = parse_decimal(self._fee.text() or "0", "Frais")
            quantity = self._quantity_value(nav)
            quote = trade_quote(self._kind, quantity, nav, rate)
        except ValueError:
            return None  # saisie incomplète : pas de récapitulatif, pas de message
        asset_id = self._position.asset_id
        candidate = Transaction(
            None, asset_id, self._kind, to_date(self._date), quantity, nav, quote.fees
        )
        try:
            after = compute_position(asset_id, [*self._holding.transactions, candidate])
        except ValueError as error:
            self._error.setText(str(error))
            return None
        return _Preview(quantity, quote, after)

    def _update(self, *_: object) -> None:
        self._error.setText("")
        buy = self._kind is TransactionType.BUY
        self._quantity_caption.setText("Montant (€)" if self._by_amount else "Nombre de parts")
        preview = self._preview()
        self._save.setEnabled(preview is not None)
        cur = self._currency
        if preview is None:
            held = format_quantity(self._position.quantity)
            self._quantity_hint.setText("" if buy else f"Maximum : {held} parts")
            values = ["—", "—", "—", "—"]
            colors = [theme.TEXT] * 4
            self._after.setText("")
        else:
            quote, after = preview.quote, preview.after
            if self._by_amount:
                self._quantity_hint.setText(
                    f"≈ {format_quantity(preview.quantity)} parts à la VL saisie"
                )
            else:
                self._quantity_hint.setText(f"≈ {money(quote.gross, cur)} hors frais")
            fees = signed_money(quote.fees if buy else -quote.fees, cur)
            realized = after.realized_pnl - self._position.realized_pnl
            values = [money(quote.gross, cur), fees, money(quote.net, cur), "—"]
            colors = [theme.TEXT] * 4
            if not buy:
                values[3] = signed_money(realized, cur)
                colors[3] = theme.variation_color(realized)
            self._after.setText(
                "Après opération : position soldée"
                if after.quantity == 0
                else f"Après opération : {format_quantity(after.quantity)} parts · "
                f"PRU {money(after.pru, cur)}"
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
        nav = parse_decimal(self._nav.text(), "Valeur liquidative")
        rate = parse_decimal(self._fee.text() or "0", "Frais")
        quantity = self._quantity_value(nav)
        quote = trade_quote(self._kind, quantity, nav, rate)
        when = to_date(self._date)
        record = self._record.isChecked() and when <= self._today
        return TradeForm(self._kind, when, quantity, nav, quote.fees, record)


# --- Page -------------------------------------------------------------------


class FundsPage(QWidget):
    """Écran « Fonds » : synthèse, liste des fonds et fiche du fonds sélectionné."""

    _COLUMNS = ["Fonds", "Valeur liquidative", "Valeur", "+/− latente"]

    def __init__(
        self,
        repository: FundRepository,
        *,
        exposure: ExposureRepository | None = None,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = datetime.now,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._exposure = exposure
        self._today = today
        self._now = now
        self._selected: int | None = None
        self._views: list[FundView] = []
        self._pending: list[FundView] = []

        title = QLabel("Fonds")
        title.setProperty("role", "title")
        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "subtitle")
        add = QPushButton("Nouveau fonds")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._new_fund)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(self._subtitle)
        header.addLayout(heading, 1)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)

        self._value_card = Card("Valeur totale")
        self._gain_card = Card("Plus-value latente")
        self._invested_card = Card("Montant investi")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._value_card, self._gain_card, self._invested_card):
            cards.addWidget(card, 1)

        self._alert = QFrame()
        self._alert.setObjectName("fundAlert")
        self._alert.setStyleSheet(
            f"QFrame#fundAlert {{ background: {theme.WARNING_SOFT}; "
            f"border: 1px solid {theme.WARNING_BORDER}; border-radius: 12px; }}"
        )
        self._alert_text = QLabel()
        self._alert_text.setTextFormat(Qt.TextFormat.RichText)
        self._alert_text.setWordWrap(True)
        alert_button = QPushButton("Saisir la VL")
        alert_button.clicked.connect(lambda _=False: self._update_nav(self._first_pending()))
        alert_layout = QHBoxLayout(self._alert)
        alert_layout.setContentsMargins(16, 12, 16, 12)
        alert_layout.setSpacing(12)
        alert_layout.addWidget(self._alert_text, 1)
        alert_layout.addWidget(alert_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._exposure_card = ExposureSummary("LA VALEUR DES FONDS")

        self._table = QTableWidget()
        self._table.setAccessibleName("Fonds détenus")
        prepare_table(self._table, self._COLUMNS, row_height=64)
        self._table.setWordWrap(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        columns = self._table.horizontalHeader()
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((170, 150, 150), start=1):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            columns.resizeSection(column, width)
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.currentCellChanged.connect(self._on_row_changed)

        self._empty = QLabel("Aucun fonds. Ajoutez-en un avec « Nouveau fonds ».")
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._detail = self._build_detail()
        self._body = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._body.setSpacing(16)
        self._body.addWidget(self._table, 1, Qt.AlignmentFlag.AlignTop)
        self._body.addWidget(self._detail, 0, Qt.AlignmentFlag.AlignTop)

        content = PageContent()
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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        frame.setMinimumWidth(280)
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
        figures = QVBoxLayout()
        figures.setSpacing(4)
        figures.addWidget(self._d_value)
        figures.addWidget(self._d_gain)
        layout.addLayout(figures)

        chart_caption = QLabel("ÉVOLUTION DE LA VL · 12 MOIS")
        chart_caption.setProperty("role", "caption")
        self._chart = PositionChart()
        self._chart.setAccessibleName("Valeur liquidative et prix de revient")
        legend = QLabel(
            f"<span style='color:{theme.ACCENT}'>■</span> Valeur liquidative &nbsp;&nbsp; "
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
            "Parts détenues",
            "PRU",
            "Valeur liquidative",
            "Montant investi",
            "Frais courants (TER)",
            "Date de la VL",
            "Secteur principal",
            "Zone principale",
        )
        for index, caption in enumerate(captions):
            grid.addWidget(self._tile(caption), index // 2, index % 2)
        layout.addLayout(grid)

        self._exposure_section = ExposureSection()
        self._exposure_section.edit_requested.connect(
            lambda: self._edit_exposure(self._selected_view())
        )
        layout.addWidget(self._exposure_section)

        self._subscribe_button = QPushButton("Souscrire")
        self._subscribe_button.setProperty("variant", "primary")
        self._subscribe_button.clicked.connect(lambda _=False: self._trade(TransactionType.BUY))
        self._redeem_button = QPushButton("Racheter")
        self._redeem_button.clicked.connect(lambda _=False: self._trade(TransactionType.SELL))
        self._nav_button = QPushButton("Mettre à jour la VL…")
        self._nav_button.clicked.connect(lambda _=False: self._update_nav(self._selected_view()))
        trades = QHBoxLayout()
        trades.setSpacing(10)
        trades.addWidget(self._subscribe_button, 1)
        trades.addWidget(self._redeem_button, 1)
        layout.addLayout(trades)
        layout.addWidget(self._nav_button)

        operations_caption = QLabel("DERNIÈRES OPÉRATIONS")
        operations_caption.setProperty("role", "caption")
        self._operations = QVBoxLayout()
        self._operations.setSpacing(0)
        layout.addWidget(operations_caption)
        layout.addLayout(self._operations)
        return frame

    def _tile(self, caption: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("fundTile")
        tile.setStyleSheet(
            f"QFrame#fundTile {{ background: {theme.RAISED}; "
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
        self._views = [build_view(h, today) for h in self._repository.list_holdings()]
        if self._selected not in {v.asset_id for v in self._views}:
            self._selected = self._views[0].asset_id if self._views else None
        self._subtitle.setText(
            f"{len(self._views)} fonds · souscriptions, rachats et valeurs liquidatives"
        )
        self._summarize()
        self._pending = outdated(self._views)
        self._alert.setVisible(bool(self._pending))
        if self._pending:
            self._alert_text.setText(alert_message(self._pending, today))
        present = bool(self._views)
        self._table.setVisible(present)
        self._detail.setVisible(present)
        self._empty.setVisible(not present)
        self._fill_table()
        self._show_selected()

    def _summarize(self) -> None:
        in_euros = [v for v in self._views if v.holding.asset.currency == CURRENCY]
        totals = fund_totals((v.position, v.nav.value if v.nav else None) for v in in_euros)
        notes = [f"{len(self._views)} fonds"]
        if totals.unvalued:
            notes.append(f"{totals.unvalued} sans VL")
        if len(in_euros) < len(self._views):
            notes.append(f"{len(self._views) - len(in_euros)} hors EUR exclus")
        self._value_card.set(money(totals.value, CURRENCY), " · ".join(notes))
        self._gain_card.set(
            signed_money(totals.gain, CURRENCY),
            _signed_percent(totals.gain_percent),
            theme.variation_color(totals.gain),
        )
        self._invested_card.set(money(totals.invested, CURRENCY), "Somme parts × PRU")
        self._show_exposure(in_euros)

    def _show_exposure(self, views: list[FundView]) -> None:
        """Répartition des fonds en EUR (les autres devises restent exclues, comme la synthèse)."""
        if self._exposure is None:
            self._exposure_card.setVisible(False)
            return
        weighted = [
            (v.asset_id, v.position.quantity * v.nav.value)
            for v in views
            if v.nav is not None and v.position.quantity > 0
        ]
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

    def _fill_row(self, table_row: int, view: FundView) -> None:
        asset = view.holding.asset
        color = theme.SERIES[view.asset_id % len(theme.SERIES)]
        broker = view.holding.details.broker
        meta = f"{asset.symbol} · {broker}" if broker else asset.symbol
        self._table.setCellWidget(
            table_row,
            0,
            _rich(
                f"<span style='color:{color}'>■</span>&nbsp; <b>{escape(asset.name)}</b><br>"
                f"{_subtle(escape(meta), 12)}"
            ),
        )
        if view.nav is None:
            nav_html = f"—<br>{_subtle('Pas de VL')}"
        else:
            source = SOURCE_LABELS[view.holding.details.valuation_source].capitalize()
            note = f"{source} · {view.nav.valuation_date:%d/%m/%Y}"
            if view.stale:
                note = f"<span style='color:{theme.WARNING}'>{note} · ancienne</span>"
            nav_html = f"{money(view.nav.value, asset.currency)}<br>{_subtle(note)}"
        self._table.setCellWidget(table_row, 1, _rich(nav_html, RIGHT, mono=True))
        parts = f"{format_quantity(view.position.quantity)} parts"
        value_text = "—" if view.value is None else money(view.value.value, asset.currency)
        self._table.setCellWidget(
            table_row, 2, _rich(f"{value_text}<br>{_subtle(parts, 12)}", RIGHT, mono=True)
        )
        if view.value is None:
            gain_html = "—"
        else:
            gain_html = (
                f"<span style='color:{theme.variation_color(view.value.gain)}'>"
                f"{signed_money(view.value.gain, asset.currency)}<br>"
                f"<span style='font-size:12px'>{_signed_percent(view.value.gain_percent)}</span>"
                "</span>"
            )
        self._table.setCellWidget(table_row, 3, _rich(gain_html, RIGHT, mono=True))

    def _show_selected(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        holding, position = view.holding, view.position
        asset, details = holding.asset, holding.details
        self._d_name.setText(asset.name)
        broker = f" · {details.broker}" if details.broker else ""
        self._d_sub.setText(f"{asset.symbol}{broker}")

        source = SOURCE_LABELS[details.valuation_source]
        if view.nav is None:
            badge, warn = "Pas de VL", True
        else:
            badge = f"VL {source} · {view.nav.valuation_date:%d/%m/%Y}"
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
        if valued is None:
            self._d_value.setText("—")
            self._d_gain.setText("")
        else:
            self._d_value.setText(money(valued.value, asset.currency))
            self._d_gain.setText(
                f"{signed_money(valued.gain, asset.currency)} · "
                f"{_signed_percent(valued.gain_percent)}"
            )
            self._d_gain.setStyleSheet(f"color: {theme.variation_color(valued.gain)};")

        points = nav_series(
            [(v.valuation_date, v.value) for v in self._repository.nav_history(view.asset_id)],
            holding.transactions,
            self._today(),
        )
        self._chart.set_data(points, position.pru if position.quantity > 0 else None)

        cost = position.quantity * position.pru
        self._tiles["Parts détenues"].setText(format_quantity(position.quantity))
        self._tiles["PRU"].setText(
            money(position.pru, asset.currency) if position.quantity > 0 else "—"
        )
        self._tiles["Valeur liquidative"].setText(
            "—" if view.nav is None else money(view.nav.value, asset.currency)
        )
        self._tiles["Montant investi"].setText(money(cost, asset.currency))
        self._tiles["Frais courants (TER)"].setText(
            "—" if details.ter is None else percent(details.ter)
        )
        self._tiles["Date de la VL"].setText(
            "—" if view.nav is None else f"{view.nav.valuation_date:%d/%m/%Y}"
        )
        sectors: list[ExposureEntry] = []
        zones: list[ExposureEntry] = []
        if self._exposure is not None:
            sectors = self._exposure.get(view.asset_id, ExposureKind.SECTOR)
            zones = self._exposure.get(view.asset_id, ExposureKind.ZONE)
        self._exposure_section.setVisible(self._exposure is not None)
        self._exposure_section.set_data(sectors, zones)
        self._tiles["Secteur principal"].setText(main_label(sectors))
        self._tiles["Zone principale"].setText(main_label(zones))
        self._redeem_button.setEnabled(position.quantity > 0)

        _clear(self._operations)
        newest_first = sorted(
            holding.transactions, key=lambda t: (t.trade_date, t.id or 0), reverse=True
        )
        for tx in newest_first[:LAST_OPERATIONS]:
            self._operations.addWidget(self._operation_row(tx, asset.currency))

    def _operation_row(self, tx: Transaction, currency: str) -> QWidget:
        buy = tx.type is TransactionType.BUY
        gross = tx.quantity * tx.unit_price
        amount = -(gross + tx.fees) if buy else gross - tx.fees
        badge = QLabel("Souscr." if buy else "Rachat")
        badge.setFixedWidth(64)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {theme.ACCENT_SOFT if buy else theme.WARNING_SOFT}; "
            f"color: {theme.ACCENT_HOVER if buy else theme.WARNING}; "
            "border-radius: 6px; padding: 3px 0; font-size: 11px; font-weight: 600;"
        )
        quantity = QLabel(f"{'+' if buy else MINUS}{format_quantity(tx.quantity)} parts")
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

    def _selected_view(self) -> FundView | None:
        for view in self._views:
            if view.asset_id == self._selected:
                return view
        return None

    def _first_pending(self) -> FundView | None:
        return self._pending[0] if self._pending else None

    # -- interactions --

    def _on_row_changed(self, current_row: int, *_: int) -> None:
        if 0 <= current_row < len(self._views):
            self._selected = self._views[current_row].asset_id
            self._show_selected()

    def select(self, asset_id: int) -> None:
        """Choisit la ligne affichée dans la fiche (appliquée à la prochaine actualisation)."""
        self._selected = asset_id

    def _edit_exposure(self, view: FundView | None) -> None:
        if view is None or self._exposure is None:
            return
        if edit_exposure(self, self._exposure, view.asset_id, view.holding.asset.name):
            self._selected = view.asset_id
            self.refresh()

    def _new_fund(self) -> None:
        dialog = FundDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            name, identifier, currency, details = dialog.form()
            created = self._repository.create(name, identifier, currency, details)
            self._selected = created.asset.id
        except ValueError as error:
            QMessageBox.warning(self, "Fonds invalide", str(error))
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
                view.asset_id, form.kind, form.when, form.quantity, form.nav, form.fees
            )
            if form.record_nav:
                self._repository.record_valuation(view.asset_id, form.when, form.nav, self._now())
        except ValueError as error:
            QMessageBox.warning(self, "Opération refusée", str(error))
        self.refresh()

    def _update_nav(self, view: FundView | None) -> None:
        if view is None:
            return
        dialog = NavDialog(self, view.holding, self._today())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            when, nav = dialog.form()
            self._repository.record_valuation(view.asset_id, when, nav, self._now())
        except ValueError as error:
            QMessageBox.warning(self, "Valeur liquidative invalide", str(error))
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
            self._detail.setMinimumWidth(280)
            self._detail.setMaximumWidth(16777215)
        self._body.setStretchFactor(self._table, 1)
