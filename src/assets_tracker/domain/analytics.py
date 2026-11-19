"""Analyses du portefeuille : rendement par année, apports, gain réalisé et latent, revenus.

Logique pure (ni Qt, ni SQLite, ni réseau), calculée depuis les mêmes sources que le
Portefeuille (`PortfolioInput`). Les assurances & pensions n'en font pas partie.

Vocabulaire :
- **apport** : argent nouveau entré dans le portefeuille (dépôt sur un compte cash, achat d'un
  titre) ; un retrait ou une vente est un apport négatif. Un achat financé par un retrait du
  compte cash s'annule naturellement (retrait − achat) ;
- **revenu versé hors du portefeuille** (`distributions`) : coupons d'obligations et intérêts
  de bons de caisse, jamais portés sur un compte. Ils comptent dans le gain, sans entrer dans la
  valeur. Un revenu que l'utilisateur enregistrerait aussi comme dépôt serait compté deux fois ;
- **gain** d'une période = valeur de fin − valeur de début − apports + revenus versés hors du
  portefeuille ;
- **rendement (TWR)** : rendement pondéré par le temps, chaînage de rendements mensuels (formule
  de Dietz modifiée), où l'apport d'un mois ne compte pas comme une performance.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import partial

from assets_tracker.domain.cash import (
    CashMovement,
    MovementKind,
    balance_of,
    month_end,
    months_range,
)
from assets_tracker.domain.models import InstrumentType, Transaction, TransactionType
from assets_tracker.domain.portfolio import (
    BASE_CURRENCY,
    PortfolioInput,
    PricedSource,
    value_on,
)
from assets_tracker.domain.positions import compute_position

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)

Dated = tuple[date, Decimal]

__all__ = [
    "Analysis",
    "Performance",
    "Unit",
    "YearResult",
    "analyze",
    "annualized",
    "capital_invested",
    "gains_by_type",
    "income_by_type",
    "performance",
    "realized_between",
    "unit_gains",
    "units_from",
    "values_by_type",
]


def _no_realized(_day: date) -> Decimal:
    return ZERO


@dataclass(frozen=True, slots=True)
class Unit:
    """Une ligne du portefeuille : un compte cash ou un actif détenu par transactions."""

    name: str
    asset_type: InstrumentType
    value_on: Callable[[date], Decimal]
    flows: tuple[Dated, ...] = ()  # apports (+) et retraits (−)
    distributions: tuple[Dated, ...] = ()  # revenus versés hors du portefeuille
    income: tuple[Dated, ...] = ()  # tous les revenus perçus, pour l'affichage
    inside_income: tuple[Dated, ...] = ()  # revenus déjà dans la valeur (intérêts du cash)
    realized_on: Callable[[date], Decimal] = _no_realized  # plus-values de vente


@dataclass(frozen=True, slots=True)
class Performance:
    start: Decimal  # valeur au début de la période
    end: Decimal  # valeur à la fin
    contributions: Decimal  # apports nets
    distributions: Decimal  # revenus versés hors du portefeuille
    gain: Decimal
    twr: Decimal | None  # rendement en % ; None sans base de calcul


@dataclass(frozen=True, slots=True)
class YearResult:
    year: int
    start_day: date
    end_day: date
    performance: Performance
    by_type: dict[InstrumentType, Decimal | None]  # rendement (TWR) de chaque type, en %
    realized: Decimal
    latent: Decimal  # variation des plus-values latentes : gain − réalisé
    income: dict[InstrumentType, Decimal]  # revenus perçus, par type


@dataclass(frozen=True, slots=True)
class Analysis:
    years: list[YearResult]  # de la première année d'activité à l'année en cours
    total: Performance  # depuis le début
    total_start: date
    total_end: date
    annualized: Decimal | None  # rendement annualisé depuis le début, en %
    types: list[InstrumentType]  # types présents, de la plus grosse valeur à la plus petite
    allocation: list[tuple[date, dict[InstrumentType, Decimal]]]  # valeur par type, fin d'année
    income_last_12_months: Decimal
    value: Decimal  # valeur à la date du jour


# --- Construction des lignes ------------------------------------------------


def units_from(data: PortfolioInput) -> list[Unit]:
    """Les lignes du portefeuille : comptes cash en euros et actifs détenus par transactions."""
    units: list[Unit] = []
    for source in data.cash:
        if source.account.currency != BASE_CURRENCY:
            continue  # comme le Portefeuille : sans taux de change, un compte étranger est exclu
        movements = list(source.movements)
        flows = tuple(
            (m.movement_date, -m.amount if m.kind is MovementKind.WITHDRAWAL else m.amount)
            for m in movements
            if m.kind in (MovementKind.OPENING, MovementKind.DEPOSIT, MovementKind.WITHDRAWAL)
        )
        interest = tuple(
            (m.movement_date, m.amount)
            for m in movements
            if m.kind in (MovementKind.INTEREST, MovementKind.LOYALTY_BONUS)
        )
        units.append(
            Unit(
                source.account.name,
                InstrumentType.CASH,
                _cash_value(movements),
                flows,
                (),
                interest,
                interest,
            )
        )
    for priced in data.priced:
        transactions = list(priced.transactions)
        if not transactions:
            continue
        asset_id = transactions[0].asset_id
        flows = tuple(
            (
                t.trade_date,
                t.quantity * t.unit_price + t.fees
                if t.type is TransactionType.BUY
                else -(t.quantity * t.unit_price - t.fees),
            )
            for t in transactions
        )
        units.append(
            Unit(
                priced.name or priced.asset_type.value,
                priced.asset_type,
                partial(_priced_value, priced),
                flows,
                tuple(priced.distributions),
                tuple(priced.income),
                (),
                partial(_realized, asset_id, transactions),
            )
        )
    return units


def _cash_value(movements: list[CashMovement]) -> Callable[[date], Decimal]:
    def value(day: date) -> Decimal:
        return balance_of(m for m in movements if m.movement_date <= day)

    return value


def _priced_value(source: PricedSource, day: date) -> Decimal:
    return value_on(source, day)


def _realized(asset_id: int, transactions: Sequence[Transaction], day: date) -> Decimal:
    held = [t for t in transactions if t.trade_date <= day]
    return compute_position(asset_id, held).realized_pnl if held else ZERO


# --- Calculs sur une période ------------------------------------------------


def _sum_between(items: Iterable[Dated], after: date, upto: date) -> Decimal:
    return sum((amount for when, amount in items if after < when <= upto), ZERO)


def _values(units: Sequence[Unit], day: date) -> Decimal:
    return sum((u.value_on(day) for u in units), ZERO)


def _boundaries(start: date, end: date) -> list[date]:
    """Début, fins de mois intermédiaires et fin : les sous-périodes du chaînage."""
    points = [start]
    for month in months_range(start, end):
        last = month_end(month)
        if start < last < end:
            points.append(last)
    if end > start:
        points.append(end)
    return points


def performance(units: Sequence[Unit], start: date, end: date) -> Performance:
    """Gain et rendement pondéré par le temps de `units` sur la période ]start, end]."""
    start_value = _values(units, start)
    end_value = _values(units, end)
    flows = [f for u in units for f in u.flows]
    contributions = _sum_between(flows, start, end)
    distributions = _sum_between((d for u in units for d in u.distributions), start, end)
    gain = end_value - start_value - contributions + distributions
    growth = ONE
    measured = False
    points = _boundaries(start, end)
    previous = start_value
    for begin, finish in zip(points, points[1:], strict=False):
        current = end_value if finish == end else _values(units, finish)
        span = (finish - begin).days
        period_flows = [(d, a) for d, a in flows if begin < d <= finish]
        weighted = sum(
            (a * Decimal((finish - d).days) / Decimal(span) for d, a in period_flows), ZERO
        )
        net = sum((a for _, a in period_flows), ZERO)
        income = _sum_between((d for u in units for d in u.distributions), begin, finish)
        base = previous + weighted
        if base > ZERO:
            growth *= ONE + (current - previous - net + income) / base
            measured = True
        previous = current
    twr = (growth - ONE) * HUNDRED if measured else None
    return Performance(start_value, end_value, contributions, distributions, gain, twr)


def annualized(twr: Decimal | None, start: date, end: date) -> Decimal | None:
    """Rendement annualisé : un rendement `twr` (en %) sur la durée `start` → `end`."""
    days = (end - start).days
    if twr is None or days <= 0:
        return None
    growth = float(ONE + twr / HUNDRED)
    if growth <= 0:
        return None
    return Decimal(growth ** (365 / days) - 1) * HUNDRED


def capital_invested(units: Sequence[Unit], start: date, end: date) -> Decimal | None:
    """Valeur de début + apports nets : la base des « points » de contribution."""
    base = _values(units, start) + _sum_between((f for u in units for f in u.flows), start, end)
    return base if base > ZERO else None


def unit_gains(units: Sequence[Unit], start: date, end: date) -> list[tuple[Unit, Decimal]]:
    """Gain de chaque ligne sur la période, du plus grand au plus petit."""
    gains = [
        (
            unit,
            unit.value_on(end)
            - unit.value_on(start)
            - _sum_between(unit.flows, start, end)
            + _sum_between(unit.distributions, start, end),
        )
        for unit in units
    ]
    return sorted((g for g in gains if g[1] != ZERO), key=lambda item: item[1], reverse=True)


def gains_by_type(units: Sequence[Unit], start: date, end: date) -> dict[InstrumentType, Decimal]:
    totals: dict[InstrumentType, Decimal] = {}
    for unit, gain in unit_gains(units, start, end):
        totals[unit.asset_type] = totals.get(unit.asset_type, ZERO) + gain
    return totals


def values_by_type(units: Sequence[Unit], day: date) -> dict[InstrumentType, Decimal]:
    totals: dict[InstrumentType, Decimal] = {}
    for unit in units:
        value = unit.value_on(day)
        if value != ZERO:
            totals[unit.asset_type] = totals.get(unit.asset_type, ZERO) + value
    return totals


def income_by_type(units: Sequence[Unit], start: date, end: date) -> dict[InstrumentType, Decimal]:
    """Revenus perçus sur la période, par type d'actif."""
    totals: dict[InstrumentType, Decimal] = {}
    for unit in units:
        amount = _sum_between(unit.income, start, end)
        if amount != ZERO:
            totals[unit.asset_type] = totals.get(unit.asset_type, ZERO) + amount
    return totals


def realized_between(units: Sequence[Unit], start: date, end: date) -> Decimal:
    """Gain réalisé : plus-values de vente, intérêts du cash et revenus versés hors portefeuille."""
    return sum(
        (
            u.realized_on(end)
            - u.realized_on(start)
            + _sum_between(u.inside_income, start, end)
            + _sum_between(u.distributions, start, end)
            for u in units
        ),
        ZERO,
    )


# --- Vue d'ensemble ---------------------------------------------------------


def _first_day(units: Sequence[Unit]) -> date | None:
    days = [d for u in units for d, _ in (*u.flows, *u.income)]
    return min(days) if days else None


def analyze(units: Sequence[Unit], today: date) -> Analysis | None:
    """Résultats année par année et depuis le début ; `None` sans aucune activité."""
    first = _first_day(units)
    if first is None or first > today:
        return None
    types = sorted(
        {u.asset_type for u in units},
        key=lambda kind: values_by_type(units, today).get(kind, ZERO),
        reverse=True,
    )
    years: list[YearResult] = []
    for year in range(first.year, today.year + 1):
        start, end = date(year - 1, 12, 31), min(date(year, 12, 31), today)
        by_type: dict[InstrumentType, Decimal | None] = {}
        for kind in types:
            part = [u for u in units if u.asset_type is kind]
            by_type[kind] = performance(part, start, end).twr
        result = performance(units, start, end)
        realized = realized_between(units, start, end)
        years.append(
            YearResult(
                year,
                start,
                end,
                result,
                by_type,
                realized,
                result.gain - realized,
                income_by_type(units, start, end),
            )
        )
    total_start = date(first.year - 1, 12, 31)
    total = performance(units, total_start, today)
    points = [total_start] + [date(y, 12, 31) for y in range(first.year, today.year)] + [today]
    allocation = [(day, values_by_type(units, day)) for day in points]
    return Analysis(
        years,
        total,
        total_start,
        today,
        annualized(total.twr, first - timedelta(days=1), today),
        types,
        allocation,
        sum(income_by_type(units, today - timedelta(days=365), today).values(), ZERO),
        _values(units, today),
    )
