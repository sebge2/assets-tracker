from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from assets_tracker.domain.models import InstrumentType
from assets_tracker.domain.valuation import (
    Valuation,
    assets_to_refresh,
    is_stale,
    needs_refresh,
)

D = Decimal
TODAY = date(2026, 11, 13)


def valuation(day: date = TODAY, value: str = "71.85") -> Valuation:
    return Valuation(InstrumentType.COMMODITY, 1, day, D(value), datetime(2026, 11, 13, 9, 0))


def test_instrument_types_cover_every_kind_of_asset() -> None:
    assert {t.value for t in InstrumentType} == {
        "STOCK",
        "BOND",
        "FUND",
        "CRYPTO",
        "CASH",
        "TERM_NOTE",
        "COMMODITY",
    }


def test_value_must_be_positive() -> None:
    with pytest.raises(ValueError, match="strictement positive"):
        valuation(value="0")


def test_only_past_days_are_stale() -> None:
    assert not is_stale(valuation(TODAY), TODAY)
    assert is_stale(valuation(date(2026, 11, 12)), TODAY)


def test_provider_is_called_once_a_day() -> None:
    assert needs_refresh(None, TODAY)  # jamais valorisé
    assert needs_refresh(date(2026, 11, 12), TODAY)  # dernière valeur d'hier
    assert not needs_refresh(TODAY, TODAY)  # déjà valorisé aujourd'hui


def test_force_overrides_the_daily_limit() -> None:
    assert needs_refresh(TODAY, TODAY, force=True)


def test_only_assets_without_a_valuation_today_are_refreshed() -> None:
    assert assets_to_refresh([1, 2, 3], valued_today={2}) == [1, 3]
    assert assets_to_refresh([1, 2, 3], valued_today={1, 2, 3}) == []
    assert assets_to_refresh([1, 2], valued_today={1, 2}, force=True) == [1, 2]
