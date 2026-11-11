from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from assets_tracker.domain.cash import (
    BaseRateChange,
    BonusDefinition,
    BonusEventKind,
    CashAccount,
    CashMovement,
    InterestFrequency,
    MovementKind,
    add_months,
    balance_of,
    base_rate_history,
    base_rate_on,
    bonus_events,
    bonus_on,
    check_movement_allowed,
    effective_rate_on,
    effective_rate_steps,
    estimated_annual_interest,
    format_iban,
    is_valid_iban,
    mask_iban,
    normalize_iban,
    statement,
    weighted_average_rate,
)

D = Decimal
VALID_IBAN = "FR7630006000011234567890189"


def account(**overrides: object) -> CashAccount:
    fields: dict[str, object] = {
        "id": None,
        "name": "Livret A",
        "bank": "Crédit Mutuel",
        "iban": VALID_IBAN,
        "currency": "EUR",
        "opened_on": date(2024, 6, 1),
        "frequency": InterestFrequency.ANNUAL,
        "payout_month": 12,
        "payout_day": 31,
    }
    fields.update(overrides)
    return CashAccount(**fields)  # type: ignore[arg-type]


def movement(
    kind: MovementKind, when: str, amount: str, movement_id: int | None = None
) -> CashMovement:
    return CashMovement(movement_id, 1, kind, date.fromisoformat(when), D(amount))


# --- IBAN -------------------------------------------------------------------


def test_iban_is_normalized_validated_and_masked() -> None:
    assert normalize_iban("fr76 3000 6000 0112 3456 7890 189") == VALID_IBAN
    assert is_valid_iban(VALID_IBAN)
    assert is_valid_iban("GB82WEST12345698765432")
    assert not is_valid_iban("FR7630006000011234567890188")  # clé de contrôle fausse
    assert not is_valid_iban("1234")
    assert mask_iban(VALID_IBAN) == "FR76 •••• •••• 0189"
    assert format_iban(VALID_IBAN) == "FR76 3000 6000 0112 3456 7890 189"


# --- Validation du compte ---------------------------------------------------


def test_account_requires_valid_fields() -> None:
    with pytest.raises(ValueError, match="nom"):
        account(name=" ")
    with pytest.raises(ValueError, match="banque"):
        account(bank="")
    with pytest.raises(ValueError, match="IBAN"):
        account(iban="FR7630006000011234567890188")
    with pytest.raises(ValueError, match="devise"):
        account(currency="euro")


def test_annual_payout_needs_an_existing_month_and_day() -> None:
    with pytest.raises(ValueError, match="mois et un jour"):
        account(payout_month=None, payout_day=None)
    with pytest.raises(ValueError, match="n'existe pas"):
        account(payout_month=2, payout_day=30)
    assert account(payout_month=2, payout_day=28).payout_day == 28


def test_monthly_payout_has_no_month_or_day() -> None:
    assert (
        account(frequency=InterestFrequency.MONTHLY, payout_month=None, payout_day=None).frequency
        is InterestFrequency.MONTHLY
    )
    with pytest.raises(ValueError, match="1er du mois"):
        account(frequency=InterestFrequency.MONTHLY)


# --- Taux de base, prime, taux effectif --------------------------------------


def test_add_months_clamps_to_end_of_month() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 6, 1), 12) == date(2025, 6, 1)
    assert add_months(date(2025, 11, 15), 3) == date(2026, 2, 15)


def test_base_rate_is_a_step_function_of_time() -> None:
    changes = [
        BaseRateChange(date(2025, 1, 1), D("3.00")),
        BaseRateChange(date(2022, 8, 1), D("2.00")),
        BaseRateChange(date(2024, 3, 1), D("2.50")),
    ]
    assert base_rate_on(changes, date(2022, 7, 31)) == D(0)
    assert base_rate_on(changes, date(2022, 8, 1)) == D("2.00")
    assert base_rate_on(changes, date(2024, 12, 31)) == D("2.50")
    assert base_rate_on(changes, date(2026, 1, 1)) == D("3.00")


def test_base_rate_history_has_deltas_in_points() -> None:
    rows = base_rate_history(
        [
            BaseRateChange(date(2024, 3, 1), D("2.50")),
            BaseRateChange(date(2022, 8, 1), D("2.00")),
            BaseRateChange(date(2025, 1, 1), D("3.00")),
        ]
    )
    assert [r.delta for r in rows] == [None, D("0.50"), D("0.50")]
    assert [r.rate for r in rows] == [D("2.00"), D("2.50"), D("3.00")]


def test_loyalty_bonus_applies_once_holding_condition_is_met() -> None:
    opened = date(2024, 6, 1)
    bonus = [BonusDefinition(date(2024, 6, 1), D("1.50"), 12)]
    assert bonus_on(opened, bonus, date(2025, 5, 31)) == D(0)
    assert bonus_on(opened, bonus, date(2025, 6, 1)) == D("1.50")
    base = [
        BaseRateChange(date(2024, 6, 1), D("1.00")),
        BaseRateChange(date(2025, 1, 1), D("1.50")),
    ]
    assert effective_rate_on(opened, base, bonus, date(2025, 5, 31)) == D("1.50")
    assert effective_rate_on(opened, base, bonus, date(2026, 11, 11)) == D("3.00")


def test_a_later_definition_replaces_or_removes_the_bonus() -> None:
    opened = date(2024, 6, 1)
    definitions = [
        BonusDefinition(date(2024, 6, 1), D("1.50"), 12),
        BonusDefinition(date(2026, 1, 1), D("0"), 0),
    ]
    assert bonus_on(opened, definitions, date(2025, 12, 31)) == D("1.50")
    assert bonus_on(opened, definitions, date(2026, 1, 1)) == D(0)


def test_bonus_defined_after_the_condition_is_met_is_active_immediately() -> None:
    opened = date(2022, 1, 1)
    definitions = [BonusDefinition(date(2026, 3, 1), D("1.00"), 12)]
    assert bonus_on(opened, definitions, date(2026, 2, 28)) == D(0)
    assert bonus_on(opened, definitions, date(2026, 3, 1)) == D("1.00")


def test_effective_rate_steps_include_bonus_activation() -> None:
    opened = date(2024, 6, 1)
    base = [
        BaseRateChange(date(2024, 6, 1), D("1.00")),
        BaseRateChange(date(2025, 1, 1), D("1.50")),
    ]
    bonus = [BonusDefinition(date(2024, 6, 1), D("1.50"), 12)]
    steps = effective_rate_steps(opened, base, bonus, date(2026, 11, 11))
    assert steps == [
        (date(2024, 6, 1), D("1.00")),
        (date(2025, 1, 1), D("1.50")),
        (date(2025, 6, 1), D("3.00")),
    ]
    assert effective_rate_steps(opened, base, bonus, date(2025, 3, 1))[-1] == (
        date(2025, 1, 1),
        D("1.50"),
    )


def test_bonus_events_list_definition_activation_and_removal() -> None:
    opened = date(2024, 6, 1)
    definitions = [
        BonusDefinition(date(2024, 6, 1), D("1.50"), 12),
        BonusDefinition(date(2026, 1, 1), D("0"), 0),
    ]
    events = bonus_events(opened, definitions, date(2026, 11, 11))
    assert [(e.event_date, e.kind) for e in events] == [
        (date(2024, 6, 1), BonusEventKind.DEFINED),
        (date(2025, 6, 1), BonusEventKind.ACTIVATED),
        (date(2026, 1, 1), BonusEventKind.REMOVED),
    ]
    not_yet = bonus_events(opened, definitions[:1], date(2025, 1, 1))
    assert [e.kind for e in not_yet] == [BonusEventKind.DEFINED]


# --- Mouvements -------------------------------------------------------------


def test_statement_gives_running_balance_in_chronological_order() -> None:
    lines = statement(
        [
            movement(MovementKind.WITHDRAWAL, "2025-06-10", "300", 3),
            movement(MovementKind.OPENING, "2022-08-01", "495", 1),
            movement(MovementKind.DEPOSIT, "2023-03-15", "1200", 2),
            movement(MovementKind.INTEREST, "2024-12-31", "47", 4),
            movement(MovementKind.INTEREST, "2025-12-31", "58", 5),
            movement(MovementKind.DEPOSIT, "2026-05-06", "500", 6),
        ]
    )
    assert [line.balance_after for line in lines] == [
        D("495"),
        D("1695"),
        D("1742"),
        D("1442"),
        D("1500"),
        D("2000"),
    ]


def test_same_day_deposit_is_applied_before_withdrawal() -> None:
    lines = statement(
        [
            movement(MovementKind.WITHDRAWAL, "2026-01-01", "100", 1),
            movement(MovementKind.DEPOSIT, "2026-01-01", "100", 2),
        ]
    )
    assert [line.balance_after for line in lines] == [D("100"), D("0")]


def test_balance_of_sums_signed_amounts() -> None:
    assert balance_of(
        [
            movement(MovementKind.DEPOSIT, "2026-01-01", "1000"),
            movement(MovementKind.WITHDRAWAL, "2026-02-01", "250.50"),
            movement(MovementKind.INTEREST, "2026-03-01", "10"),
        ]
    ) == D("759.50")


def test_withdrawal_cannot_exceed_balance() -> None:
    existing = [movement(MovementKind.DEPOSIT, "2026-01-01", "500", 1)]
    check_movement_allowed(existing, movement(MovementKind.WITHDRAWAL, "2026-02-01", "500"))
    with pytest.raises(ValueError, match="Solde insuffisant"):
        check_movement_allowed(existing, movement(MovementKind.WITHDRAWAL, "2026-02-01", "500.01"))


def test_backdated_withdrawal_cannot_make_the_balance_negative_in_the_past() -> None:
    existing = [
        movement(MovementKind.DEPOSIT, "2026-03-01", "500", 1),
    ]
    with pytest.raises(ValueError, match="2026-02-01"):
        check_movement_allowed(existing, movement(MovementKind.WITHDRAWAL, "2026-02-01", "100"))


def test_movement_amount_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positif"):
        movement(MovementKind.DEPOSIT, "2026-01-01", "0")


# --- Intérêts ---------------------------------------------------------------


def test_estimated_annual_interest_is_rounded_to_the_cent() -> None:
    assert estimated_annual_interest(D("2000"), D("3.00")) == D("60.00")
    assert estimated_annual_interest(D("10000"), D("3.00")) == D("300.00")
    assert estimated_annual_interest(D("1234.56"), D("2.5")) == D("30.86")
    assert estimated_annual_interest(D("1250"), D("0")) == D("0.00")


def test_weighted_average_rate_uses_balances_as_weights() -> None:
    positions = [
        (D("2000"), D("3.00")),
        (D("1250"), D("0.00")),
        (D("8400"), D("3.00")),
        (D("10000"), D("3.00")),
    ]
    assert weighted_average_rate(positions) == D("2.83")
    assert weighted_average_rate([]) == D("0")
