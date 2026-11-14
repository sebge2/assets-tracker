"""Bouton « Récupérer depuis Yahoo » des fenêtres de mise à jour d'un cours ou d'une VL."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QWidget

from assets_tracker.domain.exposure_import import ExposureError
from assets_tracker.ui.spinner import Spinner

PriceFetcher = Callable[[], Decimal | None]
PLACES = Decimal("0.0001")


def price_text(price: Decimal) -> str:
    """Cours prêt à être saisi : 4 décimales au plus, sans zéros inutiles ni exposant."""
    return format(price.quantize(PLACES).normalize(), "f")


def run_fetch(fetch: PriceFetcher) -> tuple[Decimal | None, str | None]:
    """(cours, None) ; (None, None) si la source n'a rien ; (None, message) si elle échoue."""
    try:
        return fetch(), None
    except ExposureError as error:
        return None, str(error)
    except Exception as error:
        return None, f"Erreur inattendue : {error}"


class _Signals(QObject):
    done = Signal(object)


class _FetchTask(QRunnable):
    def __init__(self, fetch: PriceFetcher) -> None:
        super().__init__()
        self.signals = _Signals()
        self._fetch = fetch

    def run(self) -> None:
        self.signals.done.emit(run_fetch(self._fetch))


class YahooFetchButton(QWidget):
    """Interroge Yahoo hors du thread Qt (spinner) et émet `fetched(prix)` en cas de succès.

    Une source sans donnée ou une erreur donne une popup ; rien n'est enregistré ici.
    """

    fetched = Signal(object)

    def __init__(
        self, fetch: PriceFetcher, *, background: bool = True, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._fetch = fetch
        self._background = background
        self._tasks: list[_FetchTask] = []
        self._button = QPushButton("Récupérer depuis Yahoo")
        self._button.setAccessibleName("Récupérer le cours depuis Yahoo")
        self._button.clicked.connect(lambda _=False: self.start())
        self._spinner = Spinner(16)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._button)
        layout.addWidget(self._spinner)
        layout.addStretch(1)

    @property
    def busy(self) -> bool:
        return self._spinner.running

    def start(self) -> None:
        if self.busy:
            return
        self._spinner.start()
        self._button.setEnabled(False)
        if not self._background:
            self._finish(run_fetch(self._fetch))
            return
        task = _FetchTask(self._fetch)
        task.signals.done.connect(self._finish)
        self._tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _finish(self, result: object) -> None:
        assert isinstance(result, tuple)
        price, error = result
        self._tasks.clear()
        self._spinner.stop()
        self._button.setEnabled(True)
        if error is not None:
            QMessageBox.critical(self, "Récupération impossible", error)
        elif price is None:
            QMessageBox.warning(
                self, "Récupération impossible", "Yahoo ne fournit pas de cours pour ce symbole."
            )
        else:
            self.fetched.emit(price)
