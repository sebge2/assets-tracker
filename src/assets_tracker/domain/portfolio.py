"""Vue d'ensemble du portefeuille : valeur totale, plus-values, répartition et évolution mensuelle.

Tout est recalculé depuis les mouvements et les transactions, rien n'est stocké. Les sources
actuelles sont les comptes cash et les matières premières ; un nouveau type d'actif ajoute une
entrée `PortfolioInput` sans changer le reste.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from assets_tracker.domain.cash import (
    CashAccount,
    CashMovement,
    balance_of,
    first_month,
    month_end,
    months_range,
)
from assets_tracker.domain.models import InstrumentType, Transaction
from assets_tracker.domain.positions import compute_position

ZERO = Decimal(0)
HUNDRED = Decimal(100)
BASE_CURRENCY = "EUR"


@dataclass(frozen=True, slots=True)
class CashSource:
    account: CashAccount
    movements: list[CashMovement]


@dataclass(frozen=True, slots=True)
class PricedSource:
    """Un actif détenu par transactions, valorisé par un cours (en euros par unité)."""

    asset_type: InstrumentType
    transactions: list[Transaction]
    prices: list[tuple[date, Decimal]]  # (jour, cours), de la plus ancienne à la plus récente


@dataclass(frozen=True, slots=True)
class PortfolioInput:
    cash: list[CashSource]
    priced: list[PricedSource]


@dataclass(frozen=True, slots=True)
class Slice:
    asset_type: InstrumentType
    value: Decimal
    percent: Decimal


@dataclass(frozen=True, slots=True)
class Summary:
    total: Decimal
    unrealized: Decimal
    unrealized_percent: Decimal | None  # sur le coût des positions hors cash ; None si aucun
    realized: Decimal


@dataclass(frozen=True, slots=True)
class Portfolio:
    summary: Summary
    slices: list[Slice]  # par valeur décroissante, types vides omis
    months: list[date]  # premiers jours des mois, du plus ancien au mois en cours
    totals: list[Decimal]  # valeur de fin de mois (mois en cours : aujourd'hui)
    foreign_cash: tuple[str, ...]  # devises de comptes exclus (pas de taux de change stocké)


def unit_price_on(source: PricedSource, day: date) -> Decimal | None:
    """Cours du jour : dernière valorisation ≤ `day`, sinon prix de la dernière transaction."""
    quote = [price for when, price in source.prices if when <= day]
    if quote:
        return quote[-1]
    traded = sorted(
        (t for t in source.transactions if t.trade_date <= day),
        key=lambda t: (t.trade_date, t.id or 0),
    )
    return traded[-1].unit_price if traded else None


def value_on(source: PricedSource, day: date) -> Decimal:
    """Valeur de la position à `day` (0 si rien n'est détenu)."""
    held = [t for t in source.transactions if t.trade_date <= day]
    if not held:
        return ZERO
    position = compute_position(held[0].asset_id, held)
    price = unit_price_on(source, day)
    return ZERO if price is None else position.quantity * price


def _eur_cash(sources: Iterable[CashSource]) -> list[CashSource]:
    return [s for s in sources if s.account.currency == BASE_CURRENCY]


def _cash_value_on(sources: Iterable[CashSource], day: date) -> Decimal:
    total = ZERO
    for source in sources:
        movements = [m for m in source.movements if m.movement_date <= day]
        total += balance_of(movements)
    return total


def total_on(data: PortfolioInput, day: date) -> Decimal:
    cash = _cash_value_on(_eur_cash(data.cash), day)
    return cash + sum((value_on(s, day) for s in data.priced), ZERO)


def allocation(values: dict[InstrumentType, Decimal]) -> list[Slice]:
    """Parts par type d'actif, de la plus grande à la plus petite ; les types vides sont omis."""
    positive = {kind: value for kind, value in values.items() if value > 0}
    total = sum(positive.values(), ZERO)
    ranked = sorted(positive.items(), key=lambda item: item[1], reverse=True)
    return [Slice(kind, value, value / total * HUNDRED) for kind, value in ranked]


def build_portfolio(data: PortfolioInput, today: date) -> Portfolio:
    cash_sources = _eur_cash(data.cash)
    foreign = tuple(
        sorted({s.account.currency for s in data.cash if s.account.currency != BASE_CURRENCY})
    )

    values: dict[InstrumentType, Decimal] = {}
    cash_value = _cash_value_on(cash_sources, today)
    if cash_value:
        values[InstrumentType.CASH] = cash_value
    cost = ZERO
    invested = ZERO
    realized = ZERO
    for source in data.priced:
        held = [t for t in source.transactions if t.trade_date <= today]
        if not held:
            continue
        position = compute_position(held[0].asset_id, held)
        realized += position.realized_pnl
        value = value_on(source, today)
        values[source.asset_type] = values.get(source.asset_type, ZERO) + value
        cost += position.quantity * position.pru
        invested += value
    unrealized = invested - cost
    summary = Summary(
        total=sum(values.values(), ZERO),
        unrealized=unrealized,
        unrealized_percent=unrealized / cost * HUNDRED if cost > 0 else None,
        realized=realized,
    )

    firsts = [first_month(s.account.opened_on, s.movements) for s in cash_sources]
    firsts += [
        min(t.trade_date for t in s.transactions).replace(day=1)
        for s in data.priced
        if s.transactions
    ]
    if firsts:
        months = months_range(min(firsts), today)
        totals = [total_on(data, min(month_end(m), today)) for m in months]
    else:
        months, totals = [], []
    return Portfolio(summary, allocation(values), months, totals, foreign)


def window(
    months: Sequence[date], totals: Sequence[Decimal], count: int
) -> tuple[list[date], list[Decimal]]:
    """Derniers `count` mois (0 = tout l'historique), sans remonter avant le premier mois connu."""
    if count <= 0 or not months:
        return list(months), list(totals)
    size = min(count, len(months))
    return list(months[-size:]), list(totals[-size:])


def change(totals: Sequence[Decimal]) -> tuple[Decimal, Decimal | None]:
    """Variation entre le premier et le dernier point : montant et % (None si départ nul)."""
    if len(totals) < 2:
        return ZERO, None
    delta = totals[-1] - totals[0]
    return delta, (delta / totals[0] * HUNDRED if totals[0] > 0 else None)


__all__ = [
    "BASE_CURRENCY",
    "CashSource",
    "Portfolio",
    "PortfolioInput",
    "PricedSource",
    "Slice",
    "Summary",
    "allocation",
    "build_portfolio",
    "change",
    "total_on",
    "unit_price_on",
    "value_on",
    "window",
]
