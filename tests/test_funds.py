from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.fund_repository import FundRepository
from assets_tracker.domain.funds import (
    FundDetails,
    ValuationSource,
    fee_amount,
    fund_totals,
    fund_value,
    is_nav_stale,
    nav_series,
    quantity_from_amount,
    trade_quote,
)
from assets_tracker.domain.models import Position, Transaction, TransactionType

D = Decimal


@pytest.fixture
def conn(tmp_path: Path) -> Any:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x03" * 32)
    yield connection
    connection.close()


def test_fund_subscription_valuation_and_redemption(conn: Any) -> None:
    repository = FundRepository(conn)
    fund = repository.create(
        "Amundi Monde",
        "FR0010756098",
        "EUR",
        FundDetails("PEA", D("0.38"), ValuationSource.MANUAL, None),
    )
    assert fund.asset.id is not None
    repository.add_transaction(
        fund.asset.id,
        TransactionType.BUY,
        date(2026, 1, 2),
        D("10"),
        D("100"),
        fee_amount(D("10"), D("100"), D("1")),
    )
    repository.record_valuation(fund.asset.id, date(2026, 1, 2), D("110"), datetime.now())
    repository.add_transaction(
        fund.asset.id, TransactionType.SELL, date(2026, 2, 2), D("2"), D("120"), D("0")
    )

    holding = repository.get_holding(fund.asset.id)
    assert holding is not None
    assert holding.position.quantity == D("8")
    assert holding.position.pru == D("101")
    assert holding.position.realized_pnl == D("38")
    assert holding.valuation is not None and holding.valuation.value == D("110")


def test_nav_history_lists_recorded_valuations_in_order(conn: Any) -> None:
    repository = FundRepository(conn)
    fund = repository.create(
        "Fonds", "FR0000000001", "EUR", FundDetails("", None, ValuationSource.MANUAL, None)
    )
    assert fund.asset.id is not None
    repository.record_valuation(fund.asset.id, date(2026, 3, 1), D("105"), datetime.now())
    repository.record_valuation(fund.asset.id, date(2026, 1, 1), D("100"), datetime.now())

    history = repository.nav_history(fund.asset.id)

    assert [(v.valuation_date, v.value) for v in history] == [
        (date(2026, 1, 1), D("100")),
        (date(2026, 3, 1), D("105")),
    ]


def test_fund_value_gain_and_percent() -> None:
    result = fund_value(Position(1, D("10"), D("100"), D(0)), D("110"))

    assert (result.value, result.cost, result.gain) == (D("1100"), D("1000"), D("100"))
    assert result.gain_percent == D("10")


def test_fund_value_has_no_percent_without_cost() -> None:
    assert fund_value(Position(1, D(0), D(0), D(0)), D("110")).gain_percent is None


def test_fund_totals_ignore_closed_positions_and_unvalued_gain() -> None:
    totals = fund_totals(
        [
            (Position(1, D("10"), D("100"), D(0)), D("110")),
            (Position(2, D("5"), D("200"), D(0)), None),  # sans VL
            (Position(3, D(0), D(0), D("40")), D("50")),  # soldée
        ]
    )

    assert totals.value == D("1100")
    assert totals.invested == D("2000")  # la position sans VL compte dans le montant investi
    assert totals.gain == D("100")  # mais pas dans la plus-value
    assert totals.gain_percent == D("10")
    assert totals.unvalued == 1


def test_fund_totals_of_nothing_is_zero() -> None:
    totals = fund_totals([])

    assert (totals.value, totals.invested, totals.gain) == (D(0), D(0), D(0))
    assert totals.gain_percent is None


def test_nav_is_stale_after_seven_days() -> None:
    today = date(2026, 11, 13)

    assert not is_nav_stale(date(2026, 11, 6), today)  # 7 jours
    assert is_nav_stale(date(2026, 11, 5), today)  # 8 jours


def test_subscription_quote_adds_fees_to_the_amount_debited() -> None:
    quote = trade_quote(TransactionType.BUY, D("10"), D("214.87"), D("0.5"))

    assert quote.gross == D("2148.70")
    assert quote.fees == D("10.7435")
    assert quote.net == D("2159.4435")


def test_redemption_quote_deducts_fees_from_the_amount_credited() -> None:
    quote = trade_quote(TransactionType.SELL, D("10"), D("214.87"), D("0.5"))

    assert quote.net == D("2137.9565")


def test_quote_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValueError):
        trade_quote(TransactionType.BUY, D(0), D("100"), D(0))


def test_quantity_from_amount_rounds_to_four_decimals() -> None:
    assert quantity_from_amount(D("1000"), D("3")) == D("333.3333")
    assert quantity_from_amount(D("2148.70"), D("214.87")) == D("10.0000")


def test_quantity_from_amount_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError):
        quantity_from_amount(D(0), D("100"))
    with pytest.raises(ValueError):
        quantity_from_amount(D("100"), D(0))


def _trade(day: date, price: str) -> Transaction:
    return Transaction(None, 1, TransactionType.BUY, day, D("1"), D(price))


def test_nav_series_prefers_recorded_valuations() -> None:
    today = date(2026, 11, 13)
    history = [(date(2026, 6, 1), D("100")), (date(2026, 9, 1), D("105"))]

    series = nav_series(history, [_trade(date(2026, 1, 1), "90")], today)

    assert series == history


def test_nav_series_completes_with_trade_prices_while_history_is_short() -> None:
    today = date(2026, 11, 13)
    trades = [_trade(date(2026, 1, 2), "100"), _trade(date(2026, 6, 1), "104")]

    series = nav_series([(date(2026, 6, 1), D("105"))], trades, today)

    assert series == [(date(2026, 1, 2), D("100")), (date(2026, 6, 1), D("105"))]


def test_nav_series_keeps_the_last_twelve_months_only() -> None:
    today = date(2026, 11, 13)
    history = [
        (date(2025, 10, 1), D("90")),
        (date(2026, 1, 1), D("100")),
        (date(2026, 5, 1), D("104")),
    ]

    assert nav_series(history, [], today) == history[1:]
