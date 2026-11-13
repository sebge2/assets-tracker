"""Valorisations quotidiennes : cache et historique, pour tous les types d'actifs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from assets_tracker.domain.models import InstrumentType
from assets_tracker.domain.valuation import Valuation


class ValuationRepository:
    """Une ligne par (type d'actif, actif, jour) : réécrire le même jour met la ligne à jour.

    Les jours passés ne sont jamais modifiés : l'historique est figé avec les valeurs connues.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def record(
        self,
        asset_type: InstrumentType,
        asset_id: int,
        valuation_date: date,
        value: Decimal,
        fetched_at: datetime,
    ) -> Valuation:
        """Enregistre la valeur du jour ; une valeur déjà présente pour ce jour est remplacée."""
        valuation = Valuation(asset_type, asset_id, valuation_date, value, fetched_at)
        with self._conn:
            self._conn.execute(
                "INSERT INTO valuations (asset_type, asset_id, valuation_date, value, fetched_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(asset_type, asset_id, valuation_date) DO UPDATE SET "
                "value = excluded.value, fetched_at = excluded.fetched_at",
                (
                    asset_type.value,
                    asset_id,
                    valuation_date.isoformat(),
                    str(value),
                    fetched_at.isoformat(timespec="seconds"),
                ),
            )
        return valuation

    def latest(self, asset_type: InstrumentType, asset_id: int) -> Valuation | None:
        row = self._conn.execute(
            "SELECT * FROM valuations WHERE asset_type = ? AND asset_id = ? "
            "ORDER BY valuation_date DESC LIMIT 1",
            (asset_type.value, asset_id),
        ).fetchone()
        return None if row is None else _valuation(row)

    def on_day(self, asset_type: InstrumentType, asset_id: int, day: date) -> Valuation | None:
        row = self._conn.execute(
            "SELECT * FROM valuations WHERE asset_type = ? AND asset_id = ? AND valuation_date = ?",
            (asset_type.value, asset_id, day.isoformat()),
        ).fetchone()
        return None if row is None else _valuation(row)

    def history(self, asset_type: InstrumentType, asset_id: int) -> list[Valuation]:
        """Toutes les valorisations connues, de la plus ancienne à la plus récente."""
        rows = self._conn.execute(
            "SELECT * FROM valuations WHERE asset_type = ? AND asset_id = ? "
            "ORDER BY valuation_date",
            (asset_type.value, asset_id),
        ).fetchall()
        return [_valuation(row) for row in rows]

    def valued_on(self, asset_type: InstrumentType, day: date) -> set[int]:
        """Actifs de ce type qui ont déjà une valorisation à cette date (donc rien à rappeler)."""
        rows = self._conn.execute(
            "SELECT asset_id FROM valuations WHERE asset_type = ? AND valuation_date = ?",
            (asset_type.value, day.isoformat()),
        ).fetchall()
        return {int(row["asset_id"]) for row in rows}


def _valuation(row: Any) -> Valuation:
    return Valuation(
        InstrumentType(row["asset_type"]),
        int(row["asset_id"]),
        date.fromisoformat(row["valuation_date"]),
        Decimal(row["value"]),
        datetime.fromisoformat(row["fetched_at"]),
    )
