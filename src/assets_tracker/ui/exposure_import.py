"""Import des secteurs et zones depuis Yahoo, hors du thread Qt, sans jamais écraser une saisie."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.domain.exposure import ExposureKind
from assets_tracker.domain.exposure_import import ExposureError, ImportedExposure, translate
from assets_tracker.market.exposure import ExposureFetcher


def fetch_all(
    requests: list[tuple[int, str]],
    fetcher: ExposureFetcher,
    errors: list[str] | None = None,
) -> dict[int, ImportedExposure]:
    """Interroge la source pour chaque (identifiant, symbole) ; un échec n'écrit rien.

    Une `ExposureError` (clé refusée, quota, réseau…) est ajoutée à `errors` pour être affichée ;
    toute autre exception est ignorée.
    """
    found: dict[int, ImportedExposure] = {}
    for asset_id, symbol in requests:
        try:
            raw = fetcher(symbol)
        except ExposureError as error:
            if errors is not None:
                errors.append(str(error))
            continue
        except Exception:
            continue
        imported = translate(raw) if raw is not None else None
        if imported:
            found[asset_id] = imported
    return found


class _Signals(QObject):
    done = Signal(object)


class _ImportTask(QRunnable):
    """Interroge la source dans un thread du pool ; la base n'est jamais touchée ici."""

    def __init__(self, requests: list[tuple[int, str]], fetcher: ExposureFetcher) -> None:
        super().__init__()
        self.signals = _Signals()
        self._requests = requests
        self._fetcher = fetcher

    def run(self) -> None:
        errors: list[str] = []
        found = fetch_all(self._requests, self._fetcher, errors)
        self.signals.done.emit((found, errors))


class ExposureImporter(QObject):
    """Importe les secteurs et zones d'un actif coté, uniquement à la demande de l'utilisateur.

    `import_now` (bouton « Importer ») remplace les répartitions pour lesquelles la source fournit
    des données, laisse les autres intactes et prévient de l'issue par `manual_finished`.
    `busy_changed` permet d'afficher un spinner pendant l'appel ; `failed` porte le message d'une
    erreur à afficher. Rien n'est jamais importé automatiquement.
    """

    changed = Signal()
    busy_changed = Signal(bool)
    failed = Signal(str)
    manual_finished = Signal(int, bool)  # (identifiant de l'actif, des données ont été trouvées)

    def __init__(
        self,
        repository: ExposureRepository,
        fetcher: ExposureFetcher,
        *,
        background: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._fetcher = fetcher
        self._background = background
        self._tasks: list[_ImportTask] = []
        self.busy = False

    def _set_busy(self, busy: bool) -> None:
        if busy != self.busy:
            self.busy = busy
            self.busy_changed.emit(busy)

    def import_now(
        self, asset_id: int, symbol: str, fetcher: ExposureFetcher | None = None
    ) -> None:
        """Lance l'import ; `fetcher` remplace la source par défaut."""
        if self.busy:
            return
        source = fetcher or self._fetcher
        requests = [(asset_id, symbol)]
        finish = partial(self._finish, requests=requests)
        self._set_busy(True)
        if not self._background:
            errors: list[str] = []
            found = fetch_all(requests, source, errors)
            finish((found, errors))
            return
        task = _ImportTask(requests, source)
        task.signals.done.connect(finish)
        self._tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _finish(self, results: object, *, requests: list[tuple[int, str]]) -> None:
        assert isinstance(results, tuple)
        found, errors = results
        self._tasks.clear()
        changed = False
        for asset_id, imported in found.items():
            for kind, entries in (
                (ExposureKind.SECTOR, imported.sectors),
                (ExposureKind.ZONE, imported.zones),
            ):
                if not entries:
                    continue  # Yahoo n'a rien pour ce type : la répartition existante reste
                try:
                    self._repository.set(asset_id, kind, entries)
                except ValueError:
                    continue
                changed = True
        self._set_busy(False)
        if changed:
            self.changed.emit()
        if errors:
            self.failed.emit(errors[0])
            return
        for asset_id, _ in requests:
            self.manual_finished.emit(asset_id, asset_id in found)
