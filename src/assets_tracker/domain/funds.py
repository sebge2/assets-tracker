"""Fonds : métadonnées simplifiées et validations métier."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


class ValuationSource(StrEnum):
    YAHOO = "YAHOO"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class FundDetails:
    broker: str
    ter: Decimal | None
    valuation_source: ValuationSource
    quote_symbol: str | None

    def __post_init__(self) -> None:
        if self.ter is not None and not Decimal(0) <= self.ter <= Decimal(100):
            raise ValueError("Les frais courants doivent être compris entre 0 et 100 %.")
        if self.valuation_source is ValuationSource.YAHOO and not self.quote_symbol:
            raise ValueError(
                "Un ticker Yahoo est requis pour une valeur récupérée automatiquement."
            )


def validate_identifier(identifier: str) -> str:
    value = identifier.strip().upper()
    if not value:
        raise ValueError("L'identifiant ISIN ou ticker est obligatoire.")
    if " " in value:
        raise ValueError("L'identifiant ne peut pas contenir d'espace.")
    return value


def is_isin(identifier: str) -> bool:
    return _ISIN.fullmatch(identifier) is not None


def fee_amount(quantity: Decimal, nav: Decimal, rate: Decimal) -> Decimal:
    if quantity <= 0 or nav <= 0:
        raise ValueError("Le nombre de parts et la valeur liquidative doivent être positifs.")
    if not Decimal(0) <= rate <= Decimal(100):
        raise ValueError("Les frais doivent être compris entre 0 et 100 %.")
    return quantity * nav * rate / Decimal(100)
