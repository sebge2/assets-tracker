"""Courbe en escalier du taux effectif d'un compte cash."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from assets_tracker.ui import theme
from assets_tracker.ui.formatting import percent


class RateChart(QWidget):
    """Escalier du taux effectif (base + prime) depuis l'ouverture jusqu'à aujourd'hui."""

    _LEFT = 64
    _TOP = 10
    _BOTTOM = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps: list[tuple[date, Decimal]] = []
        self._today = date.today()
        self.setMinimumHeight(200)
        self.setAccessibleName("Évolution du taux effectif du compte")

    def set_data(self, steps: list[tuple[date, Decimal]], today: date) -> None:
        self._steps = steps
        self._today = today
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._steps:
            return
        plot = QRectF(
            self._LEFT,
            self._TOP,
            max(1, self.width() - self._LEFT - 8),
            max(1, self.height() - self._TOP - self._BOTTOM),
        )
        start = self._steps[0][0]
        span = max(1, (self._today - start).days)
        y_max = max(max(rate for _, rate in self._steps), Decimal("0.5")) * Decimal("1.25")

        def x_of(when: date) -> float:
            return plot.left() + plot.width() * (when - start).days / span

        def y_of(rate: Decimal) -> float:
            return plot.bottom() - plot.height() * float(rate / y_max)

        line = QPainterPath(QPointF(plot.left(), y_of(self._steps[0][1])))
        for when, rate in self._steps[1:]:
            line.lineTo(x_of(when), line.currentPosition().y())
            line.lineTo(x_of(when), y_of(rate))
        line.lineTo(plot.right(), line.currentPosition().y())

        area = QPainterPath(line)
        area.lineTo(plot.right(), plot.bottom())
        area.lineTo(plot.left(), plot.bottom())
        area.closeSubpath()
        gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
        top = QColor(theme.GAIN)
        top.setAlphaF(0.30)
        bottom = QColor(theme.GAIN)
        bottom.setAlphaF(0.0)
        gradient.setColorAt(0, top)
        gradient.setColorAt(1, bottom)
        painter.fillPath(area, gradient)

        pen = QPen(QColor(theme.GAIN), 2.5)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(line)

        painter.setFont(theme.mono_font(10))
        painter.setPen(QColor(theme.TEXT_SUBTLE))
        label_box = QRectF(0, 0, self._LEFT - 8, 16)
        painter.drawText(
            label_box.translated(0, plot.top() - 4),
            Qt.AlignmentFlag.AlignRight,
            percent(y_max.quantize(Decimal("0.1"))),
        )
        painter.drawText(
            label_box.translated(0, plot.bottom() - 12), Qt.AlignmentFlag.AlignRight, "0 %"
        )
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 6, 120, 16),
            Qt.AlignmentFlag.AlignLeft,
            start.isoformat(),
        )
        painter.drawText(
            QRectF(plot.right() - 120, plot.bottom() + 6, 120, 16),
            Qt.AlignmentFlag.AlignRight,
            "aujourd'hui",
        )
