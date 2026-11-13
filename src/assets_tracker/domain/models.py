from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class InstrumentType(StrEnum):
    """Type d'un actif : pilote la valorisation et l'origine du cours."""

    STOCK = "STOCK"
    BOND = "BOND"
    FUND = "FUND"
    CRYPTO = "CRYPTO"
    CASH = "CASH"
    TERM_NOTE = "TERM_NOTE"
    COMMODITY = "COMMODITY"


class TransactionType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class Asset:
    id: int | None
    symbol: str  # ticker Yahoo Finance, ex. "AAPL", "CW8.PA"
    name: str
    currency: str = "EUR"


@dataclass(frozen=True, slots=True)
class Transaction:
    id: int | None
    asset_id: int
    type: TransactionType
    trade_date: date
    quantity: Decimal
    unit_price: Decimal
    fees: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class Position:
    asset_id: int
    quantity: Decimal
    pru: Decimal  # prix de revient unitaire (coût moyen pondéré, frais inclus)
    realized_pnl: Decimal
