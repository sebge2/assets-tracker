"""Graphique d'une matière : prix de vos transactions, prix de revient et dernier cours."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from assets_tracker.ui import theme
from assets_tracker.ui.formatting import format_number


class PositionChart(QWidget):
    """Courbe des prix connus (achats, ventes, dernier cours) et ligne du PRU en pointillés."""

    _LEFT = 56
    _TOP = 10
    _BOTTOM = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[date, Decimal]] = []
        self._pru: Decimal | None = None
        self.setMinimumHeight(190)
        self.setAccessibleName("Prix de la matière et prix de revient")

    def set_data(self, points: list[tuple[date, Decimal]], pru: Decimal | None) -> None:
        self._points = sorted(points, key=lambda p: p[0])
        self._pru = pru if pru else None
        if len(self._points) >= 2:
            self.setAccessibleDescription(
                f"De {format_number(self._points[0][1])} à {format_number(self._points[-1][1])} "
                f"euros entre le {self._points[0][0].isoformat()} et "
                f"le {self._points[-1][0].isoformat()}."
            )
        self.update()

    def can_draw(self) -> bool:
        return len(self._points) >= 2

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = QRectF(
            self._LEFT,
            self._TOP,
            max(1.0, self.width() - self._LEFT - 14),
            max(1.0, self.height() - self._TOP - self._BOTTOM),
        )
        if not self.can_draw():
            painter.setPen(QColor(theme.TEXT_SUBTLE))
            painter.drawText(
                plot,
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "Pas assez de prix connus pour tracer une courbe.",
            )
            return
        values = [price for _, price in self._points] + ([self._pru] if self._pru else [])
        low = min(values) * Decimal("0.98")
        high = max(values) * Decimal("1.02")
        span = max(high - low, Decimal("0.0001"))
        start = self._points[0][0]
        days = max(1, (self._points[-1][0] - start).days)

        def point(when: date, price: Decimal) -> QPointF:
            return QPointF(
                plot.left() + plot.width() * (when - start).days / days,
                plot.bottom() - plot.height() * float((price - low) / span),
            )

        painter.setFont(theme.mono_font(10))
        for fraction in (0, 0.5, 1):
            y = plot.bottom() - plot.height() * fraction
            painter.setPen(QPen(QColor(theme.BORDER), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            value = low + span * Decimal(str(fraction))
            painter.setPen(QColor(theme.TEXT_SUBTLE))
            painter.drawText(
                QRectF(0, y - 8, self._LEFT - 8, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                format_number(value, 0 if high > 100 else 1),
            )

        line = QPainterPath(point(*self._points[0]))
        for when, price in self._points[1:]:
            line.lineTo(point(when, price))
        area = QPainterPath(line)
        area.lineTo(plot.right(), plot.bottom())
        area.lineTo(plot.left(), plot.bottom())
        area.closeSubpath()
        gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
        top = QColor(theme.ACCENT)
        top.setAlphaF(0.3)
        bottom = QColor(theme.ACCENT)
        bottom.setAlphaF(0.0)
        gradient.setColorAt(0, top)
        gradient.setColorAt(1, bottom)
        painter.fillPath(area, gradient)

        if self._pru is not None:
            y = point(start, self._pru).y()
            painter.setPen(QPen(QColor(theme.WARNING), 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        pen = QPen(QColor(theme.ACCENT), 2.5)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(line)
        painter.setPen(QPen(QColor(theme.SURFACE), 2))
        painter.setBrush(QColor(theme.ACCENT))
        for when, price in self._points:
            painter.drawEllipse(point(when, price), 3.5, 3.5)

        painter.setPen(QColor(theme.TEXT_SUBTLE))
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 8, 120, 16),
            Qt.AlignmentFlag.AlignLeft,
            self._points[0][0].isoformat(),
        )
        painter.drawText(
            QRectF(plot.right() - 120, plot.bottom() + 8, 120, 16),
            Qt.AlignmentFlag.AlignRight,
            self._points[-1][0].isoformat(),
        )
