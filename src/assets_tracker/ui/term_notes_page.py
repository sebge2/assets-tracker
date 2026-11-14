"""Écran des bons de caisse : synthèse, échéancier, liste, fiche, souscription et remboursement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
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
from assets_tracker.db.term_note_repository import TermNoteHolding, TermNoteRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind, exposure_breakdown
from assets_tracker.domain.term_notes import (
    DUE_SOON_DAYS,
    InterestPayout,
    NoteValue,
    Repayment,
    TermNoteDetails,
    ladder,
    next_maturity,
    note_totals,
    note_value,
    payment_dates,
    remaining_label,
    repayment_proposal,
    term_label,
)
from assets_tracker.ui import theme
from assets_tracker.ui.exposure_widgets import (
    ExposureSection,
    ExposureSummary,
    edit_exposure,
    main_label,
)
from assets_tracker.ui.form_helpers import date_edit, labeled, row, to_date
from assets_tracker.ui.formatting import (
    NBSP,
    compact_money,
    money,
    parse_decimal,
    percent,
    signed_money,
)
from assets_tracker.ui.widgets import RIGHT, Card, PageContent, fit_height, prepare_table

CURRENCY = "EUR"
WIDE_LAYOUT = 1340  # largeur de page à partir de laquelle la fiche passe à droite du tableau
DETAIL_WIDTH = 380
TILE_CAPTIONS = (
    "Capital",
    "Taux annuel",
    "Souscription",
    "Échéance",
    "Durée",
    "Versement des intérêts",
    "Intérêts courus",
    "Intérêts déjà versés",
    "Intérêts totaux",
    "Valeur à l'échéance",
    "Secteur principal",
    "Zone principale",
)


# --- Données affichées ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoteView:
    holding: TermNoteHolding
    value: NoteValue

    @property
    def asset_id(self) -> int:
        assert self.holding.asset.id is not None
        return self.holding.asset.id

    @property
    def details(self) -> TermNoteDetails:
        return self.holding.details

    @property
    def repaid(self) -> bool:
        return self.holding.repayment is not None

    @property
    def due(self) -> bool:
        """Échu mais pas encore remboursé."""
        return self.value.ended and not self.repaid


def build_view(holding: TermNoteHolding, today: date) -> NoteView:
    return NoteView(holding, note_value(holding.details, today, holding.repayment))


def sorted_views(views: list[NoteView]) -> list[NoteView]:
    """Bons détenus par échéance, puis bons remboursés du plus récent au plus ancien."""
    held = sorted((v for v in views if not v.repaid), key=lambda v: (v.details.end, v.asset_id))
    closed = sorted(
        (v for v in views if v.holding.repayment is not None),
        key=lambda v: v.holding.repayment.paid_on if v.holding.repayment else date.min,
        reverse=True,
    )
    return [*held, *closed]


def due_message(view: NoteView, today: date) -> str:
    details = view.details
    days = (today - details.end).days
    name = f"<span style='color:{theme.WARNING};font-weight:600'>{escape(view.holding.asset.name)}</span>"
    when = "aujourd'hui" if days == 0 else f"depuis {days} jour{'s' if days > 1 else ''}"
    return (
        f"{name} ({escape(details.issuer)}) est échu {when} : "
        f"{money(details.capital, CURRENCY)} de capital et "
        f"{money(view.value.accrued, CURRENCY)} d'intérêts à recevoir. "
        "Enregistrez le remboursement pour clôturer le bon."
    )


def next_message(views: list[NoteView], today: date) -> str:
    pairs = [(v.holding.asset.name, v.details) for v in views if not v.repaid]
    upcoming = next_maturity(((n, d) for n, d in pairs if d.end >= today), today)
    if upcoming is None:
        return "Aucune échéance à venir."
    name, details = upcoming
    view = next(v for v in views if v.holding.asset.name == name and v.details == details)
    return (
        f"Prochaine échéance : {name} ({details.issuer}) le {details.end:%d/%m/%Y} "
        f"({remaining_label(details.end, today)}) — {money(details.capital, CURRENCY)} "
        f"de capital + {money(view.value.to_receive, CURRENCY)} d'intérêts."
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


class _Ladder(QWidget):
    """Échéancier : capital et intérêts à recevoir, par année."""

    HEIGHT = 150

    def __init__(self) -> None:
        super().__init__()
        self._years: dict[int, tuple[Decimal, Decimal]] = {}
        self.setMinimumHeight(self.HEIGHT)
        self.setMaximumHeight(self.HEIGHT)
        self.setAccessibleName("Échéancier des bons de caisse")

    def set_data(self, years: dict[int, tuple[Decimal, Decimal]]) -> None:
        self._years = years
        self.setAccessibleDescription(
            ", ".join(
                f"{year} : {money(capital + interest, CURRENCY)}"
                for year, (capital, interest) in years.items()
            )
        )
        self.update()

    def years(self) -> list[int]:
        """Années affichées, sans trou entre la première et la dernière."""
        if not self._years:
            return []
        return list(range(min(self._years), max(self._years) + 1))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        years = self.years()
        if not years:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        zero = (Decimal(0), Decimal(0))
        totals = [sum(self._years.get(y, zero), Decimal(0)) for y in years]
        peak = max(totals) or Decimal(1)
        slot = self.width() / len(years)
        bar = min(70.0, slot * 0.6)
        top, bottom = 18.0, 20.0
        room = self.height() - top - bottom - 6
        painter.setFont(theme.mono_font(11))
        for index, year in enumerate(years):
            capital, interest = self._years.get(year, zero)
            left = index * slot + (slot - bar) / 2
            y = self.height() - bottom
            for amount, color in ((capital, theme.ACCENT), (interest, theme.GAIN)):
                if amount <= 0:
                    continue
                height = max(2.0, float(amount / peak) * room)
                y -= height
                painter.setBrush(QColor(color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QRectF(left, y, bar, height), 3, 3)
                y -= 2
            painter.setPen(QColor(theme.TEXT_MUTED))
            label = compact_money(capital + interest, CURRENCY) if totals[index] else "—"
            painter.drawText(
                QRectF(index * slot, y - 16, slot, 14), Qt.AlignmentFlag.AlignCenter, label
            )
            painter.drawText(
                QRectF(index * slot, self.height() - 16, slot, 14),
                Qt.AlignmentFlag.AlignCenter,
                str(year),
            )


class _Progress(QWidget):
    """Barre de durée écoulée."""

    def __init__(self) -> None:
        super().__init__()
        self._percent = 0.0
        self._done = False
        self.setFixedHeight(8)

    def set_value(self, percent: Decimal, *, due: bool) -> None:
        self._percent = max(0.0, min(100.0, float(percent)))
        self._done = due
        self.setAccessibleName(f"{self._percent:.0f} % de la durée écoulée")
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BORDER_STRONG))
        painter.drawRoundedRect(QRectF(self.rect()), 4, 4)
        painter.setBrush(QColor(theme.WARNING if self._done else theme.ACCENT))
        painter.drawRoundedRect(QRectF(0, 0, self.width() * self._percent / 100, 8), 4, 4)


# --- Dialogues --------------------------------------------------------------


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


class TermNoteDialog(QDialog):
    """Création ou modification d'un bon de caisse, avec les intérêts recalculés à la frappe."""

    def __init__(
        self,
        parent: QWidget | None,
        today: date,
        holding: TermNoteHolding | None = None,
    ) -> None:
        super().__init__(parent)
        editing = holding is not None
        self.setWindowTitle("Modifier le bon de caisse" if editing else "Nouveau bon de caisse")
        self.setMinimumWidth(520)
        details = holding.details if holding else None
        self._name = QLineEdit(holding.asset.name if holding else "")
        self._issuer = QLineEdit(details.issuer if details else "")
        self._capital = QLineEdit(_plain(details.capital) if details else "")
        self._rate = QLineEdit(_plain(details.rate) if details else "")
        self._start = date_edit(details.start if details else today)
        self._end = date_edit(details.end if details else date(today.year + 1, today.month, 1))
        self._payout = QComboBox()
        self._payout.addItem("À l'échéance", InterestPayout.MATURITY)
        self._payout.addItem("Chaque année", InterestPayout.ANNUAL)
        if details is not None:
            self._payout.setCurrentIndex(0 if details.payout is InterestPayout.MATURITY else 1)
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {theme.LOSS};")
        self._summary = {
            caption: QLabel("—")
            for caption in ("Durée", "Intérêts à l'échéance", "Valeur à l'échéance")
        }

        title = QLabel(self.windowTitle())
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        recap = QFrame()
        recap.setProperty("role", "card")
        recap_layout = QHBoxLayout(recap)
        recap_layout.setContentsMargins(14, 12, 14, 12)
        for caption, value in self._summary.items():
            value.setFont(theme.mono_font(13))
            box = QVBoxLayout()
            hint = QLabel(caption)
            hint.setProperty("role", "hint")
            box.addWidget(hint)
            box.addWidget(value)
            recap_layout.addLayout(box, 1)
        note = QLabel(
            "Pas de cours : la valeur du bon est son capital plus les intérêts courus, "
            "calculés au jour le jour (base 365 jours). Les secteurs et zones se renseignent "
            "depuis la fiche."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(labeled("Nom", self._name))
        layout.addWidget(row(labeled("Émetteur", self._issuer), labeled("Devise", _currency())))
        layout.addWidget(
            row(labeled("Capital placé (€)", self._capital), labeled("Taux annuel (%)", self._rate))
        )
        layout.addWidget(
            row(
                labeled("Date de souscription", self._start),
                labeled("Date d'échéance", self._end),
            )
        )
        layout.addWidget(labeled("Versement des intérêts", self._payout))
        layout.addWidget(recap)
        layout.addWidget(note)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer" if editing else "Ajouter le bon"))
        for field in (self._capital, self._rate):
            field.textChanged.connect(self._update)
        for edit in (self._start, self._end):
            edit.dateChanged.connect(self._update)
        self._payout.currentIndexChanged.connect(self._update)
        self._update()

    def _details(self) -> TermNoteDetails:
        return TermNoteDetails(
            self._issuer.text(),
            parse_decimal(self._capital.text(), "Capital"),
            parse_decimal(self._rate.text(), "Taux"),
            to_date(self._start),
            to_date(self._end),
            InterestPayout(self._payout.currentData()),
        )

    def form(self) -> tuple[str, TermNoteDetails]:
        return self._name.text(), self._details()

    def accept(self) -> None:
        """Reste ouvert tant que la saisie est refusée : l'erreur s'affiche dans la fenêtre."""
        from assets_tracker.domain.term_notes import validate_details

        try:
            name, details = self.form()
            validate_details(name, details)
        except ValueError as error:
            self._error.setText(str(error))
            return
        super().accept()

    def summary(self) -> dict[str, str]:
        """Valeurs du récapitulatif, pour les tests."""
        return {caption: label.text() for caption, label in self._summary.items()}

    def _update(self, *_: object) -> None:
        self._error.setText("")
        try:
            details = self._details()
            if details.end <= details.start or details.capital <= 0:
                raise ValueError
            from assets_tracker.domain.term_notes import interest_total, term_days

            total = interest_total(details)
            values = [
                f"{term_label(details)} · {term_days(details)}{NBSP}j",
                signed_money(total, CURRENCY),
                money(details.capital + total, CURRENCY),
            ]
        except ValueError:
            values = ["—", "—", "—"]
        for label, text in zip(self._summary.values(), values, strict=True):
            label.setText(text)


class RepayDialog(QDialog):
    """Remboursement à l'échéance ou anticipé : capital et intérêts réellement reçus."""

    def __init__(self, parent: QWidget | None, view: NoteView, today: date) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enregistrer le remboursement")
        self.setMinimumWidth(520)
        self._view = view
        proposal = repayment_proposal(view.details, today)
        self._date = date_edit(today)
        self._capital = QLineEdit(_plain(proposal.capital))
        self._interest = QLineEdit(_plain(proposal.interest))
        self._interest_touched = False
        self._total = QLabel()
        self._total.setFont(theme.mono_font(20))
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color: {theme.LOSS};")

        title = QLabel(self.windowTitle())
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        details = view.details
        subtitle = QLabel(
            f"{view.holding.asset.name} · {details.issuer} · "
            f"échéance {details.end:%d/%m/%Y} · taux {percent(details.rate)}"
        )
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        total_row = QFrame()
        total_row.setProperty("role", "card")
        total_layout = QHBoxLayout(total_row)
        total_layout.setContentsMargins(14, 12, 14, 12)
        total_layout.addWidget(QLabel("Total encaissé"), 1)
        total_layout.addWidget(self._total)
        note = QLabel(
            "Le bon est clôturé et sort du portefeuille. Les intérêts reçus sont comptés dans "
            "la plus-value réalisée. Pour un remboursement anticipé, saisissez les intérêts "
            "réellement versés par la banque."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(labeled("Date du remboursement", self._date))
        layout.addWidget(
            row(
                labeled("Capital remboursé (€)", self._capital),
                labeled("Intérêts reçus (€)", self._interest),
            )
        )
        layout.addWidget(total_row)
        layout.addWidget(note)
        layout.addWidget(self._error)
        layout.addWidget(_buttons(self, "Enregistrer le remboursement"))
        self._date.dateChanged.connect(self._on_date)
        self._interest.textEdited.connect(self._on_interest_edited)
        for field in (self._capital, self._interest):
            field.textChanged.connect(self._update)
        self._update()

    def _on_interest_edited(self, _: str) -> None:
        self._interest_touched = True

    def _on_date(self, *_: object) -> None:
        """Tant que les intérêts n'ont pas été modifiés à la main, ils suivent la date."""
        if not self._interest_touched:
            proposal = repayment_proposal(self._view.details, to_date(self._date))
            self._interest.setText(_plain(proposal.interest))

    def form(self) -> Repayment:
        return Repayment(
            to_date(self._date),
            parse_decimal(self._capital.text(), "Capital"),
            parse_decimal(self._interest.text() or "0", "Intérêts"),
        )

    def accept(self) -> None:
        from assets_tracker.domain.term_notes import validate_repayment

        try:
            validate_repayment(self._view.details, self.form())
        except ValueError as error:
            self._error.setText(str(error))
            return
        super().accept()

    def total_text(self) -> str:
        return self._total.text()

    def _update(self, *_: object) -> None:
        self._error.setText("")
        try:
            self._total.setText(money(self.form().total, CURRENCY))
        except ValueError:
            self._total.setText("—")


def _currency() -> QLineEdit:
    field = QLineEdit(CURRENCY)
    field.setReadOnly(True)
    field.setEnabled(False)
    return field


def _plain(value: Decimal) -> str:
    """Nombre saisissable : virgule décimale, sans zéros inutiles."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


# --- Écran ------------------------------------------------------------------


class TermNotesPage(QWidget):
    _COLUMNS = ["Bon de caisse", "Taux · échéance", "Capital", "Valeur actuelle", "Intérêts totaux"]

    def __init__(
        self,
        repository: TermNoteRepository,
        *,
        exposure: ExposureRepository | None = None,
        today: Callable[[], date] = date.today,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._exposure = exposure
        self._today = today
        self._selected: int | None = None
        self._views: list[NoteView] = []

        title = QLabel("Bons de caisse")
        title.setProperty("role", "title")
        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "subtitle")
        add = QPushButton("Nouveau bon de caisse")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._new_note)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(self._subtitle)
        header.addLayout(heading, 1)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)

        self._capital_card = Card("Capital placé")
        self._value_card = Card("Valeur actuelle")
        self._interest_card = Card("Intérêts à percevoir")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._capital_card, self._value_card, self._interest_card):
            cards.addWidget(card, 1)

        self._alert = QFrame()
        self._alert.setObjectName("noteAlert")
        self._alert.setStyleSheet(
            f"QFrame#noteAlert {{ background: {theme.WARNING_SOFT}; "
            f"border: 1px solid {theme.WARNING_BORDER}; border-radius: 12px; }}"
        )
        self._alert_text = QLabel()
        self._alert_text.setTextFormat(Qt.TextFormat.RichText)
        self._alert_text.setWordWrap(True)
        alert_button = QPushButton("Voir le bon")
        alert_button.clicked.connect(lambda _=False: self._show_due())
        alert_layout = QHBoxLayout(self._alert)
        alert_layout.setContentsMargins(16, 12, 16, 12)
        alert_layout.setSpacing(12)
        alert_layout.addWidget(self._alert_text, 1)
        alert_layout.addWidget(alert_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._next = QLabel()
        self._next.setWordWrap(True)
        self._next.setProperty("role", "muted")
        self._next.setTextFormat(Qt.TextFormat.PlainText)

        self._ladder = _Ladder()
        ladder_caption = QLabel("ÉCHÉANCIER · CAPITAL ET INTÉRÊTS À RECEVOIR, PAR ANNÉE")
        ladder_caption.setProperty("role", "caption")
        ladder_legend = QLabel(
            f"<span style='color:{theme.ACCENT}'>■</span> Capital remboursé &nbsp;&nbsp; "
            f"<span style='color:{theme.GAIN}'>■</span> Intérêts perçus"
        )
        ladder_legend.setTextFormat(Qt.TextFormat.RichText)
        ladder_legend.setProperty("role", "muted")
        self._ladder_card = QFrame()
        self._ladder_card.setProperty("role", "card")
        ladder_layout = QVBoxLayout(self._ladder_card)
        ladder_layout.setContentsMargins(20, 18, 20, 18)
        ladder_layout.setSpacing(12)
        ladder_layout.addWidget(ladder_caption)
        ladder_layout.addWidget(self._ladder)
        ladder_layout.addWidget(ladder_legend)

        self._exposure_card = ExposureSummary("LA VALEUR DES BONS DE CAISSE")

        self._table = QTableWidget()
        self._table.setAccessibleName("Bons de caisse")
        prepare_table(self._table, self._COLUMNS, row_height=68)
        self._table.setWordWrap(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        columns = self._table.horizontalHeader()
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((150, 120, 140, 140), start=1):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            columns.resizeSection(column, width)
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.currentCellChanged.connect(self._on_row_changed)

        self._empty = QLabel("Aucun bon de caisse. Ajoutez-en un avec « Nouveau bon de caisse ».")
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
        layout.addWidget(self._next)
        layout.addWidget(self._ladder_card)
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
        self._d_accrued = QLabel()
        self._d_accrued.setFont(theme.mono_font(14))
        self._d_accrued.setWordWrap(True)
        figures = QVBoxLayout()
        figures.setSpacing(4)
        figures.addWidget(self._d_value)
        figures.addWidget(self._d_accrued)
        layout.addLayout(figures)

        elapsed_caption = QLabel("DURÉE ÉCOULÉE")
        elapsed_caption.setProperty("role", "caption")
        self._d_elapsed = QLabel()
        self._d_elapsed.setFont(theme.mono_font(12))
        elapsed_head = QHBoxLayout()
        elapsed_head.addWidget(elapsed_caption, 1)
        elapsed_head.addWidget(self._d_elapsed)
        self._progress = _Progress()
        self._d_start = QLabel()
        self._d_end = QLabel()
        for label in (self._d_start, self._d_end):
            label.setFont(theme.mono_font(12))
            label.setProperty("role", "muted")
        dates = QHBoxLayout()
        dates.addWidget(self._d_start)
        dates.addStretch(1)
        dates.addWidget(self._d_end)
        layout.addLayout(elapsed_head)
        layout.addWidget(self._progress)
        layout.addLayout(dates)

        self._tiles: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setSpacing(10)
        for index, caption in enumerate(TILE_CAPTIONS):
            grid.addWidget(self._tile(caption), index // 2, index % 2)
        layout.addLayout(grid)

        self._exposure_section = ExposureSection()
        self._exposure_section.edit_requested.connect(
            lambda: self._edit_exposure(self._selected_view())
        )
        layout.addWidget(self._exposure_section)

        self._repay_button = QPushButton("Enregistrer le remboursement…")
        self._repay_button.clicked.connect(lambda _=False: self._repay())
        self._cancel_button = QPushButton("Annuler le remboursement")
        self._cancel_button.clicked.connect(lambda _=False: self._cancel_repayment())
        self._edit_button = QPushButton("Modifier le bon…")
        self._edit_button.clicked.connect(lambda _=False: self._edit_note())
        self._delete_button = QPushButton("Supprimer le bon…")
        self._delete_button.clicked.connect(lambda _=False: self._delete_note())
        for button in (
            self._repay_button,
            self._cancel_button,
            self._edit_button,
            self._delete_button,
        ):
            layout.addWidget(button)

        operations_caption = QLabel("OPÉRATIONS")
        operations_caption.setProperty("role", "caption")
        self._operations = QVBoxLayout()
        self._operations.setSpacing(0)
        layout.addWidget(operations_caption)
        layout.addLayout(self._operations)
        return frame

    def _tile(self, caption: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("noteTile")
        tile.setStyleSheet(
            f"QFrame#noteTile {{ background: {theme.RAISED}; "
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
        self._views = sorted_views(
            [build_view(h, today) for h in self._repository.list_holdings()]
        )
        if self._selected not in {v.asset_id for v in self._views}:
            self._selected = self._views[0].asset_id if self._views else None
        held = sum(1 for v in self._views if not v.repaid)
        self._subtitle.setText(
            f"{held} bon{'s' if held > 1 else ''} · intérêts calculés au {today:%d/%m/%Y}"
        )
        self._summarize(today)
        due = self._first_due()
        self._alert.setVisible(due is not None)
        if due is not None:
            self._alert_text.setText(due_message(due, today))
        present = bool(self._views)
        self._next.setVisible(present)
        self._next.setText(next_message(self._views, today))
        self._table.setVisible(present)
        self._detail.setVisible(present)
        self._empty.setVisible(not present)
        self._fill_table(today)
        self._show_selected(today)

    def _pairs(self) -> list[tuple[TermNoteDetails, Repayment | None]]:
        return [(v.details, v.holding.repayment) for v in self._views]

    def _summarize(self, today: date) -> None:
        totals = note_totals(self._pairs(), today)
        self._capital_card.set(
            money(totals.capital, CURRENCY), "Somme des capitaux non remboursés"
        )
        self._value_card.set(
            money(totals.value, CURRENCY),
            f"{money(totals.accrued, CURRENCY)} d'intérêts courus",
        )
        average = "—" if totals.average_rate is None else percent(totals.average_rate)
        self._interest_card.set(
            money(totals.to_receive, CURRENCY), f"Rendement moyen {average} · jusqu'aux échéances"
        )
        years = ladder(self._pairs(), today)
        self._ladder.set_data(years)
        self._ladder_card.setVisible(bool(years))
        self._show_exposure()

    def _show_exposure(self) -> None:
        if self._exposure is None:
            self._exposure_card.setVisible(False)
            return
        weighted = [(v.asset_id, v.value.value) for v in self._views if v.value.value > 0]
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

    def _first_due(self) -> NoteView | None:
        return next((v for v in self._views if v.due), None)

    def _selected_view(self) -> NoteView | None:
        return next((v for v in self._views if v.asset_id == self._selected), None)

    def _fill_table(self, today: date) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._views))
        for table_row, view in enumerate(self._views):
            self._fill_row(table_row, view, today)
            if view.asset_id == self._selected:
                self._table.selectRow(table_row)
        self._table.blockSignals(False)
        fit_height(self._table)

    def _badge_text(self, view: NoteView, today: date) -> str:
        repayment = view.holding.repayment
        if repayment is not None:
            return f"Remboursé le {repayment.paid_on:%d/%m/%Y}"
        if view.due:
            return "Échu · à rembourser"
        return remaining_label(view.details.end, today).capitalize()

    def _fill_row(self, table_row: int, view: NoteView, today: date) -> None:
        details, asset = view.details, view.holding.asset
        color = theme.SERIES[3] if not view.repaid else theme.TEXT_SUBTLE
        meta = f"{details.issuer} · {term_label(details)}"
        self._table.setCellWidget(
            table_row,
            0,
            _rich(
                f"<span style='color:{color}'>■</span>&nbsp; <b>{escape(asset.name)}</b><br>"
                f"{_subtle(escape(meta), 12)}"
            ),
        )
        badge = self._badge_text(view, today)
        if view.due:
            badge = f"<span style='color:{theme.WARNING}'>{badge}</span>"
        self._table.setCellWidget(
            table_row,
            1,
            _rich(
                f"{percent(details.rate)} · {details.end:%d/%m/%Y}<br>{_subtle(badge)}",
                RIGHT,
                mono=True,
            ),
        )
        self._table.setCellWidget(
            table_row, 2, _rich(money(details.capital, CURRENCY), RIGHT, mono=True)
        )
        if view.repaid:
            value_html = f"—<br>{_subtle('Clôturé')}"
        else:
            value_html = (
                f"{money(view.value.value, CURRENCY)}<br>"
                f"{_subtle('+' + money(view.value.accrued, CURRENCY) + ' courus', 12)}"
            )
        self._table.setCellWidget(table_row, 3, _rich(value_html, RIGHT, mono=True))
        payout = "versés chaque année" if details.payout is InterestPayout.ANNUAL else "à l'échéance"
        total = signed_money(view.value.total, CURRENCY)
        self._table.setCellWidget(
            table_row,
            4,
            _rich(
                f"<span style='color:{theme.GAIN}'>{total}</span><br>{_subtle(payout, 12)}",
                RIGHT,
                mono=True,
            ),
        )

    def _show_selected(self, today: date) -> None:
        view = self._selected_view()
        if view is None:
            return
        details, asset, value = view.details, view.holding.asset, view.value
        self._d_name.setText(asset.name)
        self._d_sub.setText(
            f"{details.issuer} · {term_label(details)} · intérêts {details.payout.label}"
        )
        if view.repaid:
            badge, warn = self._badge_text(view, today), False
        elif view.due:
            days = (today - details.end).days
            badge, warn = f"Échu depuis {days} jours — à rembourser", True
        else:
            badge = f"En cours · {remaining_label(details.end, today)}"
            warn = (details.end - today).days <= DUE_SOON_DAYS
        background, foreground = (
            (theme.WARNING_SOFT, theme.WARNING) if warn else (theme.OVERLAY, theme.TEXT_MUTED)
        )
        self._d_badge.setText(badge)
        self._d_badge.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 10px; "
            "padding: 2px 10px; font-size: 12px; font-weight: 600;"
        )
        if view.repaid:
            self._d_value.setText(money(view.holding.repayment.total, CURRENCY))  # type: ignore[union-attr]
            self._d_accrued.setText("encaissé au remboursement")
            self._d_accrued.setStyleSheet(f"color: {theme.TEXT_SUBTLE};")
        else:
            self._d_value.setText(money(value.value, CURRENCY))
            self._d_accrued.setText(
                f"+{money(value.accrued, CURRENCY)} d'intérêts courus · "
                f"{percent(details.rate)} par an"
            )
            self._d_accrued.setStyleSheet(f"color: {theme.GAIN};")
        self._progress.set_value(value.elapsed_percent, due=view.due)
        self._d_elapsed.setText(f"{value.elapsed_percent:.0f}{NBSP}%")
        self._d_start.setText(f"{details.start:%d/%m/%Y}")
        self._d_end.setText(f"{details.end:%d/%m/%Y}")

        tiles = self._tiles
        tiles["Capital"].setText(money(details.capital, CURRENCY))
        tiles["Taux annuel"].setText(percent(details.rate))
        tiles["Souscription"].setText(f"{details.start:%d/%m/%Y}")
        tiles["Échéance"].setText(f"{details.end:%d/%m/%Y}")
        tiles["Durée"].setText(f"{term_label(details)} ({value.total_days}{NBSP}j)")
        tiles["Versement des intérêts"].setText(details.payout.label.capitalize())
        tiles["Intérêts courus"].setText(money(value.accrued, CURRENCY))
        tiles["Intérêts courus"].setStyleSheet(f"color: {theme.GAIN};")
        tiles["Intérêts déjà versés"].setText(money(value.paid, CURRENCY))
        tiles["Intérêts totaux"].setText(money(value.total, CURRENCY))
        tiles["Valeur à l'échéance"].setText(money(details.capital + value.to_receive, CURRENCY)
                                             if not view.repaid else "—")
        sectors: list[ExposureEntry] = []
        zones: list[ExposureEntry] = []
        if self._exposure is not None:
            sectors = self._exposure.get(view.asset_id, ExposureKind.SECTOR)
            zones = self._exposure.get(view.asset_id, ExposureKind.ZONE)
        self._exposure_section.setVisible(self._exposure is not None)
        self._exposure_section.set_data(sectors, zones)
        tiles["Secteur principal"].setText(main_label(sectors))
        tiles["Zone principale"].setText(main_label(zones))

        repaid = view.repaid
        self._repay_button.setVisible(not repaid)
        self._repay_button.setText(
            "Enregistrer le remboursement…" if view.value.ended else "Remboursement anticipé…"
        )
        self._repay_button.setProperty("variant", "primary" if view.value.ended else "")
        self._repay_button.style().unpolish(self._repay_button)
        self._repay_button.style().polish(self._repay_button)
        self._cancel_button.setVisible(repaid)

        _clear(self._operations)
        for kind, label, when, amount in self._operation_rows(view, today):
            self._operations.addWidget(self._operation_row(kind, label, when, amount))

    # -- opérations --

    def _operation_rows(
        self, view: NoteView, today: date
    ) -> list[tuple[str, str, date, Decimal]]:
        """Souscription, versements d'intérêts perçus et remboursement, du plus récent au plus ancien."""
        details = view.details
        rows: list[tuple[str, str, date, Decimal]] = [
            ("Souscription", "Capital placé", details.start, -details.capital)
        ]
        if details.payout is InterestPayout.ANNUAL:
            from assets_tracker.domain.term_notes import interest_paid  # noqa: F401

            dates = payment_dates(details)[:-1]
            from assets_tracker.domain.term_notes import _period_interest  # noqa: PLC2701

            begin = details.start
            for index, when in enumerate(dates, start=1):
                if when <= today:
                    interest = _period_interest(details, begin, when)
                    rows.append(("Intérêts", f"Intérêts de l'année {index}", when, interest))
                begin = when
        repayment = view.holding.repayment
        if repayment is not None:
            rows.append(("Remboursement", "Capital remboursé", repayment.paid_on, repayment.capital))
            if repayment.interest > 0:
                rows.append(("Intérêts", "Intérêts reçus", repayment.paid_on, repayment.interest))
        return sorted(rows, key=lambda r: r[2], reverse=True)

    def _operation_row(self, kind: str, label: str, when: date, amount: Decimal) -> QWidget:
        styles = {
            "Souscription": (theme.ACCENT_SOFT, theme.ACCENT_HOVER),
            "Intérêts": (theme.GAIN_SOFT, theme.GAIN),
            "Remboursement": (theme.WARNING_SOFT, theme.WARNING),
        }
        background, foreground = styles[kind]
        badge = QLabel(kind)
        badge.setFixedWidth(96)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 6px; "
            "padding: 3px 0; font-size: 11px; font-weight: 600;"
        )
        text = QLabel(label)
        day = QLabel(f"{when:%d/%m/%Y}")
        day.setProperty("role", "hint")
        figures = QVBoxLayout()
        figures.setSpacing(2)
        figures.addWidget(text)
        figures.addWidget(day)
        total = QLabel(signed_money(amount, CURRENCY))
        total.setFont(theme.mono_font(13))
        if kind == "Intérêts":
            total.setStyleSheet(f"color: {theme.GAIN};")
        line = QWidget()
        line_layout = QHBoxLayout(line)
        line_layout.setContentsMargins(0, 10, 0, 10)
        line_layout.setSpacing(12)
        line_layout.addWidget(badge)
        line_layout.addLayout(figures, 1)
        line_layout.addWidget(total)
        return line

    # -- interactions --

    def _on_row_changed(self, current_row: int, *_: int) -> None:
        if 0 <= current_row < len(self._views):
            self._selected = self._views[current_row].asset_id
            self._show_selected(self._today())

    def select(self, asset_id: int) -> None:
        """Choisit la ligne affichée dans la fiche (appliquée à la prochaine actualisation)."""
        self._selected = asset_id

    def _show_due(self) -> None:
        due = self._first_due()
        if due is None:
            return
        self._selected = due.asset_id
        self.refresh()

    def _edit_exposure(self, view: NoteView | None) -> None:
        if view is None or self._exposure is None:
            return
        if edit_exposure(self, self._exposure, view.asset_id, view.holding.asset.name):
            self._selected = view.asset_id
            self.refresh()

    def _new_note(self) -> None:
        dialog = TermNoteDialog(self, self._today())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            name, details = dialog.form()
            self._selected = self._repository.create(name, details).asset.id
        except ValueError as error:
            QMessageBox.warning(self, "Bon de caisse invalide", str(error))
        self.refresh()

    def _edit_note(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        dialog = TermNoteDialog(self, self._today(), view.holding)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            name, details = dialog.form()
            self._repository.update(view.asset_id, name, details)
        except ValueError as error:
            QMessageBox.warning(self, "Bon de caisse invalide", str(error))
        self.refresh()

    def _repay(self) -> None:
        view = self._selected_view()
        if view is None or view.repaid:
            return
        dialog = RepayDialog(self, view, self._today())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._repository.repay(view.asset_id, dialog.form())
        except ValueError as error:
            QMessageBox.warning(self, "Remboursement refusé", str(error))
        self.refresh()

    def _cancel_repayment(self) -> None:
        view = self._selected_view()
        if view is None or not view.repaid:
            return
        answer = QMessageBox.question(
            self,
            "Annuler le remboursement",
            "Le bon redeviendra détenu et le remboursement saisi sera effacé. Continuer ?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._repository.cancel_repayment(view.asset_id)
            self.refresh()

    def _delete_note(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        answer = QMessageBox.question(
            self,
            "Supprimer le bon de caisse",
            f"Supprimer « {view.holding.asset.name} » ? Cette action est définitive.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._repository.delete(view.asset_id)
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
