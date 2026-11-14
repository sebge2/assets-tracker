"""Persistance des actions, de leurs opérations et de leurs cours."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from assets_tracker.db.valuation_repository import ValuationRepository
from assets_tracker.domain.funds import ValuationSource
from assets_tracker.domain.models import (
    Asset,
    InstrumentType,
    Position,
    Transaction,
    TransactionType,
)
from assets_tracker.domain.positions import compute_position
from assets_tracker.domain.stocks import (
    StockDetails,
    validate_shares,
    validate_ticker,
)
from assets_tracker.domain.valuation import Valuation

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class StockHolding:
    asset: Asset
    details: StockDetails
    transactions: list[Transaction]
    valuation: Valuation | None  # dernier cours : valeur d'UNE action, dans sa devise

    @property
    def position(self) -> Position:
        assert self.asset.id is not None
        return compute_position(self.asset.id, self.transactions)


class StockRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._valuations = ValuationRepository(conn)

    def list_holdings(self) -> list[StockHolding]:
        rows = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN stock_details d ON d.asset_id = a.id ORDER BY a.name, a.id"
        ).fetchall()
        return [self._holding(row) for row in rows]

    def get_holding(self, asset_id: int) -> StockHolding | None:
        row = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN stock_details d ON d.asset_id = a.id WHERE a.id = ?",
            (asset_id,),
        ).fetchone()
        return None if row is None else self._holding(row)

    def create(
        self, name: str, ticker: str, currency: str, details: StockDetails
    ) -> StockHolding:
        ticker = validate_ticker(ticker)
        self._check_name_and_currency(name, currency)
        with self._conn:
            if self._conn.execute("SELECT 1 FROM assets WHERE symbol = ?", (ticker,)).fetchone():
                raise ValueError("Ce ticker est déjà utilisé.")
            cursor = self._conn.execute(
                "INSERT INTO assets (symbol, name, currency) VALUES (?, ?, ?)",
                (ticker, name.strip(), currency.strip().upper()),
            )
            asset_id = int(cursor.lastrowid)
            self._conn.execute(
                "INSERT INTO stock_details (asset_id, place, sector, dividend, valuation_source) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    asset_id,
                    details.place.strip() or None,
                    details.sector.strip() or None,
                    None if details.dividend is None else str(details.dividend),
                    details.valuation_source.value,
                ),
            )
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def update(
        self, asset_id: int, name: str, ticker: str, currency: str, details: StockDetails
    ) -> StockHolding:
        """Modifie une action. Les montants déjà enregistrés ne sont pas convertis si la devise
        change ; l'origine du dernier cours ne change pas."""
        if self.get_holding(asset_id) is None:
            raise ValueError("Action introuvable.")
        ticker = validate_ticker(ticker)
        self._check_name_and_currency(name, currency)
        with self._conn:
            if self._conn.execute(
                "SELECT 1 FROM assets WHERE symbol = ? AND id <> ?", (ticker, asset_id)
            ).fetchone():
                raise ValueError("Ce ticker est déjà utilisé.")
            self._conn.execute(
                "UPDATE assets SET symbol = ?, name = ?, currency = ? WHERE id = ?",
                (ticker, name.strip(), currency.strip().upper(), asset_id),
            )
            self._conn.execute(
                "UPDATE stock_details SET place = ?, sector = ?, dividend = ? WHERE asset_id = ?",
                (
                    details.place.strip() or None,
                    details.sector.strip() or None,
                    None if details.dividend is None else str(details.dividend),
                    asset_id,
                ),
            )
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    @staticmethod
    def _check_name_and_currency(name: str, currency: str) -> None:
        if not name.strip():
            raise ValueError("Le nom de l'action est obligatoire.")
        if not currency.strip():
            raise ValueError("La devise est obligatoire.")

    def add_transaction(
        self,
        asset_id: int,
        kind: TransactionType,
        when: date,
        quantity: Decimal,
        price: Decimal,
        fees: Decimal,
    ) -> Transaction:
        """Achat ou vente de `quantity` actions au cours `price` (devise de l'action)."""
        holding = self.get_holding(asset_id)
        if holding is None:
            raise ValueError("Action introuvable.")
        validate_shares(quantity)
        if price <= ZERO:
            raise ValueError("Le cours doit être strictement positif.")
        if fees < ZERO:
            raise ValueError("Les frais ne peuvent pas être négatifs.")
        candidate = Transaction(None, asset_id, kind, when, quantity, price, fees)
        compute_position(asset_id, [*holding.transactions, candidate])
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO transactions (asset_id, type, trade_date, quantity, unit_price, fees) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (asset_id, kind.value, when.isoformat(), str(quantity), str(price), str(fees)),
            )
        return Transaction(int(cursor.lastrowid), asset_id, kind, when, quantity, price, fees)

    def record_quote(
        self,
        asset_id: int,
        when: date,
        price: Decimal,
        now: datetime,
        source: ValuationSource,
    ) -> None:
        """Enregistre un cours ; `source` devient l'origine du dernier cours."""
        if self.get_holding(asset_id) is None:
            raise ValueError("Action introuvable.")
        self._valuations.record(InstrumentType.STOCK, asset_id, when, price, now)
        with self._conn:
            self._conn.execute(
                "UPDATE stock_details SET valuation_source = ? WHERE asset_id = ?",
                (source.value, asset_id),
            )

    def quote_history(self, asset_id: int) -> list[Valuation]:
        """Cours enregistrés, du plus ancien au plus récent."""
        return self._valuations.history(InstrumentType.STOCK, asset_id)

    def valued_on(self, day: date) -> set[int]:
        return self._valuations.valued_on(InstrumentType.STOCK, day)

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

    def _holding(self, row: Any) -> StockHolding:
        asset_id = int(row["id"])
        return StockHolding(
            Asset(asset_id, row["symbol"], row["name"], row["currency"]),
            StockDetails(
                row["place"] or "",
                row["sector"] or "",
                None if row["dividend"] is None else Decimal(row["dividend"]),
                ValuationSource(row["valuation_source"]),
            ),
            self.transactions(asset_id),
            self._valuations.latest(InstrumentType.STOCK, asset_id),
        )
