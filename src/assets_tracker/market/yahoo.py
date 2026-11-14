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


def fetch_last_quote(symbol: str) -> tuple[Decimal, str | None] | None:
    """Dernier cours et devise de cotation (None si Yahoo ne l'indique pas), ou None.

    Ne lève pas sur erreur réseau.
    """
    import yfinance as yf

    try:
        info = yf.Ticker(symbol).fast_info
        price = info["last_price"]
    except Exception:
        return None
    if not price:
        return None
    try:
        currency = str(info["currency"]).strip() or None
    except Exception:
        currency = None
    return Decimal(str(price)), currency


def fetch_fx_rate(base: str, quote: str) -> Decimal | None:
    """Taux de change `base` → `quote` (ex. USD → EUR), 1 si les devises sont identiques."""
    if base == quote:
        return Decimal(1)
    return fetch_last_price(f"{base}{quote}=X")
