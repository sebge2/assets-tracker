"""Cours des matières premières, convertis dans l'unité de détention et en euros."""

from __future__ import annotations

from decimal import Decimal

from assets_tracker.domain.commodities import CommodityDetails, convert_quote
from assets_tracker.market.yahoo import fetch_fx_rate, fetch_last_price


def fetch_holding_price(details: CommodityDetails) -> Decimal | None:
    """Dernier cours par unité de détention, en euros ; None si indisponible.

    Ne lève jamais sur erreur réseau : l'appelant garde alors le dernier cours connu.
    """
    if details.quote_symbol is None:
        return None
    price = fetch_last_price(details.quote_symbol)
    if price is None:
        return None
    rate = fetch_fx_rate(details.quote_currency, "EUR")
    if rate is None:
        return None
    try:
        return convert_quote(
            price, quote_unit=details.quote_unit, holding_unit=details.unit, fx_to_eur=rate
        )
    except ValueError:
        return None
