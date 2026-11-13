"""Persistance des obligations, de leurs opérations, coupons courus et cours."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from assets_tracker.db.valuation_repository import ValuationRepository
from assets_tracker.domain.bonds import (
    BondDetails,
    CouponFrequency,
    price_percent,
    unit_value,
    validate_quantity,
)
from assets_tracker.domain.funds import ValuationSource, validate_identifier
from assets_tracker.domain.models import (
    Asset,
    InstrumentType,
    Position,
    Transaction,
    TransactionType,
)
from assets_tracker.domain.positions import compute_position
from assets_tracker.domain.valuation import Valuation

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class BondHolding:
    asset: Asset
    details: BondDetails
    transactions: list[Transaction]
    accruals: dict[int, Decimal]  # coupon couru par identifiant de transaction
    valuation: Valuation | None  # dernier cours : valeur d'UN titre

    @property
    def position(self) -> Position:
        assert self.asset.id is not None
        return compute_position(self.asset.id, self.transactions)

    @property
    def quote_percent(self) -> Decimal | None:
        if self.valuation is None:
            return None
        return price_percent(self.valuation.value, self.details.nominal)


class BondRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._valuations = ValuationRepository(conn)

    def list_holdings(self) -> list[BondHolding]:
        rows = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN bond_details d ON d.asset_id = a.id ORDER BY d.maturity, a.name, a.id"
        ).fetchall()
        return [self._holding(row) for row in rows]

    def get_holding(self, asset_id: int) -> BondHolding | None:
        row = self._conn.execute(
            "SELECT a.id, a.symbol, a.name, a.currency, d.* FROM assets a "
            "JOIN bond_details d ON d.asset_id = a.id WHERE a.id = ?",
            (asset_id,),
        ).fetchone()
        return None if row is None else self._holding(row)

    def create(
        self, name: str, identifier: str, currency: str, details: BondDetails
    ) -> BondHolding:
        identifier = validate_identifier(identifier)
        if not name.strip():
            raise ValueError("Le nom de l'obligation est obligatoire.")
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
            self._conn.execute(
                "INSERT INTO bond_details "
                "(asset_id, issuer, nominal, coupon_rate, frequency, maturity, valuation_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    asset_id,
                    details.issuer.strip() or None,
                    str(details.nominal),
                    str(details.coupon_rate),
                    details.frequency.value,
                    details.maturity.isoformat(),
                    details.valuation_source.value,
                ),
            )
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def update(
        self, asset_id: int, name: str, identifier: str, currency: str, details: BondDetails
    ) -> BondHolding:
        """Modifie une obligation, nominal et devise compris.

        Prix des opérations et cours sont stockés en euros par titre (cours % × nominal) : quand le
        nominal change, ils sont recalculés pour que les cours en % restent identiques. Le
        changement de devise ne convertit rien (aucun taux de change n'est stocké). L'origine du
        dernier cours ne change pas.
        """
        current = self.get_holding(asset_id)
        if current is None:
            raise ValueError("Obligation introuvable.")
        identifier = validate_identifier(identifier)
        if not name.strip():
            raise ValueError("Le nom de l'obligation est obligatoire.")
        if not currency.strip():
            raise ValueError("La devise est obligatoire.")
        factor = details.nominal / current.details.nominal
        with self._conn:
            if self._conn.execute(
                "SELECT 1 FROM assets WHERE symbol = ? AND id <> ?", (identifier, asset_id)
            ).fetchone():
                raise ValueError("Cet identifiant est déjà utilisé.")
            self._conn.execute(
                "UPDATE assets SET symbol = ?, name = ?, currency = ? WHERE id = ?",
                (identifier, name.strip(), currency.strip().upper(), asset_id),
            )
            self._conn.execute(
                "UPDATE bond_details SET issuer = ?, nominal = ?, coupon_rate = ?, "
                "frequency = ?, maturity = ? WHERE asset_id = ?",
                (
                    details.issuer.strip() or None,
                    str(details.nominal),
                    str(details.coupon_rate),
                    details.frequency.value,
                    details.maturity.isoformat(),
                    asset_id,
                ),
            )
            if factor != 1:
                self._rescale(asset_id, factor)
        holding = self.get_holding(asset_id)
        assert holding is not None
        return holding

    def _rescale(self, asset_id: int, factor: Decimal) -> None:
        """Multiplie les montants par titre stockés (prix des opérations, cours)."""
        for table, key, extra, params in (
            ("transactions", "id", "", ()),
            ("valuations", "valuation_date", " AND asset_type = 'BOND'", ()),
        ):
            column = "unit_price" if table == "transactions" else "value"
            id_column = "asset_id"
            rows = self._conn.execute(
                f"SELECT {key} AS k, {column} AS v FROM {table} WHERE {id_column} = ?{extra}",
                (asset_id, *params),
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {id_column} = ? AND {key} = ?{extra}",
                    (str(Decimal(row["v"]) * factor), asset_id, row["k"]),
                )

    def add_transaction(
        self,
        asset_id: int,
        kind: TransactionType,
        when: date,
        quantity: Decimal,
        price_percent_: Decimal,
        accrued: Decimal,
    ) -> Transaction:
        """Achat ou vente de `quantity` titres au cours `price_percent_` (% du nominal).

        Le prix unitaire stocké est la valeur d'un titre en euros ; le coupon couru est conservé
        à part (jamais dans le PRU ni dans la plus-value réalisée).
        """
        holding = self.get_holding(asset_id)
        if holding is None:
            raise ValueError("Obligation introuvable.")
        validate_quantity(quantity)
        if accrued < ZERO:
            raise ValueError("Le coupon couru ne peut pas être négatif.")
        price = unit_value(price_percent_, holding.details.nominal)
        candidate = Transaction(None, asset_id, kind, when, quantity, price, ZERO)
        compute_position(asset_id, [*holding.transactions, candidate])
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO transactions (asset_id, type, trade_date, quantity, unit_price, fees) "
                "VALUES (?, ?, ?, ?, ?, '0')",
                (asset_id, kind.value, when.isoformat(), str(quantity), str(price)),
            )
            tx_id = int(cursor.lastrowid)
            self._conn.execute(
                "INSERT INTO bond_accruals (transaction_id, accrued) VALUES (?, ?)",
                (tx_id, str(accrued)),
            )
        return Transaction(tx_id, asset_id, kind, when, quantity, price, ZERO)

    def record_quote(
        self,
        asset_id: int,
        when: date,
        price_percent_: Decimal,
        now: datetime,
        source: ValuationSource,
    ) -> None:
        """Enregistre un cours (en % du nominal) ; `source` devient l'origine du dernier cours."""
        holding = self.get_holding(asset_id)
        if holding is None:
            raise ValueError("Obligation introuvable.")
        value = unit_value(price_percent_, holding.details.nominal)
        self._valuations.record(InstrumentType.BOND, asset_id, when, value, now)
        with self._conn:
            self._conn.execute(
                "UPDATE bond_details SET valuation_source = ? WHERE asset_id = ?",
                (source.value, asset_id),
            )

    def quote_history(self, asset_id: int) -> list[Valuation]:
        """Cours enregistrés (valeur d'un titre), du plus ancien au plus récent."""
        return self._valuations.history(InstrumentType.BOND, asset_id)

    def valued_on(self, day: date) -> set[int]:
        return self._valuations.valued_on(InstrumentType.BOND, day)

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

    def _accruals(self, asset_id: int) -> dict[int, Decimal]:
        rows = self._conn.execute(
            "SELECT b.transaction_id, b.accrued FROM bond_accruals b "
            "JOIN transactions t ON t.id = b.transaction_id WHERE t.asset_id = ?",
            (asset_id,),
        ).fetchall()
        return {int(r["transaction_id"]): Decimal(r["accrued"]) for r in rows}

    def _holding(self, row: Any) -> BondHolding:
        asset_id = int(row["id"])
        return BondHolding(
            Asset(asset_id, row["symbol"], row["name"], row["currency"]),
            BondDetails(
                row["issuer"] or "",
                Decimal(row["nominal"]),
                Decimal(row["coupon_rate"]),
                CouponFrequency(row["frequency"]),
                date.fromisoformat(row["maturity"]),
                ValuationSource(row["valuation_source"]),
            ),
            self.transactions(asset_id),
            self._accruals(asset_id),
            self._valuations.latest(InstrumentType.BOND, asset_id),
        )
