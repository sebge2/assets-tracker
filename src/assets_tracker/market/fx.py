"""Taux de change vers l'euro via Yahoo Finance (`USDEUR=X`)."""

from __future__ import annotations

from decimal import Decimal

from assets_tracker.market.yahoo import fetch_fx_rate


def fetch_rate_to_eur(currency: str) -> Decimal | None:
    """Euros pour une unité de `currency`, ou None si Yahoo n'en fournit pas."""
    rate = fetch_fx_rate(currency, "EUR")
    return rate if rate is not None and rate > 0 else None
