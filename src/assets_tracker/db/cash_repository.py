"""Persistance des comptes cash (taux, primes, mouvements) dans la base chiffrée."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from assets_tracker.domain.cash import (
    BaseRateChange,
    BonusDefinition,
    CashAccount,
    CashMovement,
    InterestFrequency,
    MovementKind,
    check_movement_allowed,
    statement,
)


class CashRepository:
    """Accès aux tables `cash_*`. Les montants et taux sont stockés en TEXT (Decimal)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # --- Comptes ------------------------------------------------------------

    def create_account(
        self,
        account: CashAccount,
        base_rate: Decimal,
        bonus: tuple[Decimal, int] | None = None,
    ) -> CashAccount:
        """Crée le compte avec son premier taux (et sa prime éventuelle) à la date d'ouverture."""
        first_rate = BaseRateChange(account.opened_on, base_rate)
        first_bonus = BonusDefinition(account.opened_on, *bonus) if bonus else None
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO cash_accounts (name, bank, iban, currency, opened_on, frequency,"
                " payout_month, payout_day) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account.name.strip(),
                    account.bank.strip(),
                    account.iban,
                    account.currency,
                    account.opened_on.isoformat(),
                    account.frequency.value,
                    account.payout_month,
                    account.payout_day,
                ),
            )
            account_id = int(cursor.lastrowid)
            self._insert_rate(account_id, first_rate)
            if first_bonus is not None:
                self._insert_bonus(account_id, first_bonus)
        created = self.get_account(account_id)
        assert created is not None
        return created

    def update_account(
        self,
        account: CashAccount,
        *,
        base_rate: Decimal,
        bonus: tuple[Decimal, int] | None,
        effective_date: date,
    ) -> None:
        """Met à jour le compte ; un taux ou une prime modifiés ajoutent une ligne d'historique.

        L'ancien taux est toujours conservé : l'historique n'est jamais réécrit.
        """
        if account.id is None:
            raise ValueError("Le compte n'a pas encore été enregistré.")
        new_rate = BaseRateChange(effective_date, base_rate)
        new_bonus = BonusDefinition(
            effective_date, bonus[0] if bonus else Decimal(0), bonus[1] if bonus else 0
        )
        with self._conn:
            self._conn.execute(
                "UPDATE cash_accounts SET name = ?, bank = ?, iban = ?, frequency = ?,"
                " payout_month = ?, payout_day = ? WHERE id = ?",
                (
                    account.name.strip(),
                    account.bank.strip(),
                    account.iban,
                    account.frequency.value,
                    account.payout_month,
                    account.payout_day,
                    account.id,
                ),
            )
            rates = self.base_rates(account.id)
            bonuses = self.bonuses(account.id)
            current = bonuses[-1] if bonuses else None
            current_pair = (current.rate, current.after_months) if current else (Decimal(0), 0)
            bonus_changed = current_pair != (new_bonus.rate, new_bonus.after_months)
            rate_changed = not rates or rates[-1].rate != base_rate
            if rate_changed:
                self._insert_rate(account.id, new_rate)
            elif not bonus_changed and rates[-1].effective_date != effective_date:
                # Seule la date change : on corrige la date d'effet courante au lieu d'ajouter
                # une ligne identique à l'historique.
                self._move_latest(account.id, "cash_account_rates", effective_date)
                if current is not None and current.effective_date != effective_date:
                    self._move_latest(account.id, "cash_account_bonuses", effective_date)
            if bonus_changed and (current is not None or new_bonus.rate > 0):
                self._insert_bonus(account.id, new_bonus)

    def _move_latest(self, account_id: int, table: str, effective_date: date) -> None:
        self._conn.execute(
            f"UPDATE {table} SET effective_date = ? WHERE id = ("
            f"SELECT id FROM {table} WHERE account_id = ?"
            " ORDER BY effective_date DESC, id DESC LIMIT 1)",
            (effective_date.isoformat(), account_id),
        )

    def delete_account(self, account_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM cash_accounts WHERE id = ?", (account_id,))

    def get_account(self, account_id: int) -> CashAccount | None:
        row = self._conn.execute(
            "SELECT * FROM cash_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return None if row is None else _account(row)

    def list_accounts(self) -> list[CashAccount]:
        rows = self._conn.execute("SELECT * FROM cash_accounts ORDER BY name, id").fetchall()
        return [_account(row) for row in rows]

    def has_movements(self, account_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM cash_movements WHERE account_id = ? LIMIT 1", (account_id,)
        ).fetchone()
        return row is not None

    # --- Taux et primes -----------------------------------------------------

    def base_rates(self, account_id: int) -> list[BaseRateChange]:
        rows = self._conn.execute(
            "SELECT effective_date, rate FROM cash_account_rates WHERE account_id = ?"
            " ORDER BY effective_date, id",
            (account_id,),
        ).fetchall()
        return [
            BaseRateChange(date.fromisoformat(r["effective_date"]), Decimal(r["rate"]))
            for r in rows
        ]

    def bonuses(self, account_id: int) -> list[BonusDefinition]:
        rows = self._conn.execute(
            "SELECT effective_date, rate, after_months FROM cash_account_bonuses"
            " WHERE account_id = ? ORDER BY effective_date, id",
            (account_id,),
        ).fetchall()
        return [
            BonusDefinition(
                date.fromisoformat(r["effective_date"]), Decimal(r["rate"]), int(r["after_months"])
            )
            for r in rows
        ]

    def _insert_rate(self, account_id: int, change: BaseRateChange) -> None:
        self._conn.execute(
            "INSERT INTO cash_account_rates (account_id, effective_date, rate) VALUES (?, ?, ?)",
            (account_id, change.effective_date.isoformat(), str(change.rate)),
        )

    def _insert_bonus(self, account_id: int, bonus: BonusDefinition) -> None:
        self._conn.execute(
            "INSERT INTO cash_account_bonuses (account_id, effective_date, rate, after_months)"
            " VALUES (?, ?, ?, ?)",
            (account_id, bonus.effective_date.isoformat(), str(bonus.rate), bonus.after_months),
        )

    # --- Mouvements ---------------------------------------------------------

    def movements(self, account_id: int) -> list[CashMovement]:
        rows = self._conn.execute(
            "SELECT id, account_id, kind, movement_date, amount FROM cash_movements"
            " WHERE account_id = ? ORDER BY movement_date, id",
            (account_id,),
        ).fetchall()
        return [
            CashMovement(
                int(r["id"]),
                int(r["account_id"]),
                MovementKind(r["kind"]),
                date.fromisoformat(r["movement_date"]),
                Decimal(r["amount"]),
            )
            for r in rows
        ]

    def add_movement(
        self, account_id: int, kind: MovementKind, movement_date: date, amount: Decimal
    ) -> CashMovement:
        """Ajoute un mouvement ; refuse tout mouvement qui rendrait le solde négatif."""
        candidate = CashMovement(None, account_id, kind, movement_date, amount)
        check_movement_allowed(self.movements(account_id), candidate)
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO cash_movements (account_id, kind, movement_date, amount)"
                " VALUES (?, ?, ?, ?)",
                (account_id, kind.value, movement_date.isoformat(), str(amount)),
            )
        return CashMovement(int(cursor.lastrowid), account_id, kind, movement_date, amount)

    def update_movement(
        self,
        account_id: int,
        movement_id: int,
        kind: MovementKind,
        movement_date: date,
        amount: Decimal,
    ) -> CashMovement:
        """Modifie un mouvement ; refuse toute modification qui rendrait le solde négatif."""
        movements = self.movements(account_id)
        if not any(move.id == movement_id for move in movements):
            raise ValueError("Le mouvement à modifier est introuvable.")
        candidate = CashMovement(movement_id, account_id, kind, movement_date, amount)
        others = [move for move in movements if move.id != movement_id]
        check_movement_allowed(others, candidate)
        with self._conn:
            self._conn.execute(
                "UPDATE cash_movements SET kind = ?, movement_date = ?, amount = ?"
                " WHERE id = ? AND account_id = ?",
                (kind.value, movement_date.isoformat(), str(amount), movement_id, account_id),
            )
        return candidate

    def delete_movement(self, account_id: int, movement_id: int) -> None:
        """Supprime un mouvement sans permettre un historique de solde négatif."""
        movements = self.movements(account_id)
        target = next((move for move in movements if move.id == movement_id), None)
        if target is None:
            raise ValueError("Le mouvement à supprimer est introuvable.")
        remaining = [move for move in movements if move.id != movement_id]
        if any(line.balance_after < 0 for line in statement(remaining)):
            raise ValueError(
                "Suppression impossible : un retrait ultérieur rendrait le solde négatif."
            )
        with self._conn:
            self._conn.execute(
                "DELETE FROM cash_movements WHERE id = ? AND account_id = ?",
                (movement_id, account_id),
            )


def _account(row: Any) -> CashAccount:
    return CashAccount(
        id=int(row["id"]),
        name=row["name"],
        bank=row["bank"],
        iban=row["iban"],
        currency=row["currency"],
        opened_on=date.fromisoformat(row["opened_on"]),
        frequency=InterestFrequency(row["frequency"]),
        payout_month=row["payout_month"],
        payout_day=row["payout_day"],
    )
