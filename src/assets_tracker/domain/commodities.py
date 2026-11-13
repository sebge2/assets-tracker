"""Matières premières : détention physique ou ETC, conversion de cours, aperçus d'achat et de vente.

Les positions restent recalculées depuis les transactions (voir `positions.py`) ; ce module
ne stocke rien et ne dépend ni de Qt, ni de SQLite, ni du réseau.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from assets_tracker.domain.models import Position

ZERO = Decimal(0)
HUNDRED = Decimal(100)
TROY_OUNCE_GRAMS = Decimal("31.1034768")
PHYSICAL_UNITS = ("g", "kg", "oz")
ETC_UNIT = "parts"
_WEIGHT_GRAMS = {"g": Decimal(1), "kg": Decimal(1000), "oz": TROY_OUNCE_GRAMS}


class CommodityFamily(StrEnum):
    PRECIOUS = "PRECIOUS"  # métaux précieux
    ENERGY = "ENERGY"
    INDUSTRIAL = "INDUSTRIAL"  # métaux industriels
    AGRICULTURAL = "AGRICULTURAL"


class CommodityForm(StrEnum):
    PHYSICAL = "PHYSICAL"  # lingots, pièces, barres
    ETC = "ETC"  # titre coté (ETC, ETF) sur une matière


@dataclass(frozen=True, slots=True)
class PhysicalMetal:
    """Métal précieux courant : symbole et cours spot de référence (USD par once troy)."""

    name: str
    symbol: str
    quote_symbol: str
    family: CommodityFamily


PHYSICAL_METALS = (
    PhysicalMetal("Or", "XAU", "GC=F", CommodityFamily.PRECIOUS),
    PhysicalMetal("Argent", "XAG", "SI=F", CommodityFamily.PRECIOUS),
    PhysicalMetal("Platine", "XPT", "PL=F", CommodityFamily.PRECIOUS),
    PhysicalMetal("Palladium", "XPD", "PA=F", CommodityFamily.PRECIOUS),
)


@dataclass(frozen=True, slots=True)
class CommodityDetails:
    """Ce qui distingue une matière première d'un actif ordinaire."""

    family: CommodityFamily
    form: CommodityForm
    unit: str  # g, kg, oz (physique) ou parts (ETC)
    broker: str | None = None  # ETC : courtier ou compte-titres
    quote_symbol: str | None = None  # ticker du cours ; None = pas de cours automatique
    quote_currency: str = "EUR"
    quote_unit: str | None = (
        None  # unité du cours (oz pour un métal) ; None = celle de la détention
    )

    def __post_init__(self) -> None:
        if self.form is CommodityForm.PHYSICAL and self.unit not in PHYSICAL_UNITS:
            raise ValueError("L'unité d'un métal physique est g, kg ou oz.")
        if self.form is CommodityForm.ETC and self.unit != ETC_UNIT:
            raise ValueError("Un ETC se détient en parts.")
        if not re.fullmatch(r"[A-Z]{3}", self.quote_currency):
            raise ValueError("La devise du cours doit comporter 3 lettres (ex. USD).")
        if self.quote_unit is not None and (
            self.quote_unit not in _WEIGHT_GRAMS or self.unit not in _WEIGHT_GRAMS
        ):
            raise ValueError("Une unité de cours n'a de sens que pour un poids (g, kg, oz).")


def convert_quote(
    price: Decimal, *, quote_unit: str | None, holding_unit: str, fx_to_eur: Decimal
) -> Decimal:
    """Cours exprimé dans l'unité de détention et en euros.

    `price` est dans l'unité `quote_unit` et dans la devise du cours ; `fx_to_eur` convertit
    cette devise en euros (1 si elle y est déjà).
    """
    if fx_to_eur <= ZERO or price <= ZERO:
        raise ValueError("Le cours et le taux de change doivent être strictement positifs.")
    if quote_unit is None or quote_unit == holding_unit:
        return price * fx_to_eur
    if quote_unit not in _WEIGHT_GRAMS or holding_unit not in _WEIGHT_GRAMS:
        raise ValueError(f"Conversion impossible de {quote_unit} vers {holding_unit}.")
    return price / _WEIGHT_GRAMS[quote_unit] * _WEIGHT_GRAMS[holding_unit] * fx_to_eur


def validate_trade(quantity: Decimal, unit_price: Decimal, fees: Decimal) -> None:
    if quantity <= ZERO:
        raise ValueError("La quantité doit être strictement positive.")
    if unit_price <= ZERO:
        raise ValueError("Le prix doit être strictement positif.")
    if fees < ZERO:
        raise ValueError("Les frais ne peuvent pas être négatifs.")


@dataclass(frozen=True, slots=True)
class BuyPreview:
    cost: Decimal  # quantité × prix + frais
    quantity_after: Decimal
    pru_after: Decimal  # coût moyen pondéré, frais inclus


@dataclass(frozen=True, slots=True)
class SellPreview:
    proceeds: Decimal  # quantité × prix − frais
    realized: Decimal  # plus-value réalisée : le PRU reste inchangé
    quantity_after: Decimal


def preview_buy(
    position: Position, quantity: Decimal, unit_price: Decimal, fees: Decimal
) -> BuyPreview:
    validate_trade(quantity, unit_price, fees)
    cost = quantity * unit_price + fees
    quantity_after = position.quantity + quantity
    held_cost = position.quantity * position.pru
    return BuyPreview(cost, quantity_after, (held_cost + cost) / quantity_after)


def preview_sell(
    position: Position, quantity: Decimal, unit_price: Decimal, fees: Decimal
) -> SellPreview:
    validate_trade(quantity, unit_price, fees)
    if quantity > position.quantity:
        raise ValueError("Vente supérieure à la quantité détenue.")
    proceeds = quantity * unit_price - fees
    return SellPreview(proceeds, proceeds - quantity * position.pru, position.quantity - quantity)


@dataclass(frozen=True, slots=True)
class Valuation:
    value: Decimal  # quantité × cours
    cost: Decimal  # quantité × PRU
    unrealized: Decimal
    unrealized_percent: Decimal | None  # None si le coût est nul


def valuation(position: Position, price: Decimal) -> Valuation:
    value = position.quantity * price
    cost = position.quantity * position.pru
    gain = value - cost
    return Valuation(value, cost, gain, gain / cost * HUNDRED if cost else None)


def family_shares(
    values: Mapping[CommodityFamily, Decimal],
) -> list[tuple[CommodityFamily, Decimal]]:
    """Part de chaque famille (en %) dans la valeur totale, dans l'ordre des familles."""
    total = sum(values.values(), ZERO)
    if total <= ZERO:
        return []
    return [
        (family, values[family] / total * HUNDRED) for family in CommodityFamily if family in values
    ]


def sum_values(values: Iterable[Decimal]) -> Decimal:
    return sum(values, ZERO)
