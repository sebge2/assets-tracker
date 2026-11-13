from __future__ import annotations

from decimal import Decimal

import pytest

from assets_tracker.domain.commodities import (
    PHYSICAL_METALS,
    CommodityDetails,
    CommodityFamily,
    CommodityForm,
    convert_quote,
    family_shares,
    preview_buy,
    preview_sell,
    valuation,
)
from assets_tracker.domain.models import Position

D = Decimal
GOLD = Position(1, D(100), D("62.4"), D(0))  # 100 g à 62,40 € le gramme


def details(**overrides: object) -> CommodityDetails:
    fields: dict[str, object] = {
        "family": CommodityFamily.PRECIOUS,
        "form": CommodityForm.PHYSICAL,
        "unit": "g",
        "quote_symbol": "GC=F",
        "quote_currency": "USD",
        "quote_unit": "oz",
    }
    fields.update(overrides)
    return CommodityDetails(**fields)  # type: ignore[arg-type]


# --- Détails ------------------------------------------------------------------


def test_physical_metal_is_held_in_a_weight_unit() -> None:
    assert details(unit="kg").unit == "kg"
    with pytest.raises(ValueError, match="g, kg ou oz"):
        details(unit="parts")


def test_etc_is_held_in_parts_and_cannot_have_a_weight_quote() -> None:
    etc = details(form=CommodityForm.ETC, unit="parts", quote_unit=None)
    assert etc.unit == "parts"
    with pytest.raises(ValueError, match="en parts"):
        details(form=CommodityForm.ETC, unit="g")
    with pytest.raises(ValueError, match="poids"):
        details(form=CommodityForm.ETC, unit="parts", quote_unit="oz")


def test_quote_currency_must_be_a_currency_code() -> None:
    with pytest.raises(ValueError, match="3 lettres"):
        details(quote_currency="dollar")


def test_metal_presets_are_precious_with_a_spot_symbol() -> None:
    assert {m.name for m in PHYSICAL_METALS} >= {"Or", "Argent"}
    assert all(m.family is CommodityFamily.PRECIOUS and m.quote_symbol for m in PHYSICAL_METALS)


# --- Conversion de cours ------------------------------------------------------


def test_spot_per_troy_ounce_in_usd_becomes_euros_per_gram() -> None:
    # 2 300 $ l'once, 1 € = 1,10 $ → 1 $ = 0,9090… €
    price = convert_quote(D(2300), quote_unit="oz", holding_unit="g", fx_to_eur=D(1) / D("1.10"))
    assert price.quantize(D("0.01")) == D("67.22")  # 2 300 / 31,1034768 / 1,10


def test_per_kilogram_scales_the_per_gram_price() -> None:
    per_gram = convert_quote(D(2300), quote_unit="oz", holding_unit="g", fx_to_eur=D("0.9"))
    per_kg = convert_quote(D(2300), quote_unit="oz", holding_unit="kg", fx_to_eur=D("0.9"))
    assert per_kg == per_gram * 1000


def test_same_unit_only_converts_the_currency() -> None:
    assert convert_quote(D(100), quote_unit=None, holding_unit="parts", fx_to_eur=D("0.9")) == D(90)
    assert convert_quote(D(100), quote_unit="g", holding_unit="g", fx_to_eur=D(1)) == D(100)


def test_impossible_conversions_are_refused() -> None:
    with pytest.raises(ValueError, match="Conversion impossible"):
        convert_quote(D(100), quote_unit="oz", holding_unit="parts", fx_to_eur=D(1))
    with pytest.raises(ValueError, match="strictement positifs"):
        convert_quote(D(100), quote_unit="oz", holding_unit="g", fx_to_eur=D(0))


# --- Achat, vente, valorisation -----------------------------------------------


def test_buy_preview_recomputes_the_weighted_pru_with_fees() -> None:
    preview = preview_buy(GOLD, D(50), D("71.2"), D(35))

    assert preview.cost == D(3595)  # 50 × 71,20 + 35
    assert preview.quantity_after == D(150)
    assert preview.pru_after == (D(6240) + D(3595)) / D(150)
    assert preview.pru_after.quantize(D("0.01")) == D("65.57")


def test_sell_preview_keeps_the_pru_and_realizes_the_gain() -> None:
    preview = preview_sell(GOLD, D(20), D("71.85"), D(4))

    assert preview.proceeds == D(1433)  # 20 × 71,85 − 4
    assert preview.realized == D(185)  # 1 433 − 20 × 62,40
    assert preview.quantity_after == D(80)


def test_sell_at_a_loss_gives_a_negative_realized_gain() -> None:
    petrol = Position(1, D(120), D("31.4"), D(0))
    assert preview_sell(petrol, D(40), D("29.8"), D(2)).realized == D(-66)


def test_cannot_sell_more_than_held() -> None:
    with pytest.raises(ValueError, match="supérieure"):
        preview_sell(GOLD, D("100.01"), D(70), D(0))


@pytest.mark.parametrize(
    ("quantity", "price", "fees", "message"),
    [("0", "70", "0", "quantité"), ("5", "0", "0", "prix"), ("5", "70", "-1", "frais")],
)
def test_trades_are_validated(quantity: str, price: str, fees: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        preview_buy(GOLD, D(quantity), D(price), D(fees))


def test_valuation_gives_value_cost_and_unrealized_gain() -> None:
    result = valuation(GOLD, D("71.85"))

    assert result.value == D("7185")
    assert result.cost == D("6240")
    assert result.unrealized == D("945")
    assert result.unrealized_percent is not None
    assert round(result.unrealized_percent, 1) == D("15.1")


def test_valuation_of_a_closed_position_has_no_percentage() -> None:
    closed = Position(1, D(0), D(0), D(120))
    result = valuation(closed, D(70))
    assert result.value == 0
    assert result.unrealized_percent is None


def test_family_shares_follow_family_order_and_sum_to_100() -> None:
    shares = family_shares(
        {
            CommodityFamily.ENERGY: D("3576"),
            CommodityFamily.PRECIOUS: D("16124"),
            CommodityFamily.AGRICULTURAL: D("1561.5"),
            CommodityFamily.INDUSTRIAL: D("2541"),
        }
    )
    assert [family for family, _ in shares] == [
        CommodityFamily.PRECIOUS,
        CommodityFamily.ENERGY,
        CommodityFamily.INDUSTRIAL,
        CommodityFamily.AGRICULTURAL,
    ]
    assert sum(share for _, share in shares) == D(100)
    assert round(shares[0][1], 1) == D("67.7")
    assert family_shares({}) == []
