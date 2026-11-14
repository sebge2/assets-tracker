"""Écran « Paramètres » : configuration de l'application (clé d'API Financial Modeling Prep)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.settings_repository import FMP_API_KEY, SettingsRepository
from assets_tracker.domain.exposure_import import ExposureError
from assets_tracker.market.fmp import check_api_key
from assets_tracker.ui import theme
from assets_tracker.ui.spinner import Spinner

KeyChecker = Callable[[str], None]  # lève ExposureError si la clé est refusée


class _Signals(QObject):
    done = Signal(object)  # None = succès, sinon le message d'erreur


class _CheckTask(QRunnable):
    """Vérifie la clé hors du thread Qt."""

    def __init__(self, api_key: str, checker: KeyChecker) -> None:
        super().__init__()
        self.signals = _Signals()
        self._api_key = api_key
        self._checker = checker

    def run(self) -> None:
        self.signals.done.emit(run_check(self._api_key, self._checker))


def run_check(api_key: str, checker: KeyChecker) -> str | None:
    """None si la clé est acceptée, sinon le message à afficher."""
    try:
        checker(api_key)
    except ExposureError as error:
        return str(error)
    except Exception as error:
        return f"Erreur inattendue : {error}"
    return None


class SettingsPage(QWidget):
    """Saisie et enregistrement de la clé FMP, stockée dans la table `settings`."""

    def __init__(
        self,
        settings: SettingsRepository,
        *,
        key_checker: KeyChecker = check_api_key,
        background: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._checker = key_checker
        self._background = background
        self._tasks: list[_CheckTask] = []

        title = QLabel("Paramètres")
        title.setProperty("role", "title")
        subtitle = QLabel("Configuration de l'application")
        subtitle.setProperty("role", "subtitle")

        caption = QLabel("FINANCIAL MODELING PREP")
        caption.setProperty("role", "caption")
        explain = QLabel(
            "Clé d'API utilisée pour importer les secteurs et zones géographiques des fonds. "
            "Elle est stockée dans la base chiffrée de l'application ; seuls le symbole du fonds "
            "et la clé sont envoyés à FMP."
        )
        explain.setProperty("role", "muted")
        explain.setWordWrap(True)

        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText("Clé d'API FMP")
        self._key.setAccessibleName("Clé d'API FMP")
        self._key.returnPressed.connect(self._save)
        self._show = QCheckBox("Afficher la clé")
        self._show.toggled.connect(self._toggle_echo)

        self._save_button = QPushButton("Enregistrer")
        self._save_button.setProperty("variant", "primary")
        self._save_button.clicked.connect(lambda _=False: self._save())
        self._test_button = QPushButton("Tester la clé")
        self._test_button.clicked.connect(lambda _=False: self._test())
        self._clear_button = QPushButton("Effacer")
        self._clear_button.setProperty("variant", "danger")
        self._clear_button.clicked.connect(lambda _=False: self._clear())
        self._spinner = Spinner(18)
        self._status = QLabel()
        self._status.setProperty("role", "muted")

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._save_button)
        buttons.addWidget(self._test_button)
        buttons.addWidget(self._spinner)
        buttons.addStretch(1)
        buttons.addWidget(self._clear_button)

        card = QFrame()
        card.setProperty("role", "card")
        card.setMaximumWidth(640)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)
        for widget in (caption, explain, self._key, self._show):
            card_layout.addWidget(widget)
        card_layout.addLayout(buttons)
        card_layout.addWidget(self._status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        stored = self._settings.get(FMP_API_KEY)
        self._key.setText(stored or "")
        self._clear_button.setEnabled(stored is not None)
        self._status.setStyleSheet("")
        self._status.setText("Clé enregistrée." if stored else "Aucune clé enregistrée.")

    def _toggle_echo(self, visible: bool) -> None:
        self._key.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )

    def _save(self) -> None:
        try:
            self._settings.set(FMP_API_KEY, self._key.text())
        except ValueError as error:
            QMessageBox.critical(self, "Enregistrement impossible", str(error))
            return
        self.refresh()

    def _clear(self) -> None:
        self._settings.delete(FMP_API_KEY)
        self.refresh()

    def _set_busy(self, busy: bool) -> None:
        self._spinner.set_running(busy)
        for button in (self._save_button, self._test_button, self._clear_button):
            button.setEnabled(not busy)
        self._key.setEnabled(not busy)
        if busy:
            self._status.setText("Vérification de la clé…")

    def _test(self) -> None:
        api_key = self._key.text().strip()
        if not api_key:
            QMessageBox.warning(self, "Clé manquante", "Saisissez d'abord une clé d'API.")
            return
        self._set_busy(True)
        if not self._background:
            self._finish_test(run_check(api_key, self._checker))
            return
        task = _CheckTask(api_key, self._checker)
        task.signals.done.connect(self._finish_test)
        self._tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _finish_test(self, error: object) -> None:
        self._tasks.clear()
        self._set_busy(False)
        self._clear_button.setEnabled(self._settings.get(FMP_API_KEY) is not None)
        if error is None:
            self._status.setText("Clé valide.")
            self._status.setStyleSheet(f"color: {theme.GAIN};")
            return
        self._status.setStyleSheet("")
        self._status.setText("Clé refusée ou service injoignable.")
        QMessageBox.critical(self, "Test de la clé FMP", str(error))
