from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.fund_repository import FundRepository
from assets_tracker.domain.funds import FundDetails, ValuationSource, fee_amount
from assets_tracker.domain.models import TransactionType

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
