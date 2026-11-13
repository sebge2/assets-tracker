"""Dialogues des matières premières : nouvelle position, achat, vente, modification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.commodity_repository import CommodityRepository, Holding
from assets_tracker.domain.commodities import (
    PHYSICAL_METALS,
    PHYSICAL_UNITS,
    CommodityDetails,
    CommodityFamily,
    CommodityForm,
    PhysicalMetal,
    preview_buy,
    preview_sell,
    validate_trade,
)
from assets_tracker.domain.models import Position, TransactionType
from assets_tracker.ui import icons, theme
from assets_tracker.ui.form_helpers import date_edit, labeled, row, section, to_date
from assets_tracker.ui.formatting import (
    FAMILY_LABELS,
    format_quantity,
    money,
    parse_decimal,
    signed_money,
)
from assets_tracker.ui.widgets import RIGHT, fit_height, prepare_table, table_cell

CURRENCY = "EUR"
BUY, SELL = TransactionType.BUY, TransactionType.SELL


@dataclass(frozen=True, slots=True)
class TradeForm:
    kind: TransactionType
    trade_date: date
    quantity: Decimal
    unit_price: Decimal
    fees: Decimal


@dataclass(frozen=True, slots=True)
class NewPositionForm:
    name: str
    symbol: str
    details: CommodityDetails
    first: TradeForm


@dataclass(frozen=True, slots=True)
class PositionInfoForm:
    name: str
    symbol: str
    details: CommodityDetails


def _per(details: CommodityDetails) -> str:
    return "" if details.unit == "parts" else f" / {details.unit}"


class _Results(QFrame):
    """Cadre de résultats calculés en direct : une ligne par (libellé, valeur, couleur)."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 8, 16, 8)
        self._layout.setSpacing(0)
        self._rows: list[QWidget] = []

    def set_rows(self, rows: list[tuple[str, str, str]]) -> None:
        for widget in self._rows:
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._rows = []
        for label, value, color in rows:
            line = QWidget()
            layout = QHBoxLayout(line)
            layout.setContentsMargins(0, 8, 0, 8)
            name = QLabel(label)
            name.setProperty("role", "muted")
            number = QLabel(value)
            number.setFont(theme.mono_font(15))
            number.setStyleSheet(f"color: {color};")
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(name, 1)
            layout.addWidget(number)
            self._layout.addWidget(line)
            self._rows.append(line)

    def values(self) -> list[str]:
        """Valeurs affichées, pour les tests."""
        texts: list[str] = []
        for line in self._rows:
            labels = line.findChildren(QLabel)
            texts.append(labels[1].text())
        return texts


def _error_label() -> QLabel:
    label = QLabel()
    label.setProperty("role", "error")
    label.setWordWrap(True)
    label.hide()
    return label


def _buttons(dialog: QDialog, submit: str, on_accept: Callable[[], None]) -> QDialogButtonBox:
    box = QDialogButtonBox()
    box.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
    save = box.addButton(submit, QDialogButtonBox.ButtonRole.AcceptRole)
    if save is not None:
        save.setProperty("variant", "primary")
        save.setDefault(True)
    box.accepted.connect(on_accept)
    box.rejected.connect(dialog.reject)
    return box


# --- Achat et vente -----------------------------------------------------------


class TradeDialog(QDialog):
    """Achat ou vente sur une position existante, avec aperçu du PRU ou de la plus-value."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        kind: TransactionType,
        holding: Holding,
        today: date,
        validator: Callable[[TradeForm], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self._validator = validator
        self._position: Position = holding.position
        self._details = holding.details
        physical = holding.details.form is CommodityForm.PHYSICAL
        buying = kind is BUY
        self._cost_label = "Coût de l'achat" if physical else "Coût de l'achat (frais inclus)"
        self._proceeds_label = "Produit net" if physical else "Produit net (frais déduits)"
        verb = "achat" if buying else "vente"
        self.setWindowTitle(f"{'Ajouter un achat' if buying else 'Vendre'} · {holding.asset.name}")
        self.setMinimumWidth(520)

        self._quantity = QLineEdit()
        self._quantity.setPlaceholderText("0")
        self._price = QLineEdit(
            "" if holding.price is None else format_quantity(holding.price.price, 4)
        )
        self._fees = QLineEdit("0")
        self._date = date_edit(today)
        for field in (self._quantity, self._price, self._fees):
            field.textChanged.connect(self._update_preview)

        unit = holding.details.unit
        quantity_label = f"Quantité ({unit})" if physical else "Nombre de parts"
        quantity_hint = None if buying else f"Maximum : {format_quantity(self._position.quantity)}"
        price_hint = (
            None
            if holding.price is None
            else f"Cours actuel : {money(holding.price.price, CURRENCY)}{_per(holding.details)}"
        )

        held = QLabel(
            f"Détention actuelle : {format_quantity(self._position.quantity)} {unit} · "
            f"PRU {money(self._position.pru, CURRENCY)}{_per(holding.details)}"
        )
        held.setProperty("role", "muted")
        held.setWordWrap(True)
        self._results = _Results()
        self._error = _error_label()
        note = QLabel(
            "La vente laisse le PRU inchangé et réalise une plus-value."
            if not buying
            else "Le PRU est recalculé à partir de toutes les transactions"
            + ("." if physical else ", frais inclus.")
        )
        note.setProperty("role", "hint")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)
        layout.addWidget(section(f"Nouvel {verb}" if buying else "Nouvelle vente"))
        layout.addWidget(held)
        layout.addWidget(
            row(
                labeled(quantity_label, self._quantity, quantity_hint),
                labeled(
                    f"Prix d'{verb}{' par unité' if physical else ''} (€)"
                    if buying
                    else f"Prix de vente{' par unité' if physical else ''} (€)",
                    self._price,
                    price_hint or ("Tout compris" if physical else None),
                ),
            )
        )
        if physical:
            layout.addWidget(row(labeled("Date", self._date)))
        else:
            layout.addWidget(row(labeled("Frais (€)", self._fees), labeled("Date", self._date)))
        layout.addWidget(self._results)
        layout.addWidget(note)
        layout.addWidget(self._error)
        layout.addWidget(
            _buttons(
                self, "Enregistrer l'achat" if buying else "Enregistrer la vente", self._accept
            )
        )
        self._update_preview()

    def _parsed(self) -> tuple[Decimal, Decimal, Decimal]:
        return (
            parse_decimal(self._quantity.text(), "Quantité"),
            parse_decimal(self._price.text(), "Prix"),
            parse_decimal(self._fees.text() or "0", "Frais"),
        )

    def _update_preview(self) -> None:
        unit = self._details.unit
        try:
            quantity, price, fees = self._parsed()
            if self._kind is BUY:
                buy = preview_buy(self._position, quantity, price, fees)
                rows = [
                    (self._cost_label, money(buy.cost, CURRENCY), theme.TEXT),
                    (
                        "Nouvelle détention",
                        f"{format_quantity(buy.quantity_after)} {unit}",
                        theme.TEXT,
                    ),
                    (
                        "PRU avant → après",
                        f"{money(self._position.pru, CURRENCY)} → "
                        f"{money(buy.pru_after, CURRENCY)}{_per(self._details)}",
                        theme.TEXT,
                    ),
                ]
            else:
                sell = preview_sell(self._position, quantity, price, fees)
                gain_color = (
                    theme.GAIN
                    if sell.realized > 0
                    else theme.LOSS
                    if sell.realized < 0
                    else theme.TEXT
                )
                rows = [
                    (self._proceeds_label, money(sell.proceeds, CURRENCY), theme.TEXT),
                    ("Plus-value réalisée", signed_money(sell.realized, CURRENCY), gain_color),
                    (
                        "PRU conservé",
                        f"{money(self._position.pru, CURRENCY)}{_per(self._details)}",
                        theme.TEXT,
                    ),
                    (
                        "Reste en portefeuille",
                        f"{format_quantity(sell.quantity_after)} {unit}",
                        theme.TEXT,
                    ),
                ]
        except ValueError:
            labels = (
                [self._cost_label, "Nouvelle détention", "PRU avant → après"]
                if self._kind is BUY
                else [
                    self._proceeds_label,
                    "Plus-value réalisée",
                    "PRU conservé",
                    "Reste en portefeuille",
                ]
            )
            rows = [(label, "—", theme.TEXT_SUBTLE) for label in labels]
        self._results.set_rows(rows)

    def form(self) -> TradeForm:
        """Formulaire validé ; lève `ValueError` avec un message lisible."""
        quantity, price, fees = self._parsed()
        validate_trade(quantity, price, fees)
        if self._kind is SELL:
            preview_sell(self._position, quantity, price, fees)
        return TradeForm(self._kind, to_date(self._date), quantity, price, fees)

    def _accept(self) -> None:
        try:
            form = self.form()
            if self._validator is not None:
                self._validator(form)
        except ValueError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self.accept()

    def results_values(self) -> list[str]:
        return self._results.values()


# --- Nouvelle position --------------------------------------------------------


class NewPositionDialog(QDialog):
    """Création d'une matière : forme de détention d'abord, champs selon la forme."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        today: date,
        validator: Callable[[NewPositionForm], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._validator = validator
        self.setWindowTitle("Nouvelle position")
        self.setMinimumWidth(580)

        self._physical_button = QPushButton("Physique · lingots, pièces, barres")
        self._etc_button = QPushButton("ETC / ETF · titre coté")
        group = QButtonGroup(self)
        group.setExclusive(True)
        for button in (self._physical_button, self._etc_button):
            button.setCheckable(True)
            button.setProperty("variant", "chip")
            group.addButton(button)
        self._physical_button.setChecked(True)

        # -- physique --
        self._material = QComboBox()
        self._material.setEditable(True)
        self._material.addItems([metal.name for metal in PHYSICAL_METALS])
        self._material.setCurrentIndex(0)
        self._p_family = self._family_combo(CommodityFamily.PRECIOUS)
        self._p_symbol = QLineEdit(PHYSICAL_METALS[0].symbol)
        self._p_unit = QComboBox()
        self._p_unit.addItems(PHYSICAL_UNITS)
        self._p_quantity = QLineEdit()
        self._p_price = QLineEdit()
        self._p_date = date_edit(today)
        self._p_note = QLabel()
        self._p_note.setProperty("role", "hint")
        self._p_note.setWordWrap(True)
        self._material.currentTextChanged.connect(self._apply_preset)

        physical = QWidget()
        physical_layout = QVBoxLayout(physical)
        physical_layout.setContentsMargins(0, 0, 0, 0)
        physical_layout.setSpacing(12)
        physical_layout.addWidget(
            row(
                labeled("Matière", self._material, "Or, argent, platine… ou une autre matière"),
                labeled("Famille", self._p_family),
            )
        )
        physical_layout.addWidget(
            row(labeled("Symbole", self._p_symbol), labeled("Unité", self._p_unit))
        )
        physical_layout.addWidget(
            row(
                labeled("Quantité", self._p_quantity),
                labeled(
                    "Prix d'achat par unité (€)",
                    self._p_price,
                    "Tout compris : prime et livraison incluses",
                ),
            )
        )
        physical_layout.addWidget(row(labeled("Date", self._p_date)))
        physical_layout.addWidget(self._p_note)

        # -- ETC --
        self._e_name = QLineEdit()
        self._e_name.setPlaceholderText("Ex. Pétrole WTI")
        self._e_family = self._family_combo(CommodityFamily.ENERGY)
        self._e_symbol = QLineEdit()
        self._e_symbol.setPlaceholderText("Ex. PHAU.L")
        self._e_currency = QLineEdit("USD")
        self._e_currency.setMaxLength(3)
        self._e_quantity = QLineEdit()
        self._e_price = QLineEdit()
        self._e_fees = QLineEdit("0")
        self._e_broker = QLineEdit()
        self._e_broker.setPlaceholderText("Ex. Compte-titres")
        self._e_date = date_edit(today)

        etc = QWidget()
        etc_layout = QVBoxLayout(etc)
        etc_layout.setContentsMargins(0, 0, 0, 0)
        etc_layout.setSpacing(12)
        etc_layout.addWidget(row(labeled("Nom", self._e_name), labeled("Famille", self._e_family)))
        etc_layout.addWidget(
            row(
                labeled("Symbole", self._e_symbol, "Ticker Yahoo Finance"),
                labeled("Devise du titre", self._e_currency, "Converti en euros au taux du jour"),
            )
        )
        etc_layout.addWidget(
            row(
                labeled("Nombre de parts", self._e_quantity),
                labeled("Prix d'achat (€)", self._e_price),
            )
        )
        etc_layout.addWidget(
            row(labeled("Frais (€)", self._e_fees), labeled("Courtier", self._e_broker))
        )
        etc_layout.addWidget(row(labeled("Date", self._e_date)))

        self._stack = QStackedWidget()
        self._stack.addWidget(physical)
        self._stack.addWidget(etc)
        self._physical_button.toggled.connect(self._sync_form)
        self._results = _Results()
        self._error = _error_label()
        for field in (
            self._p_quantity,
            self._p_price,
            self._e_quantity,
            self._e_price,
            self._e_fees,
        ):
            field.textChanged.connect(self._update_preview)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)
        layout.addWidget(section("1 · Forme de détention"))
        layout.addWidget(row(self._physical_button, self._etc_button))
        layout.addWidget(section("2 · Détails"))
        layout.addWidget(self._stack)
        layout.addWidget(self._results)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer", self._accept))
        self._apply_preset(self._material.currentText())
        self._update_preview()

    @staticmethod
    def _family_combo(selected: CommodityFamily) -> QComboBox:
        combo = QComboBox()
        for family in CommodityFamily:
            combo.addItem(FAMILY_LABELS[family], family)
        combo.setCurrentIndex(list(CommodityFamily).index(selected))
        return combo

    def _preset(self) -> PhysicalMetal | None:
        typed = self._material.currentText().strip().lower()
        return next((m for m in PHYSICAL_METALS if m.name.lower() == typed), None)

    def _apply_preset(self, _: str) -> None:
        preset = self._preset()
        if preset is None:
            self._p_note.setText(
                "Matière personnalisée : renseignez un symbole. Pas de cours automatique."
            )
            return
        self._p_symbol.setText(preset.symbol)
        self._p_family.setCurrentIndex(list(CommodityFamily).index(preset.family))
        self._p_note.setText(
            f"Cours automatique : {preset.quote_symbol} (USD par once troy), converti en euros "
            "et dans l'unité choisie."
        )

    def _sync_form(self) -> None:
        self._stack.setCurrentIndex(0 if self._physical_button.isChecked() else 1)
        self._update_preview()

    def _is_physical(self) -> bool:
        return self._physical_button.isChecked()

    def _trade(self) -> TradeForm:
        if self._is_physical():
            quantity, price, fees, when = (
                self._p_quantity.text(),
                self._p_price.text(),
                "0",
                to_date(self._p_date),
            )
        else:
            quantity, price, fees, when = (
                self._e_quantity.text(),
                self._e_price.text(),
                self._e_fees.text() or "0",
                to_date(self._e_date),
            )
        trade = TradeForm(
            BUY,
            when,
            parse_decimal(quantity, "Quantité"),
            parse_decimal(price, "Prix"),
            parse_decimal(fees, "Frais"),
        )
        validate_trade(trade.quantity, trade.unit_price, trade.fees)
        return trade

    def _update_preview(self) -> None:
        try:
            trade = self._trade()
        except ValueError:
            self._results.set_rows(
                [
                    ("Coût total (frais inclus)", "—", theme.TEXT_SUBTLE),
                    ("PRU", "—", theme.TEXT_SUBTLE),
                ]
            )
            return
        cost = trade.quantity * trade.unit_price + trade.fees
        unit = self._p_unit.currentText() if self._is_physical() else "parts"
        per = "" if unit == "parts" else f" / {unit}"
        self._results.set_rows(
            [
                (
                    "Coût total" if self._is_physical() else "Coût total (frais inclus)",
                    money(cost, CURRENCY),
                    theme.TEXT,
                ),
                ("PRU", f"{money(cost / trade.quantity, CURRENCY)}{per}", theme.TEXT),
            ]
        )

    def form(self) -> NewPositionForm:
        """Formulaire validé ; lève `ValueError` avec un message lisible."""
        trade = self._trade()
        if self._is_physical():
            name = self._material.currentText().strip()
            preset = self._preset()
            details = CommodityDetails(
                family=CommodityFamily(self._p_family.currentData()),
                form=CommodityForm.PHYSICAL,
                unit=self._p_unit.currentText(),
                quote_symbol=preset.quote_symbol if preset else None,
                quote_currency="USD" if preset else "EUR",
                quote_unit="oz" if preset else None,
            )
            return NewPositionForm(name, self._p_symbol.text().strip(), details, trade)
        symbol = self._e_symbol.text().strip()
        details = CommodityDetails(
            family=CommodityFamily(self._e_family.currentData()),
            form=CommodityForm.ETC,
            unit="parts",
            broker=self._e_broker.text().strip() or None,
            quote_symbol=symbol or None,
            quote_currency=self._e_currency.text().strip().upper(),
        )
        return NewPositionForm(self._e_name.text().strip(), symbol, details, trade)

    def _accept(self) -> None:
        try:
            form = self.form()
            if not form.name:
                raise ValueError("Le nom est obligatoire.")
            if not form.symbol:
                raise ValueError("Le symbole est obligatoire.")
            if self._validator is not None:
                self._validator(form)
        except ValueError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self.accept()

    def results_values(self) -> list[str]:
        return self._results.values()


# --- Modification -------------------------------------------------------------


class TransactionDialog(QDialog):
    """Correction d'une transaction existante (la position est recalculée à l'enregistrement)."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        holding: Holding,
        transaction_id: int,
        validator: Callable[[TradeForm], None],
    ) -> None:
        super().__init__(parent)
        tx = next(t for t in holding.transactions if t.id == transaction_id)
        self._validator = validator
        physical = holding.details.form is CommodityForm.PHYSICAL
        self.setWindowTitle("Modifier la transaction")
        self.setMinimumWidth(480)
        self._kind = QComboBox()
        self._kind.addItem("Achat", BUY)
        self._kind.addItem("Vente", SELL)
        self._kind.setCurrentIndex(0 if tx.type is BUY else 1)
        self._date = date_edit(tx.trade_date)
        self._quantity = QLineEdit(format_quantity(tx.quantity, 8))
        self._price = QLineEdit(format_quantity(tx.unit_price, 8))
        self._fees = QLineEdit(format_quantity(tx.fees, 8))
        self._total = QLabel()
        self._total.setFont(theme.mono_font(15))
        for field in (self._quantity, self._price, self._fees):
            field.textChanged.connect(self._update_total)
        self._error = _error_label()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)
        layout.addWidget(section("Modifier la transaction"))
        layout.addWidget(row(labeled("Opération", self._kind), labeled("Date", self._date)))
        layout.addWidget(
            row(
                labeled(
                    f"Quantité ({holding.details.unit})" if physical else "Nombre de parts",
                    self._quantity,
                ),
                labeled("Prix unitaire (€)", self._price),
            )
        )
        layout.addWidget(row(labeled("Frais (€)", self._fees)))
        layout.addWidget(labeled("Total de la transaction", self._total))
        hint = QLabel(
            "Le PRU et la plus-value de la position sont recalculés à partir de toutes ses "
            "transactions, jamais stockés."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer la transaction", self._accept))
        self._update_total()

    def _update_total(self) -> None:
        try:
            quantity = parse_decimal(self._quantity.text(), "Quantité")
            price = parse_decimal(self._price.text(), "Prix")
            fees = parse_decimal(self._fees.text() or "0", "Frais")
            self._total.setText(money(quantity * price + fees, CURRENCY))
        except ValueError:
            self._total.setText("—")

    def form(self) -> TradeForm:
        quantity = parse_decimal(self._quantity.text(), "Quantité")
        price = parse_decimal(self._price.text(), "Prix")
        fees = parse_decimal(self._fees.text() or "0", "Frais")
        validate_trade(quantity, price, fees)
        return TradeForm(
            TransactionType(self._kind.currentData()), to_date(self._date), quantity, price, fees
        )

    def _accept(self) -> None:
        try:
            self._validator(self.form())
        except ValueError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self.accept()


class EditPositionDialog(QDialog):
    """Informations de la position et liste de ses transactions (modifier, supprimer).

    Les corrections de transactions sont enregistrées dès leur validation ; les informations
    (nom, famille, symbole, courtier pour un ETC) le sont avec « Enregistrer ».
    """

    DELETE = 2  # code de retour de `exec()` quand l'utilisateur supprime la position

    def __init__(
        self, parent: QWidget | None, *, repository: CommodityRepository, asset_id: int
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._asset_id = asset_id
        self.modified = False
        holding = self._holding()
        self.setWindowTitle(f"Modifier · {holding.asset.name}")
        self.setMinimumWidth(640)

        self._name = QLineEdit(holding.asset.name)
        self._family = QComboBox()
        for family in CommodityFamily:
            self._family.addItem(FAMILY_LABELS[family], family)
        self._family.setCurrentIndex(list(CommodityFamily).index(holding.details.family))
        self._symbol = QLineEdit(holding.asset.symbol)
        physical = holding.details.form is CommodityForm.PHYSICAL
        self._extra = QLineEdit(holding.details.broker or "")
        self._error = _error_label()

        self._table = QTableWidget()
        self._table.setAccessibleName("Transactions de la position")
        prepare_table(
            self._table,
            ["Date", "Opération", "Quantité", "Prix unitaire", "Frais", ""],
            row_height=48,
        )
        columns = self._table.horizontalHeader()
        columns.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        columns.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        columns.resizeSection(5, 96)
        self._table.setWordWrap(False)

        delete = QPushButton("Supprimer la position")
        delete.setProperty("variant", "danger")
        delete.clicked.connect(self._confirm_delete)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)
        layout.addWidget(section("Informations"))
        layout.addWidget(row(labeled("Nom", self._name), labeled("Famille", self._family)))
        if physical:
            layout.addWidget(row(labeled("Symbole", self._symbol)))
        else:
            layout.addWidget(
                row(labeled("Symbole", self._symbol), labeled("Courtier", self._extra))
            )
        layout.addWidget(section("Transactions"))
        layout.addWidget(self._table)
        hint = QLabel(
            "Pour un achat ou une vente de plus, utilisez « Ajouter un achat » ou « Vendre »."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self._error)
        footer = QHBoxLayout()
        footer.addWidget(delete)
        footer.addStretch(1)
        footer.addWidget(_buttons(self, "Enregistrer", self._accept))
        layout.addLayout(footer)
        self._fill_table()

    def _holding(self) -> Holding:
        holding = self._repository.get_holding(self._asset_id)
        if holding is None:
            raise ValueError("Cette position n'existe plus.")
        return holding

    def _fill_table(self) -> None:
        holding = self._holding()
        unit = holding.details.unit
        transactions = list(reversed(holding.transactions))
        self._table.setRowCount(len(transactions))
        for index, tx in enumerate(transactions):
            buying = tx.type is BUY
            self._table.setItem(
                index, 0, table_cell(tx.trade_date.isoformat(), color=theme.TEXT_MUTED, mono=True)
            )
            self._table.setItem(
                index,
                1,
                table_cell(
                    "Achat" if buying else "Vente", color=theme.GAIN if buying else theme.LOSS
                ),
            )
            self._table.setItem(
                index,
                2,
                table_cell(f"{format_quantity(tx.quantity)} {unit}", mono=True, align=RIGHT),
            )
            self._table.setItem(
                index, 3, table_cell(money(tx.unit_price, CURRENCY), mono=True, align=RIGHT)
            )
            self._table.setItem(
                index,
                4,
                table_cell(
                    money(tx.fees, CURRENCY), color=theme.TEXT_MUTED, mono=True, align=RIGHT
                ),
            )
            self._table.setCellWidget(index, 5, self._actions(tx.id))
        fit_height(self._table, max_height=300)

    def _actions(self, transaction_id: int | None) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(6)
        layout.addStretch(1)
        for name, tip, handler in (
            ("edit", "Modifier cette transaction", self._edit_transaction),
            ("delete", "Supprimer cette transaction", self._delete_transaction),
        ):
            button = QToolButton()
            button.setProperty("role", "icon")
            button.setIcon(icons.icon(name))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(tip.split(" cette")[0])
            button.setAccessibleName(tip)
            button.clicked.connect(lambda _=False, h=handler, i=transaction_id: h(i))
            layout.addWidget(button)
        return holder

    def _edit_transaction(self, transaction_id: int | None) -> None:
        if transaction_id is None:
            return
        holding = self._holding()

        def validate(form: TradeForm) -> None:
            self._repository.check_transaction(
                self._asset_id,
                form.kind,
                form.trade_date,
                form.quantity,
                form.unit_price,
                form.fees,
                replacing=transaction_id,
            )

        dialog = TransactionDialog(
            self, holding=holding, transaction_id=transaction_id, validator=validate
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            form = dialog.form()
            self._repository.update_transaction(
                transaction_id,
                form.kind,
                form.trade_date,
                form.quantity,
                form.unit_price,
                form.fees,
            )
            self.modified = True
            self._fill_table()

    def _delete_transaction(self, transaction_id: int | None) -> None:
        if transaction_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Supprimer la transaction",
            "Supprimer cette transaction ? Le PRU et la plus-value sont recalculés.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_transaction(transaction_id)
        except ValueError as error:
            QMessageBox.warning(self, "Suppression impossible", str(error))
            return
        self.modified = True
        self._fill_table()

    def _confirm_delete(self) -> None:
        answer = QMessageBox.question(
            self,
            "Supprimer la position",
            f"Supprimer « {self._name.text()} » avec toutes ses transactions ? "
            "Cette action est irréversible.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.done(self.DELETE)

    def form(self) -> PositionInfoForm:
        """Informations validées ; lève `ValueError` avec un message lisible."""
        holding = self._holding()
        current = holding.details
        extra = self._extra.text().strip() or None
        symbol = self._symbol.text().strip()
        physical = current.form is CommodityForm.PHYSICAL
        details = CommodityDetails(
            family=CommodityFamily(self._family.currentData()),
            form=current.form,
            unit=current.unit,
            broker=current.broker if physical else extra,
            # Un ETC se cote sous son propre symbole ; un métal physique garde son cours spot.
            quote_symbol=current.quote_symbol if physical else (symbol or None),
            quote_currency=current.quote_currency,
            quote_unit=current.quote_unit,
        )
        name = self._name.text().strip()
        if not name:
            raise ValueError("Le nom est obligatoire.")
        if not symbol:
            raise ValueError("Le symbole est obligatoire.")
        return PositionInfoForm(name, symbol, details)

    def _accept(self) -> None:
        try:
            form = self.form()
            self._repository.update_position(self._asset_id, form.name, form.symbol, form.details)
        except ValueError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self.modified = True
        self.accept()
