"""Fonds : métadonnées simplifiées et validations métier."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from assets_tracker.domain.models import Position, Transaction, TransactionType

_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")

ZERO = Decimal(0)
HUNDRED = Decimal(100)
STALE_NAV_DAYS = 7  # au-delà, une VL est signalée comme à mettre à jour
HISTORY_DAYS = 365  # fenêtre de la courbe de VL
PARTS_STEP = Decimal("0.0001")  # précision d'un nombre de parts déduit d'un montant


class ValuationSource(StrEnum):
    YAHOO = "YAHOO"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class FundDetails:
    broker: str
    ter: Decimal | None
    valuation_source: ValuationSource
    quote_symbol: str | None

    def __post_init__(self) -> None:
        if self.ter is not None and not Decimal(0) <= self.ter <= Decimal(100):
            raise ValueError("Les frais courants doivent être compris entre 0 et 100 %.")
        if self.valuation_source is ValuationSource.YAHOO and not self.quote_symbol:
            raise ValueError(
                "Un ticker Yahoo est requis pour une valeur récupérée automatiquement."
            )


def validate_identifier(identifier: str) -> str:
    value = identifier.strip().upper()
    if not value:
        raise ValueError("L'identifiant ISIN ou ticker est obligatoire.")
    if " " in value:
        raise ValueError("L'identifiant ne peut pas contenir d'espace.")
    return value


def is_isin(identifier: str) -> bool:
    return _ISIN.fullmatch(identifier) is not None


def fee_amount(quantity: Decimal, nav: Decimal, rate: Decimal) -> Decimal:
    if quantity <= 0 or nav <= 0:
        raise ValueError("Le nombre de parts et la valeur liquidative doivent être positifs.")
    if not Decimal(0) <= rate <= Decimal(100):
        raise ValueError("Les frais doivent être compris entre 0 et 100 %.")
    return quantity * nav * rate / Decimal(100)


@dataclass(frozen=True, slots=True)
class FundValue:
    """Valorisation d'une position : valeur, coût (parts × PRU) et plus-value latente."""

    value: Decimal
    cost: Decimal
    gain: Decimal
    gain_percent: Decimal | None  # None si le coût est nul


def fund_value(position: Position, nav: Decimal) -> FundValue:
    value = position.quantity * nav
    cost = position.quantity * position.pru
    gain = value - cost
    return FundValue(value, cost, gain, gain / cost * HUNDRED if cost else None)


@dataclass(frozen=True, slots=True)
class FundTotals:
    value: Decimal  # valeur des positions valorisées
    invested: Decimal  # parts × PRU de toutes les positions ouvertes
    gain: Decimal  # plus-value latente des positions valorisées
    gain_percent: Decimal | None
    unvalued: int  # positions ouvertes sans VL connue


def fund_totals(positions: Iterable[tuple[Position, Decimal | None]]) -> FundTotals:
    """Totaux d'un ensemble de fonds de même devise.

    Une position sans VL compte dans le montant investi mais pas dans la valeur ni la
    plus-value : l'absence de cours ne doit pas apparaître comme une perte.
    """
    value = invested = valued_cost = ZERO
    unvalued = 0
    for position, nav in positions:
        if position.quantity <= ZERO:
            continue
        cost = position.quantity * position.pru
        invested += cost
        if nav is None:
            unvalued += 1
            continue
        value += position.quantity * nav
        valued_cost += cost
    gain = value - valued_cost
    percent = gain / valued_cost * HUNDRED if valued_cost else None
    return FundTotals(value, invested, gain, percent, unvalued)


def is_nav_stale(nav_date: date, today: date) -> bool:
    return (today - nav_date).days > STALE_NAV_DAYS


@dataclass(frozen=True, slots=True)
class TradeQuote:
    gross: Decimal  # parts × VL
    fees: Decimal
    net: Decimal  # débité à la souscription (frais ajoutés), crédité au rachat (frais déduits)


def trade_quote(
    kind: TransactionType, quantity: Decimal, nav: Decimal, fee_rate: Decimal
) -> TradeQuote:
    fees = fee_amount(quantity, nav, fee_rate)
    gross = quantity * nav
    return TradeQuote(gross, fees, gross + fees if kind is TransactionType.BUY else gross - fees)


def quantity_from_amount(amount: Decimal, nav: Decimal) -> Decimal:
    """Nombre de parts correspondant à un montant brut (hors frais), à 4 décimales."""
    if amount <= ZERO or nav <= ZERO:
        raise ValueError("Le montant et la valeur liquidative doivent être positifs.")
    return (amount / nav).quantize(PARTS_STEP, rounding=ROUND_HALF_UP)


def nav_series(
    history: Iterable[tuple[date, Decimal]], transactions: Iterable[Transaction], today: date
) -> list[tuple[date, Decimal]]:
    """Points de la courbe de VL sur 12 mois.

    Les VL enregistrées priment ; tant qu'il y en a moins de deux, on complète avec les VL
    des opérations (une par jour, la VL enregistrée l'emportant).
    """
    since = today - timedelta(days=HISTORY_DAYS)
    recorded = sorted(point for point in history if point[0] >= since)
    if len(recorded) >= 2:
        return recorded
    points = {t.trade_date: t.unit_price for t in transactions if t.trade_date >= since}
    points.update(recorded)
    return sorted(points.items())
