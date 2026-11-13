"""Cours des obligations via Yahoo Finance (en % du nominal)."""

from __future__ import annotations

from decimal import Decimal

from assets_tracker.market.yahoo import fetch_last_price


def fetch_bond_price_percent(symbol: str) -> Decimal | None:
    """Dernier cours en % du nominal, ou None si Yahoo n'en fournit pas.

    Ne lève jamais sur erreur réseau : l'appelant garde alors le dernier cours connu.
    """
    price = fetch_last_price(symbol)
    return price if price is not None and price > 0 else None
