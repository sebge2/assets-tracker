"""Courbes de projection : capital avec versements, versements cumulés, sans versement."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from assets_tracker.domain.projection import YearPoint
from assets_tracker.ui import theme
from assets_tracker.ui.formatting import NBSP, format_number


class ProjectionChart(QWidget):
    """Trois séries sur l'axe des années : capital, versements cumulés, scénario sans versement."""

    _LEFT = 96
    _TOP = 10
    _BOTTOM = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[YearPoint] = []
        self.setMinimumHeight(220)
        self.setAccessibleName("Trajectoire du capital projeté")

    def set_data(self, points: list[YearPoint]) -> None:
        self._points = points
        if points:
            last = points[-1]
            self.setAccessibleDescription(
                f"Sur {last.year} ans, le capital projeté passe de "
                f"{format_number(points[0].balance, 0)} à {format_number(last.balance, 0)} euros, "
                f"pour {format_number(last.contributed, 0)} euros versés."
            )
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if len(self._points) < 2:
            return
        plot = QRectF(
            self._LEFT,
            self._TOP,
            max(1, self.width() - self._LEFT - 8),
            max(1, self.height() - self._TOP - self._BOTTOM),
        )
        years = self._points[-1].year
        top_value = max(max(p.balance for p in self._points), Decimal(1)) * Decimal("1.08")

        def point(year: int, value: Decimal) -> QPointF:
            return QPointF(
                plot.left() + plot.width() * year / years,
                plot.bottom() - plot.height() * float(value / top_value),
            )

        def path_of(values: list[tuple[int, Decimal]]) -> QPainterPath:
            path = QPainterPath(point(*values[0]))
            for year, value in values[1:]:
                path.lineTo(point(year, value))
            return path

        balance = path_of([(p.year, p.balance) for p in self._points])
        area = QPainterPath(balance)
        area.lineTo(plot.right(), plot.bottom())
        area.lineTo(plot.left(), plot.bottom())
        area.closeSubpath()
        gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
        top = QColor(theme.ACCENT)
        top.setAlphaF(0.35)
        bottom = QColor(theme.ACCENT)
        bottom.setAlphaF(0.0)
        gradient.setColorAt(0, top)
        gradient.setColorAt(1, bottom)
        painter.fillPath(area, gradient)

        painter.setPen(QPen(QColor(theme.TEXT_SUBTLE), 1.75, Qt.PenStyle.DotLine))
        painter.drawPath(path_of([(p.year, p.balance_without_contributions) for p in self._points]))
        painter.setPen(QPen(QColor(theme.WARNING), 2, Qt.PenStyle.DashLine))
        painter.drawPath(path_of([(p.year, p.contributed) for p in self._points]))
        line = QPen(QColor(theme.ACCENT), 2.5)
        line.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(line)
        painter.drawPath(balance)

        painter.setFont(theme.mono_font(10))
        painter.setPen(QColor(theme.TEXT_SUBTLE))
        label_box = QRectF(0, 0, self._LEFT - 8, 16)
        painter.drawText(
            label_box.translated(0, plot.top() - 4),
            Qt.AlignmentFlag.AlignRight,
            f"{format_number(top_value, 0)}{NBSP}€",
        )
        painter.drawText(
            label_box.translated(0, plot.bottom() - 12),
            Qt.AlignmentFlag.AlignRight,
            f"0{NBSP}€",
        )
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 6, 120, 16), Qt.AlignmentFlag.AlignLeft, "Départ"
        )
        painter.drawText(
            QRectF(plot.right() - 120, plot.bottom() + 6, 120, 16),
            Qt.AlignmentFlag.AlignRight,
            f"{years} ans",
        )
