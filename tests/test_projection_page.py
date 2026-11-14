from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from assets_tracker.db.connection import connect
from assets_tracker.ui.main_window import MainWindow
from assets_tracker.ui.projection_page import ProjectionPage

NBSP = " "


@pytest.fixture
def page(qtbot: QtBot) -> ProjectionPage:
    widget = ProjectionPage()
    qtbot.addWidget(widget)
    widget.resize(1100, 900)
    widget.show()
    return widget


def test_default_hypotheses_are_simulated_on_open(page: ProjectionPage) -> None:
    results = page.results_text()

    # 25 000 € de départ, 500 €/mois, 6 %, 20 ans, intérêts composés annuels
    assert results["final"] == f"≈ 300 891,93{NBSP}€"
    assert results["contributed"] == f"145 000,00{NBSP}€"
    assert results["interest"] == f"≈ +155 891,93{NBSP}€"
    assert page._final._sub.text() == f"Sans versement : 80 178,39{NBSP}€"
    assert [page._table.item(r, 0).text() for r in range(page._table.rowCount())] == [
        "5 ans",
        "10 ans",
        "15 ans",
        "20 ans",
    ]
    assert page._table.item(0, 2).text() == f"67 278,20{NBSP}€"


def test_changing_hypotheses_and_simulating_updates_results(
    qtbot: QtBot, page: ProjectionPage
) -> None:
    page._initial.setText("0")
    page._monthly.setText("100")
    page._rate.setText("0")
    page._years.setValue(12)

    qtbot.keyClick(page._rate, Qt.Key.Key_Return)

    assert page.results_text()["final"] == f"≈ 14 400,00{NBSP}€"
    assert page.results_text()["interest"] == f"≈ 0,00{NBSP}€"
    assert [page._table.item(r, 0).text() for r in range(page._table.rowCount())] == [
        "5 ans",
        "10 ans",
        "12 ans",
    ]


def test_invalid_input_shows_an_error_and_keeps_previous_results(page: ProjectionPage) -> None:
    before = page.results_text()
    page._rate.setText("abc")

    page.simulate()

    assert not page._error.isHidden()
    assert "Rendement annuel" in page._error.text()
    assert page.results_text() == before

    page._rate.setText("150")
    page.simulate()
    assert "rendement" in page._error.text()

    page._rate.setText("6")
    page.simulate()
    assert page._error.isHidden()


def test_disclaimer_is_always_visible(page: ProjectionPage) -> None:
    labels = [label.text() for label in page.findChildren(type(page._error))]
    assert any("garantie de rendement" in text for text in labels)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x04" * 32)
    yield connection
    connection.close()


def test_main_window_navigates_to_the_projection_page(qtbot: QtBot, conn: Any) -> None:
    window = MainWindow(conn)
    qtbot.addWidget(window)
    window.show()

    labels = [button.text() for button in window._nav_buttons]
    assert labels == [
        "Portefeuille",
        "Comptes cash",
        "Matières premières",
        "Fonds",
        "Obligations",
        "Actions",
        "Simulation",
    ]

    qtbot.mouseClick(window._nav_buttons[7], Qt.MouseButton.LeftButton)

    assert window._stack.currentWidget() is window.projection_page
    assert window._nav_buttons[7].isChecked()
