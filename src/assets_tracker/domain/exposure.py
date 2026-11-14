"""Expositions d'un actif : répartition par secteur et par zone géographique (en %)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from assets_tracker.domain.models import InstrumentType

ZERO = Decimal(0)
HUNDRED = Decimal(100)
UNCLASSIFIED = "Non classé"
NOT_APPLICABLE = "Non applicable"  # cash et matières premières n'ont pas de zone
CASH_SECTOR = "Liquidités"
COMMODITY_SECTOR = "Matières premières"
OTHER = "Autre"


class ExposureKind(StrEnum):
    SECTOR = "SECTOR"
    ZONE = "ZONE"


KIND_LABELS = {ExposureKind.SECTOR: "secteur", ExposureKind.ZONE: "zone"}

SECTOR_SUGGESTIONS = (
    "Technologie",
    "Finance",
    "Santé",
    "Industrie",
    "Énergie",
    "Consommation",
    "Immobilier",
    "Services publics",
    "Matériaux",
    "Communication",
)
ZONE_SUGGESTIONS = (
    "France",
    "Europe",
    "États-Unis",
    "Amérique du Nord",
    "Asie-Pacifique",
    "Japon",
    "Émergents",
    "Monde",
)


@dataclass(frozen=True, slots=True)
class ExposureEntry:
    name: str
    weight: Decimal


def suggestions(kind: ExposureKind) -> tuple[str, ...]:
    return SECTOR_SUGGESTIONS if kind is ExposureKind.SECTOR else ZONE_SUGGESTIONS


def entries_total(entries: Iterable[ExposureEntry]) -> Decimal:
    return sum((e.weight for e in entries), ZERO)


def validate_entries(kind: ExposureKind, entries: Sequence[ExposureEntry]) -> list[ExposureEntry]:
    """Une répartition vide signifie « non renseignée » ; sinon elle totalise exactement 100 %."""
    label = KIND_LABELS[kind]
    cleaned: list[ExposureEntry] = []
    seen: set[str] = set()
    for entry in entries:
        name = entry.name.strip()
        if not name:
            raise ValueError(f"Le nom du {label} est obligatoire.")
        if name.casefold() in seen:
            raise ValueError(f"Le {label} « {name} » est saisi deux fois.")
        seen.add(name.casefold())
        if entry.weight <= ZERO or entry.weight > HUNDRED:
            raise ValueError(f"La part de « {name} » doit être comprise entre 0 et 100 %.")
        cleaned.append(ExposureEntry(name, entry.weight))
    if cleaned and entries_total(cleaned) != HUNDRED:
        raise ValueError(
            f"Les parts par {label} doivent totaliser 100 % (actuellement "
            f"{entries_total(cleaned).normalize():f} %)."
        )
    return cleaned


def main_exposure(entries: Sequence[ExposureEntry]) -> str | None:
    """Nom de la plus grosse part (la première en cas d'égalité), None si non renseignée."""
    if not entries:
        return None
    return max(entries, key=lambda e: e.weight).name


def exposure_breakdown(
    items: Iterable[tuple[Decimal, Sequence[ExposureEntry]]],
) -> list[tuple[str, Decimal, Decimal]]:
    """Valeur (EUR) et part de chaque secteur/zone, pondérées par la valeur de chaque actif.

    Un actif sans répartition renseignée tombe dans « Non classé », placé en dernier.
    """
    values: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for value, entries in items:
        if value <= ZERO:
            continue
        parts = [(e.name, value * e.weight / HUNDRED) for e in entries] or [(UNCLASSIFIED, value)]
        for name, part in parts:
            key = name.casefold()
            names.setdefault(key, name)
            values[key] = values.get(key, ZERO) + part
    total = sum(values.values(), ZERO)
    if total == ZERO:
        return []
    unclassified = UNCLASSIFIED.casefold()
    ranked = sorted(values.items(), key=lambda kv: (kv[0] == unclassified, -kv[1], kv[0]))
    return [(names[key], value, value / total * HUNDRED) for key, value in ranked]


# --- Répartition de l'ensemble du portefeuille --------------------------------


@dataclass(frozen=True, slots=True)
class ExposedAsset:
    """Un actif (ou un ensemble : tous les comptes cash) avec sa valeur en EUR."""

    name: str
    instrument: InstrumentType
    value: Decimal
    sectors: tuple[ExposureEntry, ...]
    zones: tuple[ExposureEntry, ...]
    asset_id: int | None = None  # None pour les comptes cash et les matières premières


@dataclass(frozen=True, slots=True)
class Contribution:
    asset: ExposedAsset
    weight: Decimal  # part de l'actif classée dans cette catégorie (100 si non renseigné)
    amount: Decimal  # en EUR
    missing: bool  # l'actif n'a aucune répartition de ce type


@dataclass(frozen=True, slots=True)
class Category:
    name: str
    value: Decimal
    percent: Decimal  # part du périmètre
    by_type: dict[InstrumentType, Decimal]
    contributions: list[Contribution]


@dataclass(frozen=True, slots=True)
class ExposureReport:
    categories: list[Category]
    total: Decimal  # somme des catégories, « Non applicable » compris
    coverage: Decimal | None  # % de la valeur renseignée ; None sans valeur à classer
    missing: list[ExposedAsset]  # sans répartition de ce type, du plus gros au plus petit


def _entries_for(asset: ExposedAsset, kind: ExposureKind) -> tuple[ExposureEntry, ...]:
    if kind is ExposureKind.SECTOR:
        if asset.instrument is InstrumentType.CASH:
            return (ExposureEntry(CASH_SECTOR, HUNDRED),)
        if asset.instrument is InstrumentType.COMMODITY:
            return (ExposureEntry(COMMODITY_SECTOR, HUNDRED),)
        return asset.sectors
    if asset.instrument in (InstrumentType.CASH, InstrumentType.COMMODITY):
        return (ExposureEntry(NOT_APPLICABLE, HUNDRED),)
    return asset.zones


def exposure_report(
    assets: Iterable[ExposedAsset],
    kind: ExposureKind,
    instrument: InstrumentType | None = None,
) -> ExposureReport:
    """Répartition par secteur ou zone d'un périmètre (tout, ou un seul type d'actif).

    « Non classé » (actifs sans répartition) puis « Non applicable » passent en dernier ; seuls
    les actifs classables comptent dans la couverture.
    """
    values: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    types: dict[str, dict[InstrumentType, Decimal]] = {}
    contributions: dict[str, list[Contribution]] = {}
    missing: list[ExposedAsset] = []
    classifiable = classified = ZERO
    for asset in assets:
        if asset.value <= ZERO or (instrument is not None and asset.instrument is not instrument):
            continue
        entries = _entries_for(asset, kind)
        absent = not entries
        parts = entries or (ExposureEntry(UNCLASSIFIED, HUNDRED),)
        if absent:
            missing.append(asset)
        if parts[0].name != NOT_APPLICABLE:
            classifiable += asset.value
            if not absent:
                classified += asset.value
        for entry in parts:
            key = entry.name.casefold()
            names.setdefault(key, entry.name)
            amount = asset.value * entry.weight / HUNDRED
            values[key] = values.get(key, ZERO) + amount
            by_type = types.setdefault(key, {})
            by_type[asset.instrument] = by_type.get(asset.instrument, ZERO) + amount
            contributions.setdefault(key, []).append(
                Contribution(asset, entry.weight, amount, absent)
            )
    total = sum(values.values(), ZERO)
    weights = {UNCLASSIFIED.casefold(): 1, NOT_APPLICABLE.casefold(): 2}
    ranked = sorted(values.items(), key=lambda kv: (weights.get(kv[0], 0), -kv[1], kv[0]))
    categories = [
        Category(
            names[key],
            value,
            value / total * HUNDRED,
            types[key],
            sorted(contributions[key], key=lambda c: (-c.amount, c.asset.name)),
        )
        for key, value in ranked
    ]
    coverage = classified / classifiable * HUNDRED if classifiable else None
    return ExposureReport(
        categories, total, coverage, sorted(missing, key=lambda a: (-a.value, a.name))
    )
