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

from assets_tracker.db.bond_repository import BondRepository
from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.db.commodity_repository import CommodityRepository
from assets_tracker.db.connection import connect
from assets_tracker.db.fund_repository import FundRepository
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.db.stock_repository import StockRepository
from assets_tracker.domain.bonds import BondDetails, CouponFrequency
from assets_tracker.domain.cash import CashAccount, InterestFrequency, MovementKind
from assets_tracker.domain.commodities import CommodityDetails, CommodityFamily, CommodityForm
from assets_tracker.domain.funds import FundDetails, ValuationSource
from assets_tracker.domain.models import Transaction, TransactionType
from assets_tracker.domain.stocks import StockDetails
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
    page = PortfolioPage(
        CashRepository(conn),
        CommodityRepository(conn),
        FundRepository(conn),
        BondRepository(conn),
        StockRepository(conn),
        FxRepository(conn),
        today=lambda: TODAY,
    )
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


def add_fund(conn: Any, currency: str = "EUR") -> None:
    """10 parts achetées 100 le 2 février, valorisées 110 aujourd'hui."""
    funds = FundRepository(conn)
    details = FundDetails("Assurance-vie", None, ValuationSource.MANUAL, None)
    fund = funds.create("Actions Monde", f"FR00000000{currency}", currency, details)
    assert fund.asset.id is not None
    funds.add_transaction(
        fund.asset.id, TransactionType.BUY, date(2026, 2, 2), D(10), D(100), D(0)
    )
    funds.record_valuation(fund.asset.id, TODAY, D(110), datetime(2026, 11, 13, 9, 0))


def add_bond(conn: Any, currency: str = "EUR") -> None:
    """10 titres achetés à 98 % le 2 mars, cotés 101 % aujourd'hui (nominal 1 000)."""
    bonds = BondRepository(conn)
    details = BondDetails(
        "État",
        D(1000),
        D(3),
        CouponFrequency.ANNUAL,
        date(2031, 5, 25),
        ValuationSource.MANUAL,
    )
    bond = bonds.create("OAT 2031", f"FR00000001{currency}", currency, details)
    assert bond.asset.id is not None
    bonds.add_transaction(
        bond.asset.id, TransactionType.BUY, date(2026, 3, 2), D(10), D(98), D("50")
    )
    bonds.record_quote(
        bond.asset.id, TODAY, D(101), datetime(2026, 11, 13, 9, 0), ValuationSource.MANUAL
    )


def add_stock(conn: Any, currency: str = "EUR") -> None:
    """10 actions achetées 100 (frais 4) le 2 février, cotées 110 aujourd'hui."""
    stocks = StockRepository(conn)
    details = StockDetails("Euronext Paris", "Industrie", D(2), ValuationSource.MANUAL)
    stock = stocks.create("Air Liquide", f"AI.{currency}", currency, details)
    assert stock.asset.id is not None
    stocks.add_transaction(
        stock.asset.id, TransactionType.BUY, date(2026, 2, 2), D(10), D(100), D(4)
    )
    stocks.record_quote(
        stock.asset.id, TODAY, D(110), datetime(2026, 11, 13, 9, 0), ValuationSource.MANUAL
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


def test_funds_count_in_the_total_and_the_allocation(qtbot: QtBot, conn: Any) -> None:
    fill(conn)
    add_fund(conn)
    page = make_page(qtbot, conn)
    shown = texts(page)

    assert f"9{NNBSP}300,00{NBSP}€" in shown  # 8 200 € + 10 parts × 110 €
    assert "Fonds" in shown
    assert page._portfolio is not None
    assert [s.asset_type.value for s in page._portfolio.slices] == ["COMMODITY", "FUND", "CASH"]


def test_a_fund_alone_is_enough_to_fill_the_page(qtbot: QtBot, conn: Any) -> None:
    add_fund(conn)
    page = make_page(qtbot, conn)

    assert page._empty.isHidden()
    assert f"1{NNBSP}100,00{NBSP}€" in texts(page)
    assert f"+100,00{NBSP}€" in " ".join(texts(page))  # plus-value latente : 10 × (110 − 100)


def test_funds_not_in_euros_are_left_out_and_reported(qtbot: QtBot, conn: Any) -> None:
    fill(conn)
    add_fund(conn, "USD")
    page = make_page(qtbot, conn)

    assert f"8{NNBSP}200,00{NBSP}€" in texts(page)  # le fonds en dollars n'est pas additionné
    assert "Fonds en USD non inclus (pas de taux de change)." in texts(page)
    assert not page._note.isHidden()


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


def test_a_bond_alone_is_valued_by_its_quote(qtbot: QtBot, conn: Any) -> None:
    add_bond(conn)
    page = make_page(qtbot, conn)

    assert page._empty.isHidden()
    assert f"10{NNBSP}100,00{NBSP}€" in texts(page)  # 10 titres × 101 % de 1 000 €
    # plus-value latente : 10 × (1 010 − 980) ; le coupon couru payé reste hors PRU
    assert f"+300,00{NBSP}€" in " ".join(texts(page))
    assert page._portfolio is not None
    assert [s.asset_type.value for s in page._portfolio.slices] == ["BOND"]


def test_bonds_not_in_euros_are_left_out_and_reported(qtbot: QtBot, conn: Any) -> None:
    fill(conn)
    add_bond(conn, "USD")
    page = make_page(qtbot, conn)

    assert f"8{NNBSP}200,00{NBSP}€" in texts(page)
    assert "Obligations en USD non incluses (pas de taux de change)." in texts(page)


def test_foreign_bonds_and_funds_are_converted_with_the_cached_rate(
    qtbot: QtBot, conn: Any
) -> None:
    fill(conn)
    add_bond(conn, "USD")
    add_fund(conn, "USD")
    FxRepository(conn).record("USD", TODAY, D("0.9"), datetime(2026, 11, 13, 9, 0))

    page = make_page(qtbot, conn)

    # 8 200 € + obligation 10 × 1 010 $ × 0,9 = 9 090 € + fonds 10 parts à 110 $ × 0,9 = 990 €
    assert f"18{NNBSP}280,00{NBSP}€" in texts(page)
    assert page._note.isHidden()


def test_a_stock_alone_is_valued_by_its_quote(qtbot: QtBot, conn: Any) -> None:
    add_stock(conn)
    page = make_page(qtbot, conn)

    assert page._empty.isHidden()
    assert f"1{NNBSP}100,00{NBSP}€" in texts(page)  # 10 actions × 110 €
    assert f"+96,00{NBSP}€" in " ".join(texts(page))  # 1 100 − (1 000 + 4 de frais)
    assert page._portfolio is not None
    assert [s.asset_type.value for s in page._portfolio.slices] == ["STOCK"]


def test_stocks_not_in_euros_are_left_out_without_a_rate_and_converted_with_one(
    qtbot: QtBot, conn: Any
) -> None:
    fill(conn)
    add_stock(conn, "USD")
    page = make_page(qtbot, conn)

    assert f"8{NNBSP}200,00{NBSP}€" in texts(page)
    assert "Actions en USD non incluses (pas de taux de change)." in texts(page)

    FxRepository(conn).record("USD", TODAY, D("0.9"), datetime(2026, 11, 13, 9, 0))
    page.refresh()

    assert f"9{NNBSP}190,00{NBSP}€" in texts(page)  # 8 200 + 10 × 110 $ × 0,9
    assert page._note.isHidden()
