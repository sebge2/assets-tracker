"""Actions : paramètres, valeur de marché, dividendes, répartition par secteur, aperçu d'opération.

Logique pure (ni Qt, ni SQLite, ni réseau). Un titre se cote dans sa devise : prix des
transactions, frais et cours (`valuations`) sont stockés dans la devise de l'action ; les
synthèses multi-devises passent par `stock_in_euros` avec le taux du cache `exchange_rates`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from assets_tracker.domain.funds import (
    STALE_NAV_DAYS,
    FundValue,
    ValuationSource,
    fund_value,
    is_nav_stale,
    nav_series,
)
from assets_tracker.domain.models import Position, Transaction, TransactionType

ZERO = Decimal(0)
HUNDRED = Decimal(100)
STALE_QUOTE_DAYS = STALE_NAV_DAYS  # au-delà, un cours est signalé comme à mettre à jour
OTHER_SECTOR = "Autre"


@dataclass(frozen=True, slots=True)
class StockDetails:
    place: str
    sector: str
    dividend: Decimal | None  # dividende annuel par action, dans la devise de l'action
    valuation_source: ValuationSource

    def __post_init__(self) -> None:
        if self.dividend is not None and self.dividend < ZERO:
            raise ValueError("Le dividende ne peut pas être négatif.")


def validate_ticker(ticker: str) -> str:
    value = ticker.strip().upper()
    if not value:
        raise ValueError("Le ticker Yahoo est obligatoire.")
    if " " in value:
        raise ValueError("Le ticker ne peut pas contenir d'espace.")
    return value


def validate_shares(quantity: Decimal) -> Decimal:
    if not quantity.is_finite() or quantity <= ZERO:
        raise ValueError("Le nombre d'actions doit être strictement positif.")
    return quantity


def is_quote_stale(quote_date: date, today: date) -> bool:
    return is_nav_stale(quote_date, today)


def stock_value(position: Position, price: Decimal) -> FundValue:
    return fund_value(position, price)


def annual_dividends(details: StockDetails, quantity: Decimal) -> Decimal:
    return ZERO if details.dividend is None else details.dividend * quantity


def dividend_yield(details: StockDetails, price: Decimal) -> Decimal | None:
    """Rendement du dividende en % du cours, `None` sans dividende connu."""
    if details.dividend is None or price <= ZERO:
        return None
    return details.dividend / price * HUNDRED


def price_change(history: Iterable[tuple[date, Decimal]]) -> Decimal | None:
    """Variation en % entre les deux derniers cours enregistrés, `None` sans deux cours."""
    points = sorted(history)
    if len(points) < 2 or points[-2][1] <= ZERO:
        return None
    return (points[-1][1] - points[-2][1]) / points[-2][1] * HUNDRED


@dataclass(frozen=True, slots=True)
class StockTotals:
    value: Decimal  # valeur des positions valorisées
    invested: Decimal  # actions × PRU de toutes les positions ouvertes
    gain: Decimal  # plus-value latente des positions valorisées
    gain_percent: Decimal | None
    dividends: Decimal  # dividendes annuels de toutes les positions ouvertes
    dividend_yield: Decimal | None  # dividendes des positions valorisées / leur valeur
    unvalued: int


def stock_totals(
    items: Iterable[tuple[Position, StockDetails, Decimal | None]],
) -> StockTotals:
    """Totaux d'actions de même devise ; `None` = pas de cours connu.

    Une position sans cours compte dans le montant investi et les dividendes, pas dans la valeur
    ni la plus-value : l'absence de cours ne doit pas apparaître comme une perte.
    """
    value = invested = valued_cost = dividends = valued_dividends = ZERO
    unvalued = 0
    for position, details, price in items:
        if position.quantity <= ZERO:
            continue
        cost = position.quantity * position.pru
        invested += cost
        dividend = annual_dividends(details, position.quantity)
        dividends += dividend
        if price is None:
            unvalued += 1
            continue
        value += position.quantity * price
        valued_cost += cost
        valued_dividends += dividend
    gain = value - valued_cost
    return StockTotals(
        value,
        invested,
        gain,
        gain / valued_cost * HUNDRED if valued_cost else None,
        dividends,
        valued_dividends / value * HUNDRED if value else None,
        unvalued,
    )


def stock_in_euros(
    position: Position, details: StockDetails, price: Decimal | None, rate: Decimal
) -> tuple[Position, StockDetails, Decimal | None]:
    """Convertit une action en euros (taux `devise → EUR`) pour `stock_totals`."""
    converted = replace(
        position, pru=position.pru * rate, realized_pnl=position.realized_pnl * rate
    )
    dividend = None if details.dividend is None else details.dividend * rate
    return (
        converted,
        replace(details, dividend=dividend),
        None if price is None else price * rate,
    )


def sector_breakdown(items: Iterable[tuple[str, Decimal]]) -> list[tuple[str, Decimal, Decimal]]:
    """(secteur, valeur, % du total), du plus gros au plus petit ; secteur vide = « Autre »."""
    per_sector: dict[str, Decimal] = {}
    for sector, value in items:
        if value <= ZERO:
            continue
        name = sector.strip() or OTHER_SECTOR
        per_sector[name] = per_sector.get(name, ZERO) + value
    total = sum(per_sector.values(), ZERO)
    ranked = sorted(per_sector.items(), key=lambda entry: (-entry[1], entry[0]))
    return [(name, value, value / total * HUNDRED) for name, value in ranked]


def price_series(
    history: Iterable[tuple[date, Decimal]], transactions: Iterable[Transaction], today: date
) -> list[tuple[date, Decimal]]:
    """Courbe du cours sur 12 mois (mêmes règles que `nav_series`)."""
    return nav_series(history, transactions, today)


@dataclass(frozen=True, slots=True)
class StockQuote:
    gross: Decimal
    fees: Decimal
    total: Decimal  # débité à l'achat (frais ajoutés), crédité à la vente (frais déduits)


def stock_trade_quote(
    kind: TransactionType, quantity: Decimal, price: Decimal, fees: Decimal
) -> StockQuote:
    validate_shares(quantity)
    if price <= ZERO:
        raise ValueError("Le cours doit être strictement positif.")
    if fees < ZERO:
        raise ValueError("Les frais ne peuvent pas être négatifs.")
    gross = quantity * price
    return StockQuote(gross, fees, gross + fees if kind is TransactionType.BUY else gross - fees)


__all__ = [
    "STALE_QUOTE_DAYS",
    "StockDetails",
    "StockQuote",
    "StockTotals",
    "ValuationSource",
    "annual_dividends",
    "dividend_yield",
    "is_quote_stale",
    "price_change",
    "price_series",
    "sector_breakdown",
    "stock_in_euros",
    "stock_totals",
    "stock_trade_quote",
    "stock_value",
    "validate_shares",
    "validate_ticker",
]
