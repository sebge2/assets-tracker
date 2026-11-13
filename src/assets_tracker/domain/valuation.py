"""Valorisation d'un actif à une date : valeur d'une unité, historisée une fois par jour.

Logique pure, commune à tous les types d'actifs (matières premières, fonds, actions…).
La valeur est celle d'**une unité** de l'actif, en euros ; la valeur d'une position est
`quantité × valorisation`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from assets_tracker.domain.models import InstrumentType

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Valuation:
    asset_type: InstrumentType
    asset_id: int
    valuation_date: date  # le jour que la valeur représente (une seule par actif et par jour)
    value: Decimal  # valeur d'une unité, en euros
    fetched_at: datetime  # instant réel où le fournisseur a été interrogé

    def __post_init__(self) -> None:
        if self.value <= ZERO:
            raise ValueError("Une valorisation doit être strictement positive.")


def is_stale(valuation: Valuation, today: date) -> bool:
    """Une valorisation d'un jour passé est un dernier cours connu, pas un cours du jour."""
    return valuation.valuation_date < today


def needs_refresh(last_valued_on: date | None, today: date, *, force: bool = False) -> bool:
    """Faut-il interroger le fournisseur ? Une seule fois par jour, sauf demande explicite."""
    return force or last_valued_on is None or last_valued_on < today


def assets_to_refresh(
    asset_ids: Iterable[int], valued_today: set[int], *, force: bool = False
) -> list[int]:
    """Actifs à interroger : ceux qui n'ont pas encore de valorisation datée du jour."""
    return [asset_id for asset_id in asset_ids if force or asset_id not in valued_today]
