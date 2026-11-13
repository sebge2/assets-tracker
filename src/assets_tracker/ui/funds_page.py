"""Écran simplifié des fonds : création, souscription, rachat et VL manuelle."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.fund_repository import FundHolding, FundRepository
from assets_tracker.domain.funds import FundDetails, ValuationSource, fee_amount
from assets_tracker.domain.models import TransactionType
from assets_tracker.ui import theme
from assets_tracker.ui.form_helpers import date_edit, labeled, row, to_date
from assets_tracker.ui.formatting import format_quantity, money, parse_decimal, signed_money
from assets_tracker.ui.widgets import RIGHT, prepare_table, table_cell


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


class TradeDialog(QDialog):
    def __init__(
        self, parent: QWidget | None, holding: FundHolding, kind: TransactionType, today: date
    ) -> None:
        super().__init__(parent)
        self._kind, self._holding = kind, holding
        self.setWindowTitle("Souscrire" if kind is TransactionType.BUY else "Racheter")
        self._quantity, self._nav, self._fee = QLineEdit(), QLineEdit(), QLineEdit("0")
        self._date = date_edit(today)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.addWidget(
            QLabel(
                f"{holding.asset.name} · détention : "
                f"{format_quantity(holding.position.quantity)} parts"
            )
        )
        layout.addWidget(
            row(
                labeled("Nombre de parts", self._quantity), labeled("Valeur liquidative", self._nav)
            )
        )
        layout.addWidget(row(labeled("Frais (%)", self._fee), labeled("Date", self._date)))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def form(self) -> tuple[date, Decimal, Decimal, Decimal]:
        quantity = parse_decimal(self._quantity.text(), "Nombre de parts")
        nav = parse_decimal(self._nav.text(), "Valeur liquidative")
        rate = parse_decimal(self._fee.text(), "Frais")
        return to_date(self._date), quantity, nav, fee_amount(quantity, nav, rate)


class FundsPage(QWidget):
    def __init__(self, repository: FundRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repository = repository
        title = QLabel("Fonds")
        title.setProperty("role", "title")
        subtitle = QLabel(
            "Fonds à valeur liquidative : souscriptions, rachats et valorisations manuelles."
        )
        subtitle.setProperty("role", "subtitle")
        add = QPushButton("Nouveau fonds")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._new_fund)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)
        self._table = QTableWidget()
        prepare_table(
            self._table, ["Fonds", "Parts", "PRU", "VL", "Valeur", "+/− value", ""], row_height=52
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.cellDoubleClicked.connect(self._subscribe)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.addLayout(header)
        layout.addWidget(self._table, 1)
        self.refresh()

    def refresh(self) -> None:
        self._holdings = self._repository.list_holdings()
        self._table.setRowCount(len(self._holdings))
        for table_row, holding in enumerate(self._holdings):
            position, valuation = holding.position, holding.valuation
            value = position.quantity * valuation.value if valuation else None
            pnl = value - position.quantity * position.pru if value is not None else None
            self._table.setItem(table_row, 0, table_cell(holding.asset.name))
            self._table.setItem(
                table_row,
                1,
                table_cell(format_quantity(position.quantity), mono=True, align=RIGHT),
            )
            self._table.setItem(
                table_row,
                2,
                table_cell(money(position.pru, holding.asset.currency), mono=True, align=RIGHT),
            )
            vl = "—" if valuation is None else money(valuation.value, holding.asset.currency)
            self._table.setItem(table_row, 3, table_cell(vl, mono=True, align=RIGHT))
            self._table.setItem(
                table_row,
                4,
                table_cell(
                    "—" if value is None else money(value, holding.asset.currency),
                    mono=True,
                    align=RIGHT,
                ),
            )
            self._table.setItem(
                table_row,
                5,
                table_cell(
                    "—" if pnl is None else signed_money(pnl, holding.asset.currency),
                    color=theme.variation_color(pnl or 0),
                    mono=True,
                    align=RIGHT,
                ),
            )
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(4, 4, 4, 4)
            subscribe = QPushButton("Souscrire")
            subscribe.clicked.connect(
                lambda _=False, item=holding: self._open_trade(item, TransactionType.BUY)
            )
            redeem = QPushButton("Racheter")
            redeem.setEnabled(position.quantity > 0)
            redeem.clicked.connect(
                lambda _=False, item=holding: self._open_trade(item, TransactionType.SELL)
            )
            action_layout.addWidget(subscribe)
            action_layout.addWidget(redeem)
            self._table.setCellWidget(table_row, 6, actions)

    def _new_fund(self) -> None:
        dialog = FundDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            name, identifier, currency, details = dialog.form()
            self._repository.create(name, identifier, currency, details)
        except ValueError as error:
            QMessageBox.warning(self, "Fonds invalide", str(error))
        self.refresh()

    def _subscribe(self, row: int, _: int) -> None:
        self._open_trade(self._holdings[row], TransactionType.BUY)

    def _open_trade(self, holding: FundHolding, kind: TransactionType) -> None:
        dialog = TradeDialog(self, holding, kind, date.today())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            when, quantity, nav, fees = dialog.form()
            assert holding.asset.id is not None
            self._repository.add_transaction(holding.asset.id, kind, when, quantity, nav, fees)
            if holding.details.valuation_source is ValuationSource.MANUAL:
                self._repository.record_valuation(holding.asset.id, when, nav, datetime.now())
        except ValueError as error:
            QMessageBox.warning(self, "Opération refusée", str(error))
        self.refresh()
