"""Import des secteurs et zones depuis Yahoo, hors du thread Qt, sans jamais écraser une saisie."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.domain.exposure import ExposureKind
from assets_tracker.domain.exposure_import import ImportedExposure, translate
from assets_tracker.market.exposure import ExposureFetcher


def fetch_all(
    requests: list[tuple[int, str]], fetcher: ExposureFetcher
) -> dict[int, ImportedExposure]:
    """Interroge Yahoo pour chaque (identifiant, symbole) ; un échec n'écrit rien."""
    found: dict[int, ImportedExposure] = {}
    for asset_id, symbol in requests:
        try:
            raw = fetcher(symbol)
        except Exception:
            continue
        imported = translate(raw) if raw is not None else None
        if imported:
            found[asset_id] = imported
    return found


class _Signals(QObject):
    done = Signal(object)


class _ImportTask(QRunnable):
    """Interroge Yahoo dans un thread du pool ; la base n'est jamais touchée ici."""

    def __init__(self, requests: list[tuple[int, str]], fetcher: ExposureFetcher) -> None:
        super().__init__()
        self.signals = _Signals()
        self._requests = requests
        self._fetcher = fetcher

    def run(self) -> None:
        self.signals.done.emit(fetch_all(self._requests, self._fetcher))


class ExposureImporter(QObject):
    """Importe les secteurs et zones d'un actif coté, uniquement à la demande de l'utilisateur.

    `import_now` (bouton « Importer ») remplace les répartitions pour lesquelles Yahoo fournit
    des données, laisse les autres intactes et prévient de l'issue par `manual_finished`.
    Rien n'est jamais importé automatiquement.
    """

    changed = Signal()
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

    def import_now(self, asset_id: int, symbol: str) -> None:
        requests = [(asset_id, symbol)]
        finish = partial(self._finish, requests=requests)
        if not self._background:
            finish(fetch_all(requests, self._fetcher))
            return
        task = _ImportTask(requests, self._fetcher)
        task.signals.done.connect(finish)
        self._tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _finish(self, results: object, *, requests: list[tuple[int, str]]) -> None:
        assert isinstance(results, dict)
        changed = False
        for asset_id, imported in results.items():
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
        if changed:
            self.changed.emit()
        for asset_id, _ in requests:
            self.manual_finished.emit(asset_id, asset_id in results)
