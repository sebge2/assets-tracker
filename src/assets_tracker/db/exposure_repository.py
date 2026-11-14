"""Persistance des secteurs et zones géographiques associés aux actifs."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from assets_tracker.domain.exposure import ExposureEntry, ExposureKind, validate_entries


class ExposureRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def get(self, asset_id: int, kind: ExposureKind) -> list[ExposureEntry]:
        rows = self._conn.execute(
            "SELECT name, weight FROM asset_exposures WHERE asset_id = ? AND kind = ?",
            (asset_id, kind.value),
        ).fetchall()
        entries = [ExposureEntry(r["name"], Decimal(r["weight"])) for r in rows]
        return sorted(entries, key=lambda e: (-e.weight, e.name.casefold()))

    def all_for(
        self, asset_ids: Iterable[int], kind: ExposureKind
    ) -> dict[int, list[ExposureEntry]]:
        return {asset_id: self.get(asset_id, kind) for asset_id in asset_ids}

    def set(self, asset_id: int, kind: ExposureKind, entries: Sequence[ExposureEntry]) -> None:
        """Remplace la répartition (atomique). Une liste vide efface la répartition."""
        cleaned = validate_entries(kind, entries)
        if self._conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
            raise ValueError("Actif introuvable.")
        with self._conn:
            self._conn.execute(
                "DELETE FROM asset_exposures WHERE asset_id = ? AND kind = ?",
                (asset_id, kind.value),
            )
            self._conn.executemany(
                "INSERT INTO asset_exposures (asset_id, kind, name, weight) VALUES (?, ?, ?, ?)",
                [(asset_id, kind.value, e.name, str(e.weight)) for e in cleaned],
            )

    def replace(
        self,
        asset_id: int,
        sectors: Sequence[ExposureEntry],
        zones: Sequence[ExposureEntry],
    ) -> None:
        """Remplace secteurs et zones ensemble : tout est validé avant la moindre écriture."""
        by_kind = {
            ExposureKind.SECTOR: validate_entries(ExposureKind.SECTOR, sectors),
            ExposureKind.ZONE: validate_entries(ExposureKind.ZONE, zones),
        }
        if self._conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
            raise ValueError("Actif introuvable.")
        with self._conn:
            self._conn.execute("DELETE FROM asset_exposures WHERE asset_id = ?", (asset_id,))
            self._conn.executemany(
                "INSERT INTO asset_exposures (asset_id, kind, name, weight) VALUES (?, ?, ?, ?)",
                [
                    (asset_id, kind.value, e.name, str(e.weight))
                    for kind, entries in by_kind.items()
                    for e in entries
                ],
            )
