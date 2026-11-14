from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind
from assets_tracker.domain.exposure_import import (
    RawExposure,
    normalize,
    sector_name,
    translate,
    zone_name,
)
from assets_tracker.ui.exposure_import import ExposureImporter, fetch_all

D = Decimal


def names(entries: list[ExposureEntry]) -> list[tuple[str, Decimal]]:
    return [(e.name, e.weight) for e in entries]


# -- logique pure --


def test_normalize_scales_to_one_hundred_and_ranks() -> None:
    entries = normalize({"B": D(1), "A": D(3)})
    assert names(entries) == [("A", D("75.0")), ("B", D("25.0"))]


def test_normalize_rounding_goes_to_the_largest_share() -> None:
    entries = normalize({"A": D(1), "B": D(1), "C": D(1)})
    assert names(entries) == [("A", D("33.4")), ("B", D("33.3")), ("C", D("33.3"))]
    assert sum(e.weight for e in entries) == D(100)


def test_normalize_groups_tiny_shares_into_other_last() -> None:
    entries = normalize({"Technologie": D(995), "Finance": D(4), "Santé": D(1)})
    assert names(entries) == [("Technologie", D("99.5")), ("Autre", D("0.5"))]


def test_normalize_ignores_non_positive_and_empty() -> None:
    assert normalize({}) == []
    assert normalize({"A": D(0), "B": D(-2)}) == []
    assert names(normalize({"A": D(2), "B": D(0)})) == [("A", D("100.0"))]


def test_name_tables() -> None:
    assert sector_name("Financial Services") == "Finance"
    assert sector_name("realestate") == "Immobilier"
    assert sector_name("Mystery") is None
    assert zone_name("United States") == "États-Unis"
    assert zone_name("Germany") == "Europe"
    assert zone_name("France") == "France"
    assert zone_name("Atlantis") is None


def test_translate_a_stock() -> None:
    result = translate(RawExposure(sector="Technology", country="United States"))
    assert names(result.sectors) == [("Technologie", D("100.0"))]
    assert names(result.zones) == [("États-Unis", D("100.0"))]


def test_translate_a_stock_with_unknown_names_leaves_them_unfilled() -> None:
    result = translate(RawExposure(sector="Mystery", country="Atlantis"))
    assert not result and result.sectors == [] and result.zones == []


def test_translate_a_fund_merges_sectors_and_has_no_zones() -> None:
    weights = {
        "technology": 0.3,
        "financial_services": 0.2,
        "healthcare": 0.1,
        "consumer_cyclical": 0.2,
        "consumer_defensive": 0.2,
    }
    result = translate(RawExposure(sector_weights=weights))
    assert names(result.sectors) == [
        ("Consommation", D("40.0")),
        ("Technologie", D("30.0")),
        ("Finance", D("20.0")),
        ("Santé", D("10.0")),
    ]
    assert result.zones == []


def test_translate_a_fund_with_an_unknown_sector_uses_other() -> None:
    result = translate(RawExposure(sector_weights={"technology": 0.5, "weird": 0.5}))
    assert names(result.sectors) == [("Technologie", D("50.0")), ("Autre", D("50.0"))]


def test_fetch_all_skips_failures() -> None:
    def fetcher(symbol: str) -> RawExposure | None:
        if symbol == "BOOM":
            raise RuntimeError("réseau")
        return RawExposure(sector="Energy", country="France") if symbol == "OK" else None

    found = fetch_all([(1, "OK"), (2, "NONE"), (3, "BOOM")], fetcher)
    assert list(found) == [1]
    assert names(found[1].sectors) == [("Énergie", D("100.0"))]


# -- importateur --


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "import.db", key_provider=lambda: b"\x0c" * 32)
    yield connection
    connection.close()


def add_asset(conn: Any, symbol: str) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO assets (symbol, name, currency) VALUES (?, ?, 'EUR')", (symbol, symbol)
        )
    return int(cursor.lastrowid)


class Fetcher:
    def __init__(self, data: dict[str, RawExposure | None]) -> None:
        self.data = data
        self.calls: list[str] = []

    def __call__(self, symbol: str) -> RawExposure | None:
        self.calls.append(symbol)
        return self.data.get(symbol)


def make(conn: Any, data: dict[str, RawExposure | None]) -> tuple[ExposureImporter, Fetcher]:
    fetcher = Fetcher(data)
    return ExposureImporter(ExposureRepository(conn), fetcher, background=False), fetcher


def test_import_now_replaces_what_yahoo_provides_and_reports(conn: Any) -> None:
    asset = add_asset(conn, "MC.PA")
    repository = ExposureRepository(conn)
    repository.set(asset, ExposureKind.SECTOR, [ExposureEntry("Ancien", D(100))])
    importer, _ = make(conn, {"MC.PA": RawExposure(sector="Consumer Cyclical", country="France")})
    outcome: list[tuple[int, bool]] = []
    importer.manual_finished.connect(lambda a, found: outcome.append((a, found)))

    importer.import_now(asset, "MC.PA")

    assert names(repository.get(asset, ExposureKind.SECTOR)) == [("Consommation", D("100.0"))]
    assert names(repository.get(asset, ExposureKind.ZONE)) == [("France", D("100.0"))]
    assert outcome == [(asset, True)]


def test_import_now_keeps_data_when_yahoo_has_nothing(conn: Any) -> None:
    asset = add_asset(conn, "NOPE")
    repository = ExposureRepository(conn)
    repository.set(asset, ExposureKind.ZONE, [ExposureEntry("France", D(100))])
    importer, _ = make(conn, {})
    outcome: list[tuple[int, bool]] = []
    importer.manual_finished.connect(lambda a, found: outcome.append((a, found)))

    importer.import_now(asset, "NOPE")

    assert names(repository.get(asset, ExposureKind.ZONE)) == [("France", D(100))]
    assert outcome == [(asset, False)]


def test_import_now_keeps_the_kind_yahoo_has_nothing_for(conn: Any) -> None:
    asset = add_asset(conn, "FUND")
    repository = ExposureRepository(conn)
    repository.set(asset, ExposureKind.ZONE, [ExposureEntry("Monde", D(100))])
    importer, _ = make(conn, {"FUND": RawExposure(sector_weights={"technology": 1.0})})

    importer.import_now(asset, "FUND")

    assert names(repository.get(asset, ExposureKind.SECTOR)) == [("Technologie", D("100.0"))]
    assert names(repository.get(asset, ExposureKind.ZONE)) == [("Monde", D(100))]


def test_nothing_is_imported_without_a_request(conn: Any) -> None:
    asset = add_asset(conn, "AAPL")
    _, fetcher = make(conn, {"AAPL": RawExposure(sector="Technology", country="France")})
    assert fetcher.calls == []
    assert ExposureRepository(conn).get(asset, ExposureKind.SECTOR) == []
