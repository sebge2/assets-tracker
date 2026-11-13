"""Obligations : paramètres, valeur de marché, coupon couru, échéancier et aperçu d'opération.

Logique pure (ni Qt, ni SQLite, ni réseau). Le cours est exprimé en % du nominal ; la valeur
d'un titre est `cours % × nominal / 100`, c'est elle que `valuations` et `transactions`
stockent (en euros par titre). Le PRU est donc lui aussi en euros par titre : on l'affiche en %
du nominal avec `price_percent`.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

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
CENT = Decimal("0.01")
STALE_QUOTE_DAYS = STALE_NAV_DAYS  # au-delà, un cours est signalé comme à mettre à jour

__all__ = [
    "STALE_QUOTE_DAYS",
    "BondDetails",
    "BondQuote",
    "BondTotals",
    "CouponFrequency",
    "ValuationSource",
]


class CouponFrequency(StrEnum):
    ANNUAL = "ANNUAL"
    SEMIANNUAL = "SEMIANNUAL"
    QUARTERLY = "QUARTERLY"

    @property
    def per_year(self) -> int:
        return {"ANNUAL": 1, "SEMIANNUAL": 2, "QUARTERLY": 4}[self.value]

    @property
    def months(self) -> int:
        return 12 // self.per_year

    @property
    def label(self) -> str:
        return {"ANNUAL": "annuel", "SEMIANNUAL": "semestriel", "QUARTERLY": "trimestriel"}[
            self.value
        ]


@dataclass(frozen=True, slots=True)
class BondDetails:
    issuer: str
    nominal: Decimal  # par titre
    coupon_rate: Decimal  # en % du nominal, par an
    frequency: CouponFrequency
    maturity: date
    valuation_source: ValuationSource

    def __post_init__(self) -> None:
        if self.nominal <= ZERO:
            raise ValueError("Le nominal doit être strictement positif.")
        if not ZERO <= self.coupon_rate <= HUNDRED:
            raise ValueError("Le taux du coupon doit être compris entre 0 et 100 %.")


def unit_value(price_percent: Decimal, nominal: Decimal) -> Decimal:
    """Valeur d'un titre pour un cours en % du nominal."""
    if price_percent <= ZERO:
        raise ValueError("Le cours doit être strictement positif.")
    return price_percent * nominal / HUNDRED


def price_percent(value: Decimal, nominal: Decimal) -> Decimal:
    """Cours en % du nominal pour la valeur d'un titre."""
    return value / nominal * HUNDRED


def bond_value(position: Position, value_per_bond: Decimal) -> FundValue:
    """Valeur de marché, coût (titres × PRU) et plus-value latente d'une position."""
    return fund_value(position, value_per_bond)


def is_quote_stale(quote_date: date, today: date) -> bool:
    return is_nav_stale(quote_date, today)


# --- Coupons ---------------------------------------------------------------


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def coupon_dates_around(details: BondDetails, on: date) -> tuple[date, date] | None:
    """Dernier coupon ≤ `on` et prochain coupon > `on` ; None une fois l'échéance atteinte.

    Les dates de coupon sont les anniversaires de l'échéance, tous les 12/n mois.
    """
    if on >= details.maturity:
        return None
    step = details.frequency.months
    following = details.maturity
    k = 1
    while True:
        previous = _add_months(details.maturity, -k * step)
        if previous <= on:
            return previous, following
        following = previous
        k += 1


def annual_coupon(details: BondDetails, quantity: Decimal) -> Decimal:
    return details.nominal * details.coupon_rate / HUNDRED * quantity


def next_coupon(details: BondDetails, quantity: Decimal, on: date) -> tuple[date, Decimal] | None:
    around = coupon_dates_around(details, on)
    if around is None:
        return None
    amount = annual_coupon(details, quantity) / details.frequency.per_year
    return around[1], amount.quantize(CENT, rounding=ROUND_HALF_UP)


def accrued_coupon(details: BondDetails, quantity: Decimal, on: date) -> Decimal:
    """Coupon couru pour `quantity` titres : prorata des jours écoulés dans la période en cours."""
    around = coupon_dates_around(details, on)
    if around is None or quantity <= ZERO:
        return ZERO
    previous, following = around
    per_period = annual_coupon(details, quantity) / details.frequency.per_year
    elapsed = Decimal((on - previous).days) / Decimal((following - previous).days)
    return (per_period * elapsed).quantize(CENT, rounding=ROUND_HALF_UP)


def current_yield(coupon_rate: Decimal, price_percent_: Decimal) -> Decimal:
    """Rendement courant : coupon annuel / cours (tous deux en % du nominal)."""
    return coupon_rate / price_percent_ * HUNDRED


def remaining_label(maturity: date, today: date) -> str:
    """`dans 4 ans 6 mois`, `dans 4 mois`, `dans 12 jours`, `échue`."""
    if maturity <= today:
        return "échue"
    months = (maturity.year - today.year) * 12 + maturity.month - today.month
    if maturity.day < today.day:
        months -= 1
    if months <= 0:
        days = (maturity - today).days
        return f"dans {days} jour{'s' if days > 1 else ''}"
    years, rest = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} an{'s' if years > 1 else ''}")
    if rest:
        parts.append(f"{rest} mois")
    return "dans " + " ".join(parts)


# --- Totaux et échéancier ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class BondTotals:
    value: Decimal  # valeur des positions valorisées
    invested: Decimal  # titres × PRU de toutes les positions ouvertes
    gain: Decimal  # plus-value latente des positions valorisées
    gain_percent: Decimal | None
    annual_coupons: Decimal  # coupons annuels de toutes les positions ouvertes
    current_yield: Decimal | None  # coupons des positions valorisées / leur valeur
    unvalued: int


def bond_totals(
    items: Iterable[tuple[Position, BondDetails, Decimal | None]],
) -> BondTotals:
    """Totaux d'obligations de même devise ; `None` = pas de cours connu.

    Une position sans cours compte dans le montant investi et les coupons, pas dans la valeur ni
    la plus-value : l'absence de cours ne doit pas apparaître comme une perte.
    """
    value = invested = valued_cost = coupons = valued_coupons = ZERO
    unvalued = 0
    for position, details, value_per_bond in items:
        if position.quantity <= ZERO:
            continue
        cost = position.quantity * position.pru
        invested += cost
        coupon = annual_coupon(details, position.quantity)
        coupons += coupon
        if value_per_bond is None:
            unvalued += 1
            continue
        value += position.quantity * value_per_bond
        valued_cost += cost
        valued_coupons += coupon
    gain = value - valued_cost
    return BondTotals(
        value,
        invested,
        gain,
        gain / valued_cost * HUNDRED if valued_cost else None,
        coupons,
        valued_coupons / value * HUNDRED if value else None,
        unvalued,
    )


def maturity_ladder(
    items: Iterable[tuple[BondDetails, Decimal]], today: date
) -> list[tuple[int, Decimal]]:
    """Nominal à échéance par année, de la première à la dernière (années vides à zéro)."""
    per_year: dict[int, Decimal] = {}
    for details, quantity in items:
        if quantity <= ZERO or details.maturity <= today:
            continue
        year = details.maturity.year
        per_year[year] = per_year.get(year, ZERO) + details.nominal * quantity
    if not per_year:
        return []
    return [(y, per_year.get(y, ZERO)) for y in range(min(per_year), max(per_year) + 1)]


def price_series(
    history: Iterable[tuple[date, Decimal]],
    transactions: Iterable[Transaction],
    nominal: Decimal,
    today: date,
) -> list[tuple[date, Decimal]]:
    """Courbe du cours sur 12 mois, en % du nominal (mêmes règles que `nav_series`)."""
    return [(day, price_percent(v, nominal)) for day, v in nav_series(history, transactions, today)]


# --- Opérations ---------------------------------------------------------------


def validate_quantity(quantity: Decimal) -> Decimal:
    if quantity <= ZERO or quantity != quantity.to_integral_value():
        raise ValueError("Le nombre de titres doit être un entier strictement positif.")
    return quantity


@dataclass(frozen=True, slots=True)
class BondQuote:
    gross: Decimal  # titres × valeur d'un titre
    accrued: Decimal  # coupon couru payé (achat) ou perçu (vente)
    total: Decimal  # débité à l'achat, crédité à la vente : montant + coupon couru


def bond_trade_quote(
    quantity: Decimal, price_percent_: Decimal, nominal: Decimal, accrued: Decimal
) -> BondQuote:
    """Aperçu d'achat ou de vente. Le coupon couru s'ajoute au montant dans les deux sens."""
    validate_quantity(quantity)
    if accrued < ZERO:
        raise ValueError("Le coupon couru ne peut pas être négatif.")
    gross = quantity * unit_value(price_percent_, nominal)
    return BondQuote(gross, accrued, gross + accrued)


def trade_amount(kind: TransactionType, quote: BondQuote) -> Decimal:
    """Flux de trésorerie signé : négatif à l'achat, positif à la vente."""
    return -quote.total if kind is TransactionType.BUY else quote.total


def bond_in_euros(
    position: Position, details: BondDetails, value_per_bond: Decimal | None, rate: Decimal
) -> tuple[Position, BondDetails, Decimal | None]:
    """Convertit une obligation en euros (taux `devise → EUR`) pour `bond_totals` et l'échéancier.

    Le nominal, le PRU et la valeur d'un titre sont multipliés par le taux ; les % ne changent pas.
    """
    converted = replace(
        position, pru=position.pru * rate, realized_pnl=position.realized_pnl * rate
    )
    return (
        converted,
        replace(details, nominal=details.nominal * rate),
        None if value_per_bond is None else value_per_bond * rate,
    )
