from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.insurance_repository import InsuranceRepository
from assets_tracker.domain.insurance import (
    ContractDetails,
    ContractKind,
    Payment,
    Statement,
    ceiling_left,
    gain_percent,
    is_stale,
    latest_projection,
    paid_in_year,
    projection_history,
    total_paid,
    totals,
    validate_details,
    validate_payment,
    validate_statement,
)

D = Decimal
TODAY = date(2026, 11, 14)
PENSION = ContractDetails(
    ContractKind.PENSION_SAVINGS,
    "Épargne-pension",
    "AG Insurance",
    date(2016, 3, 1),
    ceiling=D(1050),
)
GROUP = ContractDetails(
    ContractKind.GROUP_INSURANCE,
    "Pension complémentaire",
    "Allianz",
    date(2014, 9, 1),
    retirement_on=date(2040, 9, 1),
)


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[InsuranceRepository]:
    connection: Any = connect(tmp_path / "insurance.db", key_provider=lambda: b"\x0b" * 32)
    yield InsuranceRepository(connection)
    connection.close()


def test_validation_rules() -> None:
    validate_details(PENSION)
    with pytest.raises(ValueError, match="nom"):
        validate_details(ContractDetails(ContractKind.GROUP_INSURANCE, " ", "X", TODAY))
    with pytest.raises(ValueError, match="retraite"):
        validate_details(
            ContractDetails(ContractKind.GROUP_INSURANCE, "A", "B", TODAY, retirement_on=TODAY)
        )
    with pytest.raises(ValueError, match="épargne-pension"):
        validate_details(ContractDetails(ContractKind.GROUP_INSURANCE, "A", "B", TODAY, ceiling=D(1)))
    with pytest.raises(ValueError, match="positif"):
        validate_details(ContractDetails(ContractKind.PENSION_SAVINGS, "A", "B", TODAY, ceiling=D(0)))
    with pytest.raises(ValueError, match="montant"):
        validate_payment(PENSION, TODAY, D(0))
    with pytest.raises(ValueError, match="ouverture"):
        validate_payment(PENSION, date(2015, 1, 1), D(10))
    with pytest.raises(ValueError, match="négative"):
        validate_statement(PENSION, TODAY, D(-1), None)
    with pytest.raises(ValueError, match="projetée"):
        validate_statement(PENSION, TODAY, D(1), D(-1))


def test_paid_totals_and_ceiling() -> None:
    payments = [
        Payment(1, date(2025, 3, 1), D(1050)),
        Payment(2, date(2026, 3, 2), D(600)),
        Payment(3, date(2026, 9, 1), D(200)),
    ]
    assert total_paid(payments) == D(1850)
    assert paid_in_year(payments, 2026) == D(800)
    assert ceiling_left(PENSION, payments, 2026) == D(250)
    assert ceiling_left(PENSION, payments, 2025) == D(0)
    assert ceiling_left(GROUP, payments, 2026) is None


def test_staleness_follows_the_latest_statement() -> None:
    assert is_stale([], TODAY)
    assert not is_stale([Statement(1, date(2026, 1, 1), D(1))], TODAY)
    assert is_stale([Statement(1, date(2025, 11, 13), D(1))], TODAY)
    assert not is_stale([Statement(1, date(2025, 11, 14), D(1))], TODAY)


def test_projection_history_is_newest_first_with_changes() -> None:
    statements = [
        Statement(1, date(2024, 12, 31), D(20000), D(50000)),
        Statement(2, date(2025, 6, 30), D(21000), None),  # sans projection : ignoré
        Statement(3, date(2025, 12, 31), D(24000), D(55000)),
        Statement(4, date(2026, 12, 31), D(27000), D(52250)),
    ]
    history = projection_history(statements)
    assert [s.statement.id for s in history] == [4, 3, 1]
    assert history[0].change == D(-2750)
    assert history[0].change_percent == D(-5)
    assert history[1].change == D(5000)
    assert history[2].change is None and history[2].change_percent is None
    latest = latest_projection(statements)
    assert latest is not None and latest.id == 4


def test_totals_use_the_latest_statement_of_each_contract() -> None:
    first = ([Payment(1, TODAY, D(1000))], [Statement(1, TODAY, D(1200), D(5000))])
    second = (
        [Payment(2, TODAY, D(500)), Payment(3, TODAY, D(500))],
        [Statement(2, date(2025, 1, 1), D(900), D(4000)), Statement(3, TODAY, D(1100), None)],
    )
    result = totals([first, second])
    assert result.paid == D(2000)
    assert result.value == D(2300)
    assert result.projected == D(9000)  # 5000 + dernière projection du 2e contrat (4000)
    assert gain_percent(D(1200), D(1000)) == D(20)
    assert gain_percent(D(1), D(0)) is None


def test_create_update_and_delete_contract(repository: InsuranceRepository) -> None:
    created = repository.create(PENSION)
    assert created.details == PENSION
    assert created.payments == () and created.statements == ()
    group = repository.create(GROUP)
    assert [h.details.name for h in repository.list_contracts()] == [
        "Épargne-pension",
        "Pension complémentaire",
    ]
    renamed = repository.update(group.id, ContractDetails(
        ContractKind.GROUP_INSURANCE, "Groupe", "Allianz", date(2014, 9, 1), number="N-1"
    ))
    assert renamed.details.name == "Groupe" and renamed.details.number == "N-1"
    assert renamed.details.retirement_on is None
    repository.delete(group.id)
    assert repository.get(group.id) is None
    with pytest.raises(ValueError, match="introuvable"):
        repository.update(group.id, GROUP)


def test_payments_and_statements_round_trip(repository: InsuranceRepository) -> None:
    contract = repository.create(PENSION)
    repository.add_payment(contract.id, date(2026, 3, 2), D("1050.00"))
    repository.save_statement(contract.id, date(2025, 12, 31), D("12980"), D("55000"))
    holding = repository.save_statement(contract.id, date(2026, 12, 31), D("14280"), None)
    assert [p.amount for p in holding.payments] == [D("1050.00")]
    assert [s.projected_value for s in holding.statements] == [D(55000), None]
    # Même date : le relevé est remplacé, pas dupliqué.
    holding = repository.save_statement(contract.id, date(2025, 12, 31), D("13000"), D("56000"))
    assert len(holding.statements) == 2
    assert holding.statements[0].current_value == D(13000)
    assert holding.statements[0].projected_value == D(56000)
    with pytest.raises(ValueError, match="ouverture"):
        repository.add_payment(contract.id, date(2000, 1, 1), D(10))
    with pytest.raises(ValueError, match="ouverture"):
        repository.save_statement(contract.id, date(2000, 1, 1), D(10), None)


def test_delete_lines_and_cascade(repository: InsuranceRepository) -> None:
    contract = repository.create(PENSION)
    holding = repository.add_payment(contract.id, date(2026, 3, 2), D(100))
    holding = repository.save_statement(contract.id, date(2026, 6, 30), D(500), D(900))
    repository.delete_payment(holding.payments[0].id)
    repository.delete_statement(holding.statements[0].id)
    holding = repository.get(contract.id)
    assert holding is not None and holding.payments == () and holding.statements == ()
    repository.add_payment(contract.id, date(2026, 3, 2), D(100))
    repository.delete(contract.id)
    assert repository.list_contracts() == []
