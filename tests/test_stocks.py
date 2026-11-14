from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.stock_repository import StockRepository
from assets_tracker.domain.funds import ValuationSource
from assets_tracker.domain.models import Position, TransactionType
from assets_tracker.domain.stocks import (
    StockDetails,
    annual_dividends,
    dividend_yield,
    price_change,
    sector_breakdown,
    stock_in_euros,
    stock_totals,
    stock_trade_quote,
    validate_shares,
    validate_ticker,
)

D = Decimal
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0)


def details(
    dividend: str | None = "3.20", sector: str = "Industrie", place: str = "Euronext Paris"
) -> StockDetails:
    return StockDetails(
        place, sector, None if dividend is None else D(dividend), ValuationSource.YAHOO
    )


# --- domaine ---------------------------------------------------------------


def test_details_refuse_a_negative_dividend() -> None:
    with pytest.raises(ValueError):
        details("-1")
    assert details(None).dividend is None


def test_ticker_and_share_validation() -> None:
    assert validate_ticker(" ai.pa ") == "AI.PA"
    for bad in ("", "  ", "AI PA"):
        with pytest.raises(ValueError):
            validate_ticker(bad)
    assert validate_shares(D("0.5")) == D("0.5")  # les fractions sont permises
    for bad_quantity in (D(0), D(-1), D("NaN")):
        with pytest.raises(ValueError):
            validate_shares(bad_quantity)


def test_dividends_and_yield() -> None:
    assert annual_dividends(details(), D(12)) == D("38.40")
    assert annual_dividends(details(None), D(12)) == D(0)
    assert dividend_yield(details(), D(160)) == D(2)
    assert dividend_yield(details(None), D(160)) is None


def test_price_change_uses_the_last_two_quotes() -> None:
    assert price_change([]) is None
    assert price_change([(date(2026, 11, 12), D(100))]) is None
    history = [
        (date(2026, 11, 13), D(102)),
        (date(2026, 11, 12), D(100)),
        (date(2026, 11, 1), D(90)),
    ]
    assert price_change(history) == D(2)


def test_totals_ignore_unvalued_positions_in_gain_and_value() -> None:
    held = Position(1, D(12), D("158.40"), D(0))
    unvalued = Position(2, D(2), D("692.50"), D(0))
    closed = Position(3, D(0), D(0), D(50))
    totals = stock_totals(
        [
            (held, details(), D("171.20")),
            (unvalued, details("6.40"), None),
            (closed, details(), D(100)),
        ]
    )
    assert totals.value == D("2054.40")
    assert totals.invested == D("1900.80") + D("1385.00")
    assert totals.gain == D("153.60")  # la position sans cours n'est pas une perte
    assert totals.dividends == D("38.40") + D("12.80")
    assert totals.unvalued == 1
    assert totals.dividend_yield == D("38.40") / D("2054.40") * 100


def test_stock_in_euros_scales_amounts_and_dividends() -> None:
    position = Position(1, D(15), D("182.50"), D(10))
    converted, converted_details, price = stock_in_euros(
        position, details("1.04"), D("228.30"), D("0.5")
    )
    assert (converted.quantity, converted.pru, converted.realized_pnl) == (D(15), D("91.25"), D(5))
    assert converted_details.dividend == D("0.52")
    assert price == D("114.15")
    assert stock_in_euros(position, details(None), None, D("0.5"))[1:] == (details(None), None)
    totals = stock_totals([(converted, converted_details, price)])
    assert (totals.value, totals.gain, totals.dividends) == (D("1712.25"), D("343.50"), D("7.80"))


def test_sector_breakdown_ranks_and_groups() -> None:
    parts = sector_breakdown(
        [
            ("Technologie", D(300)),
            ("Luxe", D(100)),
            ("", D(100)),
            ("Technologie", D(500)),
            ("X", D(0)),
        ]
    )
    assert [(name, value) for name, value, _ in parts] == [
        ("Technologie", D(800)),
        ("Autre", D(100)),
        ("Luxe", D(100)),
    ]
    assert [percent for _, _, percent in parts] == [D(80), D(10), D(10)]
    assert sector_breakdown([]) == []


def test_trade_quote_adds_fees_to_a_purchase_and_deducts_them_from_a_sale() -> None:
    buy = stock_trade_quote(TransactionType.BUY, D(5), D("171.20"), D("1.90"))
    assert (buy.gross, buy.fees, buy.total) == (D("856.00"), D("1.90"), D("857.90"))
    sell = stock_trade_quote(TransactionType.SELL, D(5), D("171.20"), D("1.90"))
    assert sell.total == D("854.10")
    with pytest.raises(ValueError):
        stock_trade_quote(TransactionType.BUY, D(5), D(0), D(0))
    with pytest.raises(ValueError):
        stock_trade_quote(TransactionType.BUY, D(5), D(10), D(-1))


# --- persistance -----------------------------------------------------------


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[StockRepository]:
    connection: Any = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0d" * 32)
    yield StockRepository(connection)
    connection.close()


def create_air_liquide(repository: StockRepository) -> int:
    holding = repository.create("Air Liquide", "ai.pa", "eur", details())
    assert holding.asset.id is not None
    return holding.asset.id


def test_create_validates_and_normalizes(repository: StockRepository) -> None:
    holding = repository.create(" Air Liquide ", " ai.pa ", "eur", details("3.2", " Industrie "))
    assert (holding.asset.name, holding.asset.symbol, holding.asset.currency) == (
        "Air Liquide",
        "AI.PA",
        "EUR",
    )
    assert holding.details == details("3.2")
    assert holding.valuation is None and holding.position.quantity == D(0)
    with pytest.raises(ValueError):
        repository.create("Autre", "AI.PA", "EUR", details())  # ticker déjà utilisé
    with pytest.raises(ValueError):
        repository.create(" ", "MC.PA", "EUR", details())
    with pytest.raises(ValueError):
        repository.create("LVMH", "MC.PA", " ", details())
    assert len(repository.list_holdings()) == 1


def test_a_missing_dividend_stays_unknown(repository: StockRepository) -> None:
    holding = repository.create("ASML", "ASML.AS", "EUR", details(None, "Technologie"))
    assert holding.details.dividend is None


def test_purchases_include_fees_in_the_pru_and_sales_deduct_them(
    repository: StockRepository,
) -> None:
    asset_id = create_air_liquide(repository)
    repository.add_transaction(
        asset_id, TransactionType.BUY, date(2026, 3, 12), D(10), D("150"), D("10")
    )
    repository.add_transaction(
        asset_id, TransactionType.SELL, date(2026, 6, 2), D(4), D("160"), D("2")
    )
    holding = repository.get_holding(asset_id)
    assert holding is not None
    assert holding.position.quantity == D(6) and holding.position.pru == D("151")
    assert holding.position.realized_pnl == D("34")  # 4 × (160 − 151) − 2


def test_oversell_and_invalid_amounts_are_refused(repository: StockRepository) -> None:
    asset_id = create_air_liquide(repository)
    repository.add_transaction(asset_id, TransactionType.BUY, TODAY, D(2), D(100), D(0))
    with pytest.raises(ValueError):
        repository.add_transaction(asset_id, TransactionType.SELL, TODAY, D(3), D(100), D(0))
    with pytest.raises(ValueError):
        repository.add_transaction(asset_id, TransactionType.BUY, TODAY, D(0), D(100), D(0))
    with pytest.raises(ValueError):
        repository.add_transaction(asset_id, TransactionType.BUY, TODAY, D(1), D(0), D(0))
    with pytest.raises(ValueError):
        repository.add_transaction(asset_id, TransactionType.BUY, TODAY, D(1), D(1), D(-1))
    with pytest.raises(ValueError):
        repository.add_transaction(999, TransactionType.BUY, TODAY, D(1), D(1), D(0))
    assert len(repository.transactions(asset_id)) == 1


def test_quotes_are_stored_per_stock_and_source_follows(repository: StockRepository) -> None:
    first = create_air_liquide(repository)
    second = repository.create("ASML", "ASML.AS", "EUR", details(None)).asset.id
    assert second is not None
    repository.record_quote(first, date(2026, 11, 12), D("170"), NOW, ValuationSource.YAHOO)
    repository.record_quote(first, TODAY, D("171.20"), NOW, ValuationSource.MANUAL)

    holding = repository.get_holding(first)
    assert holding is not None and holding.valuation is not None
    assert holding.valuation.value == D("171.20")
    assert holding.details.valuation_source is ValuationSource.MANUAL
    assert [v.value for v in repository.quote_history(first)] == [D("170"), D("171.20")]
    assert repository.valued_on(TODAY) == {first}
    assert repository.quote_history(second) == []
    with pytest.raises(ValueError):
        repository.record_quote(999, TODAY, D(1), NOW, ValuationSource.MANUAL)


def test_update_changes_the_terms_and_keeps_the_history(repository: StockRepository) -> None:
    asset_id = create_air_liquide(repository)
    repository.add_transaction(asset_id, TransactionType.BUY, TODAY, D(10), D("98.40"), D(0))
    repository.record_quote(asset_id, TODAY, D("101.20"), NOW, ValuationSource.MANUAL)

    updated = repository.update(
        asset_id, " Air Liquide SA ", "ai.pa", "usd", details("3.5", "Chimie", "NYSE")
    )

    assert (updated.asset.name, updated.asset.symbol, updated.asset.currency) == (
        "Air Liquide SA",
        "AI.PA",
        "USD",
    )
    assert updated.details == StockDetails("NYSE", "Chimie", D("3.5"), ValuationSource.MANUAL)
    assert updated.position.quantity == D(10) and updated.position.pru == D("98.40")
    assert updated.valuation is not None and updated.valuation.value == D("101.20")


def test_update_refuses_bad_input_and_a_taken_ticker(repository: StockRepository) -> None:
    first = create_air_liquide(repository)
    repository.create("ASML", "ASML.AS", "EUR", details(None))
    with pytest.raises(ValueError):
        repository.update(first, " ", "AI.PA", "EUR", details())
    with pytest.raises(ValueError):
        repository.update(first, "Air Liquide", "ASML.AS", "EUR", details())
    with pytest.raises(ValueError):
        repository.update(999, "X", "X.PA", "EUR", details())
    repository.update(first, "Air Liquide", "AI.PA", "EUR", details())  # son propre ticker
