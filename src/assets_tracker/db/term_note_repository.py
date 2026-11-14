"""Persistance des bons de caisse : paramètres et remboursement éventuel."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from assets_tracker.domain.models import Asset
from assets_tracker.domain.term_notes import (
    InterestPayout,
    Repayment,
    TermNoteDetails,
    validate_details,
    validate_repayment,
)

CURRENCY = "EUR"


@dataclass(frozen=True, slots=True)
class TermNoteHolding:
    asset: Asset
    details: TermNoteDetails
    repayment: Repayment | None  # None tant que le bon n'est pas remboursé


class TermNoteRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def list_holdings(self) -> list[TermNoteHolding]:
        rows = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN term_note_details d ON d.asset_id = a.id ORDER BY d.end_date, a.name, a.id"
        ).fetchall()
        return [self._holding(row) for row in rows]

    def get_holding(self, asset_id: int) -> TermNoteHolding | None:
        row = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN term_note_details d ON d.asset_id = a.id WHERE a.id = ?",
            (asset_id,),
        ).fetchone()
        return None if row is None else self._holding(row)

    def create(self, name: str, details: TermNoteDetails) -> TermNoteHolding:
        validate_details(name, details)
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO assets (symbol, name, currency) VALUES (?, ?, ?)",
                (f"BDC-{uuid.uuid4().hex[:8].upper()}", name.strip(), CURRENCY),
            )
            asset_id = int(cursor.lastrowid)
            self._conn.execute(
                "INSERT INTO term_note_details "
                "(asset_id, issuer, capital, rate, start_date, end_date, payout) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    asset_id,
                    details.issuer.strip(),
                    str(details.capital),
                    str(details.rate),
                    details.start.isoformat(),
                    details.end.isoformat(),
                    details.payout.value,
                ),
            )
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def update(self, asset_id: int, name: str, details: TermNoteDetails) -> TermNoteHolding:
        current = self.get_holding(asset_id)
        if current is None:
            raise ValueError("Bon de caisse introuvable.")
        validate_details(name, details)
        if current.repayment is not None and current.repayment.paid_on < details.start:
            raise ValueError("La souscription ne peut pas suivre le remboursement.")
        with self._conn:
            self._conn.execute("UPDATE assets SET name = ? WHERE id = ?", (name.strip(), asset_id))
            self._conn.execute(
                "UPDATE term_note_details SET issuer = ?, capital = ?, rate = ?, "
                "start_date = ?, end_date = ?, payout = ? WHERE asset_id = ?",
                (
                    details.issuer.strip(),
                    str(details.capital),
                    str(details.rate),
                    details.start.isoformat(),
                    details.end.isoformat(),
                    details.payout.value,
                    asset_id,
                ),
            )
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def repay(self, asset_id: int, repayment: Repayment) -> TermNoteHolding:
        """Clôture le bon : capital et intérêts réellement reçus."""
        current = self.get_holding(asset_id)
        if current is None:
            raise ValueError("Bon de caisse introuvable.")
        if current.repayment is not None:
            raise ValueError("Ce bon est déjà remboursé.")
        validate_repayment(current.details, repayment)
        with self._conn:
            self._conn.execute(
                "UPDATE term_note_details SET repaid_on = ?, repaid_capital = ?, "
                "repaid_interest = ? WHERE asset_id = ?",
                (
                    repayment.paid_on.isoformat(),
                    str(repayment.capital),
                    str(repayment.interest),
                    asset_id,
                ),
            )
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def cancel_repayment(self, asset_id: int) -> TermNoteHolding:
        """Annule un remboursement saisi par erreur : le bon redevient détenu."""
        if self.get_holding(asset_id) is None:
            raise ValueError("Bon de caisse introuvable.")
        with self._conn:
            self._conn.execute(
                "UPDATE term_note_details SET repaid_on = NULL, repaid_capital = NULL, "
                "repaid_interest = NULL WHERE asset_id = ?",
                (asset_id,),
            )
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def delete(self, asset_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM assets WHERE id = ? "
                "AND EXISTS (SELECT 1 FROM term_note_details WHERE asset_id = ?)",
                (asset_id, asset_id),
            )

    @staticmethod
    def _holding(row: Any) -> TermNoteHolding:
        repayment = None
        if row["repaid_on"] is not None:
            repayment = Repayment(
                date.fromisoformat(row["repaid_on"]),
                Decimal(row["repaid_capital"]),
                Decimal(row["repaid_interest"]),
            )
        return TermNoteHolding(
            Asset(int(row["id"]), row["symbol"], row["name"], row["currency"]),
            TermNoteDetails(
                row["issuer"],
                Decimal(row["capital"]),
                Decimal(row["rate"]),
                date.fromisoformat(row["start_date"]),
                date.fromisoformat(row["end_date"]),
                InterestPayout(row["payout"]),
            ),
            repayment,
        )
