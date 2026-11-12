from __future__ import annotations

from datetime import date
from decimal import Decimal

from assets_tracker.domain.cash import (
    CashMovement,
    MovementKind,
    first_month,
    month_end,
    monthly_balances,
    months_range,
    sum_balances,
    trailing_months,
)

D = Decimal


def move(kind: MovementKind, when: str, amount: str) -> CashMovement:
    return CashMovement(None, 1, kind, date.fromisoformat(when), D(amount))


def test_month_boundaries() -> None:
    assert month_end(date(2026, 2, 1)) == date(2026, 2, 28)
    assert month_end(date(2024, 2, 10)) == date(2024, 2, 29)
    assert month_end(date(2026, 12, 1)) == date(2026, 12, 31)


def test_months_range_crosses_years() -> None:
    assert months_range(date(2025, 11, 20), date(2026, 2, 3)) == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert months_range(date(2026, 5, 5), date(2026, 5, 31)) == [date(2026, 5, 1)]


def test_trailing_months_include_the_current_month() -> None:
    months = trailing_months(date(2026, 11, 12), 12)
    assert months[0] == date(2025, 12, 1)
    assert months[-1] == date(2026, 11, 1)
    assert len(months) == 12
    assert trailing_months(date(2026, 1, 15), 1) == [date(2026, 1, 1)]


def test_month_end_balances_follow_the_movements() -> None:
    movements = [
        move(MovementKind.OPENING, "2026-01-10", "1000"),
        move(MovementKind.DEPOSIT, "2026-02-28", "500"),
        move(MovementKind.WITHDRAWAL, "2026-03-01", "200"),
        move(MovementKind.INTEREST, "2026-03-31", "12.50"),
    ]
    months = months_range(date(2026, 1, 1), date(2026, 4, 1))

    assert monthly_balances(date(2026, 1, 10), movements, months, date(2026, 4, 15)) == [
        D("1000"),
        D("1500"),  # le dépôt du 28 février compte dans février
        D("1312.50"),  # retrait du 1er mars et intérêts du 31 mars
        D("1312.50"),
    ]


def test_current_month_stops_at_today() -> None:
    movements = [
        move(MovementKind.DEPOSIT, "2026-11-02", "100"),
        move(MovementKind.DEPOSIT, "2026-11-20", "900"),
    ]

    balances = monthly_balances(
        date(2026, 11, 1), movements, [date(2026, 11, 1)], date(2026, 11, 12)
    )

    assert balances == [D("100")]


def test_no_balance_before_the_account_exists() -> None:
    movements = [move(MovementKind.DEPOSIT, "2026-03-15", "300")]
    months = months_range(date(2026, 1, 1), date(2026, 4, 1))

    balances = monthly_balances(date(2026, 3, 15), movements, months, date(2026, 4, 20))

    assert balances == [None, None, D("300"), D("300")]


def test_first_movement_older_than_opening_date_counts_as_existence() -> None:
    movements = [move(MovementKind.OPENING, "2022-08-01", "495")]
    assert first_month(date(2024, 1, 1), movements) == date(2022, 8, 1)
    assert first_month(date(2024, 1, 20), []) == date(2024, 1, 1)


def test_total_sums_accounts_and_ignores_accounts_not_yet_open() -> None:
    a = [D("100"), D("150"), D("200")]
    b = [None, D("50"), D("70")]

    assert sum_balances([a, b], 3) == [D("100"), D("200"), D("270")]
    assert sum_balances([], 2) == [D("0"), D("0")]
