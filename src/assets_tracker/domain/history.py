"""Historique des cotations d'un instrument : périodes d'affichage et statistiques.

Logique pure (ni Qt, ni SQLite, ni réseau).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

Point = tuple[date, Decimal]

# (libellé, nombre de jours ; None = tout l'historique)
PERIODS: tuple[tuple[str, int | None], ...] = (
    ("1 mois", 30),
    ("3 mois", 91),
    ("6 mois", 182),
    ("1 an", 365),
    ("5 ans", 1826),
    ("Tout", None),
)


@dataclass(frozen=True, slots=True)
class PeriodStats:
    first: Point
    last: Point
    low: Point
    high: Point
    count: int

    @property
    def change(self) -> Decimal:
        return self.last[1] - self.first[1]

    @property
    def change_pct(self) -> Decimal | None:
        """Variation en % du premier au dernier cours ; None si le premier cours est nul."""
        if self.first[1] == 0:
            return None
        return self.change / self.first[1] * 100


def daily_points(points: Iterable[Point]) -> list[Point]:
    """Un cours par jour (le dernier saisi), du plus ancien au plus récent."""
    by_day: dict[date, Decimal] = {}
    for when, value in points:
        by_day[when] = value
    return sorted(by_day.items())


def in_period(points: Iterable[Point], today: date, days: int | None) -> list[Point]:
    """Cours des `days` derniers jours jusqu'à `today` (tout l'historique si `days` est None)."""
    ordered = daily_points(points)
    if days is None:
        return ordered
    start = today - timedelta(days=days)
    return [p for p in ordered if start <= p[0] <= today]


def in_range(points: Iterable[Point], start: date, end: date) -> list[Point]:
    """Cours entre `start` et `end` inclus (vide si `start` est après `end`)."""
    return [p for p in daily_points(points) if start <= p[0] <= end]


def period_stats(points: Iterable[Point]) -> PeriodStats | None:
    """Premier, dernier, plus bas et plus haut cours ; None s'il n'y a aucun cours."""
    ordered = daily_points(points)
    if not ordered:
        return None
    return PeriodStats(
        first=ordered[0],
        last=ordered[-1],
        low=min(ordered, key=lambda p: p[1]),
        high=max(ordered, key=lambda p: p[1]),
        count=len(ordered),
    )
