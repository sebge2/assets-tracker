from __future__ import annotations

import os

# Les tests d'interface tournent sans écran.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Iterator  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_network_refresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """La fenêtre principale ne lance pas d'actualisation réseau en arrière-plan.

    Ouvrir l'écran Actions, Obligations ou Matières premières déclenche un appel Yahoo dans un
    thread ; il se terminerait après la fermeture de la base du test et lèverait une erreur dans
    un test suivant. Les tests qui veulent une actualisation la remplacent eux-mêmes.
    """
    from assets_tracker.ui.main_window import MainWindow

    original = MainWindow.__init__

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self.stocks_page.refresh_quotes = lambda force=False: False
        self.bonds_page.refresh_quotes = lambda force=False: False
        self.commodities_page.refresh_prices = lambda force=False: False

    monkeypatch.setattr(MainWindow, "__init__", init)
    yield
