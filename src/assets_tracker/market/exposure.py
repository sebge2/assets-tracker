"""Secteurs et pays d'un actif via Yahoo Finance (lecture seule, ne lève jamais)."""

from __future__ import annotations

from collections.abc import Callable

from assets_tracker.domain.exposure_import import RawExposure

ExposureFetcher = Callable[[str], RawExposure | None]
FUND_TYPES = {"ETF", "MUTUALFUND"}


def fetch_exposure(symbol: str) -> RawExposure | None:
    """Données de répartition d'un symbole, ou None si Yahoo n'en fournit pas.

    Action : secteur et pays de l'émetteur. Fonds ou ETF : poids par secteur (Yahoo ne donne pas
    de répartition géographique). Toute erreur réseau ou de format donne None.
    """
    import yfinance as yf

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        if info.get("quoteType") in FUND_TYPES:
            weights = ticker.funds_data.sector_weightings or {}
            cleaned = {str(k): float(v) for k, v in weights.items() if v}
            return RawExposure(sector_weights=cleaned) if cleaned else None
        sector, country = info.get("sector"), info.get("country")
    except Exception:
        return None
    if not sector and not country:
        return None
    return RawExposure(
        sector=str(sector) if sector else None, country=str(country) if country else None
    )
