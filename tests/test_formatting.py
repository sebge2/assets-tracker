from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from assets_tracker.domain.cash import CashAccount, InterestFrequency
from assets_tracker.ui.formatting import (
    format_number,
    frequency_label,
    money,
    parse_decimal,
    percent,
    points,
    signed_money,
)

D = Decimal
IBAN = "FR7630006000011234567890189"


def account(**overrides: object) -> CashAccount:
    fields: dict[str, object] = {
        "id": 1,
        "name": "Livret A",
        "bank": "CM",
        "iban": IBAN,
        "currency": "EUR",
        "opened_on": date(2024, 1, 1),
        "frequency": InterestFrequency.ANNUAL,
        "payout_month": 12,
        "payout_day": 31,
    }
    fields.update(overrides)
    return CashAccount(**fields)  # type: ignore[arg-type]


def test_numbers_use_french_grouping_and_decimal_comma() -> None:
    assert format_number(D("2000")) == "2 000,00"
    assert format_number(D("1234567.891")) == "1 234 567,89"
    assert format_number(D("-300.5")) == "−300,50"
    assert format_number(D("0.005")) == "0,01"
    assert format_number(D("1234.5"), places=0) == "1 235"


def test_money_percent_and_points() -> None:
    assert money(D("2000"), "EUR") == "2 000,00 €"
    assert money(D("5"), "CHF") == "5,00 CHF"
    assert signed_money(D("60"), "EUR") == "+60,00 €"
    assert signed_money(D("-300"), "EUR") == "−300,00 €"
    assert signed_money(D("0"), "EUR") == "0,00 €"
    assert percent(D("3")) == "3,00 %"
    assert points(D("0.5")) == "+0,50 pt"
    assert points(D("-0.5")) == "−0,50 pt"


def test_frequency_labels() -> None:
    assert frequency_label(account()) == "Annuel · le 31 décembre"
    assert frequency_label(account(payout_month=1, payout_day=1)) == "Annuel · le 1er janvier"
    monthly = account(frequency=InterestFrequency.MONTHLY, payout_month=None, payout_day=None)
    assert frequency_label(monthly) == "Mensuel · le 1er du mois"


def test_parse_decimal_accepts_french_input() -> None:
    assert parse_decimal("1 250,50", "Montant") == D("1250.50")
    assert parse_decimal("3.5", "Taux") == D("3.5")
    assert parse_decimal("2 000", "Montant") == D("2000")
    for bad in ("", "abc", "NaN", "Infinity"):
        with pytest.raises(ValueError, match="Montant"):
            parse_decimal(bad, "Montant")
