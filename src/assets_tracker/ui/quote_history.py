"""Fenêtre « Historique des cotations » : courbe, statistiques et liste, avec choix de période."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.domain.history import (
    PERIODS,
    Point,
    daily_points,
    in_period,
    in_range,
    period_stats,
)
from assets_tracker.ui import theme
from assets_tracker.ui.commodity_chart import PositionChart
from assets_tracker.ui.form_helpers import date_edit, to_date
from assets_tracker.ui.formatting import format_number
from assets_tracker.ui.widgets import RIGHT, fit_height, prepare_table, table_cell

TABLE_MAX_HEIGHT = 220
CUSTOM = "Personnalisée"


class QuoteHistoryDialog(QDialog):
    """Cotations enregistrées d'un instrument sur la période choisie (bascule de périodes)."""

    def __init__(
        self,
        parent: QWidget | None,
        name: str,
        points: Sequence[Point],
        today: date,
        format_value: Callable[[Decimal], str],
        *,
        column: str = "Cours",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Historique des cotations")
        self.setMinimumWidth(620)
        self._points = daily_points(points)
        self._today = today
        self._format = format_value

        title = QLabel(name)
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for label, _days in PERIODS:
            button = QPushButton(label)
            button.setProperty("variant", "chip")
            button.setCheckable(True)
            button.setAccessibleName(f"Période : {label}")
            button.clicked.connect(lambda _=False, text=label: self.select_period(text))
            self._group.addButton(button)
            self._buttons[label] = button
            chips.addWidget(button)
        custom = QPushButton(CUSTOM)
        custom.setProperty("variant", "chip")
        custom.setCheckable(True)
        custom.setAccessibleName("Période : personnalisée")
        custom.clicked.connect(lambda _=False: self.select_period(CUSTOM))
        self._group.addButton(custom)
        self._buttons[CUSTOM] = custom
        chips.addWidget(custom)
        chips.addStretch(1)

        first = self._points[0][0] if self._points else today
        self._start = date_edit(first)
        self._start.setAccessibleName("Date de début")
        self._end = date_edit(today)
        self._end.setAccessibleName("Date de fin")
        self._start.dateChanged.connect(lambda _=None: self._range_changed())
        self._end.dateChanged.connect(lambda _=None: self._range_changed())
        self._range_box = QWidget()
        range_row = QHBoxLayout(self._range_box)
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(8)
        for text, edit in (("Du", self._start), ("au", self._end)):
            range_row.addWidget(QLabel(text))
            range_row.addWidget(edit)
        range_row.addStretch(1)

        self._chart = PositionChart()
        self._chart.setAccessibleName("Historique des cotations")
        self._stats: dict[str, QLabel] = {}
        stats = QHBoxLayout()
        stats.setSpacing(10)
        for key in ("Variation", "Dernier", "Plus bas", "Plus haut", "Cotations"):
            stats.addWidget(self._tile(key))

        self._table = QTableWidget()
        self._table.setAccessibleName("Cotations enregistrées")
        prepare_table(self._table, ["Date", column], row_height=30)
        self._empty = QLabel("Aucune cotation enregistrée pour cette période.")
        self._empty.setProperty("role", "muted")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addLayout(chips)
        layout.addWidget(self._range_box)
        layout.addWidget(self._chart)
        layout.addLayout(stats)
        layout.addWidget(self._table)
        layout.addWidget(self._empty)
        layout.addWidget(buttons)
        self.select_period("Tout")

    def _tile(self, key: str) -> QFrame:
        tile = QFrame()
        tile.setStyleSheet(
            f"background: {theme.RAISED}; border: 1px solid {theme.BORDER}; border-radius: 10px;"
        )
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        caption = QLabel(key)
        caption.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; font-size: 12px; border: none;")
        value = QLabel("—")
        value.setFont(theme.mono_font(13))
        value.setStyleSheet("border: none;")
        value.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(value)
        self._stats[key] = value
        return tile

    def period(self) -> str:
        checked = self._group.checkedButton()
        return checked.text() if checked is not None else "Tout"

    def visible_points(self) -> list[Point]:
        label = self.period()
        if label == CUSTOM:
            return in_range(self._points, to_date(self._start), to_date(self._end))
        return in_period(self._points, self._today, dict(PERIODS)[label])

    def set_range(self, start: date, end: date) -> None:
        """Choisit la période personnalisée du `start` au `end` inclus."""
        for edit, day in ((self._start, start), (self._end, end)):
            edit.blockSignals(True)
            edit.setDate(QDate(day.year, day.month, day.day))
            edit.blockSignals(False)
        self.select_period(CUSTOM)

    def _range_changed(self) -> None:
        if self.period() == CUSTOM:
            self.select_period(CUSTOM)

    def select_period(self, label: str) -> None:
        self._buttons[label].setChecked(True)
        self._range_box.setVisible(label == CUSTOM)
        points = self.visible_points()
        self._chart.set_data(points, None)
        stats = period_stats(points)
        if stats is None:
            for value in self._stats.values():
                value.setText("—")
                value.setStyleSheet("border: none;")
        else:
            change, pct = stats.change, stats.change_pct
            self._stats["Variation"].setText(
                f"{'+' if change > 0 else ''}{format_number(change)}"
                + ("" if pct is None else f" ({'+' if pct > 0 else ''}{format_number(pct, 1)} %)")
            )
            self._stats["Variation"].setStyleSheet(
                f"color: {theme.variation_color(change)}; border: none;"
            )
            for key, point in (
                ("Dernier", stats.last),
                ("Plus bas", stats.low),
                ("Plus haut", stats.high),
            ):
                self._stats[key].setText(f"{self._format(point[1])}\n{point[0]:%d/%m/%Y}")
            self._stats["Cotations"].setText(str(stats.count))
        newest_first = list(reversed(points))
        self._table.setRowCount(len(newest_first))
        for row_index, (when, value) in enumerate(newest_first):
            self._table.setItem(row_index, 0, table_cell(f"{when:%d/%m/%Y}"))
            self._table.setItem(
                row_index, 1, table_cell(self._format(value), mono=True, align=RIGHT)
            )
        fit_height(self._table, TABLE_MAX_HEIGHT)
        self._table.setVisible(bool(points))
        self._empty.setVisible(not points)
