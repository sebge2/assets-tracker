"""Projection à intérêts composés annuels, avec ou sans versement mensuel (logique pure).

Estimation indicative, jamais une garantie de rendement.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)
MAX_YEARS = 60


@dataclass(frozen=True, slots=True)
class ProjectionAssumptions:
    initial: Decimal  # capital de départ
    monthly_contribution: Decimal  # versement mensuel régulier (0 = scénario sans versement)
    annual_rate_percent: Decimal  # rendement annuel en %, ex. Decimal("6")
    years: int

    def __post_init__(self) -> None:
        if self.initial < ZERO:
            raise ValueError("Le capital de départ ne peut pas être négatif.")
        if self.monthly_contribution < ZERO:
            raise ValueError("Le versement mensuel ne peut pas être négatif.")
        if not ZERO <= self.annual_rate_percent <= 100:
            raise ValueError("Le rendement annuel doit être compris entre 0 et 100 %.")
        if not 1 <= self.years <= MAX_YEARS:
            raise ValueError(f"La durée doit être comprise entre 1 et {MAX_YEARS} ans.")


@dataclass(frozen=True, slots=True)
class YearPoint:
    year: int
    contributed: Decimal  # capital de départ + versements cumulés
    balance: Decimal  # capital projeté avec versements
    interest: Decimal  # balance - contributed
    balance_without_contributions: Decimal  # scénario sans aucun versement mensuel


def project(assumptions: ProjectionAssumptions) -> list[YearPoint]:
    """Une ligne par année, de l'année 0 à `years`.

    `V(n+1) = V(n) × (1 + r) + 12 × versement_mensuel` : intérêts composés annuels, les
    versements de l'année s'ajoutent en fin d'année (sans intérêts sur l'année en cours).
    Les valeurs ne sont pas arrondies : l'arrondi se fait à l'affichage.
    """
    growth = 1 + assumptions.annual_rate_percent / 100
    yearly = assumptions.monthly_contribution * 12
    balance = assumptions.initial
    without = assumptions.initial
    contributed = assumptions.initial
    points = [YearPoint(0, contributed, balance, ZERO, without)]
    for year in range(1, assumptions.years + 1):
        balance = balance * growth + yearly
        without = without * growth
        contributed += yearly
        points.append(YearPoint(year, contributed, balance, balance - contributed, without))
    return points


def milestone_years(years: int, step: int = 5) -> list[int]:
    """Horizons du tableau : multiples de `step` jusqu'à la durée, puis la durée elle-même."""
    horizons = list(range(step, years + 1, step))
    if not horizons or horizons[-1] != years:
        horizons.append(years)
    return horizons
