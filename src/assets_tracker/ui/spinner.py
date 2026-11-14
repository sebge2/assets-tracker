"""Indicateur d'attente : un arc qui tourne, affiché pendant un appel réseau."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from assets_tracker.ui import theme


class Spinner(QWidget):
    """Arc animé, masqué tant qu'il n'est pas lancé (`start` / `stop`)."""

    def __init__(self, size: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)
        self.setAccessibleName("Chargement en cours")
        self.setVisible(False)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        self.setVisible(True)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def set_running(self, running: bool) -> None:
        self.start() if running else self.stop()

    def _advance(self) -> None:
        self._angle = (self._angle - 30) % 360
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(theme.ACCENT), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        margin = 2.0
        rect = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)
        painter.drawArc(rect, self._angle * 16, 270 * 16)
