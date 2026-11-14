from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.domain.exposure import (
    NOT_APPLICABLE,
    UNCLASSIFIED,
    ExposedAsset,
    ExposureEntry,
    ExposureKind,
    exposure_breakdown,
    exposure_report,
    main_exposure,
    validate_entries,
)
from assets_tracker.domain.models import InstrumentType

D = Decimal
SECTOR = ExposureKind.SECTOR
ZONE = ExposureKind.ZONE


def entries(*parts: tuple[str, str]) -> list[ExposureEntry]:
    return [ExposureEntry(name, D(weight)) for name, weight in parts]


def test_validate_accepts_an_empty_or_complete_breakdown() -> None:
    assert validate_entries(SECTOR, []) == []
    cleaned = validate_entries(SECTOR, entries((" Technologie ", "60.5"), ("Santé", "39.5")))
    assert [e.name for e in cleaned] == ["Technologie", "Santé"]


def test_validate_rejects_bad_totals_names_and_weights() -> None:
    with pytest.raises(ValueError, match="100"):
        validate_entries(SECTOR, entries(("Technologie", "60"), ("Santé", "30")))
    with pytest.raises(ValueError, match="100"):
        validate_entries(ZONE, entries(("Europe", "60"), ("Asie", "50")))
    with pytest.raises(ValueError, match="deux fois"):
        validate_entries(SECTOR, entries(("Santé", "50"), ("santé", "50")))
    with pytest.raises(ValueError, match="obligatoire"):
        validate_entries(SECTOR, entries((" ", "100")))
    with pytest.raises(ValueError, match="entre 0 et 100"):
        validate_entries(SECTOR, entries(("Santé", "0"), ("Finance", "100")))


def test_main_exposure_is_the_biggest_part() -> None:
    assert main_exposure([]) is None
    assert main_exposure(entries(("A", "30"), ("B", "70"))) == "B"


def test_breakdown_weights_each_asset_by_its_value() -> None:
    parts = exposure_breakdown(
        [
            (D(1000), entries(("Technologie", "60"), ("Santé", "40"))),
            (D(500), entries(("Santé", "100"))),
            (D(500), []),
            (D(0), entries(("Ignoré", "100"))),
        ]
    )

    assert [(name, value) for name, value, _ in parts] == [
        ("Santé", D(900)),
        ("Technologie", D(600)),
        (UNCLASSIFIED, D(500)),
    ]
    assert [pct for _, _, pct in parts] == [D(45), D(30), D(25)]
    assert exposure_breakdown([]) == []


def test_breakdown_merges_names_regardless_of_case() -> None:
    parts = exposure_breakdown(
        [(D(100), entries(("Europe", "100"))), (D(100), entries(("europe", "100")))]
    )
    assert parts == [("Europe", D(200), D(100))]


def exposed(
    name: str,
    instrument: InstrumentType,
    value: int,
    sectors: list[ExposureEntry] | None = None,
    zones: list[ExposureEntry] | None = None,
) -> ExposedAsset:
    return ExposedAsset(name, instrument, D(value), tuple(sectors or []), tuple(zones or []))


PORTFOLIO = [
    exposed("Apple", InstrumentType.STOCK, 3000, entries(("Technologie", "100")),
            entries(("Amérique du Nord", "60"), ("Europe", "40"))),
    exposed("Fonds monde", InstrumentType.FUND, 1000,
            entries(("Technologie", "50"), ("Santé", "50")), entries(("Europe", "100"))),
    exposed("Immobilier", InstrumentType.FUND, 500),
    exposed("Comptes cash", InstrumentType.CASH, 1500),
]  # fmt: skip


def test_report_splits_each_asset_and_ranks_special_buckets_last() -> None:
    report = exposure_report(PORTFOLIO, SECTOR)

    assert [(c.name, c.value) for c in report.categories] == [
        ("Technologie", D(3500)),
        ("Liquidités", D(1500)),
        ("Santé", D(500)),
        (UNCLASSIFIED, D(500)),
    ]
    assert report.total == D(6000)
    technologie = report.categories[0]
    assert technologie.by_type == {InstrumentType.STOCK: D(3000), InstrumentType.FUND: D(500)}
    assert [(c.asset.name, c.amount, c.missing) for c in technologie.contributions] == [
        ("Apple", D(3000), False),
        ("Fonds monde", D(500), False),
    ]
    assert report.categories[-1].contributions[0].missing is True
    assert [a.name for a in report.missing] == ["Immobilier"]


def test_report_coverage_ignores_not_applicable_value() -> None:
    sectors = exposure_report(PORTFOLIO, SECTOR)
    zones = exposure_report(PORTFOLIO, ZONE)

    assert sectors.coverage == D(5500) / D(6000) * 100
    # le cash n'a pas de zone : il sort du dénominateur (4 500 € classables)
    assert zones.coverage == D(4000) / D(4500) * 100
    assert [c.name for c in zones.categories][-2:] == [UNCLASSIFIED, NOT_APPLICABLE]
    assert zones.categories[-1].value == D(1500)


def test_report_can_be_limited_to_one_instrument_type() -> None:
    report = exposure_report(PORTFOLIO, SECTOR, InstrumentType.FUND)

    third = D(500) / D(1500) * 100
    assert [(c.name, c.percent) for c in report.categories] == [
        ("Santé", third),
        ("Technologie", third),
        (UNCLASSIFIED, third),
    ]
    assert report.coverage == D(1000) / D(1500) * 100
    assert exposure_report([], SECTOR).categories == []
    assert exposure_report([], SECTOR).coverage is None
    only_cash = exposure_report(PORTFOLIO, ZONE, InstrumentType.CASH)
    assert only_cash.coverage is None and only_cash.categories[0].name == NOT_APPLICABLE


# --- base ---------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0a" * 32)
    yield connection
    connection.close()


@pytest.fixture
def asset_id(conn: Any) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO assets (symbol, name, currency) VALUES ('AI.PA', 'Air Liquide', 'EUR')"
        )
    return int(cursor.lastrowid)


def test_replace_then_get_round_trips_sorted_by_weight(conn: Any, asset_id: int) -> None:
    repository = ExposureRepository(conn)

    repository.replace(
        asset_id,
        entries(("Santé", "30.5"), ("Technologie", "69.5")),
        entries(("Europe", "100")),
    )

    assert repository.get(asset_id, SECTOR) == entries(("Technologie", "69.5"), ("Santé", "30.5"))
    assert repository.get(asset_id, ZONE) == entries(("Europe", "100"))
    assert repository.all_for([asset_id, 999], SECTOR)[999] == []


def test_replace_is_atomic_when_one_kind_is_invalid(conn: Any, asset_id: int) -> None:
    repository = ExposureRepository(conn)
    repository.replace(asset_id, entries(("Santé", "100")), entries(("Europe", "100")))

    with pytest.raises(ValueError):
        repository.replace(asset_id, entries(("Finance", "100")), entries(("Asie", "40")))

    assert repository.get(asset_id, SECTOR) == entries(("Santé", "100"))
    assert repository.get(asset_id, ZONE) == entries(("Europe", "100"))


def test_set_one_kind_leaves_the_other_and_an_empty_list_clears(conn: Any, asset_id: int) -> None:
    repository = ExposureRepository(conn)
    repository.replace(asset_id, entries(("Santé", "100")), entries(("Europe", "100")))

    repository.set(asset_id, SECTOR, [])

    assert repository.get(asset_id, SECTOR) == []
    assert repository.get(asset_id, ZONE) == entries(("Europe", "100"))


def test_unknown_asset_is_refused_and_deleting_an_asset_removes_its_exposures(
    conn: Any, asset_id: int
) -> None:
    repository = ExposureRepository(conn)
    with pytest.raises(ValueError, match="introuvable"):
        repository.replace(999, entries(("Santé", "100")), [])

    repository.replace(asset_id, entries(("Santé", "100")), [])
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    assert repository.get(asset_id, SECTOR) == []
