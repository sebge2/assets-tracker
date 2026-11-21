"""Persistance des assurances & pensions : contrats, versements et relevés (valeurs projetées)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from assets_tracker.domain.insurance import (
    ContractDetails,
    ContractKind,
    Payment,
    Statement,
    validate_details,
    validate_payment,
    validate_statement,
)


@dataclass(frozen=True, slots=True)
class ContractHolding:
    id: int
    details: ContractDetails
    payments: tuple[Payment, ...]  # du plus ancien au plus récent
    statements: tuple[Statement, ...]  # du plus ancien au plus récent


def _sort_key(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


class InsuranceRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def list_contracts(self) -> list[ContractHolding]:
        rows = self._conn.execute("SELECT * FROM insurance_contracts ORDER BY id").fetchall()
        # Tri sans casse ni accents en Python : COLLATE NOCASE de SQLite ne replie que l'ASCII,
        # « Épargne » passerait après « Pension ».
        rows = sorted(rows, key=lambda row: (_sort_key(row["name"]), row["id"]))
        return [self._holding(row) for row in rows]

    def get(self, contract_id: int) -> ContractHolding | None:
        row = self._conn.execute(
            "SELECT * FROM insurance_contracts WHERE id = ?", (contract_id,)
        ).fetchone()
        return None if row is None else self._holding(row)

    def create(self, details: ContractDetails) -> ContractHolding:
        validate_details(details)
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO insurance_contracts "
                "(kind, name, insurer, contract_number, opened_on, retirement_on, ceiling) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                self._params(details),
            )
        holding = self.get(int(cursor.lastrowid))
        assert holding is not None
        return holding

    def update(self, contract_id: int, details: ContractDetails) -> ContractHolding:
        if self.get(contract_id) is None:
            raise ValueError("Contrat introuvable.")
        validate_details(details)
        with self._conn:
            self._conn.execute(
                "UPDATE insurance_contracts SET kind = ?, name = ?, insurer = ?, "
                "contract_number = ?, opened_on = ?, retirement_on = ?, ceiling = ? WHERE id = ?",
                (*self._params(details), contract_id),
            )
        holding = self.get(contract_id)
        assert holding is not None
        return holding

    def delete(self, contract_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM insurance_contracts WHERE id = ?", (contract_id,))

    # -- versements --

    def add_payment(self, contract_id: int, paid_on: date, amount: Decimal) -> ContractHolding:
        holding = self._require(contract_id)
        validate_payment(holding.details, paid_on, amount)
        with self._conn:
            self._conn.execute(
                "INSERT INTO insurance_payments (contract_id, paid_on, amount) VALUES (?, ?, ?)",
                (contract_id, paid_on.isoformat(), str(amount)),
            )
        return self._require(contract_id)

    def delete_payment(self, payment_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM insurance_payments WHERE id = ?", (payment_id,))

    # -- relevés et valeurs projetées --

    def save_statement(
        self,
        contract_id: int,
        recorded_on: date,
        current_value: Decimal,
        projected_value: Decimal | None,
    ) -> ContractHolding:
        """Ajoute un relevé ; un relevé existant à la même date est remplacé."""
        holding = self._require(contract_id)
        validate_statement(holding.details, recorded_on, current_value, projected_value)
        with self._conn:
            self._conn.execute(
                "INSERT INTO insurance_statements "
                "(contract_id, recorded_on, current_value, projected_value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (contract_id, recorded_on) DO UPDATE SET "
                "current_value = excluded.current_value, "
                "projected_value = excluded.projected_value",
                (
                    contract_id,
                    recorded_on.isoformat(),
                    str(current_value),
                    None if projected_value is None else str(projected_value),
                ),
            )
        return self._require(contract_id)

    def delete_statement(self, statement_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM insurance_statements WHERE id = ?", (statement_id,))

    # -- interne --

    def _require(self, contract_id: int) -> ContractHolding:
        holding = self.get(contract_id)
        if holding is None:
            raise ValueError("Contrat introuvable.")
        return holding

    @staticmethod
    def _params(details: ContractDetails) -> tuple[Any, ...]:
        return (
            details.kind.value,
            details.name.strip(),
            details.insurer.strip(),
            details.number.strip() or None,
            details.opened_on.isoformat(),
            None if details.retirement_on is None else details.retirement_on.isoformat(),
            None if details.ceiling is None else str(details.ceiling),
        )

    def _holding(self, row: Any) -> ContractHolding:
        contract_id = int(row["id"])
        payments = self._conn.execute(
            "SELECT id, paid_on, amount FROM insurance_payments WHERE contract_id = ? "
            "ORDER BY paid_on, id",
            (contract_id,),
        ).fetchall()
        statements = self._conn.execute(
            "SELECT id, recorded_on, current_value, projected_value FROM insurance_statements "
            "WHERE contract_id = ? ORDER BY recorded_on, id",
            (contract_id,),
        ).fetchall()
        return ContractHolding(
            contract_id,
            ContractDetails(
                ContractKind(row["kind"]),
                row["name"],
                row["insurer"],
                date.fromisoformat(row["opened_on"]),
                row["contract_number"] or "",
                None if row["retirement_on"] is None else date.fromisoformat(row["retirement_on"]),
                None if row["ceiling"] is None else Decimal(row["ceiling"]),
            ),
            tuple(
                Payment(int(p["id"]), date.fromisoformat(p["paid_on"]), Decimal(p["amount"]))
                for p in payments
            ),
            tuple(
                Statement(
                    int(s["id"]),
                    date.fromisoformat(s["recorded_on"]),
                    Decimal(s["current_value"]),
                    None if s["projected_value"] is None else Decimal(s["projected_value"]),
                )
                for s in statements
            ),
        )
