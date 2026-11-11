"""Seul module autorisé à accéder à Internet (cours via yfinance)."""

from __future__ import annotations

from decimal import Decimal


def fetch_last_price(symbol: str) -> Decimal | None:
    """Dernier cours connu, ou None si indisponible. Ne lève pas sur erreur réseau."""
    import yfinance as yf

    try:
        price = yf.Ticker(symbol).fast_info["last_price"]
    except Exception:
        return None
    return Decimal(str(price)) if price else None
