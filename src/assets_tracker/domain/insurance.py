"""Assurances & pensions : épargne-pension et assurance groupe, valeurs projetées par année.

Logique pure (ni Qt, ni SQLite, ni réseau). Ces contrats n'ont pas de cours : leur valeur est
celle du dernier relevé saisi à la main. Chaque relevé annuel garde aussi la **valeur projetée**
(le capital attendu à la retraite) : l'historique de ces projections montre comment l'estimation
de l'assureur évolue d'une année à l'autre. Ces contrats ne comptent pas dans le portefeuille.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal(0)
HUNDRED = Decimal(100)
STALE_DAYS = 365  # un relevé plus ancien qu'un an est signalé : la projection est à actualiser

__all__ = [
    "STALE_DAYS",
    "ContractDetails",
    "ContractKind",
    "ContractTotals",
    "Payment",
    "ProjectionStep",
    "Statement",
    "ceiling_left",
    "gain",
    "gain_percent",
    "is_stale",
    "latest_projection",
    "latest_statement",
    "paid_in_year",
    "projection_history",
    "total_paid",
    "totals",
    "validate_details",
    "validate_payment",
    "validate_statement",
]


class ContractKind(StrEnum):
    PENSION_SAVINGS = "PENSION_SAVINGS"
    GROUP_INSURANCE = "GROUP_INSURANCE"

    @property
    def label(self) -> str:
        return {
            "PENSION_SAVINGS": "Épargne-pension",
            "GROUP_INSURANCE": "Assurance groupe",
        }[self.value]


@dataclass(frozen=True, slots=True)
class ContractDetails:
    kind: ContractKind
    name: str
    insurer: str
    opened_on: date
    number: str = ""  # n° de contrat, facultatif
    retirement_on: date | None = None  # départ à la retraite prévu
    ceiling: Decimal | None = None  # plafond annuel de versement (épargne-pension seulement)


@dataclass(frozen=True, slots=True)
class Payment:
    id: int
    paid_on: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Statement:
    """Relevé : valeur actuelle du contrat et, si l'assureur la donne, la valeur projetée."""

    id: int
    recorded_on: date
    current_value: Decimal
    projected_value: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ProjectionStep:
    """Une projection de l'historique et son écart avec la projection précédente."""

    statement: Statement
    change: Decimal | None  # None pour la toute première projection
    change_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class ContractTotals:
    paid: Decimal
    value: Decimal
    projected: Decimal


# --- Validation -------------------------------------------------------------


def validate_details(details: ContractDetails) -> None:
    if not details.name.strip():
        raise ValueError("Le nom du contrat est obligatoire.")
    if not details.insurer.strip():
        raise ValueError("L'assureur est obligatoire.")
    if details.retirement_on is not None and details.retirement_on <= details.opened_on:
        raise ValueError("Le départ à la retraite doit suivre l'ouverture du contrat.")
    if details.ceiling is not None:
        if details.kind is not ContractKind.PENSION_SAVINGS:
            raise ValueError("Le plafond de versement ne concerne que l'épargne-pension.")
        if details.ceiling <= 0:
            raise ValueError("Le plafond annuel doit être positif.")


def validate_payment(details: ContractDetails, paid_on: date, amount: Decimal) -> None:
    if amount <= 0:
        raise ValueError("Le montant du versement doit être positif.")
    if paid_on < details.opened_on:
        raise ValueError("Le versement ne peut pas précéder l'ouverture du contrat.")


def validate_statement(
    details: ContractDetails,
    recorded_on: date,
    current_value: Decimal,
    projected_value: Decimal | None,
) -> None:
    if current_value < 0:
        raise ValueError("La valeur actuelle ne peut pas être négative.")
    if projected_value is not None and projected_value < 0:
        raise ValueError("La valeur projetée ne peut pas être négative.")
    if recorded_on < details.opened_on:
        raise ValueError("Le relevé ne peut pas précéder l'ouverture du contrat.")


# --- Calculs ----------------------------------------------------------------


def total_paid(payments: Iterable[Payment]) -> Decimal:
    return sum((p.amount for p in payments), ZERO)


def paid_in_year(payments: Iterable[Payment], year: int) -> Decimal:
    return sum((p.amount for p in payments if p.paid_on.year == year), ZERO)


def ceiling_left(
    details: ContractDetails, payments: Iterable[Payment], year: int
) -> Decimal | None:
    """Ce qui reste versable cette année ; `None` si le contrat n'a pas de plafond."""
    if details.ceiling is None:
        return None
    return max(ZERO, details.ceiling - paid_in_year(payments, year))


def latest_statement(statements: Sequence[Statement]) -> Statement | None:
    return max(statements, key=lambda s: s.recorded_on, default=None)


def latest_projection(statements: Sequence[Statement]) -> Statement | None:
    """Le relevé le plus récent qui porte une valeur projetée."""
    return max(
        (s for s in statements if s.projected_value is not None),
        key=lambda s: s.recorded_on,
        default=None,
    )


def is_stale(statements: Sequence[Statement], today: date) -> bool:
    """Vrai sans relevé, ou si le dernier a plus d'un an : la projection est à actualiser."""
    last = latest_statement(statements)
    return last is None or (today - last.recorded_on).days > STALE_DAYS


def gain(value: Decimal, paid: Decimal) -> Decimal:
    return value - paid


def gain_percent(value: Decimal, paid: Decimal) -> Decimal | None:
    return None if paid <= 0 else (value - paid) / paid * HUNDRED


def projection_history(statements: Sequence[Statement]) -> list[ProjectionStep]:
    """Historique des valeurs projetées, de la plus récente à la plus ancienne."""
    projected = sorted(
        (s for s in statements if s.projected_value is not None), key=lambda s: s.recorded_on
    )
    steps: list[ProjectionStep] = []
    previous: Decimal | None = None
    for statement in projected:
        value = statement.projected_value
        assert value is not None
        change: Decimal | None = None
        percent: Decimal | None = None
        if previous is not None:
            change = value - previous
            percent = None if previous == 0 else change / previous * HUNDRED
        steps.append(ProjectionStep(statement, change, percent))
        previous = value
    return steps[::-1]


def totals(contracts: Iterable[tuple[Sequence[Payment], Sequence[Statement]]]) -> ContractTotals:
    """Totaux sur plusieurs contrats : versé, dernière valeur, dernière projection."""
    paid = value = projected = ZERO
    for payments, statements in contracts:
        paid += total_paid(payments)
        last = latest_statement(statements)
        if last is not None:
            value += last.current_value
        proj = latest_projection(statements)
        if proj is not None and proj.projected_value is not None:
            projected += proj.projected_value
    return ContractTotals(paid, value, projected)
