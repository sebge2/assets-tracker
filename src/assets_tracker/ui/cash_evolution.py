"""Évolution mois par mois des comptes cash et de leur total."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.domain.cash import (
    ZERO,
    CashAccount,
    CashMovement,
    first_month,
    monthly_balances,
    months_range,
    sum_balances,
    trailing_months,
)
from assets_tracker.ui import theme
from assets_tracker.ui.evolution_chart import ChartSeries, EvolutionChart
from assets_tracker.ui.formatting import MINUS, money, month_label, signed_money
from assets_tracker.ui.widgets import RIGHT, fit_height, prepare_table, table_cell

_PERIODS = (("12 mois", 12), ("36 mois", 36), ("Tout", 0))
_TABLE_MAX_HEIGHT = 340  # au-delà (≈ 7 mois), le tableau défile


@dataclass(frozen=True, slots=True)
class EvolutionInput:
    """Un compte et ses mouvements : tout le reste est recalculé."""

    account: CashAccount
    movements: list[CashMovement]


class EvolutionView(QWidget):
    """Courbes et tableau des soldes de fin de mois, par compte et en total.

    Les devises ne sont jamais additionnées : le total est celui de la devise choisie.
    """

    def __init__(self) -> None:
        super().__init__()
        self._inputs: list[EvolutionInput] = []
        self._today = date.today()
        self._period = 12

        chips = QHBoxLayout()
        chips.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for label, months in _PERIODS:
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setProperty("variant", "chip")
            chip.setChecked(months == self._period)
            chip.clicked.connect(lambda _=False, m=months: self._set_period(m))
            group.addButton(chip)
            chips.addWidget(chip)
        self._currency = QComboBox()
        self._currency.setAccessibleName("Devise affichée")
        self._currency.currentIndexChanged.connect(lambda _: self._render())
        self._currency.hide()
        self._legend = QLabel()
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setProperty("role", "muted")
        controls = QHBoxLayout()
        controls.addLayout(chips)
        controls.addWidget(self._currency)
        controls.addStretch(1)
        controls.addWidget(self._legend)

        caption = QLabel("SOLDES DE FIN DE MOIS")
        caption.setProperty("role", "caption")
        self._chart = EvolutionChart()
        self._chart_card = QFrame()
        self._chart_card.setProperty("role", "card")
        card_layout = QVBoxLayout(self._chart_card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(12)
        card_layout.addWidget(caption)
        card_layout.addWidget(self._chart)

        self._table = QTableWidget()
        self._table.setAccessibleName("Soldes mensuels par compte")
        self._table.setWordWrap(False)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._empty = QLabel("Aucun compte dans cette devise.")
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addLayout(controls)
        layout.addWidget(self._chart_card)
        layout.addWidget(self._table)
        layout.addWidget(self._empty)

    # -- données --

    def set_inputs(self, inputs: list[EvolutionInput], today: date) -> None:
        self._inputs = inputs
        self._today = today
        currencies = sorted({i.account.currency for i in inputs}) or ["EUR"]
        previous = self._currency.currentText()
        self._currency.blockSignals(True)
        self._currency.clear()
        self._currency.addItems(currencies)
        if previous in currencies:
            self._currency.setCurrentText(previous)
        self._currency.blockSignals(False)
        self._currency.setVisible(len(currencies) > 1)
        self._render()

    def _set_period(self, months: int) -> None:
        self._period = months
        self._render()

    def months(self) -> list[date]:
        """Mois affichés pour la devise courante (jamais avant l'existence du premier compte)."""
        selected = self._selected()
        if not selected:
            return []
        earliest = min(first_month(i.account.opened_on, i.movements) for i in selected)
        if self._period == 0:
            return months_range(earliest, self._today)
        window = trailing_months(self._today, self._period)
        return [m for m in window if m >= earliest] or [window[-1]]

    def _selected(self) -> list[EvolutionInput]:
        currency = self._currency.currentText()
        return [i for i in self._inputs if i.account.currency == currency]

    # -- rendu --

    def _render(self) -> None:
        selected = self._selected()
        months = self.months()
        currency = self._currency.currentText() or "EUR"
        series = [
            ChartSeries(
                item.account.name,
                theme.SERIES[index % len(theme.SERIES)],
                monthly_balances(item.account.opened_on, item.movements, months, self._today),
            )
            for index, item in enumerate(selected)
        ]
        total = sum_balances([s.balances for s in series], len(months))
        self._chart.set_data(months, series, total, currency)
        legend = " &nbsp;&nbsp; ".join(
            f"<span style='color:{s.color}'>■</span> {s.name}" for s in series
        )
        total_key = f"<span style='color:{theme.ACCENT}'>■</span> <b>Total</b>"
        self._legend.setText(f"{legend} &nbsp;&nbsp; {total_key}" if series else "")

        self._table.setVisible(bool(selected))
        self._empty.setVisible(not selected)
        self._chart_card.setVisible(bool(selected))
        if selected:
            self._fill_table(series, months, total, currency)

    def _fill_table(
        self, series: list[ChartSeries], months: list[date], total: list[Decimal], currency: str
    ) -> None:
        headers = ["Mois", *(s.name for s in series), "Total", "Variation"]
        prepare_table(self._table, headers, row_height=44)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.setRowCount(len(months))
        total_column = 1 + len(series)
        for row, index in enumerate(reversed(range(len(months)))):
            label = month_label(months[index])
            if months[index].year == self._today.year and months[index].month == self._today.month:
                label += " (en cours)"
            self._table.setItem(row, 0, table_cell(label))
            for column, serie in enumerate(series, start=1):
                balance = serie.balances[index]
                self._table.setItem(
                    row,
                    column,
                    table_cell(
                        "—" if balance is None else money(balance, currency),
                        color=theme.TEXT_SUBTLE if balance is None else theme.TEXT_MUTED,
                        mono=True,
                        align=RIGHT,
                    ),
                )
            self._table.setItem(
                row, total_column, table_cell(money(total[index], currency), mono=True, align=RIGHT)
            )
            if index == 0:
                text, color = "—", theme.TEXT_MUTED
            else:
                delta = total[index] - total[index - 1]
                if delta > ZERO:
                    text, color = f"▲ {signed_money(delta, currency)}", theme.GAIN
                elif delta < ZERO:
                    text, color = f"▼ {MINUS}{money(-delta, currency)}", theme.LOSS
                else:
                    text, color = money(delta, currency), theme.TEXT_MUTED
            self._table.setItem(
                row, total_column + 1, table_cell(text, color=color, mono=True, align=RIGHT)
            )
            for column in range(self._table.columnCount()):
                item = self._table.item(row, column)
                if item is not None:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        fit_height(self._table, max_height=_TABLE_MAX_HEIGHT)
