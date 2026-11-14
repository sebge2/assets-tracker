from __future__ import annotations

from decimal import Decimal

import pytest
from pytestqt.qtbot import QtBot
from PySide6.QtWidgets import QMessageBox

from assets_tracker.domain.exposure_import import ExposureError
from assets_tracker.ui.quote_import import YahooFetchButton, price_text, run_fetch

D = Decimal


def test_price_text_is_compact_and_plain() -> None:
    assert price_text(D("12.910000")) == "12.91"
    assert price_text(D("100")) == "100"
    assert price_text(D("11.69123456")) == "11.6912"


def test_run_fetch_outcomes() -> None:
    assert run_fetch(lambda: D("1")) == (D("1"), None)
    assert run_fetch(lambda: None) == (None, None)

    def boom() -> Decimal:
        raise ExposureError("quota")

    assert run_fetch(boom) == (None, "quota")


def test_button_emits_price_and_stops_spinner(qtbot: QtBot) -> None:
    button = YahooFetchButton(lambda: D("12.91"), background=False)
    qtbot.addWidget(button)
    received: list[object] = []
    button.fetched.connect(received.append)
    button.start()
    assert received == [D("12.91")]
    assert not button.busy


def test_button_warns_when_yahoo_has_nothing(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: shown.append(text))
    button = YahooFetchButton(lambda: None, background=False)
    qtbot.addWidget(button)
    received: list[object] = []
    button.fetched.connect(received.append)
    button.start()
    assert received == [] and len(shown) == 1


def test_button_shows_error_popup(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: shown.append(text))

    def boom() -> Decimal:
        raise ExposureError("réseau")

    button = YahooFetchButton(boom, background=False)
    qtbot.addWidget(button)
    button.start()
    assert shown == ["réseau"]
