"""Persistance des matières premières : actifs, détails, transactions et valorisations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from assets_tracker.db.valuation_repository import ValuationRepository
from assets_tracker.domain.commodities import (
    CommodityDetails,
    CommodityFamily,
    CommodityForm,
    validate_trade,
)
from assets_tracker.domain.models import (
    Asset,
    InstrumentType,
    Position,
    Transaction,
    TransactionType,
)
from assets_tracker.domain.positions import compute_position
from assets_tracker.domain.valuation import Valuation


@dataclass(frozen=True, slots=True)
class PriceRecord:
    price: Decimal  # dans l'unité de détention, en euros
    fetched_at: datetime  # instant réel de l'appel au fournisseur
    valuation_date: date  # jour représenté ; antérieur à aujourd'hui = dernier cours connu


@dataclass(frozen=True, slots=True)
class Holding:
    """Une matière première avec tout ce qu'il faut pour la valoriser."""

    asset: Asset
    details: CommodityDetails
    transactions: list[Transaction]
    price: PriceRecord | None  # dernière valorisation
    history: list[PriceRecord]  # une valorisation par jour connu, de la plus ancienne

    @property
    def position(self) -> Position:
        assert self.asset.id is not None
        return compute_position(self.asset.id, self.transactions)


class CommodityRepository:
    """Accès aux tables `assets`, `commodity_details`, `transactions` et `valuations`."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._valuations = ValuationRepository(conn)

    # --- Lecture ------------------------------------------------------------

    def list_holdings(self) -> list[Holding]:
        rows = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN commodity_details d ON d.asset_id = a.id ORDER BY a.name, a.id"
        ).fetchall()
        return [self._holding(row) for row in rows]

    def get_holding(self, asset_id: int) -> Holding | None:
        row = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN commodity_details d ON d.asset_id = a.id WHERE a.id = ?",
            (asset_id,),
        ).fetchone()
        return None if row is None else self._holding(row)

    def _holding(self, row: Any) -> Holding:
        asset_id = int(row["id"])
        asset = Asset(asset_id, row["symbol"], row["name"], row["currency"])
        details = CommodityDetails(
            family=CommodityFamily(row["family"]),
            form=CommodityForm(row["form"]),
            unit=row["unit"],
            broker=row["broker"],
            quote_symbol=row["quote_symbol"],
            quote_currency=row["quote_currency"],
            quote_unit=row["quote_unit"],
        )
        history = self.price_history(asset_id)
        return Holding(
            asset, details, self.transactions(asset_id), history[-1] if history else None, history
        )

    def transactions(self, asset_id: int) -> list[Transaction]:
        rows = self._conn.execute(
            "SELECT id, asset_id, type, trade_date, quantity, unit_price, fees FROM transactions "
            "WHERE asset_id = ? ORDER BY trade_date, id",
            (asset_id,),
        ).fetchall()
        return [
            Transaction(
                int(r["id"]),
                int(r["asset_id"]),
                TransactionType(r["type"]),
                date.fromisoformat(r["trade_date"]),
                Decimal(r["quantity"]),
                Decimal(r["unit_price"]),
                Decimal(r["fees"]),
            )
            for r in rows
        ]

    def price(self, asset_id: int) -> PriceRecord | None:
        """Dernière valorisation connue (celle du jour, sinon la plus récente)."""
        latest = self._valuations.latest(InstrumentType.COMMODITY, asset_id)
        return None if latest is None else _record(latest.value, latest)

    def price_history(self, asset_id: int) -> list[PriceRecord]:
        return [
            _record(v.value, v)
            for v in self._valuations.history(InstrumentType.COMMODITY, asset_id)
        ]

    def valued_on(self, day: date) -> set[int]:
        """Matières déjà valorisées ce jour-là : inutile de rappeler le fournisseur."""
        return self._valuations.valued_on(InstrumentType.COMMODITY, day)

    # --- Positions ----------------------------------------------------------

    def create_position(
        self,
        name: str,
        symbol: str,
        details: CommodityDetails,
        first: Transaction,
    ) -> Holding:
        """Crée la matière avec son premier achat, en une seule transaction SQL."""
        name, symbol = name.strip(), symbol.strip()
        self._check_identity(name, symbol)
        if first.type is not TransactionType.BUY:
            raise ValueError("Une position commence par un achat.")
        validate_trade(first.quantity, first.unit_price, first.fees)
        with self._conn:
            if self._symbol_taken(symbol, None):
                raise ValueError(f"Une matière porte déjà le symbole « {symbol} ».")
            cursor = self._conn.execute(
                "INSERT INTO assets (symbol, name, currency) VALUES (?, ?, 'EUR')", (symbol, name)
            )
            asset_id = int(cursor.lastrowid)
            self._insert_details(asset_id, details)
            self._insert_transaction(asset_id, first)
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def update_position(
        self, asset_id: int, name: str, symbol: str, details: CommodityDetails
    ) -> None:
        name, symbol = name.strip(), symbol.strip()
        self._check_identity(name, symbol)
        with self._conn:
            if self._symbol_taken(symbol, asset_id):
                raise ValueError(f"Une matière porte déjà le symbole « {symbol} ».")
            self._conn.execute(
                "UPDATE assets SET name = ?, symbol = ? WHERE id = ?", (name, symbol, asset_id)
            )
            self._conn.execute(
                "UPDATE commodity_details SET family = ?, form = ?, unit = ?, "
                "broker = ?, quote_symbol = ?, quote_currency = ?, quote_unit = ? "
                "WHERE asset_id = ?",
                (*self._details_values(details), asset_id),
            )

    def delete_position(self, asset_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))

    @staticmethod
    def _check_identity(name: str, symbol: str) -> None:
        if not name:
            raise ValueError("Le nom est obligatoire.")
        if not symbol:
            raise ValueError("Le symbole est obligatoire.")

    def _symbol_taken(self, symbol: str, own_id: int | None) -> bool:
        row = self._conn.execute(
            "SELECT id FROM assets WHERE symbol = ? AND id IS NOT ?", (symbol, own_id)
        ).fetchone()
        return row is not None

    @staticmethod
    def _details_values(details: CommodityDetails) -> tuple[Any, ...]:
        return (
            details.family.value,
            details.form.value,
            details.unit,
            details.broker,
            details.quote_symbol,
            details.quote_currency,
            details.quote_unit,
        )

    def _insert_details(self, asset_id: int, details: CommodityDetails) -> None:
        self._conn.execute(
            "INSERT INTO commodity_details (asset_id, family, form, unit, broker, "
            "quote_symbol, quote_currency, quote_unit) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, *self._details_values(details)),
        )

    # --- Transactions -------------------------------------------------------

    def add_transaction(
        self,
        asset_id: int,
        kind: TransactionType,
        trade_date: date,
        quantity: Decimal,
        unit_price: Decimal,
        fees: Decimal,
    ) -> Transaction:
        """Ajoute un achat ou une vente ; refuse une vente qui dépasserait la quantité détenue."""
        self.check_transaction(asset_id, kind, trade_date, quantity, unit_price, fees)
        candidate = Transaction(None, asset_id, kind, trade_date, quantity, unit_price, fees)
        with self._conn:
            tx_id = self._insert_transaction(asset_id, candidate)
        return Transaction(tx_id, asset_id, kind, trade_date, quantity, unit_price, fees)

    def check_transaction(
        self,
        asset_id: int,
        kind: TransactionType,
        trade_date: date,
        quantity: Decimal,
        unit_price: Decimal,
        fees: Decimal,
        replacing: int | None = None,
    ) -> None:
        """Essai à blanc : lève `ValueError` si l'opération rendrait l'historique incohérent."""
        validate_trade(quantity, unit_price, fees)
        candidate = Transaction(replacing, asset_id, kind, trade_date, quantity, unit_price, fees)
        history = [t for t in self.transactions(asset_id) if t.id != replacing]
        self._replay([*history, candidate], asset_id)

    def update_transaction(
        self,
        transaction_id: int,
        kind: TransactionType,
        trade_date: date,
        quantity: Decimal,
        unit_price: Decimal,
        fees: Decimal,
    ) -> None:
        current = self._transaction(transaction_id)
        self.check_transaction(
            current.asset_id, kind, trade_date, quantity, unit_price, fees, replacing=transaction_id
        )
        with self._conn:
            self._conn.execute(
                "UPDATE transactions SET type = ?, trade_date = ?, quantity = ?, unit_price = ?, "
                "fees = ? WHERE id = ?",
                (
                    kind.value,
                    trade_date.isoformat(),
                    str(quantity),
                    str(unit_price),
                    str(fees),
                    transaction_id,
                ),
            )

    def delete_transaction(self, transaction_id: int) -> None:
        current = self._transaction(transaction_id)
        history = [t for t in self.transactions(current.asset_id) if t.id != transaction_id]
        if not history:
            raise ValueError(
                "Une position garde au moins une transaction : supprimez la position à la place."
            )
        self._replay(history, current.asset_id)
        with self._conn:
            self._conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

    def _transaction(self, transaction_id: int) -> Transaction:
        row = self._conn.execute(
            "SELECT asset_id FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Cette transaction n'existe plus.")
        for tx in self.transactions(int(row["asset_id"])):
            if tx.id == transaction_id:
                return tx
        raise ValueError("Cette transaction n'existe plus.")

    @staticmethod
    def _replay(history: list[Transaction], asset_id: int) -> None:
        try:
            compute_position(asset_id, history)
        except ValueError:
            raise ValueError(
                "Cette opération rendrait la quantité détenue négative à une date donnée."
            ) from None

    def _insert_transaction(self, asset_id: int, tx: Transaction) -> int:
        cursor = self._conn.execute(
            "INSERT INTO transactions (asset_id, type, trade_date, quantity, unit_price, fees) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                tx.type.value,
                tx.trade_date.isoformat(),
                str(tx.quantity),
                str(tx.unit_price),
                str(tx.fees),
            ),
        )
        return int(cursor.lastrowid)

    # --- Cours --------------------------------------------------------------

    def record_price(
        self, asset_id: int, price: Decimal, valuation_date: date, fetched_at: datetime
    ) -> None:
        """Historise la valorisation du jour ; elle sert de cache et de dernier cours hors ligne."""
        self._valuations.record(
            InstrumentType.COMMODITY, asset_id, valuation_date, price, fetched_at
        )

    def set_price(self, asset_id: int, price: Decimal, fetched_at: datetime) -> None:
        """Comme `record_price`, pour le jour de `fetched_at`."""
        self.record_price(asset_id, price, fetched_at.date(), fetched_at)


def _record(value: Decimal, valuation: Valuation) -> PriceRecord:
    return PriceRecord(value, valuation.fetched_at, valuation.valuation_date)
