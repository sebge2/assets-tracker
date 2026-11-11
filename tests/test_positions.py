from datetime import date
from decimal import Decimal as D

import pytest

from assets_tracker.domain.models import Transaction, TransactionType
from assets_tracker.domain.positions import compute_position

BUY, SELL = TransactionType.BUY, TransactionType.SELL


def tx(
    i: int, kind: TransactionType, day: int, qty: str, price: str, fees: str = "0"
) -> Transaction:
    return Transaction(i, 1, kind, date(2025, 1, day), D(qty), D(price), D(fees))


def test_pru_is_weighted_average_with_fees() -> None:
    pos = compute_position(1, [tx(1, BUY, 1, "10", "100", "10"), tx(2, BUY, 2, "10", "120")])
    assert pos.quantity == D(20)
    assert pos.pru == D("110.5")  # (1010 + 1200) / 20


def test_sell_keeps_pru_and_realizes_pnl() -> None:
    pos = compute_position(1, [tx(1, BUY, 1, "10", "100"), tx(2, SELL, 2, "4", "130", "2")])
    assert pos.quantity == D(6)
    assert pos.pru == D(100)
    assert pos.realized_pnl == D(118)  # 4 * 30 - 2


def test_oversell_raises() -> None:
    with pytest.raises(ValueError):
        compute_position(1, [tx(1, BUY, 1, "1", "100"), tx(2, SELL, 2, "2", "100")])
