"""Formatage français des montants, taux et libellés (sans dépendance à Qt)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from assets_tracker.domain.cash import CashAccount, InterestFrequency
from assets_tracker.domain.commodities import CommodityFamily

NBSP = " "
NNBSP = " "
MINUS = "−"

MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

_MONTHS_SHORT = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)

_CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def format_number(value: Decimal, places: int = 2) -> str:
    """`2000` → `2 000,00` (espace fine insécable, virgule décimale, vrai signe moins)."""
    quantized = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = MINUS if quantized < 0 else ""
    integer, _, fraction = f"{abs(quantized):f}".partition(".")
    grouped = f"{int(integer):,}".replace(",", NNBSP)
    return f"{sign}{grouped},{fraction}" if places else f"{sign}{grouped}"


def money(amount: Decimal, currency: str) -> str:
    return f"{format_number(amount)}{NBSP}{_CURRENCY_SYMBOLS.get(currency, currency)}"


def compact_money(amount: Decimal, currency: str) -> str:
    """Montant sans décimales, pour les graduations d'un axe : `12 500 €`."""
    return f"{format_number(amount, 0)}{NBSP}{_CURRENCY_SYMBOLS.get(currency, currency)}"


def signed_money(amount: Decimal, currency: str) -> str:
    """`+60,00 €` ou `−300,00 €` ; zéro sans signe."""
    if amount == 0:
        return money(amount, currency)
    prefix = "+" if amount > 0 else ""
    return prefix + money(amount, currency)


def percent(rate: Decimal) -> str:
    return f"{format_number(rate)}{NBSP}%"


def points(delta: Decimal) -> str:
    """Variation de taux en points : `+0,50 pt`."""
    prefix = "+" if delta > 0 else ""
    return f"{prefix}{format_number(delta)}{NBSP}pt"


def frequency_label(account: CashAccount) -> str:
    if account.frequency is InterestFrequency.MONTHLY:
        return "Mensuel · le 1er du mois"
    assert account.payout_month is not None
    assert account.payout_day is not None
    day = "1er" if account.payout_day == 1 else str(account.payout_day)
    return f"Annuel · le {day} {MONTHS_FR[account.payout_month - 1]}"


def parse_decimal(text: str, field: str) -> Decimal:
    """Lit un nombre saisi à la française (`1 250,50`) ; lève `ValueError` sinon."""
    cleaned = "".join(text.split()).replace(NBSP, "").replace(NNBSP, "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"{field} : nombre invalide.") from None
    if not value.is_finite():
        raise ValueError(f"{field} : nombre invalide.")
    return value


def month_label(month: date, *, short: bool = False) -> str:
    """`novembre 2026` (ou `nov. 2026` en abrégé)."""
    names = _MONTHS_SHORT if short else MONTHS_FR
    return f"{names[month.month - 1]} {month.year}"


FAMILY_LABELS = {
    CommodityFamily.PRECIOUS: "Métaux précieux",
    CommodityFamily.ENERGY: "Énergie",
    CommodityFamily.INDUSTRIAL: "Métaux industriels",
    CommodityFamily.AGRICULTURAL: "Agricole",
}


def format_quantity(value: Decimal, max_places: int = 4) -> str:
    """Quantité sans zéros inutiles : `100`, `0,5`, `1 250,25`."""
    quantized = value.quantize(Decimal(1).scaleb(-max_places), rounding=ROUND_HALF_UP)
    text = f"{abs(quantized):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    integer, _, fraction = text.partition(".")
    grouped = f"{int(integer):,}".replace(",", NNBSP)
    sign = MINUS if quantized < 0 else ""
    return f"{sign}{grouped},{fraction}" if fraction else f"{sign}{grouped}"


def short_datetime(moment: datetime) -> str:
    """`12 nov. 21:03`."""
    return f"{moment.day} {_MONTHS_SHORT[moment.month - 1]} {moment:%H:%M}"


def relative_age(delta_seconds: float) -> str:
    """Ancienneté d'un cours : `À l'instant`, `Il y a 4 min`, `Il y a 3 h`, `Il y a 2 j`."""
    minutes = int(delta_seconds // 60)
    if minutes < 1:
        return "À l'instant"
    if minutes < 60:
        return f"Il y a {minutes} min"
    if minutes < 24 * 60:
        return f"Il y a {minutes // 60} h"
    return f"Il y a {minutes // (24 * 60)} j"
