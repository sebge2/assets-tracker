from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from assets_tracker.market.stocks import fetch_stock_price, to_currency

D = Decimal


def rate_table(rates: dict[tuple[str, str], Decimal]) -> Callable[[str, str], Decimal | None]:
    return lambda base, quote: rates.get((base, quote))


def test_usd_quote_is_converted_to_the_instrument_currency() -> None:
    price = fetch_stock_price(
        "X",
        "EUR",
        quote=lambda _: (D("100"), "USD"),
        rate=rate_table({("USD", "EUR"): D("0.9")}),
    )
    assert price == D("90.0")


def test_same_currency_is_kept_without_rate_lookup() -> None:
    def no_rate(base: str, quote: str) -> Decimal | None:
        raise AssertionError("aucun taux attendu")

    price = fetch_stock_price("X", "eur", quote=lambda _: (D("12.5"), "EUR"), rate=no_rate)
    assert price == D("12.5")


def test_missing_rate_gives_none_not_a_wrong_price() -> None:
    price = fetch_stock_price(
        "X", "EUR", quote=lambda _: (D("100"), "USD"), rate=rate_table({})
    )
    assert price is None


def test_unknown_quote_currency_keeps_price() -> None:
    assert fetch_stock_price("X", "EUR", quote=lambda _: (D("7"), None)) == D("7")


def test_no_quote_gives_none() -> None:
    assert fetch_stock_price("X", "EUR", quote=lambda _: None) is None


def test_pence_are_converted_to_pounds_then_to_target() -> None:
    assert to_currency(D("1500"), "GBp", "GBP", rate_table({})) == D("15")
    assert to_currency(
        D("1500"), "GBX", "EUR", rate_table({("GBP", "EUR"): D("1.2")})
    ) == D("18.0")
