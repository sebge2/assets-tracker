"""Anneau de répartition : une part par type d'actif, montant au survol."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from assets_tracker.ui import theme
from assets_tracker.ui.formatting import format_number, money


@dataclass(frozen=True, slots=True)
class DonutSlice:
    label: str
    color: str
    value: Decimal
    percent: Decimal


class AllocationDonut(QWidget):
    """Anneau interactif : la part survolée s'épaissit, les autres s'estompent.

    Le centre affiche le total, ou le libellé, le montant et le pourcentage de la part survolée.
    `highlighted` émet l'indice de la part (ou -1) : la légende peut ainsi suivre.
    """

    highlighted = Signal(int)

    _GAP_DEGREES = 2.0
    _THICKNESS = 26.0
    _EXTRA = 8.0  # épaisseur ajoutée à la part survolée

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slices: list[DonutSlice] = []
        self._total = Decimal(0)
        self._hover = -1
        self.setMouseTracking(True)
        self.setMinimumSize(220, 220)
        self.setAccessibleName("Répartition du portefeuille par type d'actif")

    def set_slices(self, slices: list[DonutSlice], total: Decimal) -> None:
        self._slices = slices
        self._total = total
        self._hover = -1
        self.setAccessibleDescription(
            ", ".join(f"{s.label} {format_number(s.percent, 1)} %" for s in slices)
        )
        self.update()

    def set_highlight(self, index: int) -> None:
        """Met une part en avant (-1 : aucune) sans émettre `highlighted`."""
        index = index if 0 <= index < len(self._slices) else -1
        if index != self._hover:
            self._hover = index
            self.update()

    def hovered(self) -> int:
        return self._hover

    # -- géométrie --

    def _center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def _outer_radius(self) -> float:
        return min(self.width(), self.height()) / 2 - self._EXTRA / 2 - 2

    def _spans(self) -> list[tuple[float, float]]:
        """(angle de départ, étendue) en degrés, depuis le haut et dans le sens horaire."""
        total = float(sum((s.value for s in self._slices), Decimal(0)))
        if total <= 0:
            return []
        gap = self._GAP_DEGREES if len(self._slices) > 1 else 0.0
        spans: list[tuple[float, float]] = []
        angle = 0.0
        for item in self._slices:
            sweep = 360.0 * float(item.value) / total
            spans.append((angle + gap / 2, max(0.0, sweep - gap)))
            angle += sweep
        return spans

    def slice_at(self, pos: QPointF) -> int:
        """Indice de la part sous `pos`, -1 hors de l'anneau."""
        center = self._center()
        dx, dy = pos.x() - center.x(), pos.y() - center.y()
        radius = math.hypot(dx, dy)
        outer = self._outer_radius() + self._EXTRA / 2
        if not outer - self._THICKNESS - self._EXTRA <= radius <= outer:
            return -1
        angle = math.degrees(math.atan2(dx, -dy)) % 360.0  # 0° en haut, sens horaire
        total = float(sum((s.value for s in self._slices), Decimal(0)))
        if total <= 0:
            return -1
        start = 0.0
        for index, item in enumerate(self._slices):
            end = start + 360.0 * float(item.value) / total
            if start <= angle < end:
                return index
            start = end
        return len(self._slices) - 1

    # -- interaction --

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (API Qt)
        self._set_hover(self.slice_at(event.position()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802 (API Qt)
        self._set_hover(-1)

    def _set_hover(self, index: int) -> None:
        if index != self._hover:
            self._hover = index
            self.highlighted.emit(index)
            self.update()

    # -- dessin --

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self._center()
        base = self._outer_radius()
        track = QPen(QColor(theme.RAISED), self._THICKNESS)
        painter.setPen(track)
        ring = QRectF(
            center.x() - base + self._THICKNESS / 2,
            center.y() - base + self._THICKNESS / 2,
            (base - self._THICKNESS / 2) * 2,
            (base - self._THICKNESS / 2) * 2,
        )
        painter.drawEllipse(ring)

        for index, ((start, sweep), item) in enumerate(
            zip(self._spans(), self._slices, strict=True)
        ):
            active = index == self._hover
            thickness = self._THICKNESS + (self._EXTRA if active else 0.0)
            color = QColor(item.color)
            if self._hover >= 0 and not active:
                color.setAlphaF(0.35)
            pen = QPen(color, thickness)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            radius = base + (self._EXTRA / 2 if active else 0.0) - thickness / 2
            arc = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            painter.drawArc(arc, int((90.0 - start) * 16), int(-sweep * 16))

        self._paint_center(painter, center, base - self._THICKNESS)

    def _paint_center(self, painter: QPainter, center: QPointF, radius: float) -> None:
        box = QRectF(center.x() - radius + 6, center.y() - 30, (radius - 6) * 2, 60)
        if 0 <= self._hover < len(self._slices):
            item = self._slices[self._hover]
            lines: list[tuple[str, str, QFont, int]] = [
                (item.label, theme.TEXT_MUTED, theme.mono_font(12), 18),
                (money(item.value, "EUR"), theme.TEXT, theme.mono_font(15, bold=True), 24),
                (f"{format_number(item.percent, 1)} %", item.color, theme.mono_font(12), 18),
            ]
        else:
            lines = [
                ("Total", theme.TEXT_MUTED, theme.mono_font(12), 18),
                (money(self._total, "EUR"), theme.TEXT, theme.mono_font(15, bold=True), 24),
            ]
        y = box.top() + (60 - sum(height for *_, height in lines)) / 2
        for text, color, font, height in lines:
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(
                QRectF(box.left(), y, box.width(), height),
                Qt.AlignmentFlag.AlignCenter,
                painter.fontMetrics().elidedText(
                    text, Qt.TextElideMode.ElideRight, int(box.width())
                ),
            )
            y += height
