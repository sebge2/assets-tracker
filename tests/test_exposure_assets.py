from __future__ import annotations

from decimal import Decimal

from pytestqt.qtbot import QtBot

from assets_tracker.domain.exposure import (
    ExposedAsset,
    ExposureEntry,
    asset_key,
    filter_assets,
)
from assets_tracker.domain.models import InstrumentType
from assets_tracker.ui.exposure_assets_dialog import ExposureAssetsDialog
from assets_tracker.ui.portfolio_exposure import PortfolioExposureCard

D = Decimal


def asset(
    name: str, kind: InstrumentType, value: int, sector: str, asset_id: int | None
) -> ExposedAsset:
    return ExposedAsset(name, kind, D(value), (ExposureEntry(sector, D(100)),), (), asset_id)


STOCK = asset("Air Liquide", InstrumentType.STOCK, 1000, "Industrie", 1)
FUND = asset("Actions Monde", InstrumentType.FUND, 3000, "Technologie", 2)
COMMODITY = ExposedAsset("Matières premières", InstrumentType.COMMODITY, D(500), (), ())


def test_asset_key_uses_id_or_name() -> None:
    assert asset_key(STOCK) == ("STOCK", 1)
    assert asset_key(COMMODITY) == ("COMMODITY", "Matières premières")


def test_filter_assets_drops_excluded_only() -> None:
    kept = filter_assets([STOCK, FUND, COMMODITY], {asset_key(FUND)})
    assert kept == [STOCK, COMMODITY]
    assert filter_assets([STOCK], set()) == [STOCK]


def make_card(qtbot: QtBot) -> PortfolioExposureCard:
    card = PortfolioExposureCard()
    qtbot.addWidget(card)
    card.set_assets([STOCK, FUND])
    return card


def test_excluding_an_asset_recomputes_the_breakdown(qtbot: QtBot) -> None:
    card = make_card(qtbot)
    assert len(card._rows) == 2

    card.set_excluded({asset_key(FUND)})

    assert len(card._rows) == 1
    assert card._report is not None
    assert [c.name for c in card._report.categories] == ["Industrie"]
    assert card._report.categories[0].percent == D(100)
    assert card._assets_button.text() == "Actifs : 1/2…"


def test_including_everything_restores_the_default(qtbot: QtBot) -> None:
    card = make_card(qtbot)
    card.set_excluded({asset_key(FUND)})
    card.set_excluded(set())
    assert len(card._rows) == 2
    assert card._assets_button.text() == "Choisir les actifs…"


def test_exclusion_survives_a_refresh_of_the_assets(qtbot: QtBot) -> None:
    card = make_card(qtbot)
    card.set_excluded({asset_key(STOCK)})
    card.set_assets([STOCK, FUND])
    assert len(card._rows) == 1
    assert card.excluded() == {asset_key(STOCK)}


def test_excluding_everything_says_so(qtbot: QtBot) -> None:
    card = make_card(qtbot)
    card.set_excluded({asset_key(STOCK), asset_key(FUND)})
    assert not card._empty.isHidden()
    assert "exclus" in card._empty.text()


def test_dialog_reflects_and_returns_exclusions(qtbot: QtBot) -> None:
    dialog = ExposureAssetsDialog(None, [STOCK, FUND, COMMODITY], {asset_key(FUND)})
    qtbot.addWidget(dialog)
    assert dialog.excluded() == {asset_key(FUND)}
    dialog.set_all(False)
    assert dialog.excluded() == {asset_key(STOCK), asset_key(FUND), asset_key(COMMODITY)}
    dialog.set_all(True)
    assert dialog.excluded() == set()
