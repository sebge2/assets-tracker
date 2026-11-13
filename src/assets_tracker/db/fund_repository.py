"""Persistance des fonds, de leurs opérations et valorisations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from assets_tracker.db.valuation_repository import ValuationRepository
from assets_tracker.domain.funds import FundDetails, ValuationSource, validate_identifier
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
class FundHolding:
    asset: Asset
    details: FundDetails
    transactions: list[Transaction]
    valuation: Valuation | None

    @property
    def position(self) -> Position:
        assert self.asset.id is not None
        return compute_position(self.asset.id, self.transactions)


class FundRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._valuations = ValuationRepository(conn)

    def list_holdings(self) -> list[FundHolding]:
        rows = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN fund_details d ON d.asset_id = a.id ORDER BY a.name, a.id"
        ).fetchall()
        return [self._holding(row) for row in rows]

    def get_holding(self, asset_id: int) -> FundHolding | None:
        row = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN fund_details d ON d.asset_id = a.id WHERE a.id = ?",
            (asset_id,),
        ).fetchone()
        return None if row is None else self._holding(row)

    def create(
        self, name: str, identifier: str, currency: str, details: FundDetails
    ) -> FundHolding:
        identifier = validate_identifier(identifier)
        if not name.strip():
            raise ValueError("Le nom du fonds est obligatoire.")
        with self._conn:
            if self._conn.execute(
                "SELECT 1 FROM assets WHERE symbol = ?", (identifier,)
            ).fetchone():
                raise ValueError("Cet identifiant est déjà utilisé.")
            cursor = self._conn.execute(
                "INSERT INTO assets (symbol, name, currency) VALUES (?, ?, ?)",
                (identifier, name.strip(), currency.upper()),
            )
            asset_id = int(cursor.lastrowid)
            self._insert_details(asset_id, details)
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def add_transaction(
        self,
        asset_id: int,
        kind: TransactionType,
        when: date,
        quantity: Decimal,
        nav: Decimal,
        fees: Decimal,
    ) -> Transaction:
        candidate = Transaction(None, asset_id, kind, when, quantity, nav, fees)
        history = self.transactions(asset_id)
        compute_position(asset_id, [*history, candidate])
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO transactions (asset_id, type, trade_date, quantity, unit_price, fees) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (asset_id, kind.value, when.isoformat(), str(quantity), str(nav), str(fees)),
            )
        return Transaction(int(cursor.lastrowid), asset_id, kind, when, quantity, nav, fees)

    def record_valuation(self, asset_id: int, when: date, value: Decimal, now: datetime) -> None:
        if value <= 0:
            raise ValueError("La valeur liquidative doit être strictement positive.")
        self._valuations.record(InstrumentType.FUND, asset_id, when, value, now)

    def transactions(self, asset_id: int) -> list[Transaction]:
        rows = self._conn.execute(
            "SELECT * FROM transactions WHERE asset_id = ? ORDER BY trade_date, id", (asset_id,)
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

    def _holding(self, row: Any) -> FundHolding:
        asset_id = int(row["id"])
        return FundHolding(
            Asset(asset_id, row["symbol"], row["name"], row["currency"]),
            FundDetails(
                row["broker"] or "",
                Decimal(row["ter"]) if row["ter"] else None,
                ValuationSource(row["valuation_source"]),
                row["quote_symbol"],
            ),
            self.transactions(asset_id),
            self._valuations.latest(InstrumentType.FUND, asset_id),
        )

    def _insert_details(self, asset_id: int, details: FundDetails) -> None:
        self._conn.execute(
            "INSERT INTO fund_details (asset_id, broker, ter, valuation_source, quote_symbol) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                asset_id,
                details.broker.strip() or None,
                str(details.ter) if details.ter is not None else None,
                details.valuation_source.value,
                details.quote_symbol.strip().upper() if details.quote_symbol else None,
            ),
        )
