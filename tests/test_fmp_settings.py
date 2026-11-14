from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pytestqt.qtbot import QtBot
from PySide6.QtWidgets import QMessageBox

from assets_tracker.db.connection import connect
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.settings_repository import FMP_API_KEY, SettingsRepository
from assets_tracker.domain.exposure import ExposureKind
from assets_tracker.domain.exposure_import import ExposureError, RawExposure, translate
from assets_tracker.market import fmp
from assets_tracker.ui.exposure_import import ExposureImporter, fetch_all
from assets_tracker.ui.settings_page import SettingsPage
from assets_tracker.ui.spinner import Spinner

D = Decimal


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0a" * 32)
    yield connection
    connection.close()


# -- table de configuration --


def test_settings_roundtrip_and_replace(conn: Any) -> None:
    settings = SettingsRepository(conn)
    assert settings.get(FMP_API_KEY) is None
    settings.set(FMP_API_KEY, "  abc  ")
    assert settings.get(FMP_API_KEY) == "abc"
    settings.set(FMP_API_KEY, "def")
    assert settings.all() == {FMP_API_KEY: "def"}


def test_settings_empty_value_deletes(conn: Any) -> None:
    settings = SettingsRepository(conn)
    settings.set("k", "v")
    settings.set("k", "   ")
    assert settings.get("k") is None


def test_settings_rejects_empty_key(conn: Any) -> None:
    with pytest.raises(ValueError):
        SettingsRepository(conn).set(" ", "v")


# -- client FMP (sans réseau) --


def test_percent_accepts_numbers_and_strings() -> None:
    assert fmp._percent("97.26%") == 97.26
    assert fmp._percent(12) == 12.0
    assert fmp._percent("n/a") == 0.0


def test_weights_sum_duplicates_and_skip_invalid() -> None:
    rows = [
        {"sector": "Technology", "weightPercentage": "30%"},
        {"sector": "Technology", "weightPercentage": 5},
        {"sector": "Energy", "weightPercentage": 0},
        {"weightPercentage": 9},
    ]
    assert fmp._weights(rows, "sector") == {"Technology": 35.0}
    assert fmp._weights({"Error Message": "x"}, "sector") == {}


def test_error_message_hides_nothing_sensitive_and_is_readable() -> None:
    text = fmp._error_message(401, json.dumps({"Error Message": "Invalid API KEY."}))
    assert "refusée" in text and "Invalid API KEY." in text
    assert "limite" in fmp._error_message(429, "")


def test_missing_key_raises_without_network() -> None:
    with pytest.raises(ExposureError):
        fmp.fetch_fund_exposure("SPY", "  ")


def test_fetch_fund_exposure_builds_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(path: str, params: dict[str, str], key: str) -> Any:
        assert key == "K" and params == {"symbol": "SPY"}
        if path.endswith("sector-weightings"):
            return [{"sector": "Technology", "weightPercentage": 60}]
        return [{"country": "United States", "weightPercentage": "100%"}]

    monkeypatch.setattr(fmp, "_get", fake_get)
    raw = fmp.fetch_fund_exposure("SPY", "K")
    assert raw == RawExposure(
        sector_weights={"Technology": 60.0}, country_weights={"United States": 100.0}
    )


def test_fetch_fund_exposure_none_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fmp, "_get", lambda *a, **k: [])
    assert fmp.fetch_fund_exposure("X", "K") is None


def test_translate_groups_countries_and_unknown_into_other() -> None:
    raw = RawExposure(
        country_weights={"United States": 50.0, "Canada": 10.0, "France": 30.0, "Narnia": 10.0}
    )
    zones = {z.name: z.weight for z in translate(raw).zones}
    assert zones == {
        "États-Unis": D("50.0"),
        "France": D("30.0"),
        "Amérique du Nord": D("10.0"),
        "Autre": D("10.0"),
    }


# -- import : erreurs et état d'attente --


def add_asset(conn: Any) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO assets (symbol, name, currency) VALUES ('F', 'F', 'EUR')"
        )
    return int(cursor.lastrowid)


def raising(_: str) -> RawExposure | None:
    raise ExposureError("clé refusée")


def test_fetch_all_collects_exposure_errors() -> None:
    errors: list[str] = []
    assert fetch_all([(1, "A")], raising, errors) == {}
    assert errors == ["clé refusée"]


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
    assert failures == ["clé refusée"]
    assert finished == []


def test_importer_uses_the_given_fetcher(conn: Any) -> None:
    asset = add_asset(conn)
    repository = ExposureRepository(conn)
    importer = ExposureImporter(repository, raising, background=False)
    importer.import_now(asset, "F", lambda _: RawExposure(sector_weights={"Energy": 1.0}))
    assert [(e.name, e.weight) for e in repository.get(asset, ExposureKind.SECTOR)] == [
        ("Énergie", D("100.0"))
    ]


# -- écran Paramètres --


def test_spinner_toggles(qtbot: QtBot) -> None:
    spinner = Spinner()
    qtbot.addWidget(spinner)
    spinner.set_running(True)
    assert spinner.running
    spinner.set_running(False)
    assert not spinner.running


def test_settings_page_saves_and_clears_key(qtbot: QtBot, conn: Any) -> None:
    settings = SettingsRepository(conn)
    page = SettingsPage(settings, background=False)
    qtbot.addWidget(page)
    page._key.setText("secret")
    page._save()
    assert settings.get(FMP_API_KEY) == "secret"
    page._clear()
    assert settings.get(FMP_API_KEY) is None
    assert page._key.text() == ""


def test_settings_page_test_failure_shows_popup(
    qtbot: QtBot, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda parent, title, text: shown.append(text)
    )
    page = SettingsPage(SettingsRepository(conn), key_checker=raising_check, background=False)
    qtbot.addWidget(page)
    page._key.setText("bad")
    page._test()
    assert shown == ["clé refusée"]
    assert not page._spinner.running


def raising_check(_: str) -> None:
    raise ExposureError("clé refusée")


def test_settings_page_test_success(qtbot: QtBot, conn: Any) -> None:
    page = SettingsPage(SettingsRepository(conn), key_checker=lambda _: None, background=False)
    qtbot.addWidget(page)
    page._key.setText("good")
    page._test()
    assert page._status.text() == "Clé valide."


# -- actions : Yahoo d'abord, FMP en secours --


def test_stock_profile_maps_iso_country(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fmp, "_get", lambda *a, **k: [{"sector": "Technology", "country": "FR"}]
    )
    assert fmp.fetch_stock_exposure("X", "K") == RawExposure(sector="Technology", country="France")


def test_stock_profile_empty_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fmp, "_get", lambda *a, **k: [{"sector": "", "country": None}])
    assert fmp.fetch_stock_exposure("X", "K") is None


def test_fallback_not_called_when_yahoo_has_data() -> None:
    calls: list[str] = []

    def fallback(symbol: str, key: str) -> RawExposure | None:
        calls.append(symbol)
        return None

    yahoo = RawExposure(sector="Energy")
    result = fmp.fetch_with_fallback("X", primary=lambda _: yahoo, api_key="K", fallback=fallback)
    assert result is yahoo and calls == []


def test_fallback_used_when_yahoo_has_nothing() -> None:
    fmp_data = RawExposure(sector="Utilities", country="Spain")
    result = fmp.fetch_with_fallback(
        "X", primary=lambda _: None, api_key="K", fallback=lambda s, k: fmp_data
    )
    assert result is fmp_data


def test_fallback_error_propagates() -> None:
    def boom(symbol: str, key: str) -> RawExposure | None:
        raise ExposureError("quota")

    with pytest.raises(ExposureError):
        fmp.fetch_with_fallback("X", primary=lambda _: None, api_key="K", fallback=boom)
