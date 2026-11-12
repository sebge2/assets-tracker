"""Courbes mensuelles : un solde par compte et le total, avec axes gradués et points survolables."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QToolTip, QWidget

from assets_tracker.ui import theme
from assets_tracker.ui.formatting import compact_money, format_number, money, month_label

_STEPS = (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(5), Decimal(10))


def nice_ticks(peak: Decimal, target: int = 5) -> list[Decimal]:
    """Graduations « rondes » de 0 jusqu'au premier palier au-dessus de `peak`.

    Le pas est un 1, 2, 2,5 ou 5 × une puissance de 10, pour environ `target` intervalles.
    """
    if peak <= 0:
        peak = Decimal(1)
    raw = peak / target
    magnitude = Decimal(10) ** raw.adjusted()
    step = next(m * magnitude for m in _STEPS if m * magnitude >= raw)
    count = math.ceil(peak / step)
    return [step * i for i in range(count + 1)]


@dataclass(frozen=True, slots=True)
class ChartSeries:
    name: str
    color: str
    balances: list[Decimal | None]  # None : compte pas encore ouvert


@dataclass(frozen=True, slots=True)
class Dot:
    """Un point de courbe : sa position à l'écran et ce qu'il représente."""

    pos: QPointF
    name: str
    color: str
    month: date
    value: Decimal
    is_total: bool


class EvolutionChart(QWidget):
    """Soldes de fin de mois : une courbe fine par compte, le total en trait épais.

    Chaque point affiche son montant au survol.
    """

    _LEFT = 84
    _TOP = 14
    _RIGHT = 44  # marge pour le dernier libellé de mois, centré sur son point
    _BOTTOM = 34
    _HIT_RADIUS = 14.0
    _LABEL_SPACING = 76  # espace minimal entre deux libellés de mois, en pixels

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._months: list[date] = []
        self._series: list[ChartSeries] = []
        self._total: list[Decimal] = []
        self._currency = "EUR"
        self._hover: Dot | None = None
        self.setMinimumHeight(280)
        self.setMouseTracking(True)
        self.setAccessibleName("Évolution mensuelle des soldes")

    def set_data(
        self,
        months: list[date],
        series: list[ChartSeries],
        total: list[Decimal],
        currency: str = "EUR",
    ) -> None:
        self._months = months
        self._series = series
        self._total = total
        self._currency = currency
        self._hover = None
        if months and total:
            self.setAccessibleDescription(
                f"Total de {format_number(total[0], 0)} {currency} en {month_label(months[0])} à "
                f"{format_number(total[-1], 0)} {currency} en {month_label(months[-1])}."
            )
        self.update()

    # -- géométrie --

    def _plot(self) -> QRectF:
        return QRectF(
            self._LEFT,
            self._TOP,
            max(1.0, self.width() - self._LEFT - self._RIGHT),
            max(1.0, self.height() - self._TOP - self._BOTTOM),
        )

    def ticks(self) -> list[Decimal]:
        values = [*self._total]
        values += [b for s in self._series for b in s.balances if b is not None]
        return nice_ticks(max(values, default=Decimal(1)))

    def dots(self) -> list[Dot]:
        """Tous les points visibles, le total en dernier (dessiné et survolé en priorité)."""
        if len(self._months) < 2:
            return []
        plot = self._plot()
        top = self.ticks()[-1]
        count = len(self._months)

        def at(index: int, value: Decimal) -> QPointF:
            return QPointF(
                plot.left() + plot.width() * index / (count - 1),
                plot.bottom() - plot.height() * float(value / top),
            )

        dots = [
            Dot(at(i, value), s.name, s.color, self._months[i], value, False)
            for s in self._series
            for i, value in enumerate(s.balances)
            if value is not None
        ]
        dots += [
            Dot(at(i, value), "Total", theme.ACCENT, self._months[i], value, True)
            for i, value in enumerate(self._total)
        ]
        return dots

    def dot_at(self, pos: QPointF) -> Dot | None:
        """Point le plus proche de `pos` dans un rayon de survol (le total gagne les égalités)."""
        best: Dot | None = None
        best_distance = self._HIT_RADIUS
        for dot in self.dots():
            distance = math.hypot(dot.pos.x() - pos.x(), dot.pos.y() - pos.y())
            if distance <= best_distance:
                best, best_distance = dot, distance
        return best

    def tooltip_text(self, dot: Dot) -> str:
        return f"{dot.name} · {month_label(dot.month)}\n{money(dot.value, self._currency)}"

    # -- interaction --

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (API Qt)
        dot = self.dot_at(event.position())
        if dot != self._hover:
            self._hover = dot
            self.update()
        if dot is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(event.globalPosition().toPoint(), self.tooltip_text(dot), self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802 (API Qt)
        self._hover = None
        QToolTip.hideText()
        self.update()

    # -- dessin --

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        if len(self._months) < 2:
            painter.setPen(QColor(theme.TEXT_SUBTLE))
            painter.drawText(
                plot, Qt.AlignmentFlag.AlignCenter, "Au moins deux mois sont nécessaires."
            )
            return
        ticks = self.ticks()
        top = ticks[-1]
        self._paint_axes(painter, plot, ticks, top)

        dots = self.dots()
        for series in [*self._series, None]:
            owned = [d for d in dots if (d.is_total if series is None else d.name == series.name)]
            if not owned:
                continue
            path = self._path_through(owned)
            if series is None:
                self._paint_area(painter, path, plot)
                pen = QPen(QColor(theme.ACCENT), 2.75)
            else:
                pen = QPen(QColor(series.color), 1.75)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)

        self._paint_dots(painter, plot, dots)

    @staticmethod
    def _path_through(dots: list[Dot]) -> QPainterPath:
        path = QPainterPath(dots[0].pos)
        for dot in dots[1:]:
            path.lineTo(dot.pos)
        return path

    @staticmethod
    def _paint_area(painter: QPainter, line: QPainterPath, plot: QRectF) -> None:
        area = QPainterPath(line)
        area.lineTo(line.currentPosition().x(), plot.bottom())
        area.lineTo(plot.left(), plot.bottom())
        area.closeSubpath()
        gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
        top_color = QColor(theme.ACCENT)
        top_color.setAlphaF(0.22)
        bottom_color = QColor(theme.ACCENT)
        bottom_color.setAlphaF(0.0)
        gradient.setColorAt(0, top_color)
        gradient.setColorAt(1, bottom_color)
        painter.fillPath(area, gradient)

    def _paint_axes(
        self, painter: QPainter, plot: QRectF, ticks: list[Decimal], top: Decimal
    ) -> None:
        painter.setFont(theme.mono_font(10))
        label_box = QRectF(0, 0, self._LEFT - 10, 16)
        for tick in ticks:
            y = plot.bottom() - plot.height() * float(tick / top)
            painter.setPen(QPen(QColor(theme.BORDER), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(theme.TEXT_SUBTLE))
            painter.drawText(
                label_box.translated(0, y - 8),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                compact_money(tick, self._currency),
            )

        count = len(self._months)
        spacing = plot.width() / (count - 1)
        every = max(1, math.ceil(self._LABEL_SPACING / spacing))
        for index, month in enumerate(self._months):
            x = plot.left() + spacing * index
            painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
            painter.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 4))
            if (count - 1 - index) % every == 0:
                painter.setPen(QColor(theme.TEXT_SUBTLE))
                painter.drawText(
                    QRectF(x - 40, plot.bottom() + 8, 80, 16),
                    Qt.AlignmentFlag.AlignCenter,
                    month_label(month, short=True),
                )

    def _paint_dots(self, painter: QPainter, plot: QRectF, dots: list[Dot]) -> None:
        hovered = self._hover
        if hovered is not None:
            painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1, Qt.PenStyle.DashLine))
            painter.drawLine(
                QPointF(hovered.pos.x(), plot.top()), QPointF(hovered.pos.x(), plot.bottom())
            )
        for dot in dots:
            radius = 4.0 if dot.is_total else 3.0
            if dot == hovered:
                radius += 2.5
            painter.setPen(QPen(QColor(theme.SURFACE), 1.5))
            painter.setBrush(QColor(dot.color))
            painter.drawEllipse(dot.pos, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
