"""Import des secteurs et zones depuis les données Yahoo : traduction et normalisation à 100 %.

Logique pure (ni Qt, ni SQLite, ni réseau). Les données brutes viennent de
`market/exposure.py` ; ici on les traduit en libellés français, on regroupe les parts
minuscules et on s'assure que chaque répartition totalise exactement 100 %.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from assets_tracker.domain.exposure import ExposureEntry

HUNDRED = Decimal(100)
TENTH = Decimal("0.1")
MIN_SHARE = Decimal("1")  # en dessous, la part rejoint « Autre »
OTHER = "Autre"

# Secteurs Yahoo (libellé d'une action ou clé d'un fonds) → secteur français.
SECTORS = {
    "technology": "Technologie",
    "financial services": "Finance",
    "financial_services": "Finance",
    "healthcare": "Santé",
    "industrials": "Industrie",
    "energy": "Énergie",
    "consumer cyclical": "Consommation",
    "consumer_cyclical": "Consommation",
    "consumer defensive": "Consommation",
    "consumer_defensive": "Consommation",
    "real estate": "Immobilier",
    "realestate": "Immobilier",
    "utilities": "Services publics",
    "basic materials": "Matériaux",
    "basic_materials": "Matériaux",
    "communication services": "Communication",
    "communication_services": "Communication",
}

FRANCE = "France"
EUROPE = "Europe"
USA = "États-Unis"
NORTH_AMERICA = "Amérique du Nord"
JAPAN = "Japon"
ASIA_PACIFIC = "Asie-Pacifique"
EMERGING = "Émergents"

COUNTRIES = {
    "france": FRANCE,
    "united states": USA,
    "usa": USA,
    "canada": NORTH_AMERICA,
    "japan": JAPAN,
    **dict.fromkeys(
        (
            "germany", "italy", "spain", "netherlands", "belgium", "luxembourg", "ireland",
            "portugal", "austria", "finland", "greece", "switzerland", "united kingdom",
            "sweden", "norway", "denmark", "poland", "czech republic", "hungary",
        ),
        EUROPE,
    ),  # fmt: skip
    **dict.fromkeys(
        ("australia", "new zealand", "hong kong", "singapore", "south korea", "taiwan"),
        ASIA_PACIFIC,
    ),
    **dict.fromkeys(
        (
            "china", "india", "brazil", "mexico", "south africa", "turkey", "indonesia",
            "thailand", "malaysia", "philippines", "chile", "colombia", "saudi arabia",
            "united arab emirates", "israel",
        ),
        EMERGING,
    ),  # fmt: skip
}


class ExposureError(Exception):
    """Échec d'une source de données (clé refusée, quota, réseau…) : le message est affiché."""


@dataclass(frozen=True, slots=True)
class RawExposure:
    """Ce qu'une source fournit : secteur et pays (action) ou poids sectoriels/pays (fonds)."""

    sector: str | None = None
    country: str | None = None
    sector_weights: Mapping[str, float] | None = None
    country_weights: Mapping[str, float] | None = None  # fonds : poids par pays (anglais)


@dataclass(frozen=True, slots=True)
class ImportedExposure:
    sectors: list[ExposureEntry]
    zones: list[ExposureEntry]

    def __bool__(self) -> bool:
        return bool(self.sectors or self.zones)


def sector_name(label: str) -> str | None:
    """Secteur français d'un libellé Yahoo, None s'il est inconnu."""
    return SECTORS.get(label.strip().lower())


def zone_name(country: str) -> str | None:
    """Zone d'un pays (en anglais, comme Yahoo), None s'il est inconnu."""
    return COUNTRIES.get(country.strip().lower())


def normalize(shares: Mapping[str, Decimal]) -> list[ExposureEntry]:
    """Parts quelconques → répartition à 100 % (une décimale), petites parts dans « Autre ».

    Les parts nulles ou négatives sont ignorées ; la plus grande part absorbe l'arrondi. Liste
    vide s'il n'y a rien à répartir.
    """
    positive = {name: share for name, share in shares.items() if share > 0}
    total = sum(positive.values(), Decimal(0))
    if total <= 0:
        return []
    scaled = {name: share / total * HUNDRED for name, share in positive.items()}
    kept: dict[str, Decimal] = {}
    other = Decimal(0)
    for name, share in scaled.items():
        if share < MIN_SHARE and name != OTHER:
            other += share
        else:
            kept[name] = kept.get(name, Decimal(0)) + share
    if other > 0:
        kept[OTHER] = kept.get(OTHER, Decimal(0)) + other
    rounded = {name: share.quantize(TENTH, rounding=ROUND_HALF_UP) for name, share in kept.items()}
    rounded = {name: share for name, share in rounded.items() if share > 0}
    if not rounded:
        return []
    biggest = max(rounded, key=lambda name: rounded[name])
    rounded[biggest] += HUNDRED - sum(rounded.values(), Decimal(0))
    ranked = sorted(rounded.items(), key=lambda item: (item[0] == OTHER, -item[1], item[0]))
    return [ExposureEntry(name, share) for name, share in ranked]


def translate(raw: RawExposure) -> ImportedExposure:
    """Traduit les données Yahoo (fonds : poids sectoriels ; action : secteur et pays).

    Un secteur ou un pays inconnu de la table est ignoré plutôt que deviné : la répartition
    correspondante reste non renseignée.
    """
    sectors: dict[str, Decimal] = {}
    if raw.sector_weights:
        for label, weight in raw.sector_weights.items():
            name = sector_name(label)
            share = Decimal(str(weight))
            if share > 0:
                key = name or OTHER
                sectors[key] = sectors.get(key, Decimal(0)) + share
    elif raw.sector and (name := sector_name(raw.sector)):
        sectors[name] = Decimal(1)
    zones: dict[str, Decimal] = {}
    if raw.country_weights:
        for label, weight in raw.country_weights.items():
            share = Decimal(str(weight))
            if share > 0:
                key = zone_name(label) or OTHER
                zones[key] = zones.get(key, Decimal(0)) + share
    elif raw.country and (zone := zone_name(raw.country)):
        zones[zone] = Decimal(1)
    return ImportedExposure(normalize(sectors), normalize(zones))


__all__ = [
    "ExposureError",
    "ImportedExposure",
    "RawExposure",
    "normalize",
    "sector_name",
    "translate",
    "zone_name",
]
