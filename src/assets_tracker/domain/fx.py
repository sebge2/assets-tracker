"""Taux de change : conversion d'un montant en euros.

Logique pure. Un taux est la valeur d'UNE unité de la devise, en euros (USD → 0,92). Le taux
utilisé est le dernier connu (cache `exchange_rates`) : il s'applique au prix de revient comme
à la valeur de marché, donc la plus-value latente d'un titre étranger n'inclut pas l'effet de
change (aucun historique de taux n'est conservé).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal

from assets_tracker.domain.models import Position, Transaction

BASE_CURRENCY = "EUR"


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    currency: str
    rate_date: date
    rate: Decimal  # euros pour une unité de `currency`
    fetched_at: datetime


def validate_rate(rate: Decimal) -> Decimal:
    if not rate.is_finite() or rate <= 0:
        raise ValueError("Le taux de change doit être strictement positif.")
    return rate


def rate_for(currency: str, rates: Mapping[str, ExchangeRate]) -> Decimal | None:
    """Taux vers l'euro : 1 pour l'euro, `None` si la devise n'a aucun taux connu."""
    if currency == BASE_CURRENCY:
        return Decimal(1)
    known = rates.get(currency)
    return None if known is None else known.rate


def currencies_to_refresh(
    currencies: Iterable[str],
    rates: Mapping[str, ExchangeRate],
    today: date,
    *,
    force: bool = False,
) -> list[str]:
    """Devises étrangères sans taux daté du jour (un appel par jour et par devise)."""
    wanted = sorted({c for c in currencies if c != BASE_CURRENCY})
    return [c for c in wanted if force or c not in rates or rates[c].rate_date != today]


def transactions_in_euros(transactions: Iterable[Transaction], rate: Decimal) -> list[Transaction]:
    return [replace(t, unit_price=t.unit_price * rate, fees=t.fees * rate) for t in transactions]


def position_in_euros(position: Position, rate: Decimal) -> Position:
    return replace(position, pru=position.pru * rate, realized_pnl=position.realized_pnl * rate)
