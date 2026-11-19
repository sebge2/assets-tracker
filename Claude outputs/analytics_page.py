"""Écran Analyses : rendement par année civile, contributions, gains réalisés et revenus.

Lecture seule, recalculé depuis les mêmes sources que le Portefeuille. Les assurances & pensions
n'en font pas partie. La période choisie (« Depuis le début » ou une année) pilote les cartes,
la contribution par type et les moteurs / freins.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.domain.analytics import (
    Analysis,
    Performance,
    Unit,
    YearResult,
    analyze,
    capital_invested,
    gains_by_type,
    unit_gains,
    units_from,
)
from assets_tracker.domain.models import InstrumentType
from assets_tracker.domain.portfolio import PortfolioInput
from assets_tracker.ui import theme
from assets_tracker.ui.analytics_charts import ReturnBars, ShareArea, StackedBars
from assets_tracker.ui.formatting import (
    ASSET_LABELS,
    NBSP,
    format_number,
    money,
    signed_money,
)
from assets_tracker.ui.widgets import RIGHT, Card, PageContent, fit_height, prepare_table, table_cell

ZERO = Decimal(0)
HUNDRED = Decimal(100)
_ROW_HEIGHT = 36
_TOP_MOVERS = 3

# Libellés des sources de revenus : le cash rapporte des intérêts, les obligations des coupons.
INCOME_LABELS = {
    InstrumentType.CASH: "Intérêts cash",
    InstrumentType.BOND: "Coupons",
    InstrumentType.TERM_NOTE: "Bons de caisse",
    InstrumentType.FUND: "Fonds",
    InstrumentType.STOCK: "Actions",
    InstrumentType.COMMODITY: "Matières premières",
    InstrumentType.CRYPTO: "Crypto",
}
_TABS = (("Performance", 0), ("Revenus", 1))


def _signed_percent(value: Decimal | None) -> str:
    if value is None:
        return "—"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{format_number(value)}{NBSP}%"


def _signed_points(value: Decimal) -> str:
    prefix = "+" if value > 0 else ""
    return f"{prefix}{format_number(value)}{NBSP}pt"


def _color(value: Decimal | None) -> str:
    return theme.TEXT_MUTED if value is None else theme.variation_color(value)


def _caption(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setProperty("role", "caption")
    return label


def _section(caption: str, *widgets: QWidget) -> QFrame:
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)
    layout.addWidget(_caption(caption))
    for widget in widgets:
        layout.addWidget(widget)
    return frame


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "muted")
    label.setWordWrap(True)
    return label


def _clear(layout: QHBoxLayout | QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


class AnalyticsPage(QWidget):
    """Performance et revenus du portefeuille, année par année."""

    def __init__(
        self,
        load: Callable[[], PortfolioInput],
        *,
        today: Callable[[], date] = date.today,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._load = load
        self._today = today
        self._units: list[Unit] = []
        self._analysis: Analysis | None = None
        self._year: int | None = None  # None : depuis le début
        self._table_years: list[int | None] = []

        title = QLabel("Analyses")
        title.setProperty("role", "title")
        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "subtitle")
        header = QVBoxLayout()
        header.setSpacing(4)
        header.addWidget(title)
        header.addWidget(self._subtitle)

        self._stack = QStackedWidget()
        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        tab_group = QButtonGroup(self)
        tab_group.setExclusive(True)
        self._tab_buttons: list[QPushButton] = []
        for label, index in _TABS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("variant", "chip")
            button.setChecked(index == 0)
            button.clicked.connect(lambda _=False, i=index: self._stack.setCurrentIndex(i))
            tab_group.addButton(button)
            self._tab_buttons.append(button)
            tabs.addWidget(button)
        tabs.addStretch(1)

        self._period_row = QHBoxLayout()
        self._period_row.setSpacing(8)
        self._period_group = QButtonGroup(self)
        self._period_group.setExclusive(True)
        self._period_chips: dict[int | None, QPushButton] = {}
        self._period_label = _caption("Période")
        self._period_box = QWidget()
        period_layout = QHBoxLayout(self._period_box)
        period_layout.setContentsMargins(0, 0, 0, 0)
        period_layout.setSpacing(12)
        period_layout.addWidget(self._period_label)
        period_layout.addLayout(self._period_row, 1)

        self._stack.addWidget(self._build_performance())
        self._stack.addWidget(self._build_income())

        self._empty = QLabel(
            "Rien à analyser pour l'instant : ajoutez un compte cash, une position ou un bon de caisse."
        )
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)

        content = PageContent()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(20)
        layout.addLayout(header)
        layout.addLayout(tabs)
        layout.addWidget(self._stack)
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

    # -- construction --

    def _build_performance(self) -> QWidget:
        self._return_card = Card("Rendement")
        self._gain_card = Card("Gain")
        self._flow_card = Card("Apports nets")
        self._value_card = Card("Valeur")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._return_card, self._gain_card, self._flow_card, self._value_card):
            cards.addWidget(card, 1)

        self._return_bars = ReturnBars()
        self._return_bars.year_clicked.connect(self._select_year)
        self._years_table = QTableWidget()
        self._years_table.cellClicked.connect(self._on_years_row)
        self._grid_table = QTableWidget()
        self._realized_table = QTableWidget()
        self._contribution_table = QTableWidget()
        self._movers_table = QTableWidget()
        self._drags_table = QTableWidget()
        self._area = ShareArea()
        self._contribution_note = _note("")
        self._area_legend = QHBoxLayout()
        self._area_legend.setSpacing(16)

        movers = QHBoxLayout()
        movers.setSpacing(16)
        movers.addWidget(_section("Moteurs", self._movers_table), 1)
        movers.addWidget(_section("Freins", self._drags_table), 1)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        layout.addWidget(self._period_box)
        layout.addLayout(cards)
        layout.addWidget(
            _section(
                "Rendement par année civile",
                self._return_bars,
                _note(
                    "Rendement pondéré par le temps : les apports et retraits ne comptent pas "
                    "comme une performance. Cliquez sur une année pour la détailler."
                ),
            )
        )
        layout.addWidget(_section("Détail par année", self._years_table))
        layout.addWidget(_section("Rendement par type d'actif et par année", self._grid_table))
        layout.addWidget(
            _section("Contribution à la performance", self._contribution_table, self._contribution_note)
        )
        layout.addLayout(movers)
        layout.addWidget(
            _section(
                "Gain réalisé et latent",
                self._realized_table,
                _note(
                    "Réalisé : plus-values de vente, intérêts et coupons encaissés. "
                    "Latent : variation des plus-values non encore encaissées."
                ),
            )
        )
        layout.addWidget(
            _section("Répartition dans le temps", self._area, self._wrap(self._area_legend))
        )
        return page

    def _build_income(self) -> QWidget:
        self._income_year_card = Card("Revenus de l'année en cours")
        self._income_12_card = Card("12 derniers mois")
        self._yield_card = Card("Rendement courant")
        self._main_card = Card("Source principale")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (
            self._income_year_card,
            self._income_12_card,
            self._yield_card,
            self._main_card,
        ):
            cards.addWidget(card, 1)
        self._income_bars = StackedBars()
        self._income_legend = QHBoxLayout()
        self._income_legend.setSpacing(16)
        self._income_table = QTableWidget()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        layout.addLayout(cards)
        layout.addWidget(
            _section("Revenus encaissés par année", self._income_bars, self._wrap(self._income_legend))
        )
        layout.addWidget(
            _section(
                "Détail par source",
                self._income_table,
                _note(
                    "Intérêts des comptes cash, coupons d'obligations et intérêts des bons de "
                    "caisse. Les dividendes d'actions ne sont pas encore saisis."
                ),
            )
        )
        return page

    @staticmethod
    def _wrap(layout: QHBoxLayout) -> QWidget:
        box = QWidget()
        box.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return box

    # -- données --

    def refresh(self) -> None:
        self._units = units_from(self._load())
        self._analysis = analyze(self._units, self._today())
        analysis = self._analysis
        has_data = analysis is not None
        self._stack.setVisible(has_data)
        for button in self._tab_buttons:
            button.setVisible(has_data)
        self._empty.setVisible(not has_data)
        if analysis is None:
            self._subtitle.setText("Aucune donnée")
            return
        years = [y.year for y in analysis.years]
        if self._year not in years:
            self._year = None
        first = analysis.years[0].year
        self._subtitle.setText(
            f"Depuis {first} · valeur actuelle {money(analysis.value, 'EUR')} · "
            "hors assurances et pensions"
        )
        self._rebuild_periods(years)
        self._render()

    def _rebuild_periods(self, years: list[int]) -> None:
        for button in self._period_chips.values():
            self._period_group.removeButton(button)
        _clear(self._period_row)
        self._period_chips = {}
        for year, label in [(None, "Depuis le début"), *[(y, str(y)) for y in years]]:
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setProperty("variant", "chip")
            chip.setChecked(year == self._year)
            chip.clicked.connect(lambda _=False, y=year: self._select_year(y))
            self._period_group.addButton(chip)
            self._period_chips[year] = chip
            self._period_row.addWidget(chip)
        self._period_row.addStretch(1)

    def _select_year(self, year: int | None) -> None:
        self._year = year
        chip = self._period_chips.get(year)
        if chip is not None:
            chip.setChecked(True)
        self._render()

    def _on_years_row(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._table_years):
            self._select_year(self._table_years[row])

    def selected_year(self) -> int | None:
        return self._year

    # -- rendu --

    def _span(self, analysis: Analysis) -> tuple[date, date, Performance, str]:
        if self._year is None:
            sub = (
                f"{_signed_percent(analysis.annualized)} par an"
                if analysis.annualized is not None
                else "Période trop courte"
            )
            return analysis.total_start, analysis.total_end, analysis.total, sub
        result = next(y for y in analysis.years if y.year == self._year)
        part = " (en cours)" if result.end_day < date(result.year, 12, 31) else ""
        return result.start_day, result.end_day, result.performance, f"Année {result.year}{part}"

    def _render(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        start, end, perf, sub = self._span(analysis)
        self._return_card.set(_signed_percent(perf.twr), sub, _color(perf.twr))
        self._gain_card.set(
            signed_money(perf.gain, "EUR"),
            "Valeur finale − initiale − apports + revenus",
            _color(perf.gain),
        )
        self._flow_card.set(
            signed_money(perf.contributions, "EUR"), "Dépôts et achats, moins retraits et ventes"
        )
        self._value_card.set(money(perf.end, "EUR"), f"Début de période : {money(perf.start, 'EUR')}")
        self._fill_bars(analysis)
        self._fill_years(analysis)
        self._fill_grid(analysis)
        self._fill_contribution(start, end)
        self._fill_realized(analysis)
        self._fill_area(analysis)
        self._fill_income(analysis)

    def _fill_bars(self, analysis: Analysis) -> None:
        self._return_bars.set_data(
            [y.year for y in analysis.years], [y.performance.twr for y in analysis.years]
        )
        self._return_bars.set_selected(self._year)

    def _fill_years(self, analysis: Analysis) -> None:
        table = self._years_table
        prepare_table(
            table,
            ["Période", "Valeur initiale", "Apports nets", "Gain", "Rendement", "Valeur finale"],
            row_height=_ROW_HEIGHT,
        )
        rows: list[tuple[int | None, str, Performance]] = [
            (None, "Depuis le début", analysis.total),
            *[(y.year, str(y.year), y.performance) for y in analysis.years],
        ]
        self._table_years = [key for key, _, _ in rows]
        table.setRowCount(len(rows))
        for row, (key, label, perf) in enumerate(rows):
            weight = key == self._year
            table.setItem(row, 0, table_cell(label, color=theme.ACCENT if weight else theme.TEXT))
            table.setItem(row, 1, table_cell(money(perf.start, "EUR"), mono=True, align=RIGHT))
            table.setItem(
                row, 2, table_cell(signed_money(perf.contributions, "EUR"), mono=True, align=RIGHT)
            )
            table.setItem(
                row,
                3,
                table_cell(
                    signed_money(perf.gain, "EUR"), color=_color(perf.gain), mono=True, align=RIGHT
                ),
            )
            table.setItem(
                row,
                4,
                table_cell(
                    _signed_percent(perf.twr), color=_color(perf.twr), mono=True, align=RIGHT
                ),
            )
            table.setItem(row, 5, table_cell(money(perf.end, "EUR"), mono=True, align=RIGHT))
        fit_height(table)

    def _fill_grid(self, analysis: Analysis) -> None:
        table = self._grid_table
        headers = ["Type", *[str(y.year) for y in analysis.years]]
        prepare_table(table, headers, row_height=_ROW_HEIGHT)
        table.setRowCount(len(analysis.types))
        for row, kind in enumerate(analysis.types):
            table.setItem(row, 0, table_cell(ASSET_LABELS[kind]))
            for column, result in enumerate(analysis.years, start=1):
                value = result.by_type.get(kind)
                table.setItem(
                    row,
                    column,
                    table_cell(
                        _signed_percent(value), color=_color(value), mono=True, align=RIGHT
                    ),
                )
        fit_height(table)

    def _fill_contribution(self, start: date, end: date) -> None:
        base = capital_invested(self._units, start, end)
        gains = gains_by_type(self._units, start, end)
        kinds = sorted(gains, key=lambda kind: gains[kind], reverse=True)
        prepare_table(
            self._contribution_table,
            ["Type", "Gain", "Contribution"],
            row_height=_ROW_HEIGHT,
        )
        self._contribution_table.setRowCount(len(kinds))
        for row, kind in enumerate(kinds):
            gain = gains[kind]
            points = None if base is None else gain / base * HUNDRED
            self._contribution_table.setItem(row, 0, table_cell(ASSET_LABELS[kind]))
            self._contribution_table.setItem(
                row,
                1,
                table_cell(signed_money(gain, "EUR"), color=_color(gain), mono=True, align=RIGHT),
            )
            self._contribution_table.setItem(
                row,
                2,
                table_cell(
                    "—" if points is None else _signed_points(points),
                    color=_color(points),
                    mono=True,
                    align=RIGHT,
                ),
            )
        fit_height(self._contribution_table)
        self._contribution_note.setText(
            "Points de rendement sur le capital investi (valeur de début + apports) : "
            + (money(base, "EUR") if base is not None else "indisponible")
            + "."
        )
        ranked = unit_gains(self._units, start, end)
        self._fill_movers(self._movers_table, [g for g in ranked if g[1] > ZERO][:_TOP_MOVERS])
        losses = [g for g in reversed(ranked) if g[1] < ZERO][:_TOP_MOVERS]
        self._fill_movers(self._drags_table, losses)

    @staticmethod
    def _fill_movers(table: QTableWidget, rows: list[tuple[Unit, Decimal]]) -> None:
        prepare_table(table, ["Ligne", "Gain"], row_height=_ROW_HEIGHT)
        table.setRowCount(len(rows))
        for row, (unit, gain) in enumerate(rows):
            table.setItem(row, 0, table_cell(unit.name))
            table.setItem(
                row,
                1,
                table_cell(signed_money(gain, "EUR"), color=_color(gain), mono=True, align=RIGHT),
            )
        fit_height(table)

    def _fill_realized(self, analysis: Analysis) -> None:
        table = self._realized_table
        prepare_table(table, ["Année", "Gain total", "Réalisé", "Latent"], row_height=_ROW_HEIGHT)
        years: list[YearResult] = analysis.years
        table.setRowCount(len(years))
        for row, result in enumerate(years):
            table.setItem(row, 0, table_cell(str(result.year)))
            for column, amount in enumerate(
                (result.performance.gain, result.realized, result.latent), start=1
            ):
                table.setItem(
                    row,
                    column,
                    table_cell(
                        signed_money(amount, "EUR"), color=_color(amount), mono=True, align=RIGHT
                    ),
                )
        fit_height(table)

    def _fill_area(self, analysis: Analysis) -> None:
        points = analysis.allocation
        labels = [str(day.year) if day.month == 12 else f"{day:%d/%m/%y}" for day, _ in points]
        series: list[tuple[str, str, list[Decimal]]] = []
        for kind in reversed(analysis.types):
            shares: list[Decimal] = []
            for _, values in points:
                total = sum(values.values(), ZERO)
                shares.append(values.get(kind, ZERO) / total * HUNDRED if total > ZERO else ZERO)
            series.append((ASSET_LABELS[kind], theme.ASSET_COLORS[kind.value], shares))
        self._area.set_data(labels, series)
        self._set_legend(self._area_legend, [(name, color) for name, color, _ in series])

    @staticmethod
    def _set_legend(layout: QHBoxLayout, entries: list[tuple[str, str]]) -> None:
        _clear(layout)
        for name, color in entries:
            label = QLabel(f"<span style='color:{color}'>●</span> {name}")
            label.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(label)
        layout.addStretch(1)

    def _fill_income(self, analysis: Analysis) -> None:
        years = analysis.years
        kinds = sorted(
            {kind for y in years for kind in y.income},
            key=lambda kind: -sum((y.income.get(kind, ZERO) for y in years), ZERO),
        )
        totals = [sum(y.income.values(), ZERO) for y in years]
        current = totals[-1]
        grand = {kind: sum((y.income.get(kind, ZERO) for y in years), ZERO) for kind in kinds}
        self._income_year_card.set(money(current, "EUR"), f"Année {years[-1].year}, à ce jour")
        self._income_12_card.set(money(analysis.income_last_12_months, "EUR"), "Revenus encaissés")
        yield_text = (
            f"{format_number(analysis.income_last_12_months / analysis.value * HUNDRED)}{NBSP}%"
            if analysis.value > ZERO
            else "—"
        )
        self._yield_card.set(yield_text, "12 derniers mois ÷ valeur actuelle")
        main = max(kinds, key=lambda kind: grand[kind], default=None)
        self._main_card.set(
            "—" if main is None else INCOME_LABELS[main],
            "Aucun revenu enregistré"
            if main is None
            else f"{money(grand[main], 'EUR')} depuis le début",
        )
        self._income_bars.set_data(
            [y.year for y in years],
            [
                [
                    (INCOME_LABELS[kind], theme.ASSET_COLORS[kind.value], y.income.get(kind, ZERO))
                    for kind in kinds
                ]
                for y in years
            ],
        )
        self._set_legend(
            self._income_legend,
            [(INCOME_LABELS[kind], theme.ASSET_COLORS[kind.value]) for kind in kinds],
        )
        table = self._income_table
        prepare_table(
            table,
            ["Année", *[INCOME_LABELS[k] for k in kinds], "Total", "vs année préc."],
            row_height=_ROW_HEIGHT,
        )
        table.setRowCount(len(years))
        for row, result in enumerate(years):
            table.setItem(row, 0, table_cell(str(result.year)))
            for column, kind in enumerate(kinds, start=1):
                table.setItem(
                    row,
                    column,
                    table_cell(money(result.income.get(kind, ZERO), "EUR"), mono=True, align=RIGHT),
                )
            total = totals[row]
            table.setItem(
                row, len(kinds) + 1, table_cell(money(total, "EUR"), mono=True, align=RIGHT)
            )
            previous = totals[row - 1] if row else ZERO
            change = (total - previous) / previous * HUNDRED if previous > ZERO else None
            table.setItem(
                row,
                len(kinds) + 2,
                table_cell(_signed_percent(change), color=_color(change), mono=True, align=RIGHT),
            )
        fit_height(table)
