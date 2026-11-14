"""Widgets partagés des répartitions par secteur et par zone : barre, synthèse, fiche."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from html import escape

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind, main_exposure
from assets_tracker.ui import theme
from assets_tracker.ui.exposure_dialog import ExposureDialog, weight_text
from assets_tracker.ui.formatting import NBSP, format_number

Parts = list[tuple[str, Decimal, Decimal]]


class BreakdownBar(QWidget):
    """Barre empilée de la part de chaque secteur ou zone."""

    def __init__(self, name: str, height: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parts: Parts = []
        self._name = name
        self.setFixedHeight(height)
        self.setAccessibleName(name)

    def set_data(self, parts: Parts) -> None:
        self._parts = parts
        self.setAccessibleDescription(
            ". ".join(f"{name} : {format_number(pct, 1)} %" for name, _, pct in parts)
        )
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        if not self._parts:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        gap = 2.0
        usable = max(1.0, self.width() - gap * (len(self._parts) - 1))
        x = 0.0
        for index, (_, _, pct) in enumerate(self._parts):
            width = usable * float(pct) / 100
            painter.setBrush(QColor(theme.SERIES[index % len(theme.SERIES)]))
            painter.drawRoundedRect(QRectF(x, 0, max(width, 1.0), self.height()), 4, 4)
            x += width + gap


def legend_html(parts: Parts) -> str:
    return "&nbsp;&nbsp;&nbsp;".join(
        f"<span style='color:{theme.SERIES[index % len(theme.SERIES)]}'>■</span> "
        f"{escape(name)} <b>{format_number(pct, 1)}{NBSP}%</b>"
        for index, (name, _, pct) in enumerate(parts)
    )


def entries_parts(entries: Sequence[ExposureEntry]) -> Parts:
    """Répartition d'un seul actif, sous la même forme que la synthèse."""
    return [(e.name, e.weight, e.weight) for e in entries]


def _legend_label() -> QLabel:
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    return label


class ExposureSummary(QFrame):
    """Carte de synthèse : répartition par secteur et par zone, pondérée par la valeur en EUR."""

    def __init__(self, subject: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self._blocks: dict[ExposureKind, tuple[QLabel, BreakdownBar, QLabel]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        titles = {
            ExposureKind.SECTOR: "RÉPARTITION PAR SECTEUR",
            ExposureKind.ZONE: "RÉPARTITION GÉOGRAPHIQUE",
        }
        for kind, title in titles.items():
            caption = QLabel(f"{title} · PART DE {subject}, EN EUR")
            caption.setProperty("role", "caption")
            bar = BreakdownBar(title.capitalize())
            legend = _legend_label()
            layout.addWidget(caption)
            layout.addWidget(bar)
            layout.addWidget(legend)
            self._blocks[kind] = (caption, bar, legend)

    def set_data(self, sectors: Parts, zones: Parts) -> None:
        for kind, parts in ((ExposureKind.SECTOR, sectors), (ExposureKind.ZONE, zones)):
            caption, bar, legend = self._blocks[kind]
            bar.set_data(parts)
            legend.setText(legend_html(parts))
            for widget in (caption, bar, legend):
                widget.setVisible(bool(parts))
        self.setVisible(bool(sectors or zones))

    def legend_text(self, kind: ExposureKind) -> str:
        return self._blocks[kind][2].text()


class ExposureSection(QWidget):
    """Section « Secteurs et zones » de la fiche d'un actif, avec le bouton Modifier."""

    edit_requested = Signal()
    import_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        caption = QLabel("SECTEURS ET ZONES")
        caption.setProperty("role", "caption")
        self._import = QPushButton("Importer")
        self._import.setAccessibleName("Importer les secteurs et zones depuis Yahoo")
        self._import.setToolTip("Récupère les secteurs et zones depuis Yahoo Finance")
        self._import.clicked.connect(lambda _=False: self.import_requested.emit())
        self._import.setVisible(False)
        self._edit = QPushButton("Modifier")
        self._edit.setAccessibleName("Modifier les secteurs et zones")
        self._edit.clicked.connect(lambda _=False: self.edit_requested.emit())
        head = QHBoxLayout()
        head.addWidget(caption, 1)
        head.addWidget(self._import)
        head.addWidget(self._edit)
        self._bars: dict[ExposureKind, tuple[BreakdownBar, QLabel]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(head)
        for kind, name in (
            (ExposureKind.SECTOR, "Secteurs"),
            (ExposureKind.ZONE, "Zones géographiques"),
        ):
            bar = BreakdownBar(name, height=10)
            legend = _legend_label()
            layout.addWidget(bar)
            layout.addWidget(legend)
            self._bars[kind] = (bar, legend)

    def set_import_available(self, available: bool) -> None:
        """Affiche le bouton « Importer » (données Yahoo) quand la page sait le faire."""
        self._import.setVisible(available)

    def set_data(self, sectors: Sequence[ExposureEntry], zones: Sequence[ExposureEntry]) -> None:
        for kind, entries in ((ExposureKind.SECTOR, sectors), (ExposureKind.ZONE, zones)):
            bar, legend = self._bars[kind]
            parts = entries_parts(entries)
            bar.set_data(parts)
            bar.setVisible(bool(parts))
            legend.setText(
                legend_html(parts)
                if parts
                else "<i>Secteurs non renseignés</i>"
                if kind is ExposureKind.SECTOR
                else "<i>Zones non renseignées</i>"
            )

    def legend_text(self, kind: ExposureKind) -> str:
        return self._bars[kind][1].text()


def main_label(entries: Sequence[ExposureEntry]) -> str:
    """`Technologie · 60 %` ou `—` pour une tuile de fiche."""
    name = main_exposure(entries)
    if name is None:
        return "—"
    weight = next(e.weight for e in entries if e.name == name)
    return f"{name} · {weight_text(weight)}{NBSP}%"


def edit_exposure(
    parent: QWidget, repository: ExposureRepository, asset_id: int, asset_name: str
) -> bool:
    """Ouvre le dialogue, enregistre et renvoie True si la répartition a changé."""
    dialog = ExposureDialog(
        parent,
        asset_name,
        repository.get(asset_id, ExposureKind.SECTOR),
        repository.get(asset_id, ExposureKind.ZONE),
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    try:
        repository.replace(asset_id, dialog.sectors(), dialog.zones())
    except ValueError as error:
        QMessageBox.warning(parent, "Répartition invalide", str(error))
        return False
    return True
