"""Écran des obligations : synthèse, échéancier, liste, fiche, achat et vente."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape

from PySide6.QtCore import QDate, QObject, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QResizeEvent
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

from assets_tracker.db.bond_repository import BondHolding, BondRepository
from assets_tracker.domain.bonds import (
    BondDetails,
    BondQuote,
    CouponFrequency,
    accrued_coupon,
    bond_totals,
    bond_trade_quote,
    bond_value,
    current_yield,
    is_quote_stale,
    maturity_ladder,
    next_coupon,
    price_percent,
    price_series,
    remaining_label,
    unit_value,
    validate_quantity,
)
from assets_tracker.domain.funds import FundValue, ValuationSource
from assets_tracker.domain.models import Position, Transaction, TransactionType
from assets_tracker.domain.positions import compute_position
from assets_tracker.domain.valuation import Valuation, assets_to_refresh
from assets_tracker.market.bonds import fetch_bond_price_percent
from assets_tracker.ui import theme
from assets_tracker.ui.commodity_chart import PositionChart
from assets_tracker.ui.form_helpers import date_edit, labeled, row, to_date
from assets_tracker.ui.formatting import (
    MINUS,
    NBSP,
    compact_money,
    format_number,
    format_quantity,
    money,
    parse_decimal,
    signed_money,
)
from assets_tracker.ui.widgets import RIGHT, Card, fit_height, prepare_table

CURRENCY = "EUR"
WIDE_LAYOUT = 1340  # largeur de page à partir de laquelle la fiche passe à droite du tableau
DETAIL_WIDTH = 380
LAST_OPERATIONS = 3
SOURCE_LABELS = {ValuationSource.YAHOO: "Yahoo", ValuationSource.MANUAL: "manuel"}
QuoteFetcher = Callable[[str], Decimal | None]


# --- Données affichées ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BondView:
    """Une obligation valorisée : tout est recalculé depuis ses opérations et son dernier cours."""

    holding: BondHolding
    position: Position
    quote: Valuation | None  # valeur d'UN titre ; None sans cours connu
    value: FundValue | None
    stale: bool  # cours plus ancien que le seuil de `domain.bonds`

    @property
    def asset_id(self) -> int:
        assert self.holding.asset.id is not None
        return self.holding.asset.id

    @property
    def details(self) -> BondDetails:
        return self.holding.details


def build_view(holding: BondHolding, today: date) -> BondView:
    position = holding.position
    quote = holding.valuation
    value = bond_value(position, quote.value) if quote is not None else None
    stale = quote is not None and is_quote_stale(quote.valuation_date, today)
    return BondView(holding, position, quote, value, stale)


def outdated(views: list[BondView]) -> list[BondView]:
    """Positions ouvertes sans cours ou au cours ancien, la plus urgente d'abord."""
    pending = [v for v in views if v.position.quantity > 0 and (v.quote is None or v.stale)]
    return sorted(pending, key=lambda v: v.quote.valuation_date if v.quote else date.min)


def alert_message(pending: list[BondView], today: date, failed: set[int]) -> str:
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
        text += f" {others} autre{'s' if others > 1 else ''} obligation{'s' if others > 1 else ''}"
        text += " à mettre à jour."
    return text


def next_maturity(views: list[BondView], today: date) -> BondView | None:
    open_bonds = [v for v in views if v.position.quantity > 0 and v.details.maturity > today]
    return min(open_bonds, key=lambda v: v.details.maturity, default=None)


def _pct(value: Decimal | None, places: int = 2) -> str:
    return "—" if value is None else f"{format_number(value, places)}{NBSP}%"


def _signed_pct(value: Decimal | None) -> str:
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


# --- Actualisation des cours hors du thread Qt -------------------------------


def fetch_quotes(
    requests: list[tuple[int, str]], fetcher: QuoteFetcher
) -> dict[int, Decimal | None]:
    """Un cours (ou None) par obligation ; une erreur ne bloque jamais les autres."""
    results: dict[int, Decimal | None] = {}
    for asset_id, symbol in requests:
        try:
            results[asset_id] = fetcher(symbol)
        except Exception:
            results[asset_id] = None
    return results


class _TaskSignals(QObject):
    done = Signal(object)


class _QuoteTask(QRunnable):
    """Interroge Yahoo dans un thread du pool ; la base n'est jamais touchée ici."""

    def __init__(self, requests: list[tuple[int, str]], fetcher: QuoteFetcher) -> None:
        super().__init__()
        self.signals = _TaskSignals()
        self._requests = requests
        self._fetcher = fetcher

    def run(self) -> None:
        self.signals.done.emit(fetch_quotes(self._requests, self._fetcher))


# --- Échéancier ---------------------------------------------------------------


class LadderChart(QWidget):
    """Barres du nominal arrivant à échéance, une par année."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._years: list[tuple[int, Decimal]] = []
        self.setMinimumHeight(150)
        self.setAccessibleName("Échéancier du nominal par année")

    def set_data(self, years: list[tuple[int, Decimal]]) -> None:
        self._years = years
        self.setAccessibleDescription(
            ". ".join(f"{year} : {compact_money(amount, CURRENCY)}" for year, amount in years)
        )
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        if not self._years:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(theme.mono_font(11))
        slot = self.width() / len(self._years)
        bar_width = min(72.0, slot * 0.6)
        top, bottom = 22.0, 22.0
        plot = max(1.0, self.height() - top - bottom)
        peak = max(amount for _, amount in self._years) or Decimal(1)
        for index, (year, amount) in enumerate(self._years):
            center = slot * (index + 0.5)
            height = max(2.0, float(amount / peak) * plot) if amount else 2.0
            color = QColor(theme.SERIES[index % len(theme.SERIES)] if amount else theme.BORDER)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            bar = QRectF(center - bar_width / 2, top + plot - height, bar_width, height)
            painter.drawRoundedRect(bar, 5, 5)
            painter.setPen(QColor(theme.TEXT_MUTED))
            if amount:
                label = QRectF(center - slot / 2, bar.top() - 20, slot, 18)
                painter.drawText(
                    label, Qt.AlignmentFlag.AlignCenter, compact_money(amount, CURRENCY)
                )
            painter.setPen(QColor(theme.TEXT_SUBTLE))
            axis = QRectF(center - slot / 2, top + plot + 4, slot, 16)
            painter.drawText(axis, Qt.AlignmentFlag.AlignCenter, str(year))


# --- Dialogues --------------------------------------------------------------


class _ValidatedDialog(QDialog):
    """Reste ouvert tant que la saisie est invalide : l'erreur s'affiche dans la fenêtre."""

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self._error = QLabel()
        self._error.setProperty("role", "error")
        self._error.setWordWrap(True)

    def validate(self) -> None:
        """Lève `ValueError` si la saisie est invalide."""
        raise NotImplementedError

    def show_error(self, message: str) -> None:
        self._error.setText(message)

    def accept(self) -> None:
        try:
            self.validate()
        except ValueError as error:
            self.show_error(str(error))
            return
        self._error.setText("")
        super().accept()


class BondDialog(_ValidatedDialog):
    """Nouvelle obligation : le cours est demandé à Yahoo, saisi à la main s'il est absent."""

    def __init__(
        self, parent: QWidget | None, today: date, holding: BondHolding | None = None
    ) -> None:
        super().__init__(parent)
        self._source = holding.details.valuation_source if holding else ValuationSource.YAHOO
        self.setWindowTitle("Modifier l'obligation" if holding else "Nouvelle obligation")
        self.setMinimumWidth(560)
        self._name, self._identifier = QLineEdit(), QLineEdit()
        self._issuer, self._currency = QLineEdit(), QLineEdit("EUR")
        self._nominal, self._rate = QLineEdit("1000"), QLineEdit()
        self._frequency = QComboBox()
        for frequency in CouponFrequency:
            self._frequency.addItem(frequency.label.capitalize(), frequency)
        self._maturity = date_edit(today)
        hint = QLabel(
            "Le cours est récupéré automatiquement via Yahoo. S'il est introuvable, vous serez "
            "invité à le saisir à la main, avec une alerte s'il date de plus de 7 jours."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        title = QLabel("Modifier l'obligation" if holding else "Nouvelle obligation")
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        layout.addWidget(title)
        layout.addWidget(labeled("Nom", self._name))
        layout.addWidget(
            row(
                labeled("ISIN ou ticker", self._identifier),
                labeled("Émetteur / courtier", self._issuer),
            )
        )
        layout.addWidget(
            row(
                labeled("Devise", self._currency),
                labeled("Nominal par titre", self._nominal),
            )
        )
        layout.addWidget(
            row(
                labeled("Taux du coupon (%)", self._rate),
                labeled("Fréquence du coupon", self._frequency),
            )
        )
        layout.addWidget(labeled("Date d'échéance", self._maturity))
        layout.addWidget(hint)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer" if holding else "Ajouter l'obligation"))
        if holding is not None:
            self._load(holding)

    def _load(self, holding: BondHolding) -> None:
        """Préremplit la saisie avec l'obligation à modifier."""
        details = holding.details
        self._name.setText(holding.asset.name)
        self._identifier.setText(holding.asset.symbol)
        self._issuer.setText(details.issuer)
        self._currency.setText(holding.asset.currency)
        self._nominal.setText(format_quantity(details.nominal))
        self._rate.setText(format_quantity(details.coupon_rate))
        self._frequency.setCurrentIndex(self._frequency.findData(details.frequency))
        self._maturity.setDate(
            QDate(details.maturity.year, details.maturity.month, details.maturity.day)
        )
        hint = QLabel(
            "Si vous changez le nominal, les cours en % sont conservés et les montants en euros "
            "de vos opérations sont recalculés. Changer la devise ne convertit rien."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(layout.count() - 3, hint)

    def validate(self) -> None:
        self.form()

    def form(self) -> tuple[str, str, str, BondDetails]:
        return (
            self._name.text(),
            self._identifier.text(),
            self._currency.text(),
            BondDetails(
                self._issuer.text(),
                parse_decimal(self._nominal.text(), "Nominal"),
                parse_decimal(self._rate.text(), "Taux du coupon"),
                CouponFrequency(self._frequency.currentData()),
                to_date(self._maturity),
                self._source,
            ),
        )


class QuoteDialog(_ValidatedDialog):
    """Saisie manuelle du cours (en % du nominal), sans passer par une opération."""

    def __init__(self, parent: QWidget | None, holding: BondHolding, today: date) -> None:
        super().__init__(parent)
        self._today = today
        self.setWindowTitle("Mettre à jour le cours")
        self.setMinimumWidth(460)
        self._price = QLineEdit()
        self._date = date_edit(today)
        latest = holding.quote_percent
        hint = (
            f"Dernier cours connu : {_pct(latest)} ({holding.valuation.valuation_date:%d/%m/%Y})"
            if latest is not None and holding.valuation is not None
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
            row(labeled("Cours (% du nominal)", self._price, hint), labeled("Date", self._date))
        )
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer"))

    def validate(self) -> None:
        self.form()

    def form(self) -> tuple[date, Decimal]:
        price = parse_decimal(self._price.text(), "Cours")
        when = to_date(self._date)
        if when > self._today:
            raise ValueError("La date du cours ne peut pas être dans le futur.")
        return when, price


@dataclass(frozen=True, slots=True)
class TradeForm:
    kind: TransactionType
    when: date
    quantity: Decimal
    price: Decimal  # % du nominal
    accrued: Decimal
    record_quote: bool  # le cours saisi devient le cours daté de l'opération


@dataclass(frozen=True, slots=True)
class _Preview:
    quantity: Decimal
    quote: BondQuote
    after: Position


class TradeDialog(QDialog):
    """Achat ou vente, avec récapitulatif recalculé à chaque frappe."""

    def __init__(
        self, parent: QWidget | None, holding: BondHolding, kind: TransactionType, today: date
    ) -> None:
        super().__init__(parent)
        self._holding, self._today, self._kind = holding, today, kind
        self._position = holding.position
        self._details = holding.details
        self._accrued_touched = False
        self.setWindowTitle("Acheter ou vendre")
        self.setMinimumWidth(560)

        self._title = QLabel()
        self._title.setProperty("role", "title")
        self._title.setStyleSheet("font-size: 20px;")
        held = format_quantity(self._position.quantity)
        subtitle = QLabel(f"{holding.asset.name} · détention {held} titres")
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

        latest = holding.quote_percent
        self._quantity = QLineEdit()
        self._price = QLineEdit(format_number(latest) if latest is not None else "")
        self._accrued = QLineEdit()
        self._date = date_edit(today)
        self._quantity_hint = QLabel()
        self._quantity_hint.setProperty("role", "hint")
        self._accrued_caption = QLabel()
        self._accrued_caption.setProperty("role", "muted")
        price_hint = (
            f"Prérempli : dernier cours ({holding.valuation.valuation_date:%d/%m/%Y})"
            if latest is not None and holding.valuation is not None
            else "Aucun cours connu : saisissez-le."
        )

        quantity_box = QWidget()
        quantity_layout = QVBoxLayout(quantity_box)
        quantity_layout.setContentsMargins(0, 0, 0, 0)
        quantity_layout.setSpacing(6)
        quantity_caption = QLabel("Titres")
        quantity_caption.setProperty("role", "muted")
        quantity_layout.addWidget(quantity_caption)
        quantity_layout.addWidget(self._quantity)
        quantity_layout.addWidget(self._quantity_hint)
        accrued_box = QWidget()
        accrued_layout = QVBoxLayout(accrued_box)
        accrued_layout.setContentsMargins(0, 0, 0, 0)
        accrued_layout.setSpacing(6)
        accrued_layout.addWidget(self._accrued_caption)
        accrued_layout.addWidget(self._accrued)
        accrued_hint = QLabel("Prérempli, modifiable selon l'avis d'opéré")
        accrued_hint.setProperty("role", "hint")
        accrued_layout.addWidget(accrued_hint)

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
        layout.addWidget(row(quantity_box, labeled("Prix (% du nominal)", self._price, price_hint)))
        layout.addWidget(row(accrued_box, labeled("Date", self._date)))
        layout.addWidget(summary)
        layout.addWidget(self._error)
        layout.addWidget(self._record)
        layout.addWidget(self._buttons)

        self._quantity.textChanged.connect(self._update)
        self._price.textChanged.connect(self._update)
        self._accrued.textEdited.connect(self._on_accrued_edited)
        self._accrued.textChanged.connect(self._update)
        self._date.dateChanged.connect(self._update)
        self._apply_kind()
        self._update()

    # -- saisie --

    def _on_accrued_edited(self, _text: str) -> None:
        self._accrued_touched = True

    def _set_kind(self, kind: TransactionType) -> None:
        self._kind = kind
        self._apply_kind()
        self._update()

    def _apply_kind(self) -> None:
        buy = self._kind is TransactionType.BUY
        self._title.setText("Acheter des titres" if buy else "Vendre des titres")
        self._accrued_caption.setText("Coupon couru payé (€)" if buy else "Coupon couru perçu (€)")
        self._save.setText("Enregistrer l'achat" if buy else "Enregistrer la vente")
        captions = (
            ("Montant", "Coupon couru payé", "Total débité")
            if buy
            else (
                "Montant brut",
                "Coupon couru perçu (hors plus-value)",
                "Total crédité",
                "Plus-value réalisée",
            )
        )
        for index, (line, caption, value) in enumerate(self._summary_rows):
            line.setVisible(index < len(captions))
            caption.setText(captions[index] if index < len(captions) else "")
            caption.setStyleSheet(f"color: {theme.TEXT if index == 2 else theme.TEXT_MUTED};")
            value.setFont(theme.mono_font(17 if index == 2 else 14, bold=index == 2))

    def _quantity_value(self) -> Decimal:
        return validate_quantity(parse_decimal(self._quantity.text(), "Nombre de titres"))

    def _suggest_accrued(self) -> None:
        """Tant que le coupon couru n'a pas été modifié à la main, il suit titres et date."""
        if self._accrued_touched:
            return
        try:
            accrued = accrued_coupon(self._details, self._quantity_value(), to_date(self._date))
            text = format_number(accrued)
        except ValueError:
            text = ""
        if self._accrued.text() != text:
            self._accrued.setText(text)

    def _preview(self) -> _Preview | None:
        try:
            price = parse_decimal(self._price.text(), "Cours")
            quantity = self._quantity_value()
            accrued = parse_decimal(self._accrued.text() or "0", "Coupon couru")
            quote = bond_trade_quote(quantity, price, self._details.nominal, accrued)
        except ValueError:
            return None  # saisie incomplète : pas de récapitulatif, pas de message
        asset_id = self._position.asset_id
        candidate = Transaction(
            None,
            asset_id,
            self._kind,
            to_date(self._date),
            quantity,
            unit_value(price, self._details.nominal),
            Decimal(0),
        )
        try:
            after = compute_position(asset_id, [*self._holding.transactions, candidate])
        except ValueError as error:
            self._error.setText(str(error))
            return None
        return _Preview(quantity, quote, after)

    def _update(self, *_: object) -> None:
        self._error.setText("")
        self._suggest_accrued()
        buy = self._kind is TransactionType.BUY
        preview = self._preview()
        self._save.setEnabled(preview is not None)
        held = format_quantity(self._position.quantity)
        self._quantity_hint.setText("Entier, sans fraction" if buy else f"Maximum : {held} titres")
        nominal = self._details.nominal
        if preview is None:
            values = ["—", "—", "—", "—"]
            colors = [theme.TEXT] * 4
            self._after.setText("")
        else:
            quote, after = preview.quote, preview.after
            realized = after.realized_pnl - self._position.realized_pnl
            values = [
                money(quote.gross, CURRENCY),
                signed_money(quote.accrued, CURRENCY),
                money(quote.total, CURRENCY),
                "—",
            ]
            colors = [theme.TEXT] * 4
            if not buy:
                values[3] = signed_money(realized, CURRENCY)
                colors[3] = theme.variation_color(realized)
            self._after.setText(
                "Après opération : position soldée"
                if after.quantity == 0
                else f"Après opération : {format_quantity(after.quantity)} titres · "
                f"PRU {_pct(price_percent(after.pru, nominal))}"
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
        price = parse_decimal(self._price.text(), "Cours")
        quantity = self._quantity_value()
        accrued = parse_decimal(self._accrued.text() or "0", "Coupon couru")
        bond_trade_quote(quantity, price, self._details.nominal, accrued)  # valide les montants
        when = to_date(self._date)
        record = self._record.isChecked() and when <= self._today
        return TradeForm(self._kind, when, quantity, price, accrued, record)


# --- Page -------------------------------------------------------------------


class BondsPage(QWidget):
    """Écran « Obligations » : synthèse, échéancier, liste et fiche de l'obligation choisie."""

    _COLUMNS = ["Obligation", "Coupon · échéance", "Cours (% nominal)", "Valeur", "+/− latente"]

    def __init__(
        self,
        repository: BondRepository,
        *,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = datetime.now,
        quote_fetcher: QuoteFetcher = fetch_bond_price_percent,
        background: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._today = today
        self._now = now
        self._quote_fetcher = quote_fetcher
        self._background = background
        self._selected: int | None = None
        self._views: list[BondView] = []
        self._pending: list[BondView] = []
        self._failed: set[int] = set()  # Yahoo n'a pas répondu pour ces obligations
        self._attempted: set[int] = set()  # déjà interrogées depuis le lancement
        self._refreshing = False
        self._tasks: list[_QuoteTask] = []
        self._maturity_view: int | None = None  # obligation de la prochaine échéance

        title = QLabel("Obligations")
        title.setProperty("role", "title")
        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "subtitle")
        add = QPushButton("Nouvelle obligation")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._new_bond)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(self._subtitle)
        header.addLayout(heading, 1)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)

        self._value_card = Card("Valeur de marché")
        self._gain_card = Card("Plus-value latente")
        self._coupon_card = Card("Coupons annuels")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._value_card, self._gain_card, self._coupon_card):
            cards.addWidget(card, 1)

        self._alert = QFrame()
        self._alert.setObjectName("bondAlert")
        self._alert.setStyleSheet(
            f"QFrame#bondAlert {{ background: {theme.WARNING_SOFT}; "
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

        self._maturity = QFrame()
        self._maturity.setObjectName("bondMaturity")
        self._maturity.setStyleSheet(
            f"QFrame#bondMaturity {{ background: {theme.ACCENT_SOFT}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 12px; }}"
        )
        self._maturity_text = QLabel()
        self._maturity_text.setTextFormat(Qt.TextFormat.RichText)
        self._maturity_text.setWordWrap(True)
        self._maturity_button = QPushButton("Voir")
        self._maturity_button.clicked.connect(self._show_next_maturity)
        maturity_layout = QHBoxLayout(self._maturity)
        maturity_layout.setContentsMargins(16, 12, 16, 12)
        maturity_layout.setSpacing(12)
        maturity_layout.addWidget(self._maturity_text, 1)
        maturity_layout.addWidget(self._maturity_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._ladder_card = QFrame()
        self._ladder_card.setProperty("role", "card")
        ladder_layout = QVBoxLayout(self._ladder_card)
        ladder_layout.setContentsMargins(20, 18, 20, 18)
        ladder_layout.setSpacing(10)
        ladder_caption = QLabel("ÉCHÉANCIER · NOMINAL ARRIVANT À ÉCHÉANCE PAR ANNÉE")
        ladder_caption.setProperty("role", "caption")
        self._ladder = LadderChart()
        ladder_layout.addWidget(ladder_caption)
        ladder_layout.addWidget(self._ladder)

        self._table = QTableWidget()
        self._table.setAccessibleName("Obligations détenues")
        prepare_table(self._table, self._COLUMNS, row_height=64)
        self._table.setWordWrap(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        columns = self._table.horizontalHeader()
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((150, 160, 150, 130), start=1):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            columns.resizeSection(column, width)
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.currentCellChanged.connect(self._on_row_changed)

        self._empty = QLabel("Aucune obligation. Ajoutez-en une avec « Nouvelle obligation ».")
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
        layout.addWidget(self._maturity)
        layout.addWidget(self._ladder_card)
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
        figures = QVBoxLayout()
        figures.setSpacing(4)
        figures.addWidget(self._d_value)
        figures.addWidget(self._d_gain)
        layout.addLayout(figures)

        chart_caption = QLabel("ÉVOLUTION DU COURS · 12 MOIS")
        chart_caption.setProperty("role", "caption")
        self._chart = PositionChart()
        self._chart.setAccessibleName("Cours en pourcentage du nominal et prix de revient")
        legend = QLabel(
            f"<span style='color:{theme.ACCENT}'>■</span> Cours (% du nominal) &nbsp;&nbsp; "
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
            "Titres",
            "Nominal",
            "PRU",
            "Cours",
            "Coupon",
            "Rendement courant",
            "Coupon couru",
            "Prochain coupon",
            "Échéance",
            "Durée restante",
        )
        for index, caption in enumerate(captions):
            grid.addWidget(self._tile(caption), index // 2, index % 2)
        layout.addLayout(grid)

        self._buy_button = QPushButton("Acheter")
        self._buy_button.setProperty("variant", "primary")
        self._buy_button.clicked.connect(lambda _=False: self._trade(TransactionType.BUY))
        self._sell_button = QPushButton("Vendre")
        self._sell_button.clicked.connect(lambda _=False: self._trade(TransactionType.SELL))
        self._quote_button = QPushButton("Mettre à jour le cours…")
        self._quote_button.clicked.connect(
            lambda _=False: self._update_quote(self._selected_view())
        )
        trades = QHBoxLayout()
        trades.setSpacing(10)
        trades.addWidget(self._buy_button, 1)
        trades.addWidget(self._sell_button, 1)
        layout.addLayout(trades)
        layout.addWidget(self._quote_button)
        self._edit_button = QPushButton("Modifier l'obligation…")
        self._edit_button.clicked.connect(lambda _=False: self._edit_bond(self._selected_view()))
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
        tile.setObjectName("bondTile")
        tile.setStyleSheet(
            f"QFrame#bondTile {{ background: {theme.RAISED}; "
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
        count = len(self._views)
        dated = [v.quote.valuation_date for v in self._views if v.quote is not None]
        self._subtitle.setText(
            f"{count} obligation{'s' if count > 1 else ''}"
            + (f" · cours au {max(dated):%d/%m/%Y}" if dated else "")
        )
        self._summarize(today)
        self._pending = outdated(self._views)
        self._alert.setVisible(bool(self._pending))
        if self._pending:
            self._alert_text.setText(alert_message(self._pending, today, self._failed))
        self._show_maturity(today)
        present = bool(self._views)
        self._table.setVisible(present)
        self._detail.setVisible(present)
        self._empty.setVisible(not present)
        self._fill_table()
        self._show_selected()

    def _summarize(self, today: date) -> None:
        in_euros = [v for v in self._views if v.holding.asset.currency == CURRENCY]
        totals = bond_totals(
            (v.position, v.details, v.quote.value if v.quote else None) for v in in_euros
        )
        notes = ["Cours × nominal × titres"]
        if totals.unvalued:
            notes.append(f"{totals.unvalued} sans cours")
        if len(in_euros) < len(self._views):
            notes.append(f"{len(self._views) - len(in_euros)} hors EUR exclue(s)")
        self._value_card.set(money(totals.value, CURRENCY), " · ".join(notes))
        self._gain_card.set(
            signed_money(totals.gain, CURRENCY),
            _signed_pct(totals.gain_percent),
            theme.variation_color(totals.gain),
        )
        self._coupon_card.set(
            money(totals.annual_coupons, CURRENCY),
            f"Rendement courant {_pct(totals.current_yield)}",
        )
        ladder = maturity_ladder(
            ((v.details, v.position.quantity) for v in in_euros),
            today,
        )
        self._ladder.set_data(ladder)
        self._ladder_card.setVisible(bool(ladder))

    def _show_maturity(self, today: date) -> None:
        view = next_maturity(self._views, today)
        self._maturity.setVisible(view is not None)
        self._maturity_view = view.asset_id if view is not None else None
        if view is None:
            return
        details, quantity = view.details, view.position.quantity
        due = next_coupon(details, quantity, details.maturity - timedelta(days=1))
        coupon = f" + dernier coupon de {money(due[1], view.holding.asset.currency)}" if due else ""
        nominal = money(details.nominal * quantity, view.holding.asset.currency)
        self._maturity_text.setText(
            f"Prochaine échéance : <b>{escape(view.holding.asset.name)}</b> le "
            f"{details.maturity:%d/%m/%Y} ({remaining_label(details.maturity, today)}) — "
            f"{nominal} de nominal{coupon}."
        )

    def _show_next_maturity(self) -> None:
        if self._maturity_view is not None:
            self._selected = self._maturity_view
            self.refresh()

    def _fill_table(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._views))
        for table_row, view in enumerate(self._views):
            self._fill_row(table_row, view)
            if view.asset_id == self._selected:
                self._table.selectRow(table_row)
        self._table.blockSignals(False)
        fit_height(self._table)

    def _fill_row(self, table_row: int, view: BondView) -> None:
        asset, details = view.holding.asset, view.details
        currency = asset.currency
        color = theme.SERIES[view.asset_id % len(theme.SERIES)]
        meta = f"{asset.symbol} · {details.issuer}" if details.issuer else asset.symbol
        self._table.setCellWidget(
            table_row,
            0,
            _rich(
                f"<span style='color:{color}'>■</span>&nbsp; <b>{escape(asset.name)}</b><br>"
                f"{_subtle(escape(meta), 12)}"
            ),
        )
        self._table.setCellWidget(
            table_row,
            1,
            _rich(
                f"{_pct(details.coupon_rate)}<br>{_subtle(f'{details.maturity:%d/%m/%Y}', 12)}",
                RIGHT,
                mono=True,
            ),
        )
        if view.quote is None:
            quote_html = f"—<br>{_subtle('Pas de cours')}"
        else:
            source = SOURCE_LABELS[details.valuation_source]
            note = f"{source} · {view.quote.valuation_date:%d/%m/%Y}"
            if view.stale:
                note = f"<span style='color:{theme.WARNING}'>{note} · ancien</span>"
            quote_html = f"{_pct(view.holding.quote_percent)}<br>{_subtle(note)}"
        self._table.setCellWidget(table_row, 2, _rich(quote_html, RIGHT, mono=True))
        titles = f"{format_quantity(view.position.quantity)} titres"
        value_text = "—" if view.value is None else money(view.value.value, currency)
        self._table.setCellWidget(
            table_row, 3, _rich(f"{value_text}<br>{_subtle(titles, 12)}", RIGHT, mono=True)
        )
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
        asset = holding.asset
        currency = asset.currency
        today = self._today()
        self._d_name.setText(asset.name)
        issuer = f" · {details.issuer}" if details.issuer else ""
        self._d_sub.setText(f"{asset.symbol}{issuer}")

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
        if valued is None:
            self._d_value.setText("—")
            self._d_gain.setText("")
        else:
            self._d_value.setText(money(valued.value, currency))
            self._d_gain.setText(
                f"{signed_money(valued.gain, currency)} · {_signed_pct(valued.gain_percent)}"
            )
            self._d_gain.setStyleSheet(f"color: {theme.variation_color(valued.gain)};")

        points = price_series(
            [(v.valuation_date, v.value) for v in self._repository.quote_history(view.asset_id)],
            holding.transactions,
            details.nominal,
            today,
        )
        pru = price_percent(position.pru, details.nominal) if position.quantity > 0 else None
        self._chart.set_data(points, pru)

        quote = holding.quote_percent
        around = next_coupon(details, position.quantity or Decimal(1), today)
        self._tiles["Titres"].setText(format_quantity(position.quantity))
        self._tiles["Nominal"].setText(money(details.nominal, currency))
        self._tiles["PRU"].setText(_pct(pru))
        self._tiles["Cours"].setText(_pct(quote))
        self._tiles["Coupon"].setText(f"{_pct(details.coupon_rate)} · {details.frequency.label}")
        self._tiles["Rendement courant"].setText(
            "—" if quote is None else _pct(current_yield(details.coupon_rate, quote))
        )
        self._tiles["Coupon couru"].setText(
            money(accrued_coupon(details, position.quantity, today), currency)
        )
        self._tiles["Prochain coupon"].setText(
            "—" if around is None else f"{around[0]:%d/%m/%Y} · {money(around[1], currency)}"
        )
        self._tiles["Échéance"].setText(f"{details.maturity:%d/%m/%Y}")
        self._tiles["Durée restante"].setText(remaining_label(details.maturity, today))
        self._sell_button.setEnabled(position.quantity > 0)

        _clear(self._operations)
        newest_first = sorted(
            holding.transactions, key=lambda t: (t.trade_date, t.id or 0), reverse=True
        )
        for tx in newest_first[:LAST_OPERATIONS]:
            self._operations.addWidget(self._operation_row(holding, tx))

    def _operation_row(self, holding: BondHolding, tx: Transaction) -> QWidget:
        buy = tx.type is TransactionType.BUY
        nominal = holding.details.nominal
        accrued = holding.accruals.get(tx.id or 0, Decimal(0))
        gross = tx.quantity * tx.unit_price
        amount = -(gross + accrued) if buy else gross + accrued
        badge = QLabel("Achat" if buy else "Vente")
        badge.setFixedWidth(64)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {theme.ACCENT_SOFT if buy else theme.WARNING_SOFT}; "
            f"color: {theme.ACCENT_HOVER if buy else theme.WARNING}; "
            "border-radius: 6px; padding: 3px 0; font-size: 11px; font-weight: 600;"
        )
        quantity = QLabel(
            f"{'+' if buy else MINUS}{format_quantity(tx.quantity)} titres · "
            f"{_pct(price_percent(tx.unit_price, nominal))}"
        )
        quantity.setFont(theme.mono_font(13))
        day = QLabel(f"{tx.trade_date:%d/%m/%Y}")
        day.setProperty("role", "hint")
        figures = QVBoxLayout()
        figures.setSpacing(2)
        figures.addWidget(quantity)
        figures.addWidget(day)
        total = QLabel(signed_money(amount, holding.asset.currency))
        total.setFont(theme.mono_font(13))
        line = QWidget()
        line_layout = QHBoxLayout(line)
        line_layout.setContentsMargins(0, 10, 0, 10)
        line_layout.setSpacing(12)
        line_layout.addWidget(badge)
        line_layout.addLayout(figures, 1)
        line_layout.addWidget(total)
        return line

    def _selected_view(self) -> BondView | None:
        for view in self._views:
            if view.asset_id == self._selected:
                return view
        return None

    def _first_pending(self) -> BondView | None:
        return self._pending[0] if self._pending else None

    # -- cours Yahoo --

    def refresh_quotes(self, force: bool = False) -> None:
        """Demande à Yahoo le cours des positions ouvertes, une fois par jour et par obligation.

        Un échec garde le dernier cours connu : l'alerte demande alors une saisie manuelle.
        """
        if self._refreshing:
            return
        open_bonds = [
            h
            for h in self._repository.list_holdings()
            if h.asset.id is not None and h.position.quantity > 0
        ]
        due = set(
            assets_to_refresh(
                [h.asset.id for h in open_bonds if h.asset.id is not None],
                self._repository.valued_on(self._today()),
                force=force,
            )
        )
        if not force:
            due -= self._attempted
        requests = [
            (h.asset.id, h.asset.symbol)
            for h in open_bonds
            if h.asset.id is not None and h.asset.id in due
        ]
        if not requests:
            return
        self._refreshing = True
        self._attempted.update(asset_id for asset_id, _ in requests)
        if not self._background:
            self._finish_refresh(fetch_quotes(requests, self._quote_fetcher))
            return
        task = _QuoteTask(requests, self._quote_fetcher)
        task.signals.done.connect(self._finish_refresh)
        self._tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _finish_refresh(self, results: object) -> None:
        assert isinstance(results, dict)
        stamp, today = self._now(), self._today()
        for asset_id, price in results.items():
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

    def _new_bond(self) -> None:
        dialog = BondDialog(self, self._today())
        # La fenêtre reste ouverte (saisie conservée) tant que l'obligation est refusée.
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                name, identifier, currency, details = dialog.form()
                created = self._repository.create(name, identifier, currency, details)
            except ValueError as error:
                dialog.show_error(str(error))
                continue
            self._selected = created.asset.id
            break
        self.refresh()
        self.refresh_quotes()

    def _edit_bond(self, view: BondView | None) -> None:
        if view is None:
            return
        dialog = BondDialog(self, self._today(), view.holding)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                name, identifier, currency, details = dialog.form()
                self._repository.update(view.asset_id, name, identifier, currency, details)
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
                view.asset_id, form.kind, form.when, form.quantity, form.price, form.accrued
            )
            if form.record_quote:
                self._repository.record_quote(
                    view.asset_id, form.when, form.price, self._now(), ValuationSource.MANUAL
                )
        except ValueError as error:
            QMessageBox.warning(self, "Opération refusée", str(error))
        self.refresh()

    def _update_quote(self, view: BondView | None) -> None:
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
        self._body.setStretchFactor(self._table, 1)
