from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.bond_repository import BondRepository
from assets_tracker.db.connection import connect
from assets_tracker.domain.bonds import (
    BondDetails,
    CouponFrequency,
    accrued_coupon,
    annual_coupon,
    bond_totals,
    bond_trade_quote,
    bond_value,
    coupon_dates_around,
    current_yield,
    is_quote_stale,
    maturity_ladder,
    next_coupon,
    price_percent,
    remaining_label,
    trade_amount,
    unit_value,
    validate_quantity,
)
from assets_tracker.domain.funds import ValuationSource
from assets_tracker.domain.models import Position, TransactionType

D = Decimal
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0)


def bond(
    rate: str = "3.00",
    frequency: CouponFrequency = CouponFrequency.ANNUAL,
    maturity: date = date(2031, 5, 25),
) -> BondDetails:
    return BondDetails("État", D(1000), D(rate), frequency, maturity, ValuationSource.YAHOO)


def test_unit_value_and_percent_round_trip() -> None:
    assert unit_value(D("101.20"), D(1000)) == D("1012.00")
    assert price_percent(D("1012"), D(1000)) == D("101.2")
    with pytest.raises(ValueError):
        unit_value(D(0), D(1000))


def test_details_validation() -> None:
    with pytest.raises(ValueError):
        BondDetails("", D(0), D(3), CouponFrequency.ANNUAL, TODAY, ValuationSource.YAHOO)
    with pytest.raises(ValueError):
        BondDetails("", D(1000), D(101), CouponFrequency.ANNUAL, TODAY, ValuationSource.YAHOO)


def test_coupon_dates_follow_maturity_anniversaries() -> None:
    assert coupon_dates_around(bond(), TODAY) == (date(2026, 5, 25), date(2027, 5, 25))
    semi = bond(frequency=CouponFrequency.SEMIANNUAL, maturity=date(2029, 9, 12))
    assert coupon_dates_around(semi, TODAY) == (date(2026, 9, 12), date(2027, 3, 12))
    assert coupon_dates_around(bond(), date(2031, 5, 25)) is None


def test_coupon_date_on_the_day_has_nothing_accrued() -> None:
    assert accrued_coupon(bond(), D(10), date(2026, 5, 25)) == D(0)


def test_month_end_maturity_is_clamped() -> None:
    semi = bond(frequency=CouponFrequency.SEMIANNUAL, maturity=date(2029, 8, 31))
    assert coupon_dates_around(semi, date(2026, 11, 13)) == (date(2026, 8, 31), date(2027, 2, 28))


def test_accrued_coupon_matches_the_mockup() -> None:
    assert annual_coupon(bond(), D(10)) == D(300)
    assert accrued_coupon(bond(), D(10), TODAY) == D("141.37")  # 300 × 172 / 365
    semi = bond("4.50", CouponFrequency.SEMIANNUAL, date(2029, 9, 12))
    assert accrued_coupon(semi, D(8), TODAY) == D("61.66")  # 180 × 62 / 181


def test_next_coupon_amount() -> None:
    assert next_coupon(bond(), D(10), TODAY) == (date(2027, 5, 25), D("300.00"))
    semi = bond("4.50", CouponFrequency.SEMIANNUAL, date(2029, 9, 12))
    assert next_coupon(semi, D(8), TODAY) == (date(2027, 3, 12), D("180.00"))
    assert next_coupon(bond(), D(10), date(2031, 6, 1)) is None


def test_current_yield() -> None:
    assert current_yield(D("3.00"), D("101.20")).quantize(D("0.01")) == D("2.96")


def test_remaining_label() -> None:
    assert remaining_label(date(2031, 5, 25), TODAY) == "dans 4 ans 6 mois"
    assert remaining_label(date(2027, 3, 20), TODAY) == "dans 4 mois"
    assert remaining_label(date(2028, 11, 13), TODAY) == "dans 2 ans"
    assert remaining_label(date(2026, 11, 25), TODAY) == "dans 12 jours"
    assert remaining_label(date(2026, 11, 13), TODAY) == "échue"


def test_bond_value_and_totals() -> None:
    position = Position(1, D(10), D(984), D(0))
    valued = bond_value(position, D(1012))
    assert (valued.value, valued.gain) == (D(10120), D(280))
    other = Position(2, D(8), D(1005), D(0))
    unvalued = Position(3, D(5), D(962), D(0))
    closed = Position(4, D(0), D(0), D(50))
    totals = bond_totals(
        [
            (position, bond(), D(1012)),
            (other, bond("4.50", CouponFrequency.SEMIANNUAL, date(2029, 9, 12)), D(991)),
            (unvalued, bond("2.40", maturity=date(2033, 2, 15)), None),
            (closed, bond(), D(1000)),
        ]
    )
    assert totals.value == D(18048)
    assert totals.invested == D(9840 + 8040 + 4810)
    assert totals.gain == D(168)  # 280 − 112 ; la position sans cours n'est pas une perte
    assert totals.annual_coupons == D(300 + 360 + 120)
    assert totals.unvalued == 1
    assert totals.current_yield is not None


def test_maturity_ladder_fills_empty_years_and_skips_matured() -> None:
    items = [
        (bond(maturity=date(2027, 3, 20)), D(6)),
        (bond(maturity=date(2029, 9, 12)), D(8)),
        (bond(maturity=date(2029, 12, 1)), D(2)),
        (bond(maturity=date(2026, 1, 1)), D(3)),  # déjà échue
        (bond(maturity=date(2031, 5, 25)), D(0)),  # position soldée
    ]
    assert maturity_ladder(items, TODAY) == [
        (2027, D(6000)),
        (2028, D(0)),
        (2029, D(10000)),
    ]
    assert maturity_ladder([], TODAY) == []


def test_trade_quote_adds_accrued_both_ways() -> None:
    quote = bond_trade_quote(D(5), D("101.20"), D(1000), D("70.68"))
    assert (quote.gross, quote.accrued, quote.total) == (D(5060), D("70.68"), D("5130.68"))
    assert trade_amount(TransactionType.BUY, quote) == D("-5130.68")
    assert trade_amount(TransactionType.SELL, quote) == D("5130.68")


def test_trade_quote_rejects_invalid_input() -> None:
    for quantity in (D(0), D("2.5"), D(-1)):
        with pytest.raises(ValueError):
            validate_quantity(quantity)
    with pytest.raises(ValueError):
        bond_trade_quote(D(1), D(100), D(1000), D(-1))


def test_stale_quote_threshold() -> None:
    assert not is_quote_stale(date(2026, 11, 6), TODAY)
    assert is_quote_stale(date(2026, 11, 5), TODAY)


# --- Persistance --------------------------------------------------------------


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[BondRepository]:
    connection: Any = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0a" * 32)
    yield BondRepository(connection)
    connection.close()


def create_oat(repository: BondRepository) -> int:
    holding = repository.create("OAT 3,00 % 2031", "fr0000000201", "eur", bond())
    assert holding.asset.id is not None
    return holding.asset.id


def test_create_validates_and_normalizes(repository: BondRepository) -> None:
    asset_id = create_oat(repository)
    holding = repository.get_holding(asset_id)
    assert holding is not None
    assert holding.asset.symbol == "FR0000000201"
    assert holding.asset.currency == "EUR"
    assert holding.details == bond()
    with pytest.raises(ValueError):
        repository.create("Autre", "FR0000000201", "EUR", bond())
    with pytest.raises(ValueError):
        repository.create(" ", "FR0000000299", "EUR", bond())
    assert [h.asset.id for h in repository.list_holdings()] == [asset_id]


def test_buy_stores_unit_price_in_euros_and_accrued_apart(repository: BondRepository) -> None:
    asset_id = create_oat(repository)
    repository.add_transaction(
        asset_id, TransactionType.BUY, date(2026, 3, 12), D(6), D("98.10"), D(0)
    )
    repository.add_transaction(
        asset_id, TransactionType.BUY, date(2026, 6, 2), D(4), D("98.85"), D("26.30")
    )
    holding = repository.get_holding(asset_id)
    assert holding is not None
    assert [t.unit_price for t in holding.transactions] == [D("981.00"), D("988.50")]
    assert all(t.fees == 0 for t in holding.transactions)
    assert sorted(holding.accruals.values()) == [D(0), D("26.30")]
    assert holding.position.quantity == D(10)
    assert holding.position.pru == D("984.00")  # 98,40 % : le coupon couru n'entre pas dans le PRU


def test_sell_realizes_gain_without_accrued(repository: BondRepository) -> None:
    asset_id = create_oat(repository)
    repository.add_transaction(
        asset_id, TransactionType.BUY, date(2026, 3, 12), D(10), D("98.40"), D(0)
    )
    repository.add_transaction(
        asset_id, TransactionType.SELL, date(2026, 11, 13), D(5), D("101.20"), D("70.68")
    )
    holding = repository.get_holding(asset_id)
    assert holding is not None
    assert holding.position.quantity == D(5)
    assert holding.position.realized_pnl == D(140)


def test_oversell_and_fractional_quantity_are_refused(repository: BondRepository) -> None:
    asset_id = create_oat(repository)
    repository.add_transaction(asset_id, TransactionType.BUY, TODAY, D(2), D(100), D(0))
    with pytest.raises(ValueError):
        repository.add_transaction(asset_id, TransactionType.SELL, TODAY, D(3), D(100), D(0))
    with pytest.raises(ValueError):
        repository.add_transaction(asset_id, TransactionType.BUY, TODAY, D("1.5"), D(100), D(0))
    with pytest.raises(ValueError):
        repository.add_transaction(asset_id, TransactionType.BUY, TODAY, D(1), D(100), D(-1))
    holding = repository.get_holding(asset_id)
    assert holding is not None
    assert len(holding.transactions) == 1  # rien n'est écrit à moitié


def test_quotes_are_stored_per_bond_and_source_follows(repository: BondRepository) -> None:
    asset_id = create_oat(repository)
    assert repository.valued_on(TODAY) == set()
    repository.record_quote(asset_id, date(2026, 11, 1), D("100.50"), NOW, ValuationSource.YAHOO)
    repository.record_quote(asset_id, TODAY, D("101.20"), NOW, ValuationSource.MANUAL)
    holding = repository.get_holding(asset_id)
    assert holding is not None
    assert holding.valuation is not None
    assert holding.valuation.value == D("1012.00")
    assert holding.quote_percent == D("101.2")
    assert holding.details.valuation_source is ValuationSource.MANUAL
    assert repository.valued_on(TODAY) == {asset_id}
    assert [v.value for v in repository.quote_history(asset_id)] == [D("1005.00"), D("1012.00")]
    with pytest.raises(ValueError):
        repository.record_quote(asset_id, TODAY, D(0), NOW, ValuationSource.MANUAL)
    with pytest.raises(ValueError):
        repository.record_quote(999, TODAY, D(100), NOW, ValuationSource.MANUAL)
