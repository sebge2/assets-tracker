"""Cours des actions via Yahoo Finance, ramenés à la devise de l'instrument."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from assets_tracker.market.yahoo import fetch_fx_rate, fetch_last_quote

# Yahoo cote certaines places en sous-unité : (devise principale, sous-unités par unité).
MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}

QuoteSource = Callable[[str], tuple[Decimal, str | None] | None]
RateSource = Callable[[str, str], Decimal | None]


def to_currency(
    price: Decimal,
    quoted_in: str | None,
    currency: str | None,
    rate: RateSource,
) -> Decimal | None:
    """Convertit un cours coté en `quoted_in` vers `currency`, None si le taux manque.

    Sans devise de cotation (ou de destination), le cours est gardé tel quel.
    """
    if quoted_in in MINOR_UNITS:
        quoted_in, divisor = MINOR_UNITS[quoted_in]
        price /= divisor
    if not quoted_in or not currency or quoted_in.upper() == currency.upper():
        return price
    factor = rate(quoted_in.upper(), currency.upper())
    if factor is None or factor <= 0:
        return None
    return price * factor


def fetch_stock_price(
    symbol: str,
    currency: str | None = None,
    *,
    quote: QuoteSource = fetch_last_quote,
    rate: RateSource = fetch_fx_rate,
) -> Decimal | None:
    """Dernier cours dans `currency` (devise de l'instrument), ou None si indisponible.

    Yahoo coté en USD pour un titre suivi en EUR : le cours est converti au taux du jour. Si le
    taux de change manque, rien n'est enregistré (le dernier cours connu reste affiché) plutôt
    qu'un cours dans la mauvaise devise. Ne lève jamais sur erreur réseau.
    """
    found = quote(symbol)
    if found is None:
        return None
    price = to_currency(found[0], found[1], currency, rate)
    return price if price is not None and price > 0 else None
