"""Écran Assurances & pensions : épargne-pension et assurance groupe, valeurs projetées par année.

Ces contrats ne comptent pas dans le portefeuille. Leur valeur est celle du dernier relevé,
saisi à la main ; chaque relevé garde la valeur projetée à la retraite, ce qui donne l'historique
des projections à actualiser une fois par an.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
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

from assets_tracker.db.insurance_repository import ContractHolding, InsuranceRepository
from assets_tracker.domain.insurance import (
    ContractDetails,
    ContractKind,
    ProjectionStep,
    ceiling_left,
    gain,
    gain_percent,
    is_stale,
    latest_projection,
    latest_statement,
    paid_in_year,
    projection_history,
    total_paid,
    totals,
    validate_details,
)
from assets_tracker.ui import theme
from assets_tracker.ui.form_helpers import date_edit, labeled, row, to_date
from assets_tracker.ui.formatting import (
    compact_money,
    money,
    parse_decimal,
    percent,
    signed_money,
)
from assets_tracker.ui.widgets import RIGHT, Card, PageContent, fit_height, prepare_table

CURRENCY = "EUR"
WIDE_LAYOUT = 1340  # largeur de page à partir de laquelle la fiche passe à droite du tableau
DETAIL_WIDTH = 400
KIND_COLORS = {
    ContractKind.PENSION_SAVINGS: "#B38CFF",
    ContractKind.GROUP_INSURANCE: "#4FD1C5",
}
FACT_CAPTIONS = (
    "Plus-value",
    "Rendement total",
    "Ouverture",
    "Départ à la retraite",
    "Dernier relevé",
    "N° de contrat",
)


# --- Données affichées ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContractView:
    holding: ContractHolding
    paid: Decimal
    value: Decimal | None  # None tant qu'aucun relevé n'est saisi
    projected: Decimal | None
    statement_on: date | None
    stale: bool

    @property
    def contract_id(self) -> int:
        return self.holding.id

    @property
    def details(self) -> ContractDetails:
        return self.holding.details


def build_view(holding: ContractHolding, today: date) -> ContractView:
    last = latest_statement(holding.statements)
    projection = latest_projection(holding.statements)
    return ContractView(
        holding,
        total_paid(holding.payments),
        None if last is None else last.current_value,
        None if projection is None else projection.projected_value,
        None if last is None else last.recorded_on,
        is_stale(holding.statements, today),
    )


def stale_message(view: ContractView, today: date) -> str:
    name = view.details.name
    if view.statement_on is None:
        return f"{name} n'a pas encore de relevé : saisissez la valeur et la projection."
    days = (today - view.statement_on).days
    return (
        f"Le dernier relevé de {name} date du {view.statement_on:%d/%m/%Y} ({days} jours). "
        "Actualisez la valeur projetée."
    )


# --- Widgets ----------------------------------------------------------------


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


def _plain(value: Decimal) -> str:
    """Nombre saisissable : virgule décimale, sans zéros inutiles."""
    return format(value.normalize(), "f").replace(".", ",")


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


class _ProjectionChart(QWidget):
    """Valeur projetée à chaque relevé : une ligne dans le temps, un point par actualisation."""

    HEIGHT = 150

    def __init__(self) -> None:
        super().__init__()
        self._points: list[tuple[date, Decimal]] = []
        self.setMinimumHeight(self.HEIGHT)
        self.setMaximumHeight(self.HEIGHT)
        self.setAccessibleName("Historique des valeurs projetées")

    def set_points(self, points: list[tuple[date, Decimal]]) -> None:
        self._points = sorted(points)
        self.update()

    def points(self) -> list[tuple[date, Decimal]]:
        return list(self._points)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        if not self._points:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, top, bottom = 8.0, 8.0, 22.0, 20.0
        width = self.width() - left - right
        height = self.height() - top - bottom
        values = [float(v) for _, v in self._points]
        low, high = min(values), max(values)
        if high == low:
            low, high = low * 0.9, high * 1.1 or 1.0
        first, last = self._points[0][0].toordinal(), self._points[-1][0].toordinal()

        def at(moment: date, value: Decimal) -> QPointF:
            span = last - first
            x = left + (width / 2 if span == 0 else (moment.toordinal() - first) / span * width)
            y = top + height * (1 - (float(value) - low) / (high - low))
            return QPointF(x, y)

        positions = [at(moment, value) for moment, value in self._points]
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.drawLine(QPointF(left, top + height), QPointF(left + width, top + height))
        painter.setPen(QPen(QColor(theme.ACCENT), 2.4))
        for start, end in zip(positions, positions[1:], strict=False):
            painter.drawLine(start, end)
        painter.setBrush(QColor(theme.ACCENT))
        painter.setPen(Qt.PenStyle.NoPen)
        for position in positions:
            painter.drawEllipse(position, 4.0, 4.0)
        painter.setFont(theme.mono_font(11))
        painter.setPen(QColor(theme.TEXT_MUTED))
        count = len(self._points)
        for index, ((moment, value), position) in enumerate(
            zip(self._points, positions, strict=True)
        ):
            if index in (0, count - 1) or count <= 4:
                painter.drawText(
                    QRectF(position.x() - 45, position.y() - 20, 90, 14),
                    Qt.AlignmentFlag.AlignCenter,
                    compact_money(value, CURRENCY),
                )
            painter.drawText(
                QRectF(position.x() - 25, self.height() - 16, 50, 14),
                Qt.AlignmentFlag.AlignCenter,
                str(moment.year),
            )


class _Gauge(QWidget):
    """Barre de progression du plafond annuel de versement."""

    def __init__(self) -> None:
        super().__init__()
        self._ratio = 0.0
        self._full = False
        self.setFixedHeight(8)

    def set_value(self, paid: Decimal, ceiling: Decimal) -> None:
        self._ratio = min(1.0, float(paid / ceiling)) if ceiling > 0 else 0.0
        self._full = paid >= ceiling
        self.update()

    def ratio(self) -> float:
        return self._ratio

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BORDER_STRONG))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), 8), 4, 4)
        painter.setBrush(QColor(theme.GAIN if self._full else theme.ACCENT))
        painter.drawRoundedRect(QRectF(0, 0, self.width() * self._ratio, 8), 4, 4)


# --- Dialogues --------------------------------------------------------------


class ContractDialog(QDialog):
    """Création ou modification d'un contrat (épargne-pension ou assurance groupe)."""

    def __init__(
        self, parent: QWidget | None, today: date, holding: ContractHolding | None = None
    ) -> None:
        super().__init__(parent)
        editing = holding is not None
        self.setWindowTitle("Modifier le contrat" if editing else "Nouveau contrat")
        self.setMinimumWidth(520)
        details = holding.details if holding else None
        self._kind = QComboBox()
        for kind in ContractKind:
            self._kind.addItem(kind.label, kind)
        if details is not None:
            self._kind.setCurrentIndex(list(ContractKind).index(details.kind))
        self._name = QLineEdit(details.name if details else "")
        self._insurer = QLineEdit(details.insurer if details else "")
        self._number = QLineEdit(details.number if details else "")
        self._opened = date_edit(details.opened_on if details else today)
        self._has_retirement = QCheckBox("Départ à la retraite prévu")
        self._has_retirement.setChecked(bool(details and details.retirement_on))
        self._retirement = date_edit(
            details.retirement_on
            if details and details.retirement_on
            else date(today.year + 20, 1, 1)
        )
        self._ceiling = QLineEdit(_plain(details.ceiling) if details and details.ceiling else "")
        self._ceiling_box = labeled(
            "Plafond annuel de versement (€)",
            self._ceiling,
            "Facultatif : alimente la jauge « versé cette année ».",
        )
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {theme.LOSS};")

        title = QLabel(self.windowTitle())
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        note = QLabel(
            "Pas de cours : la valeur du contrat est celle de votre dernier relevé, à actualiser "
            "chaque année avec la valeur projetée. Ce contrat n'entre pas dans le portefeuille."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(labeled("Type de contrat", self._kind))
        layout.addWidget(row(labeled("Nom", self._name), labeled("Assureur", self._insurer)))
        layout.addWidget(
            row(
                labeled("N° de contrat (facultatif)", self._number),
                labeled("Date d'ouverture", self._opened),
            )
        )
        layout.addWidget(self._has_retirement)
        layout.addWidget(labeled("Date de départ à la retraite", self._retirement))
        layout.addWidget(self._ceiling_box)
        layout.addWidget(note)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer" if editing else "Ajouter le contrat"))
        self._kind.currentIndexChanged.connect(self._on_kind)
        self._has_retirement.toggled.connect(self._retirement.setEnabled)
        self._retirement.setEnabled(self._has_retirement.isChecked())
        self._on_kind()

    def _on_kind(self, *_: object) -> None:
        self._ceiling_box.setVisible(
            ContractKind(self._kind.currentData()) is ContractKind.PENSION_SAVINGS
        )

    def form(self) -> ContractDetails:
        kind = ContractKind(self._kind.currentData())
        ceiling_text = self._ceiling.text().strip()
        ceiling = (
            parse_decimal(ceiling_text, "Plafond")
            if ceiling_text and kind is ContractKind.PENSION_SAVINGS
            else None
        )
        return ContractDetails(
            kind,
            self._name.text(),
            self._insurer.text(),
            to_date(self._opened),
            self._number.text(),
            to_date(self._retirement) if self._has_retirement.isChecked() else None,
            ceiling,
        )

    def accept(self) -> None:
        """Reste ouvert tant que la saisie est refusée : l'erreur s'affiche dans la fenêtre."""
        try:
            validate_details(self.form())
        except ValueError as error:
            self._error.setText(str(error))
            return
        super().accept()


class PaymentDialog(QDialog):
    def __init__(self, parent: QWidget | None, view: ContractView, today: date) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajouter un versement")
        self.setMinimumWidth(460)
        self._view = view
        self._date = date_edit(today)
        self._amount = QLineEdit()
        self._total = QLabel()
        self._total.setFont(theme.mono_font(14))
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {theme.LOSS};")
        title = QLabel(self.windowTitle())
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        note = QLabel(
            "Un versement augmente le total versé, pas la valeur : celle-ci ne change qu'avec "
            "un nouveau relevé."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(QLabel(f"{view.details.name} · {view.details.insurer}"))
        layout.addWidget(
            row(labeled("Date du versement", self._date), labeled("Montant (€)", self._amount))
        )
        layout.addWidget(labeled("Total versé après ce versement", self._total))
        layout.addWidget(note)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer le versement"))
        self._amount.textChanged.connect(self._update)
        self._update()

    def form(self) -> tuple[date, Decimal]:
        return to_date(self._date), parse_decimal(self._amount.text(), "Montant")

    def accept(self) -> None:
        from assets_tracker.domain.insurance import validate_payment

        try:
            paid_on, amount = self.form()
            validate_payment(self._view.details, paid_on, amount)
        except ValueError as error:
            self._error.setText(str(error))
            return
        super().accept()

    def total_text(self) -> str:
        return self._total.text()

    def _update(self, *_: object) -> None:
        try:
            amount = parse_decimal(self._amount.text(), "Montant")
        except ValueError:
            amount = Decimal(0)
        self._total.setText(money(self._view.paid + amount, CURRENCY))


class StatementDialog(QDialog):
    """Actualisation annuelle : valeur actuelle et valeur projetée, avec l'écart à la précédente."""

    def __init__(self, parent: QWidget | None, view: ContractView, today: date) -> None:
        super().__init__(parent)
        self.setWindowTitle("Actualiser la projection")
        self.setMinimumWidth(500)
        self._view = view
        self._previous = view.projected
        self._date = date_edit(today)
        self._value = QLineEdit(_plain(view.value) if view.value is not None else "")
        self._projected = QLineEdit(_plain(view.projected) if view.projected is not None else "")
        self._variation = QLabel("—")
        self._variation.setFont(theme.mono_font(14))
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {theme.LOSS};")
        title = QLabel(self.windowTitle())
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        previous = (
            f"Dernière projection : {money(view.projected, CURRENCY)}"
            if view.projected is not None
            else "Aucune projection enregistrée."
        )
        note = QLabel(
            "Reprenez les chiffres du relevé annuel de l'assureur. Chaque relevé est conservé : "
            "il alimente l'historique des valeurs projetées. Un relevé à la même date remplace "
            "le précédent."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(QLabel(f"{view.details.name} · {view.details.insurer}"))
        layout.addWidget(
            row(
                labeled("Date du relevé", self._date),
                labeled("Valeur actuelle (€)", self._value),
            )
        )
        layout.addWidget(labeled("Valeur projetée à la retraite (€)", self._projected, previous))
        layout.addWidget(labeled("Écart avec la projection précédente", self._variation))
        layout.addWidget(note)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer le relevé"))
        self._projected.textChanged.connect(self._update)
        self._update()

    def form(self) -> tuple[date, Decimal, Decimal | None]:
        text = self._projected.text().strip()
        projected = parse_decimal(text, "Valeur projetée") if text else None
        return to_date(self._date), parse_decimal(self._value.text(), "Valeur actuelle"), projected

    def accept(self) -> None:
        from assets_tracker.domain.insurance import validate_statement

        try:
            recorded_on, value, projected = self.form()
            validate_statement(self._view.details, recorded_on, value, projected)
        except ValueError as error:
            self._error.setText(str(error))
            return
        super().accept()

    def variation_text(self) -> str:
        return self._variation.text()

    def _update(self, *_: object) -> None:
        text = self._projected.text().strip()
        if self._previous is None or not text:
            self._variation.setText("—")
            return
        try:
            delta = parse_decimal(text, "Valeur projetée") - self._previous
        except ValueError:
            self._variation.setText("—")
            return
        self._variation.setText(signed_money(delta, CURRENCY))
        self._variation.setStyleSheet(f"color: {theme.variation_color(delta)};")


# --- Écran ------------------------------------------------------------------


class InsurancePage(QWidget):
    _COLUMNS = ["Contrat", "Total versé", "Valeur actuelle", "Valeur projetée"]

    def __init__(
        self,
        repository: InsuranceRepository,
        *,
        today: Callable[[], date] = date.today,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._today = today
        self._selected: int | None = None
        self._views: list[ContractView] = []

        title = QLabel("Assurances et pensions")
        title.setProperty("role", "title")
        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "subtitle")
        add = QPushButton("Nouveau contrat")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._new_contract)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(self._subtitle)
        header.addLayout(heading, 1)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)

        self._value_card = Card("Valeur actuelle")
        self._paid_card = Card("Total versé")
        self._projected_card = Card("Valeur projetée")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._value_card, self._paid_card, self._projected_card):
            cards.addWidget(card, 1)

        self._alert = QFrame()
        self._alert.setObjectName("insuranceAlert")
        self._alert.setStyleSheet(
            f"QFrame#insuranceAlert {{ background: {theme.WARNING_SOFT}; "
            f"border: 1px solid {theme.WARNING_BORDER}; border-radius: 12px; }}"
        )
        self._alert_text = QLabel()
        self._alert_text.setWordWrap(True)
        alert_button = QPushButton("Voir le contrat")
        alert_button.clicked.connect(lambda _=False: self._show_stale())
        alert_layout = QHBoxLayout(self._alert)
        alert_layout.setContentsMargins(16, 12, 16, 12)
        alert_layout.setSpacing(12)
        alert_layout.addWidget(self._alert_text, 1)
        alert_layout.addWidget(alert_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._table = QTableWidget()
        self._table.setAccessibleName("Contrats d'assurance et de pension")
        prepare_table(self._table, self._COLUMNS, row_height=68)
        self._table.setWordWrap(False)
        columns = self._table.horizontalHeader()
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((130, 150, 160), start=1):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            columns.resizeSection(column, width)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.currentCellChanged.connect(self._on_row_changed)

        self._empty = QLabel(
            "Aucun contrat. Ajoutez une épargne-pension ou une assurance groupe "
            "avec « Nouveau contrat »."
        )
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

        self._d_badge = QLabel()
        badge = QHBoxLayout()
        badge.addWidget(self._d_badge)
        badge.addStretch(1)
        self._d_name = QLabel()
        self._d_name.setProperty("role", "title")
        self._d_name.setStyleSheet("font-size: 22px;")
        self._d_name.setWordWrap(True)
        self._d_sub = QLabel()
        self._d_sub.setProperty("role", "subtitle")
        self._d_sub.setWordWrap(True)
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
        self._d_gain.setWordWrap(True)
        figures = QVBoxLayout()
        figures.setSpacing(4)
        figures.addWidget(self._d_value)
        figures.addWidget(self._d_gain)
        layout.addLayout(figures)

        chart_caption = QLabel("VALEUR PROJETÉE, PAR ACTUALISATION")
        chart_caption.setProperty("role", "caption")
        self._chart = _ProjectionChart()
        self._chart_empty = QLabel("Aucune projection : actualisez-la pour démarrer l'historique.")
        self._chart_empty.setProperty("role", "muted")
        self._chart_empty.setWordWrap(True)
        layout.addWidget(chart_caption)
        layout.addWidget(self._chart)
        layout.addWidget(self._chart_empty)

        self._ceiling_box = QFrame()
        self._ceiling_box.setObjectName("ceilingBox")
        self._ceiling_box.setStyleSheet(
            f"QFrame#ceilingBox {{ background: {theme.RAISED}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 12px; }}"
        )
        ceiling_layout = QVBoxLayout(self._ceiling_box)
        ceiling_layout.setContentsMargins(14, 12, 14, 12)
        ceiling_layout.setSpacing(8)
        self._d_ceiling_head = QLabel()
        self._d_ceiling_head.setFont(theme.mono_font(13))
        self._gauge = _Gauge()
        self._d_ceiling_left = QLabel()
        self._d_ceiling_left.setProperty("role", "muted")
        ceiling_layout.addWidget(self._d_ceiling_head)
        ceiling_layout.addWidget(self._gauge)
        ceiling_layout.addWidget(self._d_ceiling_left)
        layout.addWidget(self._ceiling_box)

        self._tiles: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setSpacing(10)
        for index, caption in enumerate(FACT_CAPTIONS):
            grid.addWidget(self._tile(caption), index // 2, index % 2)
        layout.addLayout(grid)

        self._update_button = QPushButton("Actualiser la projection…")
        self._update_button.setProperty("variant", "primary")
        self._update_button.clicked.connect(lambda _=False: self._update_statement())
        self._payment_button = QPushButton("Ajouter un versement…")
        self._payment_button.clicked.connect(lambda _=False: self._add_payment())
        self._edit_button = QPushButton("Modifier le contrat…")
        self._edit_button.clicked.connect(lambda _=False: self._edit_contract())
        self._delete_button = QPushButton("Supprimer le contrat…")
        self._delete_button.clicked.connect(lambda _=False: self._delete_contract())
        for button in (
            self._update_button,
            self._payment_button,
            self._edit_button,
            self._delete_button,
        ):
            layout.addWidget(button)

        history_caption = QLabel("HISTORIQUE DES PROJECTIONS")
        history_caption.setProperty("role", "caption")
        self._history = QVBoxLayout()
        self._history.setSpacing(0)
        payments_caption = QLabel("VERSEMENTS")
        payments_caption.setProperty("role", "caption")
        self._payments = QVBoxLayout()
        self._payments.setSpacing(0)
        layout.addWidget(history_caption)
        layout.addLayout(self._history)
        layout.addWidget(payments_caption)
        layout.addLayout(self._payments)
        return frame

    def _tile(self, caption: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("insuranceTile")
        tile.setStyleSheet(
            f"QFrame#insuranceTile {{ background: {theme.RAISED}; "
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
        self._views = [build_view(h, today) for h in self._repository.list_contracts()]
        if self._selected not in {v.contract_id for v in self._views}:
            self._selected = self._views[0].contract_id if self._views else None
        self._summarize(today)
        self._fill_table(today)
        has = bool(self._views)
        self._table.setVisible(has)
        self._detail.setVisible(has)
        self._empty.setVisible(not has)
        if has:
            self._show_selected(today)

    def _summarize(self, today: date) -> None:
        count = len(self._views)
        self._subtitle.setText(
            f"{count} contrat{'s' if count > 1 else ''} · non inclus dans la valeur du portefeuille"
        )
        total = totals((v.holding.payments, v.holding.statements) for v in self._views)
        any_value = any(v.value is not None for v in self._views)
        any_projection = any(v.projected is not None for v in self._views)
        diff = gain(total.value, total.paid)
        pct = gain_percent(total.value, total.paid)
        self._value_card.set(
            money(total.value, CURRENCY) if any_value else "—",
            "Dernier relevé de chaque contrat"
            if pct is None or not any_value
            else f"{signed_money(diff, CURRENCY)} · {percent(pct)} depuis les versements",
            theme.TEXT,
        )
        self._paid_card.set(money(total.paid, CURRENCY), "Somme de tous les versements")
        self._projected_card.set(
            money(total.projected, CURRENCY) if any_projection else "—",
            "Dernière projection de chaque contrat",
        )
        stale = self._first_stale()
        self._alert.setVisible(stale is not None)
        if stale is not None:
            self._alert_text.setText(stale_message(stale, today))

    def _first_stale(self) -> ContractView | None:
        return next((v for v in self._views if v.stale), None)

    def _selected_view(self) -> ContractView | None:
        return next((v for v in self._views if v.contract_id == self._selected), None)

    def _fill_table(self, today: date) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._views))
        for table_row, view in enumerate(self._views):
            self._fill_row(table_row, view)
            if view.contract_id == self._selected:
                self._table.selectRow(table_row)
        self._table.blockSignals(False)
        fit_height(self._table)

    def _fill_row(self, table_row: int, view: ContractView) -> None:
        details = view.details
        color = KIND_COLORS[details.kind]
        meta = f"{details.insurer} · {details.kind.label}"
        self._table.setCellWidget(
            table_row,
            0,
            _rich(
                f"<span style='color:{color}'>■</span>&nbsp; <b>{escape(details.name)}</b><br>"
                f"{_subtle(escape(meta), 12)}"
            ),
        )
        self._table.setCellWidget(table_row, 1, _rich(money(view.paid, CURRENCY), RIGHT, mono=True))
        if view.value is None:
            value_html = f"—<br>{_subtle('Aucun relevé', 12)}"
        else:
            note = f"Relevé du {view.statement_on:%d/%m/%Y}" if view.statement_on else ""
            if view.stale:
                note = f"<span style='color:{theme.WARNING}'>{note} · ancien</span>"
            else:
                note = _subtle(note, 12)
            value_html = f"{money(view.value, CURRENCY)}<br>{note}"
        self._table.setCellWidget(table_row, 2, _rich(value_html, RIGHT, mono=True))
        projected = "—" if view.projected is None else money(view.projected, CURRENCY)
        self._table.setCellWidget(table_row, 3, _rich(projected, RIGHT, mono=True))

    def _show_selected(self, today: date) -> None:
        view = self._selected_view()
        if view is None:
            return
        holding, details = view.holding, view.details
        self._d_name.setText(details.name)
        self._d_sub.setText(f"{details.insurer} · {details.kind.label}")
        if view.statement_on is None:
            badge, stale = "Aucun relevé", True
        else:
            badge, stale = f"Relevé du {view.statement_on:%d/%m/%Y}", view.stale
        if stale and view.statement_on is not None:
            badge += " — à actualiser"
        self._d_badge.setText(badge)
        self._d_badge.setStyleSheet(
            f"color: {theme.WARNING if stale else theme.TEXT_MUTED}; font-size: 11px;"
        )
        self._d_value.setText("—" if view.value is None else money(view.value, CURRENCY))
        if view.value is not None:
            diff = gain(view.value, view.paid)
            pct = gain_percent(view.value, view.paid)
            self._d_gain.setText(
                signed_money(diff, CURRENCY) + ("" if pct is None else f" · {percent(pct)}")
            )
            self._d_gain.setStyleSheet(f"color: {theme.variation_color(diff)};")
        else:
            self._d_gain.setText("")
        history = projection_history(holding.statements)
        self._chart.set_points(
            [
                (s.statement.recorded_on, s.statement.projected_value)
                for s in history
                if s.statement.projected_value is not None
            ]
        )
        self._chart.setVisible(bool(history))
        self._chart_empty.setVisible(not history)

        ceiling = details.ceiling
        self._ceiling_box.setVisible(ceiling is not None)
        if ceiling is not None:
            year = today.year
            paid = paid_in_year(holding.payments, year)
            left = ceiling_left(details, holding.payments, year) or Decimal(0)
            self._d_ceiling_head.setText(
                f"Versé en {year} : {money(paid, CURRENCY)} / {money(ceiling, CURRENCY)}"
            )
            self._gauge.set_value(paid, ceiling)
            self._d_ceiling_left.setText(
                "Plafond atteint" if left == 0 else f"Encore {money(left, CURRENCY)} versables"
            )

        facts = {
            "Plus-value": "—"
            if view.value is None
            else signed_money(gain(view.value, view.paid), CURRENCY),
            "Rendement total": "—"
            if view.value is None or gain_percent(view.value, view.paid) is None
            else percent(gain_percent(view.value, view.paid) or Decimal(0)),
            "Ouverture": f"{details.opened_on:%d/%m/%Y}",
            "Départ à la retraite": "—"
            if details.retirement_on is None
            else f"{details.retirement_on:%d/%m/%Y}",
            "Dernier relevé": "—" if view.statement_on is None else f"{view.statement_on:%d/%m/%Y}",
            "N° de contrat": details.number or "—",
        }
        for caption, text in facts.items():
            self._tiles[caption].setText(text)

        _clear(self._history)
        if history:
            for step in history:
                self._history.addWidget(self._history_row(step))
        else:
            self._history.addWidget(self._muted("Aucune projection enregistrée."))
        _clear(self._payments)
        if holding.payments:
            for payment in reversed(holding.payments):
                self._payments.addWidget(
                    self._line(
                        f"{payment.paid_on:%d/%m/%Y}",
                        money(payment.amount, CURRENCY),
                        None,
                        lambda pid=payment.id: self._delete_payment(pid),
                    )
                )
        else:
            self._payments.addWidget(self._muted("Aucun versement enregistré."))

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "muted")
        return label

    def _history_row(self, step: ProjectionStep) -> QWidget:
        statement = step.statement
        if step.change is None:
            change = ""
        else:
            sign = signed_money(step.change, CURRENCY)
            extra = "" if step.change_percent is None else f" ({percent(step.change_percent)})"
            change = (
                f"<span style='color:{theme.variation_color(step.change)}'>{sign}{extra}</span>"
            )
        projected = statement.projected_value
        assert projected is not None
        return self._line(
            f"{statement.recorded_on:%d/%m/%Y}",
            money(projected, CURRENCY),
            change,
            lambda sid=statement.id: self._delete_statement(sid),
        )

    def _line(
        self, left: str, amount: str, extra: str | None, on_delete: Callable[[], None]
    ) -> QWidget:
        box = QWidget()
        line = QHBoxLayout(box)
        line.setContentsMargins(0, 8, 0, 8)
        line.setSpacing(12)
        when = QLabel(left)
        when.setProperty("role", "muted")
        value = QLabel(amount)
        value.setFont(theme.mono_font(13))
        line.addWidget(when, 1)
        if extra:
            detail = QLabel(extra)
            detail.setTextFormat(Qt.TextFormat.RichText)
            detail.setFont(theme.mono_font(12))
            line.addWidget(detail)
        line.addWidget(value)
        remove = QPushButton("✕")
        remove.setAccessibleName("Supprimer cette ligne")
        remove.setFixedWidth(32)
        remove.clicked.connect(lambda _=False: on_delete())
        line.addWidget(remove)
        return box

    def history_rows(self) -> int:
        """Nombre de lignes de l'historique des projections, pour les tests."""
        view = self._selected_view()
        return 0 if view is None else len(projection_history(view.holding.statements))

    def select(self, contract_id: int) -> None:
        self._selected = contract_id
        self.refresh()

    def _on_row_changed(self, current_row: int, *_: int) -> None:
        if 0 <= current_row < len(self._views):
            self._selected = self._views[current_row].contract_id
            self._show_selected(self._today())

    def _show_stale(self) -> None:
        stale = self._first_stale()
        if stale is not None:
            self.select(stale.contract_id)

    # -- actions --

    def _new_contract(self) -> None:
        dialog = ContractDialog(self, self._today())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._selected = self._repository.create(dialog.form()).id
        except ValueError as error:
            QMessageBox.warning(self, "Contrat invalide", str(error))
        self.refresh()

    def _edit_contract(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        dialog = ContractDialog(self, self._today(), view.holding)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._repository.update(view.contract_id, dialog.form())
        except ValueError as error:
            QMessageBox.warning(self, "Contrat invalide", str(error))
        self.refresh()

    def _add_payment(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        dialog = PaymentDialog(self, view, self._today())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            paid_on, amount = dialog.form()
            self._repository.add_payment(view.contract_id, paid_on, amount)
        except ValueError as error:
            QMessageBox.warning(self, "Versement refusé", str(error))
        self.refresh()

    def _update_statement(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        dialog = StatementDialog(self, view, self._today())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            recorded_on, value, projected = dialog.form()
            self._repository.save_statement(view.contract_id, recorded_on, value, projected)
        except ValueError as error:
            QMessageBox.warning(self, "Relevé refusé", str(error))
        self.refresh()

    def _delete_statement(self, statement_id: int) -> None:
        answer = QMessageBox.question(
            self, "Supprimer le relevé", "Supprimer ce relevé et sa valeur projetée ?"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._repository.delete_statement(statement_id)
            self.refresh()

    def _delete_payment(self, payment_id: int) -> None:
        answer = QMessageBox.question(self, "Supprimer le versement", "Supprimer ce versement ?")
        if answer == QMessageBox.StandardButton.Yes:
            self._repository.delete_payment(payment_id)
            self.refresh()

    def _delete_contract(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        answer = QMessageBox.question(
            self,
            "Supprimer le contrat",
            f"Supprimer « {view.details.name} » avec ses versements et ses relevés ? "
            "Cette action est définitive.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._repository.delete(view.contract_id)
            self._selected = None
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
