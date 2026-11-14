from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.domain.fx import (
    ExchangeRate,
    currencies_to_refresh,
    position_in_euros,
    rate_for,
    transactions_in_euros,
    validate_rate,
)
from assets_tracker.domain.models import Position, Transaction, TransactionType

D = Decimal
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0)


def rate(currency: str, day: date, value: str) -> ExchangeRate:
    return ExchangeRate(currency, day, D(value), NOW)


def test_euro_needs_no_rate_and_unknown_currencies_have_none() -> None:
    rates = {"USD": rate("USD", TODAY, "0.92")}
    assert rate_for("EUR", rates) == D(1)
    assert rate_for("USD", rates) == D("0.92")
    assert rate_for("CHF", rates) is None


def test_rates_are_refreshed_once_a_day_per_foreign_currency() -> None:
    rates = {"USD": rate("USD", TODAY, "0.92"), "GBP": rate("GBP", date(2026, 11, 12), "1.15")}
    assert currencies_to_refresh(["EUR", "USD", "GBP", "CHF", "CHF"], rates, TODAY) == [
        "CHF",
        "GBP",
    ]
    assert currencies_to_refresh(["USD", "EUR"], rates, TODAY, force=True) == ["USD"]


def test_rate_must_be_positive() -> None:
    assert validate_rate(D("0.92")) == D("0.92")
    for bad in (D(0), D(-1), D("NaN")):
        with pytest.raises(ValueError):
            validate_rate(bad)


def test_transactions_and_position_are_converted() -> None:
    buy = Transaction(1, 7, TransactionType.BUY, TODAY, D(10), D(980), D(5))
    [converted] = transactions_in_euros([buy], D("0.5"))
    assert (converted.unit_price, converted.fees, converted.quantity) == (D(490), D("2.5"), D(10))
    position = position_in_euros(Position(7, D(10), D(980), D(40)), D("0.5"))
    assert (position.pru, position.realized_pnl, position.quantity) == (D(490), D(20), D(10))


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[FxRepository]:
    connection: Any = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0c" * 32)
    yield FxRepository(connection)
    connection.close()


def test_repository_keeps_the_latest_rate_per_currency(repository: FxRepository) -> None:
    repository.record("usd", date(2026, 11, 12), D("0.91"), NOW)
    repository.record("USD", TODAY, D("0.92"), NOW)
    repository.record("GBP", date(2026, 11, 10), D("1.15"), NOW)
    repository.record("USD", TODAY, D("0.93"), NOW)  # même jour : mis à jour

    rates = repository.latest_rates()

    assert {c: r.rate for c, r in rates.items()} == {"USD": D("0.93"), "GBP": D("1.15")}
    assert rates["USD"].rate_date == TODAY
    latest = repository.latest("usd")
    assert latest is not None and latest.rate == D("0.93")
    assert repository.latest("CHF") is None


def test_repository_refuses_euro_and_invalid_rates(repository: FxRepository) -> None:
    with pytest.raises(ValueError):
        repository.record("EUR", TODAY, D(1), NOW)
    with pytest.raises(ValueError):
        repository.record("USD", TODAY, D(0), NOW)
    assert repository.latest_rates() == {}
