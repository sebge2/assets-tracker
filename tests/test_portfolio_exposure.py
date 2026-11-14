from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from assets_tracker.db.bond_repository import BondRepository
from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.db.commodity_repository import CommodityRepository
from assets_tracker.db.connection import connect
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.fund_repository import FundRepository
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.db.stock_repository import StockRepository
from assets_tracker.domain.bonds import BondDetails, CouponFrequency
from assets_tracker.domain.exposure import (
    NOT_APPLICABLE,
    UNCLASSIFIED,
    ExposedAsset,
    ExposureEntry,
    ExposureKind,
)
from assets_tracker.domain.funds import FundDetails, ValuationSource
from assets_tracker.domain.models import InstrumentType, TransactionType
from assets_tracker.domain.stocks import StockDetails
from assets_tracker.ui.main_window import MainWindow
from assets_tracker.ui.portfolio_exposure import PortfolioExposureCard
from assets_tracker.ui.portfolio_page import PortfolioPage

D = Decimal
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0b" * 32)
    yield connection
    connection.close()


def entry(name: str, weight: int = 100) -> ExposureEntry:
    return ExposureEntry(name, D(weight))


def seed(conn: Any) -> dict[str, int]:
    """Une action et un fonds renseignés, une obligation sans aucune répartition."""
    ids: dict[str, int] = {}
    stocks, funds, bonds = StockRepository(conn), FundRepository(conn), BondRepository(conn)
    stock = stocks.create(
        "Air Liquide", "AI.PA", "EUR", StockDetails("Euronext Paris", None, ValuationSource.MANUAL)
    )
    fund = funds.create(
        "Actions Monde",
        "FR0010756098",
        "EUR",
        FundDetails("Assurance-vie", None, ValuationSource.MANUAL, None),
    )
    bond = bonds.create(
        "OAT 2031",
        "FR0000000001",
        "EUR",
        BondDetails(
            "État", D(1000), D(3), CouponFrequency.ANNUAL, date(2031, 5, 25), ValuationSource.MANUAL
        ),
    )
    assert stock.asset.id and fund.asset.id and bond.asset.id
    ids.update(stock=stock.asset.id, fund=fund.asset.id, bond=bond.asset.id)
    buy = TransactionType.BUY
    stocks.add_transaction(stock.asset.id, buy, date(2026, 2, 2), D(10), D(100), D(0))
    stocks.record_quote(stock.asset.id, TODAY, D(110), NOW, ValuationSource.MANUAL)  # 1 100 €
    funds.add_transaction(fund.asset.id, buy, date(2026, 2, 2), D(10), D(100), D(0))
    funds.record_valuation(fund.asset.id, TODAY, D(120), NOW)  # 1 200 €
    bonds.add_transaction(bond.asset.id, buy, date(2026, 3, 2), D(2), D(98), D(0))
    bonds.record_quote(bond.asset.id, TODAY, D(101), NOW, ValuationSource.MANUAL)  # 2 020 €
    exposure = ExposureRepository(conn)
    exposure.replace(
        stock.asset.id, [entry("Industrie", 70), entry("Santé", 30)], [entry("Europe")]
    )
    exposure.replace(fund.asset.id, [entry("Technologie")], [entry("Amérique du Nord")])
    return ids


def make_page(qtbot: QtBot, conn: Any, *, with_exposure: bool = True) -> PortfolioPage:
    page = PortfolioPage(
        CashRepository(conn),
        CommodityRepository(conn),
        FundRepository(conn),
        BondRepository(conn),
        StockRepository(conn),
        FxRepository(conn),
        exposure=ExposureRepository(conn) if with_exposure else None,
        today=lambda: TODAY,
    )
    qtbot.addWidget(page)
    page.resize(1200, 900)
    return page


def choose(card: PortfolioExposureCard, scope: str) -> None:
    for chip in card._scope_chips[1]:
        if chip.text() == scope:
            chip.click()
            return
    raise AssertionError(scope)


def test_card_is_hidden_without_an_exposure_repository(qtbot: QtBot, conn: Any) -> None:
    seed(conn)

    page = make_page(qtbot, conn, with_exposure=False)

    assert page._exposure_card.isHidden()


def test_card_splits_the_whole_portfolio_by_sector(qtbot: QtBot, conn: Any) -> None:
    seed(conn)
    page = make_page(qtbot, conn)
    card = page._exposure_card

    assert not card.isHidden()
    report = card._report
    assert report is not None
    by_name = {c.name: c.value for c in report.categories}
    # 1 100 € d'action : 70 % Industrie, 30 % Santé ; fonds 1 200 € en Technologie
    assert by_name["Industrie"] == D(770) and by_name["Santé"] == D(330)
    assert by_name["Technologie"] == D(1200)
    assert by_name[UNCLASSIFIED] == D(2020)  # l'obligation n'a pas de secteur
    assert report.categories[-1].name == UNCLASSIFIED
    assert [a.name for a in report.missing] == ["OAT 2031"]
    assert len(card._rows) == len(report.categories)


def test_scope_chips_limit_the_report_to_one_asset_type(qtbot: QtBot, conn: Any) -> None:
    seed(conn)
    card = make_page(qtbot, conn)._exposure_card

    choose(card, "Actions")
    assert card._report is not None
    assert [c.name for c in card._report.categories] == ["Industrie", "Santé"]
    assert card._report.coverage == D(100)
    assert "100,0" in card._coverage_text.text()

    choose(card, "Obligations")
    assert card._report is not None
    assert [c.name for c in card._report.categories] == [UNCLASSIFIED]
    assert card._report.coverage == D(0)
    assert card._d_name.text() == UNCLASSIFIED


def test_zone_view_marks_cash_and_commodities_not_applicable(qtbot: QtBot, conn: Any) -> None:
    card = PortfolioExposureCard()
    qtbot.addWidget(card)
    card.set_assets(
        [
            ExposedAsset("Apple", InstrumentType.STOCK, D(900), (), (entry("Europe"),), 1),
            ExposedAsset("Comptes cash", InstrumentType.CASH, D(100), (), ()),
        ]
    )

    card._view_chips[1][1].click()  # Zones géographiques

    assert card._report is not None
    assert [c.name for c in card._report.categories] == ["Europe", NOT_APPLICABLE]
    assert card._report.coverage == D(100)
    assert card._kind is ExposureKind.ZONE


def test_selecting_a_category_shows_its_contributors(qtbot: QtBot, conn: Any) -> None:
    seed(conn)
    card = make_page(qtbot, conn)._exposure_card
    assert card._report is not None
    names = [c.name for c in card._report.categories]

    card._rows[names.index("Santé")].click()

    assert card._d_name.text() == "Santé"
    assert card._d_items.count() == 1  # seule l'action Air Liquide y contribue
    assert card._rows[names.index("Santé")].isChecked()


def test_a_missing_asset_button_asks_for_its_sheet(qtbot: QtBot, conn: Any) -> None:
    ids = seed(conn)
    page = make_page(qtbot, conn)

    report = page._exposure_card._report
    assert report is not None
    with qtbot.waitSignal(page.asset_requested) as signal:
        page._exposure_card._request(report.missing[0])

    assert signal.args == [InstrumentType.BOND, ids["bond"]]


def test_main_window_opens_the_asset_sheet_to_complete_it(qtbot: QtBot, conn: Any) -> None:
    ids = seed(conn)
    window = MainWindow(conn)
    qtbot.addWidget(window)

    window.portfolio_page.asset_requested.emit(InstrumentType.BOND, ids["bond"])

    assert window._stack.currentWidget() is window.bonds_page
    assert window.bonds_page._selected == ids["bond"]
