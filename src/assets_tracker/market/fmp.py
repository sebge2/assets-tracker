"""Financial Modeling Prep : répartition par secteur et par pays d'un fonds (clé d'API requise).

Contrairement à Yahoo, une erreur est signalée (`ExposureError`, message lisible) pour que
l'écran puisse l'afficher. Seul le symbole du fonds et la clé quittent l'application : jamais
le contenu du portefeuille. La clé n'apparaît dans aucun message d'erreur.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from assets_tracker.domain.exposure_import import ExposureError, RawExposure

BASE_URL = "https://financialmodelingprep.com/stable"
TIMEOUT = 20.0


def _error_message(status: int, body: str) -> str:
    detail = ""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            detail = str(data.get("Error Message") or data.get("message") or "")
    except ValueError:
        pass
    reasons = {
        401: "clé d'API refusée",
        402: "ce service n'est pas inclus dans votre abonnement FMP",
        403: "clé d'API invalide ou accès non autorisé",
        404: "service introuvable",
        429: "limite d'appels FMP atteinte, réessayez plus tard",
    }
    reason = reasons.get(status, f"erreur HTTP {status}")
    return f"{reason} ({detail})" if detail else reason


def _get(path: str, params: dict[str, str], api_key: str) -> Any:
    """Appelle un service FMP et renvoie le JSON décodé ; lève `ExposureError` en cas d'échec."""
    if not api_key.strip():
        raise ExposureError("Aucune clé d'API FMP n'est configurée.")
    query = urllib.parse.urlencode({**params, "apikey": api_key.strip()})
    request = urllib.request.Request(
        f"{BASE_URL}/{path}?{query}", headers={"User-Agent": "assets-tracker"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", errors="replace") if error.fp else ""
        raise ExposureError(f"FMP : {_error_message(error.code, text)}.") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise ExposureError(f"FMP injoignable : {reason}.") from None
    try:
        data = json.loads(body)
    except ValueError:
        raise ExposureError("FMP : réponse illisible.") from None
    if isinstance(data, dict) and (data.get("Error Message") or data.get("message")):
        raise ExposureError(f"FMP : {data.get('Error Message') or data.get('message')}")
    return data


def _percent(value: Any) -> float:
    """« 97.26% », 97.26 ou None → 97.26 (0 si illisible)."""
    try:
        return float(str(value).strip().rstrip("%").replace(",", "."))
    except ValueError:
        return 0.0


def _weights(rows: Any, label: str) -> dict[str, float]:
    if not isinstance(rows, list):
        return {}
    found: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get(label):
            continue
        weight = _percent(row.get("weightPercentage", row.get("weight")))
        if weight > 0:
            found[str(row[label])] = found.get(str(row[label]), 0.0) + weight
    return found


def fetch_fund_exposure(symbol: str, api_key: str) -> RawExposure | None:
    """Poids sectoriels et géographiques d'un fonds, None si FMP n'a rien. Lève `ExposureError`."""
    sectors = _weights(_get("etf/sector-weightings", {"symbol": symbol}, api_key), "sector")
    countries = _weights(_get("etf/country-weightings", {"symbol": symbol}, api_key), "country")
    if not sectors and not countries:
        return None
    return RawExposure(sector_weights=sectors or None, country_weights=countries or None)


# Codes pays ISO du profil FMP → noms anglais attendus par `domain.exposure_import`.
ISO_COUNTRIES = {
    "US": "United States", "FR": "France", "DE": "Germany", "GB": "United Kingdom",
    "IT": "Italy", "ES": "Spain", "NL": "Netherlands", "BE": "Belgium", "LU": "Luxembourg",
    "IE": "Ireland", "PT": "Portugal", "AT": "Austria", "FI": "Finland", "GR": "Greece",
    "CH": "Switzerland", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "PL": "Poland",
    "CZ": "Czech Republic", "HU": "Hungary", "CA": "Canada", "JP": "Japan",
    "AU": "Australia", "NZ": "New Zealand", "HK": "Hong Kong", "SG": "Singapore",
    "KR": "South Korea", "TW": "Taiwan", "CN": "China", "IN": "India", "BR": "Brazil",
    "MX": "Mexico", "ZA": "South Africa", "TR": "Turkey", "ID": "Indonesia",
    "TH": "Thailand", "MY": "Malaysia", "PH": "Philippines", "CL": "Chile",
    "CO": "Colombia", "SA": "Saudi Arabia", "AE": "United Arab Emirates", "IL": "Israel",
}  # fmt: skip


def fetch_stock_exposure(symbol: str, api_key: str) -> RawExposure | None:
    """Secteur et pays de l'émetteur d'une action (profil FMP). Lève `ExposureError`."""
    rows = _get("profile", {"symbol": symbol}, api_key)
    profile = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    sector = str(profile.get("sector") or "").strip()
    code = str(profile.get("country") or "").strip()
    country = ISO_COUNTRIES.get(code.upper(), code)
    if not sector and not country:
        return None
    return RawExposure(sector=sector or None, country=country or None)


def fetch_with_fallback(
    symbol: str,
    *,
    primary: Callable[[str], RawExposure | None],
    api_key: str,
    fallback: Callable[[str, str], RawExposure | None] = fetch_stock_exposure,
) -> RawExposure | None:
    """Données de la source principale (Yahoo) ; FMP seulement si elle n'a rien."""
    raw = primary(symbol)
    return raw if raw is not None else fallback(symbol, api_key)


def check_api_key(api_key: str) -> None:
    """Vérifie la clé par un appel léger ; lève `ExposureError` si elle est refusée."""
    _get("quote-short", {"symbol": "AAPL"}, api_key)
