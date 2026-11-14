"""Carte « Répartition par secteur et par zone » de l'écran Portefeuille."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.domain.exposure import (
    NOT_APPLICABLE,
    UNCLASSIFIED,
    Category,
    Contribution,
    ExposedAsset,
    ExposureKind,
    ExposureReport,
    exposure_report,
)
from assets_tracker.domain.models import InstrumentType
from assets_tracker.ui import theme
from assets_tracker.ui.formatting import (
    ASSET_LABELS,
    NBSP,
    compact_money,
    format_number,
    money,
)

MISSING_COLOR = "#5A6379"
MAX_MISSING_SHOWN = 3
SCOPES: tuple[tuple[str, InstrumentType | None], ...] = (
    ("Portefeuille", None),
    ("Actions", InstrumentType.STOCK),
    ("Obligations", InstrumentType.BOND),
    ("Fonds", InstrumentType.FUND),
)
VIEWS = (("Secteurs", ExposureKind.SECTOR), ("Zones géographiques", ExposureKind.ZONE))
_TYPE_ORDER = (
    InstrumentType.STOCK,
    InstrumentType.FUND,
    InstrumentType.BOND,
    InstrumentType.CASH,
    InstrumentType.COMMODITY,
)


def _pct(value: Decimal, places: int = 1) -> str:
    return f"{format_number(value, places)}{NBSP}%"


def _color(instrument: InstrumentType) -> str:
    return theme.ASSET_COLORS[instrument.value]


class StackedBar(QWidget):
    """Barre découpée en segments (valeur, couleur), sur une fraction `fill` de la largeur."""

    def __init__(self, height: int = 12, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[tuple[Decimal, str]] = []
        self._fill = 1.0
        self._hatched = False
        self.setFixedHeight(height)

    def set_segments(
        self, segments: Sequence[tuple[Decimal, str]], fill: float = 1.0, hatched: bool = False
    ) -> None:
        self._segments = [(v, c) for v, c in segments if v > 0]
        self._fill = max(0.0, min(1.0, fill))
        self._hatched = hatched
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        total = sum((v for v, _ in self._segments), Decimal(0))
        if total <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        gap = 2.0
        usable = max(6.0, self.width() * self._fill) - gap * (len(self._segments) - 1)
        x = 0.0
        for value, color in self._segments:
            width = max(1.0, usable * float(value / total))
            rect = QRectF(x, 0, width, self.height())
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(rect, 3, 3)
            if self._hatched:
                painter.setBrush(QBrush(QColor("#2E3446"), Qt.BrushStyle.BDiagPattern))
                painter.drawRoundedRect(rect, 3, 3)
            x += width + gap


class _CategoryRow(QPushButton):
    """Une catégorie : nom, barre découpée par type d'actif, part et montant."""

    def __init__(self, category: Category, fill: float) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setAccessibleName(
            f"{category.name} : {_pct(category.percent)}, {compact_money(category.value, 'EUR')}"
        )
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 10px; "
            "text-align: left; }"
            f"QPushButton:hover {{ background: {theme.HOVER_ROW}; }}"
            f"QPushButton:checked {{ background: {theme.HOVER_ROW}; "
            f"border-left: 3px solid {theme.ACCENT}; }}"
        )
        muted = category.name in (UNCLASSIFIED, NOT_APPLICABLE)
        name = QLabel(category.name)
        name.setMinimumWidth(110)
        name.setMaximumWidth(150)
        color = theme.WARNING if category.name == UNCLASSIFIED else theme.TEXT_MUTED
        name.setStyleSheet(
            f"color: {color}; font-style: italic;" if muted else "font-weight: 500;"
        )
        hatched = category.name == UNCLASSIFIED
        bar = StackedBar()
        segments = [
            (category.by_type[t], MISSING_COLOR if hatched else _color(t))
            for t in _TYPE_ORDER
            if t in category.by_type
        ]
        bar.set_segments(segments, fill, hatched)
        share = QLabel(_pct(category.percent))
        share.setFont(theme.mono_font(13))
        share.setFixedWidth(64)
        share.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value = QLabel(compact_money(category.value, "EUR"))
        value.setFont(theme.mono_font(13))
        value.setProperty("role", "muted")
        value.setFixedWidth(84)
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(14)
        for widget, stretch in ((name, 0), (bar, 1), (share, 0), (value, 0)):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(widget, stretch)


class PortfolioExposureCard(QFrame):
    """Secteurs ou zones de tout le portefeuille, ou d'un seul type d'actif.

    `set_assets` reçoit les actifs valorisés en EUR ; la carte calcule et affiche le rapport.
    Cliquer une catégorie en détaille les contributeurs ; « À compléter » ouvre la fiche.
    """

    asset_requested = Signal(object, int)  # (InstrumentType, identifiant de l'actif)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self._assets: list[ExposedAsset] = []
        self._kind = ExposureKind.SECTOR
        self._scope: InstrumentType | None = None
        self._selected: str | None = None
        self._report: ExposureReport | None = None
        self._rows: list[_CategoryRow] = []

        title = QLabel("Répartition par secteur et par zone")
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 18px;")
        subtitle = QLabel(
            "Chaque actif est pondéré par sa valeur en EUR et ventilé selon ses secteurs et zones."
        )
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(subtitle)

        self._view_chips = self._chips(
            [(label, kind) for label, kind in VIEWS], self._set_kind, "Type de répartition"
        )
        self._scope_chips = self._chips(
            [(label, scope) for label, scope in SCOPES], self._set_scope, "Périmètre"
        )
        controls = QHBoxLayout()
        controls.setSpacing(16)
        for chips in (self._view_chips, self._scope_chips):
            controls.addLayout(chips[0])
        controls.addStretch(1)

        self._coverage_text = QLabel()
        self._coverage_bar = StackedBar(6)
        self._missing = QHBoxLayout()
        self._missing.setSpacing(8)
        coverage_line = QHBoxLayout()
        coverage_line.setSpacing(16)
        coverage_box = QVBoxLayout()
        coverage_box.setSpacing(6)
        coverage_box.addWidget(self._coverage_text)
        coverage_box.addWidget(self._coverage_bar)
        coverage_line.addLayout(coverage_box, 1)
        coverage_line.addLayout(self._missing)
        self._coverage = QFrame()
        self._coverage.setObjectName("coverageBox")
        self._coverage.setStyleSheet(
            f"QFrame#coverageBox {{ background: {theme.RAISED}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 12px; }}"
        )
        coverage_layout = QVBoxLayout(self._coverage)
        coverage_layout.setContentsMargins(16, 12, 16, 12)
        coverage_layout.addLayout(coverage_line)

        self._legend = QLabel()
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setProperty("role", "muted")
        self._list = QVBoxLayout()
        self._list.setSpacing(4)
        self._stats = QLabel()
        self._stats.setProperty("role", "muted")
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._legend)
        left.addLayout(self._list)
        left.addWidget(self._stats)
        left.addStretch(1)

        self._d_caption = QLabel()
        self._d_caption.setProperty("role", "caption")
        self._d_name = QLabel()
        self._d_name.setStyleSheet("font-size: 22px; font-weight: 700;")
        self._d_value = QLabel()
        self._d_value.setFont(theme.mono_font(14))
        self._d_bar = StackedBar(8)
        self._d_items = QVBoxLayout()
        self._d_items.setSpacing(0)
        self._d_note = QLabel()
        self._d_note.setProperty("role", "muted")
        self._d_note.setWordWrap(True)
        self._detail = QFrame()
        self._detail.setObjectName("exposureDetail")
        self._detail.setStyleSheet(
            f"QFrame#exposureDetail {{ background: {theme.RAISED}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 12px; }}"
        )
        self._detail.setMinimumWidth(260)
        detail_layout = QVBoxLayout(self._detail)
        detail_layout.setContentsMargins(18, 18, 18, 18)
        detail_layout.setSpacing(12)
        for widget in (self._d_caption, self._d_name, self._d_value, self._d_bar):
            detail_layout.addWidget(widget)
        detail_layout.addLayout(self._d_items)
        detail_layout.addWidget(self._d_note)
        detail_layout.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(24)
        body.addLayout(left, 3)
        body.addWidget(self._detail, 2)

        self._empty = QLabel("Rien à répartir pour ce périmètre.")
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)
        layout.addLayout(heading)
        layout.addLayout(controls)
        layout.addWidget(self._coverage)
        layout.addLayout(body)
        layout.addWidget(self._empty)

    def _chips(
        self, items: list[tuple[str, Any]], handler: Callable[[Any], None], label: str
    ) -> tuple[QHBoxLayout, list[QPushButton]]:
        layout = QHBoxLayout()
        layout.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons: list[QPushButton] = []
        for index, (text, value) in enumerate(items):
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setProperty("variant", "chip")
            chip.setChecked(index == 0)
            chip.setAccessibleDescription(label)
            chip.clicked.connect(lambda _=False, v=value: handler(v))
            group.addButton(chip)
            buttons.append(chip)
            layout.addWidget(chip)
        return layout, buttons

    # -- données --

    def set_assets(self, assets: Sequence[ExposedAsset]) -> None:
        self._assets = list(assets)
        self._render()

    def _set_kind(self, kind: ExposureKind) -> None:
        self._kind, self._selected = kind, None
        self._render()

    def _set_scope(self, scope: InstrumentType | None) -> None:
        self._scope, self._selected = scope, None
        self._render()

    def _select(self, name: str) -> None:
        self._selected = name
        self._render()

    # -- affichage --

    def _render(self) -> None:
        report = exposure_report(self._assets, self._kind, self._scope)
        self._report = report
        categories = report.categories
        names = [c.name for c in categories]
        if self._selected not in names:
            self._selected = names[0] if names else None
        present = bool(categories)
        self._empty.setVisible(not present)
        self._coverage.setVisible(present)
        self._detail.setVisible(present)

        for row in self._rows:
            self._list.removeWidget(row)
            row.deleteLater()
        self._rows = []
        biggest = max((c.value for c in categories), default=Decimal(1))
        used: list[InstrumentType] = []
        for category in categories:
            row = _CategoryRow(category, float(category.value / biggest))
            row.setChecked(category.name == self._selected)
            row.clicked.connect(lambda _=False, n=category.name: self._select(n))
            self._list.addWidget(row)
            self._rows.append(row)
            used += [t for t in _TYPE_ORDER if t in category.by_type and t not in used]
        self._legend.setText(
            "&nbsp;&nbsp;&nbsp;".join(
                f"<span style='color:{_color(t)}'>■</span> {ASSET_LABELS[t]}"
                for t in _TYPE_ORDER
                if t in used
            )
        )
        self._fill_stats(report)
        self._fill_coverage(report)
        selected = next((c for c in categories if c.name == self._selected), None)
        if selected is not None:
            self._fill_detail(selected)

    def _fill_stats(self, report: ExposureReport) -> None:
        real = [c for c in report.categories if c.name not in (UNCLASSIFIED, NOT_APPLICABLE)]
        if not real:
            self._stats.setText("")
            return
        noun = "secteurs" if self._kind is ExposureKind.SECTOR else "zones"
        top3 = sum((c.percent for c in real[:3]), Decimal(0))
        self._stats.setText(
            f"{len(real)} {noun} renseignés · trois premiers : {_pct(top3)} · "
            f"plus grande part : {real[0].name} ({_pct(real[0].percent)})"
        )

    def _fill_coverage(self, report: ExposureReport) -> None:
        coverage = report.coverage
        if coverage is None:
            self._coverage.setVisible(False)
            return
        self._coverage_text.setText(f"Données renseignées : {_pct(coverage)} de la valeur")
        color = theme.WARNING if report.missing else theme.GAIN
        self._coverage_bar.set_segments(
            [(coverage, color), (Decimal(100) - coverage, theme.BORDER_STRONG)]
        )
        while self._missing.count():
            item = self._missing.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        if not report.missing:
            done = QLabel("Tous les actifs sont renseignés.")
            done.setStyleSheet(f"color: {theme.GAIN}; font-weight: 600;")
            self._missing.addWidget(done)
            return
        head = QLabel("À compléter")
        head.setStyleSheet(f"color: {theme.WARNING}; font-weight: 600;")
        self._missing.addWidget(head)
        for asset in report.missing[:MAX_MISSING_SHOWN]:
            button = QPushButton(f"{asset.name} · {compact_money(asset.value, 'EUR')}")
            button.setEnabled(asset.asset_id is not None)
            button.clicked.connect(lambda _=False, a=asset: self._request(a))
            self._missing.addWidget(button)
        extra = len(report.missing) - MAX_MISSING_SHOWN
        if extra > 0:
            self._missing.addWidget(QLabel(f"+ {extra} autre(s)"))

    def _request(self, asset: ExposedAsset) -> None:
        if asset.asset_id is not None:
            self.asset_requested.emit(asset.instrument, asset.asset_id)

    def _fill_detail(self, category: Category) -> None:
        noun = "SECTEUR" if self._kind is ExposureKind.SECTOR else "ZONE"
        self._d_caption.setText(f"{noun} · DÉTAIL")
        self._d_name.setText(category.name)
        self._d_value.setText(
            f"{money(category.value, 'EUR')} · {_pct(category.percent)} du périmètre"
        )
        hatched = category.name == UNCLASSIFIED
        self._d_bar.set_segments(
            [
                (category.by_type[t], MISSING_COLOR if hatched else _color(t))
                for t in _TYPE_ORDER
                if t in category.by_type
            ],
            1.0,
            hatched,
        )
        while self._d_items.count():
            item = self._d_items.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        for contribution in category.contributions:
            self._d_items.addWidget(self._contribution_row(contribution))
        if hatched:
            note = "Ces actifs n'ont ni secteur ni zone : ouvrez leur fiche, bouton « Modifier »."
        elif category.name == NOT_APPLICABLE:
            note = "Le cash et les matières premières n'ont pas de zone géographique."
        else:
            note = "Montants en EUR, pondérés par la part de chaque actif dans cette catégorie."
        self._d_note.setText(note)

    def _contribution_row(self, contribution: Contribution) -> QWidget:
        asset = contribution.asset
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {_color(asset.instrument)};")
        name = QLabel(asset.name)
        name.setStyleSheet("font-weight: 500;")
        if contribution.missing:
            detail = f"{ASSET_LABELS[asset.instrument]} · répartition non renseignée"
        else:
            detail = (
                f"{ASSET_LABELS[asset.instrument]} · "
                f"{format_number(contribution.weight, 0)}{NBSP}% de l'actif"
            )
        sub = QLabel(detail)
        sub.setProperty("role", "hint")
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(name)
        text.addWidget(sub)
        amount = QLabel(compact_money(contribution.amount, "EUR"))
        amount.setFont(theme.mono_font(13))
        line = QWidget()
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(12)
        layout.addWidget(dot)
        layout.addLayout(text, 1)
        if contribution.missing and asset.asset_id is not None:
            complete = QPushButton("Compléter")
            complete.clicked.connect(lambda _=False, a=asset: self._request(a))
            layout.addWidget(complete)
        layout.addWidget(amount)
        return line
