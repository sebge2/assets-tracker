from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.db.connection import connect
from assets_tracker.domain.cash import (
    BonusDefinition,
    CashAccount,
    InterestFrequency,
    MovementKind,
)

D = Decimal
IBAN = "FR7630006000011234567890189"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x01" * 32)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn: Any) -> CashRepository:
    return CashRepository(conn)


def new_account(**overrides: object) -> CashAccount:
    fields: dict[str, object] = {
        "id": None,
        "name": "Livret Avenir",
        "bank": "Boursorama",
        "iban": IBAN,
        "currency": "EUR",
        "opened_on": date(2024, 6, 1),
        "frequency": InterestFrequency.MONTHLY,
    }
    fields.update(overrides)
    return CashAccount(**fields)  # type: ignore[arg-type]


def test_create_account_records_first_rate_and_bonus(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"), bonus=(D("1.50"), 12))

    assert account.id is not None
    assert repo.get_account(account.id) == account
    assert repo.list_accounts() == [account]
    rates = repo.base_rates(account.id)
    assert [(r.effective_date, r.rate) for r in rates] == [(date(2024, 6, 1), D("1.00"))]
    assert repo.bonuses(account.id) == [BonusDefinition(date(2024, 6, 1), D("1.50"), 12)]


def test_account_without_bonus_has_no_bonus_history(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("3.00"))
    assert account.id is not None
    assert repo.bonuses(account.id) == []


def test_changing_the_rate_keeps_the_previous_one(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"))
    assert account.id is not None

    repo.update_account(account, base_rate=D("1.50"), bonus=None, effective_date=date(2025, 1, 1))

    assert [(r.effective_date, r.rate) for r in repo.base_rates(account.id)] == [
        (date(2024, 6, 1), D("1.00")),
        (date(2025, 1, 1), D("1.50")),
    ]


def test_changing_only_the_effective_date_moves_the_current_entry(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"), bonus=(D("1.50"), 12))
    assert account.id is not None

    repo.update_account(
        account, base_rate=D("1.00"), bonus=(D("1.50"), 12), effective_date=date(2024, 10, 1)
    )

    assert [(r.effective_date, r.rate) for r in repo.base_rates(account.id)] == [
        (date(2024, 10, 1), D("1.00"))
    ]
    assert [b.effective_date for b in repo.bonuses(account.id)] == [date(2024, 10, 1)]


def test_saving_without_changes_adds_no_history(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"), bonus=(D("1.50"), 12))
    assert account.id is not None

    repo.update_account(
        account, base_rate=D("1.00"), bonus=(D("1.50"), 12), effective_date=date(2024, 6, 1)
    )

    assert len(repo.base_rates(account.id)) == 1
    assert len(repo.bonuses(account.id)) == 1


def test_bonus_can_be_modified_then_removed(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"), bonus=(D("1.50"), 12))
    assert account.id is not None

    repo.update_account(
        account, base_rate=D("1.00"), bonus=(D("2.00"), 6), effective_date=date(2025, 1, 1)
    )
    repo.update_account(account, base_rate=D("1.00"), bonus=None, effective_date=date(2026, 1, 1))

    assert [(b.effective_date, b.rate, b.after_months) for b in repo.bonuses(account.id)] == [
        (date(2024, 6, 1), D("1.50"), 12),
        (date(2025, 1, 1), D("2.00"), 6),
        (date(2026, 1, 1), D("0"), 0),
    ]


def test_adding_a_bonus_to_an_account_without_one(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"))
    assert account.id is not None

    repo.update_account(
        account, base_rate=D("1.00"), bonus=(D("1.00"), 12), effective_date=date(2025, 1, 1)
    )

    assert repo.bonuses(account.id) == [BonusDefinition(date(2025, 1, 1), D("1.00"), 12)]


def test_update_changes_identity_and_payout(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"))
    renamed = new_account(
        id=account.id,
        name="Avenir+",
        frequency=InterestFrequency.ANNUAL,
        payout_month=12,
        payout_day=31,
    )

    repo.update_account(renamed, base_rate=D("1.00"), bonus=None, effective_date=date(2025, 1, 1))

    assert account.id is not None
    saved = repo.get_account(account.id)
    assert saved is not None
    assert (saved.name, saved.frequency, saved.payout_month, saved.payout_day) == (
        "Avenir+",
        InterestFrequency.ANNUAL,
        12,
        31,
    )


def test_iban_is_unique(repo: CashRepository, conn: Any) -> None:
    repo.create_account(new_account(), D("1.00"))
    with pytest.raises(Exception, match="UNIQUE"):
        repo.create_account(new_account(name="Doublon"), D("1.00"))


def test_movements_are_stored_in_decimal_and_ordered(repo: CashRepository, conn: Any) -> None:
    account = repo.create_account(new_account(), D("1.00"))
    assert account.id is not None

    repo.add_movement(account.id, MovementKind.DEPOSIT, date(2026, 2, 1), D("1200.50"))
    repo.add_movement(account.id, MovementKind.OPENING, date(2024, 6, 1), D("495"))
    repo.add_movement(account.id, MovementKind.INTEREST, date(2026, 3, 1), D("25"))

    movements = repo.movements(account.id)
    assert [m.kind for m in movements] == [
        MovementKind.OPENING,
        MovementKind.DEPOSIT,
        MovementKind.INTEREST,
    ]
    assert movements[1].amount == D("1200.50")
    stored = conn.execute("SELECT typeof(amount) AS t FROM cash_movements").fetchone()["t"]
    assert stored == "text"
    assert repo.has_movements(account.id)


def test_withdrawal_beyond_balance_is_refused_and_not_stored(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"))
    assert account.id is not None
    repo.add_movement(account.id, MovementKind.DEPOSIT, date(2026, 1, 1), D("100"))

    with pytest.raises(ValueError, match="Solde insuffisant"):
        repo.add_movement(account.id, MovementKind.WITHDRAWAL, date(2026, 2, 1), D("100.01"))

    assert len(repo.movements(account.id)) == 1


def test_deleting_a_movement_removes_it_from_the_history(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"))
    assert account.id is not None
    movement = repo.add_movement(account.id, MovementKind.DEPOSIT, date(2026, 1, 1), D("100"))
    assert movement.id is not None

    repo.delete_movement(account.id, movement.id)

    assert repo.movements(account.id) == []


def test_deleting_a_deposit_that_funds_a_withdrawal_is_refused(repo: CashRepository) -> None:
    account = repo.create_account(new_account(), D("1.00"))
    assert account.id is not None
    deposit = repo.add_movement(account.id, MovementKind.DEPOSIT, date(2026, 1, 1), D("100"))
    assert deposit.id is not None
    repo.add_movement(account.id, MovementKind.WITHDRAWAL, date(2026, 1, 2), D("50"))

    with pytest.raises(ValueError, match="retrait ultérieur"):
        repo.delete_movement(account.id, deposit.id)

    assert len(repo.movements(account.id)) == 2


def test_due_base_interest_and_loyalty_bonus_are_credited_separately(
    repo: CashRepository,
) -> None:
    account = repo.create_account(
        new_account(opened_on=date(2026, 1, 1)), D("3.65"), bonus=(D("3.65"), 0)
    )
    assert account.id is not None
    repo.add_movement(account.id, MovementKind.DEPOSIT, date(2026, 1, 1), D("1000"))

    repo.credit_due_yields(date(2026, 2, 1))

    credited = repo.movements(account.id)
    assert [(m.kind, m.movement_date, m.amount) for m in credited[1:]] == [
        (MovementKind.INTEREST, date(2026, 2, 1), D("3.10")),
        (MovementKind.LOYALTY_BONUS, date(2026, 2, 1), D("3.10")),
    ]
    repo.credit_due_yields(date(2026, 2, 1))
    assert repo.movements(account.id) == credited


def test_deleting_an_account_removes_its_history(repo: CashRepository, conn: Any) -> None:
    account = repo.create_account(new_account(), D("1.00"), bonus=(D("1.50"), 12))
    assert account.id is not None
    repo.add_movement(account.id, MovementKind.DEPOSIT, date(2026, 1, 1), D("100"))

    repo.delete_account(account.id)

    assert repo.list_accounts() == []
    for table in ("cash_account_rates", "cash_account_bonuses", "cash_movements"):
        assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0


def test_interest_accrues_from_a_movement_dated_before_the_opening(repo: CashRepository) -> None:
    account = repo.create_account(
        new_account(
            opened_on=date(2026, 11, 11),
            frequency=InterestFrequency.MONTHLY,
            payout_month=None,
            payout_day=None,
        ),
        D("3.00"),
    )
    assert account.id is not None
    repo.update_account(account, base_rate=D("3.00"), bonus=None, effective_date=date(2026, 10, 1))
    repo.add_movement(account.id, MovementKind.OPENING, date(2026, 10, 1), D("1000"))

    repo.credit_due_yields(date(2026, 11, 12))

    interest = [m for m in repo.movements(account.id) if m.kind is MovementKind.INTEREST]
    assert [(m.movement_date, m.amount) for m in interest] == [(date(2026, 11, 1), D("2.55"))]
