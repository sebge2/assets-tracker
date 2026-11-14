from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from assets_tracker.db.connection import connect
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.domain.exposure import ExposureKind
from assets_tracker.domain.exposure_import import ExposureError, RawExposure
from assets_tracker.ui.exposure_import import ExposureImporter, fetch_all
from assets_tracker.ui.spinner import Spinner

D = Decimal


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0a" * 32)
    yield connection
    connection.close()


def add_asset(conn: Any) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO assets (symbol, name, currency) VALUES ('F', 'F', 'EUR')"
        )
    return int(cursor.lastrowid)


def raising(_: str) -> RawExposure | None:
    raise ExposureError("service indisponible")


def test_fetch_all_collects_exposure_errors() -> None:
    errors: list[str] = []
    assert fetch_all([(1, "A")], raising, errors) == {}
    assert errors == ["service indisponible"]


def test_importer_reports_failure_and_busy(conn: Any) -> None:
    asset = add_asset(conn)
    importer = ExposureImporter(ExposureRepository(conn), raising, background=False)
    busy: list[bool] = []
    failures: list[str] = []
    finished: list[tuple[int, bool]] = []
    importer.busy_changed.connect(busy.append)
    importer.failed.connect(failures.append)
    importer.manual_finished.connect(lambda a, f: finished.append((a, f)))
    importer.import_now(asset, "F")
    assert busy == [True, False]
    assert failures == ["service indisponible"]
    assert finished == []


def test_importer_uses_the_given_fetcher(conn: Any) -> None:
    asset = add_asset(conn)
    repository = ExposureRepository(conn)
    importer = ExposureImporter(repository, raising, background=False)
    importer.import_now(asset, "F", lambda _: RawExposure(sector_weights={"Energy": 1.0}))
    assert [(e.name, e.weight) for e in repository.get(asset, ExposureKind.SECTOR)] == [
        ("Énergie", D("100.0"))
    ]


def test_spinner_toggles(qtbot: QtBot) -> None:
    spinner = Spinner()
    qtbot.addWidget(spinner)
    spinner.set_running(True)
    assert spinner.running
    spinner.set_running(False)
    assert not spinner.running
