"""Graphiques de l'écran Analyses : barres annuelles, barres empilées, aire de répartition."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from assets_tracker.ui import theme
from assets_tracker.ui.evolution_chart import nice_ticks
from assets_tracker.ui.formatting import compact_money, format_number

ZERO = Decimal(0)
_LEFT = 64
_TOP = 12
_RIGHT = 12
_BOTTOM = 28


def _plot(widget: QWidget) -> QRectF:
    return QRectF(
        _LEFT,
        _TOP,
        max(1.0, widget.width() - _LEFT - _RIGHT),
        max(1.0, widget.height() - _TOP - _BOTTOM),
    )


def _axis_pen() -> QPen:
    return QPen(QColor(theme.BORDER), 1)


def _label(painter: QPainter, rect: QRectF, text: str, align: Qt.AlignmentFlag) -> None:
    painter.setPen(QColor(theme.TEXT_SUBTLE))
    painter.drawText(rect, int(align | Qt.AlignmentFlag.AlignVCenter), text)


class _YearChart(QWidget):
    """Base : une colonne par année, cliquable pour choisir la période."""

    year_clicked = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._years: list[int] = []
        self._selected: int | None = None
        self.setMinimumHeight(240)
        self.setMouseTracking(True)

    def set_selected(self, year: int | None) -> None:
        self._selected = year
        self.update()

    def _column(self, index: int) -> QRectF:
        plot = _plot(self)
        slot = plot.width() / max(1, len(self._years))
        return QRectF(plot.left() + slot * index, plot.top(), slot, plot.height())

    def _index_at(self, x: float) -> int | None:
        for index in range(len(self._years)):
            column = self._column(index)
            if column.left() <= x < column.right():
                return index
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (API Qt)
        index = self._index_at(event.position().x())
        if index is not None and event.position().y() <= self.height() - _BOTTOM + 20:
            self.year_clicked.emit(self._years[index])

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (API Qt)
        index = self._index_at(event.position().x())
        if index is None:
            QToolTip.hideText()
            return
        QToolTip.showText(event.globalPosition().toPoint(), self._tooltip(index), self)

    def _tooltip(self, index: int) -> str:
        raise NotImplementedError

    def _draw_years(self, painter: QPainter) -> None:
        for index, year in enumerate(self._years):
            column = self._column(index)
            if year == self._selected:
                painter.fillRect(column, QColor(theme.ACCENT_SOFT))
        for index, year in enumerate(self._years):
            column = self._column(index)
            rect = QRectF(column.left(), column.bottom() + 4, column.width(), _BOTTOM - 6)
            _label(painter, rect, str(year), Qt.AlignmentFlag.AlignHCenter)


class ReturnBars(_YearChart):
    """Rendement annuel en %, barres vertes au-dessus de zéro et rouges en dessous."""

    def __init__(self) -> None:
        super().__init__()
        self._values: list[Decimal | None] = []
        self.setAccessibleName("Rendement par année civile")

    def set_data(self, years: list[int], values: list[Decimal | None]) -> None:
        self._years = years
        self._values = values
        self.update()

    def _bounds(self) -> tuple[Decimal, Decimal]:
        numbers = [v for v in self._values if v is not None]
        top, bottom = max([ZERO, *numbers]), min([ZERO, *numbers])
        high = nice_ticks(top)[-1] if top > 0 else ZERO
        low = -nice_ticks(-bottom)[-1] if bottom < 0 else ZERO
        return (low, Decimal(1)) if high == low else (low, high)

    def _y(self, value: Decimal, low: Decimal, high: Decimal) -> float:
        plot = _plot(self)
        span = float(high - low) or 1.0
        return plot.bottom() - plot.height() * float(value - low) / span

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._years:
            return
        low, high = self._bounds()
        plot = _plot(self)
        self._draw_years(painter)
        ticks = {ZERO, high, low}
        step = (high - low) / 4
        ticks.update(low + step * i for i in range(5))
        for tick in sorted(ticks):
            y = self._y(tick, low, high)
            painter.setPen(_axis_pen() if tick != ZERO else QPen(QColor(theme.BORDER_STRONG), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            _label(
                painter,
                QRectF(0, y - 9, _LEFT - 8, 18),
                f"{format_number(tick, 0)} %",
                Qt.AlignmentFlag.AlignRight,
            )
        zero = self._y(ZERO, low, high)
        for index, value in enumerate(self._values):
            if value is None:
                continue
            column = self._column(index)
            width = min(48.0, column.width() * 0.5)
            y = self._y(value, low, high)
            rect = QRectF(column.center().x() - width / 2, min(y, zero), width, abs(zero - y))
            painter.fillRect(rect, QColor(theme.GAIN if value >= 0 else theme.LOSS))
            text_rect = QRectF(column.left(), (y - 20) if value >= 0 else (y + 2), column.width(), 18)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                f"{format_number(value, 1)} %",
            )

    def _tooltip(self, index: int) -> str:
        value = self._values[index]
        shown = "—" if value is None else f"{format_number(value)} %"
        return f"{self._years[index]} : {shown}"


class StackedBars(_YearChart):
    """Montants par année, empilés par catégorie."""

    def __init__(self) -> None:
        super().__init__()
        self._stacks: list[list[tuple[str, str, Decimal]]] = []  # par année : (nom, couleur, montant)
        self.setAccessibleName("Revenus encaissés par année")

    def set_data(self, years: list[int], stacks: list[list[tuple[str, str, Decimal]]]) -> None:
        self._years = years
        self._stacks = stacks
        self.update()

    def _top(self) -> Decimal:
        totals = [sum((a for _, _, a in stack), ZERO) for stack in self._stacks]
        return nice_ticks(max(totals, default=Decimal(1)))[-1]

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._years:
            return
        top = self._top()
        plot = _plot(self)
        self._draw_years(painter)
        for tick in nice_ticks(top):
            y = plot.bottom() - plot.height() * float(tick / top)
            painter.setPen(_axis_pen())
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            _label(
                painter,
                QRectF(0, y - 9, _LEFT - 8, 18),
                compact_money(tick, "EUR"),
                Qt.AlignmentFlag.AlignRight,
            )
        for index, stack in enumerate(self._stacks):
            column = self._column(index)
            width = min(56.0, column.width() * 0.5)
            base = plot.bottom()
            for _, color, amount in stack:
                if amount <= ZERO:
                    continue
                height = plot.height() * float(amount / top)
                base -= height
                painter.fillRect(
                    QRectF(column.center().x() - width / 2, base, width, height), QColor(color)
                )

    def _tooltip(self, index: int) -> str:
        stack = self._stacks[index]
        lines = [f"{name} : {format_number(amount)} €" for name, _, amount in stack if amount > ZERO]
        total = sum((a for _, _, a in stack), ZERO)
        return "\n".join([f"{self._years[index]} : {format_number(total)} €", *lines])


class ShareArea(QWidget):
    """Répartition par type d'actif dans le temps, en % de la valeur (aire empilée à 100 %)."""

    def __init__(self) -> None:
        super().__init__()
        self._labels: list[str] = []
        self._series: list[tuple[str, str, list[Decimal]]] = []  # (nom, couleur, parts en %)
        self.setMinimumHeight(240)
        self.setAccessibleName("Répartition du portefeuille dans le temps")

    def set_data(self, labels: list[str], series: list[tuple[str, str, list[Decimal]]]) -> None:
        self._labels = labels
        self._series = series
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        count = len(self._labels)
        if count < 2 or not self._series:
            return
        plot = _plot(self)
        for tick in (0, 25, 50, 75, 100):
            y = plot.bottom() - plot.height() * tick / 100
            painter.setPen(_axis_pen())
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            _label(painter, QRectF(0, y - 9, _LEFT - 8, 18), f"{tick} %", Qt.AlignmentFlag.AlignRight)

        def x_at(i: int) -> float:
            return plot.left() + plot.width() * i / (count - 1)

        lower = [Decimal(0)] * count
        for _, color, shares in self._series:
            upper = [lower[i] + shares[i] for i in range(count)]
            path = QPainterPath()
            path.moveTo(x_at(0), plot.bottom() - plot.height() * float(upper[0]) / 100)
            for i in range(1, count):
                path.lineTo(x_at(i), plot.bottom() - plot.height() * float(upper[i]) / 100)
            for i in range(count - 1, -1, -1):
                path.lineTo(x_at(i), plot.bottom() - plot.height() * float(lower[i]) / 100)
            path.closeSubpath()
            fill = QColor(color)
            fill.setAlpha(210)
            painter.fillPath(path, fill)
            lower = upper
        for i, text in enumerate(self._labels):
            left = min(max(x_at(i) - 30, 0.0), self.width() - 60.0)
            _label(painter, QRectF(left, plot.bottom() + 4, 60, 18), text, Qt.AlignmentFlag.AlignHCenter)
