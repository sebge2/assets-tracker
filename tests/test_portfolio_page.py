from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.db.commodity_repository import CommodityRepository
from assets_tracker.db.connection import connect
from assets_tracker.domain.cash import CashAccount, InterestFrequency, MovementKind
from assets_tracker.domain.commodities import CommodityDetails, CommodityFamily, CommodityForm
from assets_tracker.domain.models import Transaction, TransactionType
from assets_tracker.ui.allocation_donut import AllocationDonut, DonutSlice
from assets_tracker.ui.main_window import MainWindow
from assets_tracker.ui.portfolio_page import PortfolioPage

D = Decimal
TODAY = date(2026, 11, 13)
NBSP = "\u00a0"
NNBSP = "\u202f"
IBAN = "FR7630006000011234567890189"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x08" * 32)
    yield connection
    connection.close()


def make_page(qtbot: QtBot, conn: Any) -> PortfolioPage:
    page = PortfolioPage(CashRepository(conn), CommodityRepository(conn), today=lambda: TODAY)
    qtbot.addWidget(page)
    page.resize(1200, 900)
    return page


def fill(conn: Any) -> None:
    cash = CashRepository(conn)
    livret = cash.create_account(
        CashAccount(
            None, "Livret", "Banque", IBAN, "EUR", date(2026, 1, 10), InterestFrequency.MONTHLY
        ),
        D("2.5"),
    )
    assert livret.id is not None
    cash.add_movement(livret.id, MovementKind.DEPOSIT, date(2026, 1, 10), D(1000))
    gold = CommodityDetails(
        CommodityFamily.PRECIOUS, CommodityForm.PHYSICAL, "g",
        quote_symbol="GC=F", quote_currency="USD", quote_unit="oz",
    )  # fmt: skip
    holding = CommodityRepository(conn).create_position(
        "Or",
        "XAU",
        gold,
        Transaction(None, 0, TransactionType.BUY, date(2026, 3, 5), D(100), D(60), D(0)),
    )
    assert holding.asset.id is not None
    CommodityRepository(conn).record_price(
        holding.asset.id, D(72), TODAY, datetime(2026, 11, 13, 9, 42)
    )


def texts(page: PortfolioPage) -> list[str]:
    return [label.text() for label in page.findChildren(QLabel)]


def test_empty_portfolio_shows_a_hint(qtbot: QtBot, conn: Any) -> None:
    page = make_page(qtbot, conn)
    assert not page._empty.isHidden()
    assert page._evolution.isHidden() and page._allocation.isHidden()
    assert page._subtitle.text() == "Aucun cours actualisé"


def test_page_shows_totals_and_allocation(qtbot: QtBot, conn: Any) -> None:
    fill(conn)
    page = make_page(qtbot, conn)
    shown = texts(page)

    assert f"8{NNBSP}200,00{NBSP}€" in shown  # 100 g × 72 € + 1 000 € de cash
    assert page._subtitle.text() == "Cours mis à jour à 09:42"
    assert page._empty.isHidden()
    assert page._donut.accessibleDescription().startswith("Matières premières")


def test_period_chips_change_the_chart_window(qtbot: QtBot, conn: Any) -> None:
    fill(conn)
    page = make_page(qtbot, conn)
    assert len(page._chart.dots()) == 11  # janvier → novembre, plafonné par l'historique

    page._chips[3].click()
    assert len(page._chart.dots()) == 3
    page._chips[0].click()
    assert len(page._chart.dots()) == 11
    assert "depuis le début" in page._delta.text()


def test_donut_hover_reports_the_slice_and_resets(qtbot: QtBot) -> None:
    donut = AllocationDonut()
    qtbot.addWidget(donut)
    donut.resize(240, 240)
    donut.set_slices(
        [
            DonutSlice("Cash", "#4FD1C5", D(25), D(25)),
            DonutSlice("Or", "#FF9F7A", D(75), D(75)),
        ],
        D(100),
    )
    center = donut._center()
    ring = donut._outer_radius() - 12

    with qtbot.waitSignal(donut.highlighted) as signal:
        donut._set_hover(
            donut.slice_at(QPointF(center.x() + ring * 0.17, center.y() - ring * 0.98))
        )
    assert signal.args == [0]  # juste à droite du haut : la première part (25 %)
    assert donut.slice_at(QPointF(center.x() - ring, center.y())) == 1  # à gauche : la seconde
    assert donut.slice_at(center) == -1  # le trou central n'est pas une part
    donut.set_highlight(-1)
    assert donut.hovered() == -1


def test_main_window_opens_on_the_portfolio(qtbot: QtBot, conn: Any) -> None:
    window = MainWindow(conn)
    qtbot.addWidget(window)
    assert window._stack.currentWidget() is window.portfolio_page
