"""Page « Projection » : intérêts composés avec versement mensuel (estimation indicative)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.domain.projection import (
    MAX_YEARS,
    ProjectionAssumptions,
    YearPoint,
    milestone_years,
    project,
)
from assets_tracker.ui import theme
from assets_tracker.ui.formatting import (
    money,
    parse_decimal,
    percent,
    signed_money,
)
from assets_tracker.ui.projection_chart import ProjectionChart
from assets_tracker.ui.widgets import RIGHT, Card, fit_height, prepare_table, table_cell

CURRENCY = "EUR"
APPROX = "≈ "

# Valeurs de départ. À terme : valeur actuelle du portefeuille et performance annuelle
# historique, dès que la valorisation et l'historique mensuel existent.
DEFAULT_INITIAL = "25 000"
DEFAULT_MONTHLY = "500"
DEFAULT_RATE = "6,0"
DEFAULT_YEARS = 20

DISCLAIMER = (
    "Estimation indicative, jamais une garantie de rendement. Intérêts composés annuels, "
    "versements ajoutés en fin d'année, sans frais ni fiscalité."
)


def _labeled(text: str, widget: QWidget) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    label = QLabel(text)
    label.setProperty("role", "muted")
    layout.addWidget(label)
    layout.addWidget(widget)
    return box


class ProjectionPage(QWidget):
    """Hypothèses à gauche ; résultats, trajectoire et horizons à droite."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        title = QLabel("Projection")
        title.setProperty("role", "title")
        subtitle = QLabel(
            "Intérêts composés avec versement mensuel. Estimation, sans garantie de rendement."
        )
        subtitle.setProperty("role", "subtitle")

        self._initial = QLineEdit(DEFAULT_INITIAL)
        self._monthly = QLineEdit(DEFAULT_MONTHLY)
        self._rate = QLineEdit(DEFAULT_RATE)
        self._years = QSpinBox()
        self._years.setRange(1, MAX_YEARS)
        self._years.setSuffix(" ans")
        self._years.setValue(DEFAULT_YEARS)
        self._error = QLabel()
        self._error.setProperty("role", "error")
        self._error.setWordWrap(True)
        self._error.hide()
        run = QPushButton("Simuler")
        run.setProperty("variant", "primary")
        run.clicked.connect(self.simulate)
        for field in (self._initial, self._monthly, self._rate):
            field.returnPressed.connect(self.simulate)

        caption = QLabel("HYPOTHÈSES")
        caption.setProperty("role", "caption")
        form = QFrame()
        form.setProperty("role", "card")
        form.setFixedWidth(300)
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(20, 18, 20, 20)
        form_layout.setSpacing(14)
        form_layout.addWidget(caption)
        form_layout.addWidget(_labeled("Capital de départ (€)", self._initial))
        form_layout.addWidget(_labeled("Versement mensuel (€)", self._monthly))
        form_layout.addWidget(_labeled("Rendement annuel (%)", self._rate))
        form_layout.addWidget(_labeled("Durée", self._years))
        form_layout.addWidget(self._error)
        form_layout.addWidget(run)
        form_layout.addStretch(1)

        self._final = Card("Capital final")
        self._contributed = Card("Total versé")
        self._interest = Card("Intérêts cumulés")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._final, self._contributed, self._interest):
            cards.addWidget(card, 1)

        legend = QLabel(
            f"<span style='color:{theme.ACCENT}'>■</span> Capital &nbsp;&nbsp; "
            f"<span style='color:{theme.WARNING}'>■</span> Versements &nbsp;&nbsp; "
            f"<span style='color:{theme.TEXT_SUBTLE}'>■</span> Sans versement"
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setProperty("role", "muted")
        chart_caption = QLabel("TRAJECTOIRE")
        chart_caption.setProperty("role", "caption")
        chart_header = QHBoxLayout()
        chart_header.addWidget(chart_caption)
        chart_header.addStretch(1)
        chart_header.addWidget(legend)
        self._chart = ProjectionChart()
        chart_card = QFrame()
        chart_card.setProperty("role", "card")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(20, 16, 20, 16)
        chart_layout.setSpacing(12)
        chart_layout.addLayout(chart_header)
        chart_layout.addWidget(self._chart)

        self._table = QTableWidget()
        self._table.setAccessibleName("Capital projeté par horizon")
        prepare_table(
            self._table,
            ["Horizon", "Versé", "Capital", "Intérêts", "Sans versement"],
            row_height=44,
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        note = QLabel(DISCLAIMER)
        note.setProperty("role", "hint")
        note.setWordWrap(True)

        results = QVBoxLayout()
        results.setSpacing(16)
        results.addLayout(cards)
        results.addWidget(chart_card)
        results.addWidget(self._table)
        results.addWidget(note)
        results.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(form, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(results, 1)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(24)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(subtitle)
        layout.addLayout(heading)
        layout.addLayout(body)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.simulate()

    # -- calcul --

    def assumptions(self) -> ProjectionAssumptions:
        """Lit le formulaire ; lève `ValueError` avec un message lisible."""
        return ProjectionAssumptions(
            initial=parse_decimal(self._initial.text(), "Capital de départ"),
            monthly_contribution=parse_decimal(self._monthly.text(), "Versement mensuel"),
            annual_rate_percent=parse_decimal(self._rate.text(), "Rendement annuel"),
            years=self._years.value(),
        )

    def simulate(self) -> None:
        try:
            assumptions = self.assumptions()
        except ValueError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self._error.hide()
        self._show(assumptions, project(assumptions))

    def _show(self, assumptions: ProjectionAssumptions, points: list[YearPoint]) -> None:
        last = points[-1]
        rate = percent(assumptions.annual_rate_percent)
        self._final.set(
            f"{APPROX}{money(last.balance, CURRENCY)}",
            f"Sans versement : {money(last.balance_without_contributions, CURRENCY)}",
        )
        self._contributed.set(
            money(last.contributed, CURRENCY),
            f"dont {money(assumptions.initial, CURRENCY)} de départ",
        )
        self._interest.set(
            f"{APPROX}{signed_money(last.interest, CURRENCY)}",
            f"à {rate} par an",
            theme.GAIN if last.interest > 0 else theme.TEXT,
        )
        self._chart.set_data(points)

        horizons = milestone_years(assumptions.years)
        self._table.setRowCount(len(horizons))
        for row, year in enumerate(horizons):
            point = points[year]
            self._table.setItem(row, 0, table_cell(f"{year} an{'s' if year > 1 else ''}"))
            self._table.setItem(
                row, 1, table_cell(money(point.contributed, CURRENCY), mono=True, align=RIGHT)
            )
            self._table.setItem(
                row, 2, table_cell(money(point.balance, CURRENCY), mono=True, align=RIGHT)
            )
            self._table.setItem(
                row,
                3,
                table_cell(
                    signed_money(point.interest, CURRENCY),
                    color=theme.GAIN if point.interest > 0 else theme.TEXT_MUTED,
                    mono=True,
                    align=RIGHT,
                ),
            )
            self._table.setItem(
                row,
                4,
                table_cell(
                    money(point.balance_without_contributions, CURRENCY),
                    color=theme.TEXT_MUTED,
                    mono=True,
                    align=RIGHT,
                ),
            )
        fit_height(self._table)

    def results_text(self) -> dict[str, str]:
        """Valeurs affichées, pour les tests."""
        return {
            "final": self._final._value.text(),
            "contributed": self._contributed._value.text(),
            "interest": self._interest._value.text(),
        }
