from __future__ import annotations

from pytestqt.qtbot import QtBot

from assets_tracker.domain.funds import ValuationSource
from assets_tracker.ui.funds_page import FundDialog


def test_fund_dialog_returns_a_valuation_source_enum(qtbot: QtBot) -> None:
    dialog = FundDialog(None)
    qtbot.addWidget(dialog)
    dialog._name.setText("Fonds Monde")
    dialog._identifier.setText("FR0010756098")

    _, _, _, details = dialog.form()

    assert details.valuation_source is ValuationSource.MANUAL
