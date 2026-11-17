from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from assets_tracker.db.connection import connect
from assets_tracker.db.valuation_repository import ValuationRepository
from assets_tracker.domain.models import InstrumentType
from assets_tracker.ui.main_window import AUTO_REFRESH_MS, MainWindow


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0c" * 32)
    yield connection
    connection.close()


def make_window(qtbot: QtBot, conn: Any) -> MainWindow:
    window = MainWindow(conn)
    qtbot.addWidget(window)
    return window


def stub(window: MainWindow, started: dict[str, bool]) -> list[tuple[str, bool]]:
    """Remplace les actualisations réseau des pages ; renvoie les appels reçus."""
    calls: list[tuple[str, bool]] = []

    def make(name: str):  # type: ignore[no-untyped-def]
        def refresh(force: bool = False) -> bool:
            calls.append((name, force))
            return started[name]

        return refresh

    window.stocks_page.refresh_quotes = make("stocks")  # type: ignore[method-assign]
    window.bonds_page.refresh_quotes = make("bonds")  # type: ignore[method-assign]
    window.commodities_page.refresh_prices = make("commodities")  # type: ignore[method-assign]
    return calls


def test_last_fetched_at_is_the_latest_recorded_call(conn: Any) -> None:
    repository = ValuationRepository(conn)
    assert repository.last_fetched_at() is None
    early, late = datetime(2026, 11, 16, 9, 0), datetime(2026, 11, 17, 8, 30)
    repository.record(InstrumentType.STOCK, 1, date(2026, 11, 16), Decimal(10), early)
    repository.record(InstrumentType.BOND, 2, date(2026, 11, 17), Decimal(99), late)
    assert repository.last_fetched_at() == datetime(2026, 11, 17, 8, 30)


def test_portfolio_shows_last_refresh_date(qtbot: QtBot, conn: Any) -> None:
    window = make_window(qtbot, conn)
    page = window.portfolio_page
    assert page._last_refresh.text() == "Aucun cours enregistré"
    page.set_last_refresh(datetime(2026, 11, 17, 14, 5))
    assert page._last_refresh.text() == "Dernière actualisation : 17/11/2026 à 14:05"


def test_button_forces_every_page_and_spins_until_all_finish(qtbot: QtBot, conn: Any) -> None:
    window = make_window(qtbot, conn)
    calls = stub(window, {"stocks": True, "bonds": True, "commodities": False})
    page = window.portfolio_page

    page._refresh_button.click()

    assert sorted(calls) == [("bonds", True), ("commodities", True), ("stocks", True)]
    assert not page._refresh_button.isEnabled()
    assert page._refresh_spinner.running

    ValuationRepository(conn).record(
        InstrumentType.STOCK, 1, date(2026, 11, 17), Decimal(10), datetime(2026, 11, 17, 18, 30)
    )
    window.stocks_page.quotes_refreshed.emit()
    assert not page._refresh_button.isEnabled()  # les obligations n'ont pas fini
    window.bonds_page.quotes_refreshed.emit()

    assert page._refresh_button.isEnabled()
    assert not page._refresh_spinner.running
    assert page._last_refresh.text() == "Dernière actualisation : 17/11/2026 à 18:30"


def test_nothing_to_refresh_does_not_leave_the_button_disabled(qtbot: QtBot, conn: Any) -> None:
    window = make_window(qtbot, conn)
    stub(window, {"stocks": False, "bonds": False, "commodities": False})
    window.portfolio_page._refresh_button.click()
    assert window.portfolio_page._refresh_button.isEnabled()
    assert not window.portfolio_page._refresh_spinner.running


def test_second_request_while_refreshing_is_ignored(qtbot: QtBot, conn: Any) -> None:
    window = make_window(qtbot, conn)
    calls = stub(window, {"stocks": True, "bonds": False, "commodities": False})
    window.refresh_all_quotes()
    window.refresh_all_quotes()
    assert len(calls) == 3  # un seul passage : les pages ne sont pas relancées


def test_auto_refresh_runs_every_four_hours(qtbot: QtBot, conn: Any) -> None:
    window = make_window(qtbot, conn)
    assert AUTO_REFRESH_MS == 4 * 60 * 60 * 1000
    assert window._auto_refresh.isActive()
    assert window._auto_refresh.interval() == AUTO_REFRESH_MS
    calls = stub(window, {"stocks": False, "bonds": False, "commodities": False})
    window._auto_refresh.timeout.emit()
    assert len(calls) == 3 and all(force for _, force in calls)

