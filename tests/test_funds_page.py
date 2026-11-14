from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog, QLabel
from pytestqt.qtbot import QtBot

from assets_tracker.db.connection import connect
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.fund_repository import FundRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind
from assets_tracker.domain.funds import FundDetails, ValuationSource
from assets_tracker.domain.models import TransactionType
from assets_tracker.ui.exposure_dialog import ExposureDialog
from assets_tracker.ui.funds_page import FundDialog, FundsPage, NavDialog, TradeDialog

D = Decimal
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0)
NBSP = " "
NNBSP = " "


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x09" * 32)
    yield connection
    connection.close()


@pytest.fixture
def repository(conn: Any) -> FundRepository:
    return FundRepository(conn)


def seed(repository: FundRepository) -> None:
    """Deux fonds : l'un valorisé aujourd'hui, l'autre avec une VL vieille de 14 jours."""
    world = repository.create(
        "Actions Monde",
        "FR0010756098",
        "EUR",
        FundDetails("Assurance-vie", D("0.95"), ValuationSource.MANUAL, None),
    )
    bonds = repository.create(
        "Obligations Euro",
        "FR0000000002",
        "EUR",
        FundDetails("Compte-titres", D("0.35"), ValuationSource.MANUAL, None),
    )
    assert world.asset.id is not None and bonds.asset.id is not None
    repository.add_transaction(
        world.asset.id, TransactionType.BUY, date(2026, 1, 2), D(10), D(100), D(0)
    )
    repository.add_transaction(
        bonds.asset.id, TransactionType.BUY, date(2026, 3, 1), D(5), D(200), D(0)
    )
    repository.record_valuation(world.asset.id, TODAY, D(110), NOW)
    repository.record_valuation(bonds.asset.id, date(2026, 10, 30), D(210), NOW)


def make_page(qtbot: QtBot, repository: FundRepository) -> FundsPage:
    page = FundsPage(
        repository,
        exposure=ExposureRepository(repository._conn),
        today=lambda: TODAY,
        now=lambda: NOW,
    )
    qtbot.addWidget(page)
    page.resize(1200, 900)
    return page


@pytest.fixture
def page(qtbot: QtBot, repository: FundRepository) -> FundsPage:
    seed(repository)
    return make_page(qtbot, repository)


def test_fund_dialog_returns_a_valuation_source_enum(qtbot: QtBot) -> None:
    dialog = FundDialog(None)
    qtbot.addWidget(dialog)
    dialog._name.setText("Fonds Monde")
    dialog._identifier.setText("FR0010756098")

    _, _, _, details = dialog.form()

    assert details.valuation_source is ValuationSource.MANUAL


# --- page -------------------------------------------------------------------


def test_cards_summarize_the_funds(page: FundsPage) -> None:
    # 10 × 110 + 5 × 210 = 2 150 € pour 2 000 € investis
    assert page._value_card._value.text() == f"2{NNBSP}150,00{NBSP}€"
    assert page._value_card._sub.text() == "2 fonds"
    assert page._gain_card._value.text() == f"+150,00{NBSP}€"
    assert page._gain_card._sub.text() == f"+7,5{NBSP}%"
    assert page._invested_card._value.text() == f"2{NNBSP}000,00{NBSP}€"


def test_table_has_four_columns_and_no_weight(page: FundsPage) -> None:
    headers = [page._table.horizontalHeaderItem(c).text() for c in range(page._table.columnCount())]

    assert headers == ["Fonds", "Valeur liquidative", "Valeur", "+/− latente"]
    assert page._table.rowCount() == 2
    assert "Actions Monde" in page._table.cellWidget(0, 0).text()
    assert "FR0010756098" in page._table.cellWidget(0, 0).text()


def test_old_nav_is_flagged_in_the_table_and_the_alert(page: FundsPage) -> None:
    text = page._alert_text.text()

    assert not page._alert.isHidden()
    assert "Obligations Euro" in text
    assert "30/10/2026" in text
    assert "14 jours" in text
    assert "ancienne" in page._table.cellWidget(1, 1).text()
    assert "ancienne" not in page._table.cellWidget(0, 1).text()


def test_alert_disappears_once_every_nav_is_recent(
    page: FundsPage, repository: FundRepository
) -> None:
    bonds = repository.list_holdings()[1]
    assert bonds.asset.id is not None

    repository.record_valuation(bonds.asset.id, TODAY, D(212), NOW)
    page.refresh()

    assert page._alert.isHidden()


def test_a_fund_without_nav_is_reported_first(page: FundsPage, repository: FundRepository) -> None:
    fund = repository.create(
        "Fonds sans VL", "FR0000000003", "EUR", FundDetails("", None, ValuationSource.MANUAL, None)
    )
    assert fund.asset.id is not None
    repository.add_transaction(
        fund.asset.id, TransactionType.BUY, date(2026, 5, 1), D(1), D(50), D(0)
    )

    page.refresh()

    assert "Fonds sans VL" in page._alert_text.text()
    assert "pas encore de valeur liquidative" in page._alert_text.text()
    assert "1 autre fonds" in page._alert_text.text()
    # la position sans VL compte dans le montant investi mais pas dans la plus-value
    assert page._invested_card._value.text() == f"2{NNBSP}050,00{NBSP}€"
    assert page._gain_card._value.text() == f"+150,00{NBSP}€"
    assert "1 sans VL" in page._value_card._sub.text()


def test_a_sold_out_fund_is_not_reported_as_outdated(
    page: FundsPage, repository: FundRepository
) -> None:
    bonds = repository.list_holdings()[1]
    assert bonds.asset.id is not None
    repository.add_transaction(
        bonds.asset.id, TransactionType.SELL, date(2026, 11, 1), D(5), D(210), D(0)
    )

    page.refresh()

    assert page._alert.isHidden()
    page._table.setCurrentCell(1, 0)
    assert not page._redeem_button.isEnabled()


def test_selecting_a_row_shows_the_fund_detail(page: FundsPage) -> None:
    assert page._d_name.text() == "Actions Monde"
    assert page._d_value.text() == f"1{NNBSP}100,00{NBSP}€"
    assert page._d_gain.text() == f"+100,00{NBSP}€ · +10,0{NBSP}%"

    page._table.setCurrentCell(1, 0)

    assert page._d_name.text() == "Obligations Euro"
    assert "à mettre à jour" in page._d_source.text()
    assert page._tiles["Parts détenues"].text() == "5"
    assert page._tiles["PRU"].text() == f"200,00{NBSP}€"
    assert page._tiles["Valeur liquidative"].text() == f"210,00{NBSP}€"
    assert page._tiles["Montant investi"].text() == f"1{NNBSP}000,00{NBSP}€"
    assert page._tiles["Frais courants (TER)"].text() == f"0,35{NBSP}%"
    assert page._tiles["Date de la VL"].text() == "30/10/2026"


def test_detail_lists_the_latest_operations_first(
    page: FundsPage, repository: FundRepository
) -> None:
    fund = repository.list_holdings()[0]
    assert fund.asset.id is not None
    repository.add_transaction(
        fund.asset.id, TransactionType.SELL, date(2026, 6, 1), D(2), D(105), D(0)
    )

    page.refresh()

    assert page._operations.count() == 2
    newest = page._operations.itemAt(0).widget()
    assert newest is not None
    assert newest.findChildren(QLabel)[0].text() == "Rachat"


def test_empty_page_invites_to_create_a_fund(qtbot: QtBot, repository: FundRepository) -> None:
    page = make_page(qtbot, repository)

    assert not page._empty.isHidden()
    assert page._table.isHidden()
    assert page._detail.isHidden()
    assert page._alert.isHidden()
    assert page._value_card._value.text() == f"0,00{NBSP}€"


def test_trade_flow_records_the_operation_and_the_nav(
    page: FundsPage, repository: FundRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: TradeDialog) -> QDialog.DialogCode:
        self._quantity.setText("2")
        self._nav.setText("120")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TradeDialog, "exec", fill)

    page._trade(TransactionType.BUY)

    holding = repository.list_holdings()[0]
    assert holding.position.quantity == D(12)
    assert holding.valuation is not None and holding.valuation.value == D(120)
    assert page._d_value.text() == f"1{NNBSP}440,00{NBSP}€"  # 12 parts × 120 €


def test_nav_update_clears_the_alert(
    page: FundsPage, repository: FundRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: NavDialog) -> QDialog.DialogCode:
        self._nav.setText("215,50")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(NavDialog, "exec", fill)
    page._table.setCurrentCell(1, 0)

    page._update_nav(page._selected_view())

    bonds = repository.list_holdings()[1]
    assert bonds.valuation is not None
    assert (bonds.valuation.valuation_date, bonds.valuation.value) == (TODAY, D("215.50"))
    assert page._alert.isHidden()


# --- dialogues --------------------------------------------------------------


def test_subscription_summary_adds_the_fees_to_the_amount_debited(
    qtbot: QtBot, page: FundsPage, repository: FundRepository
) -> None:
    dialog = TradeDialog(None, repository.list_holdings()[0], TransactionType.BUY, TODAY)
    qtbot.addWidget(dialog)

    dialog._quantity.setText("10")
    dialog._fee.setText("0,5")

    assert dialog._nav.text() == "110,00"  # préremplie avec la dernière VL
    assert dialog.summary_rows() == [
        ("Montant brut", f"1{NNBSP}100,00{NBSP}€"),
        ("Frais d'entrée", f"+5,50{NBSP}€"),
        ("Total débité", f"1{NNBSP}105,50{NBSP}€"),
    ]
    assert dialog._after.text().startswith("Après opération : 20 parts")


def test_redemption_summary_shows_the_realized_gain(
    qtbot: QtBot, page: FundsPage, repository: FundRepository
) -> None:
    dialog = TradeDialog(None, repository.list_holdings()[0], TransactionType.SELL, TODAY)
    qtbot.addWidget(dialog)

    dialog._quantity.setText("4")
    dialog._nav.setText("120")

    assert dialog.summary_rows() == [
        ("Montant brut", f"480,00{NBSP}€"),
        ("Frais de sortie", f"0,00{NBSP}€"),
        ("Montant crédité", f"480,00{NBSP}€"),
        ("Plus-value réalisée", f"+80,00{NBSP}€"),  # 4 × (120 − PRU 100)
    ]
    assert dialog._after.text() == f"Après opération : 6 parts · PRU 100,00{NBSP}€"


def test_redeeming_more_than_held_is_refused(
    qtbot: QtBot, page: FundsPage, repository: FundRepository
) -> None:
    dialog = TradeDialog(None, repository.list_holdings()[0], TransactionType.SELL, TODAY)
    qtbot.addWidget(dialog)

    dialog._quantity.setText("11")

    assert dialog._error.text() == "Vente supérieure à la quantité détenue"
    assert not dialog._save.isEnabled()


def test_trade_dialog_switches_between_subscription_and_redemption(
    qtbot: QtBot, page: FundsPage, repository: FundRepository
) -> None:
    dialog = TradeDialog(None, repository.list_holdings()[0], TransactionType.BUY, TODAY)
    qtbot.addWidget(dialog)
    assert dialog._title.text() == "Souscrire des parts"
    assert len(dialog.summary_rows()) == 3

    dialog._kind_buttons[TransactionType.SELL].click()

    assert dialog._title.text() == "Racheter des parts"
    assert dialog._save.text() == "Enregistrer le rachat"
    assert len(dialog.summary_rows()) == 4


def test_redemption_is_unavailable_without_parts(
    qtbot: QtBot, repository: FundRepository
) -> None:
    fund = repository.create(
        "Fonds vide", "FR0000000009", "EUR", FundDetails("", None, ValuationSource.MANUAL, None)
    )
    dialog = TradeDialog(None, fund, TransactionType.BUY, TODAY)
    qtbot.addWidget(dialog)

    assert not dialog._kind_buttons[TransactionType.SELL].isEnabled()


def test_amount_entry_is_converted_to_parts(
    qtbot: QtBot, page: FundsPage, repository: FundRepository
) -> None:
    dialog = TradeDialog(None, repository.list_holdings()[0], TransactionType.BUY, TODAY)
    qtbot.addWidget(dialog)

    dialog._mode_buttons[True].click()
    dialog._quantity.setText("1 100")

    form = dialog.form()
    assert form.quantity == D("10.0000")
    assert form.kind is TransactionType.BUY
    assert "≈ 10 parts" in dialog._quantity_hint.text()


def test_nav_is_recorded_for_manual_funds_only_and_not_in_the_future(
    qtbot: QtBot, page: FundsPage, repository: FundRepository
) -> None:
    manual = TradeDialog(None, repository.list_holdings()[0], TransactionType.BUY, TODAY)
    qtbot.addWidget(manual)
    manual._quantity.setText("1")
    assert manual.form().record_nav is True

    manual._date.setDate(QDate(2026, 11, 20))
    assert manual.form().record_nav is False

    yahoo = repository.create(
        "Fonds Yahoo", "FR0000000004", "EUR", FundDetails("", None, ValuationSource.YAHOO, "CW8.PA")
    )
    auto = TradeDialog(None, yahoo, TransactionType.BUY, TODAY)
    qtbot.addWidget(auto)
    assert auto._record.isChecked() is False


def test_nav_dialog_returns_the_date_and_value(
    qtbot: QtBot, page: FundsPage, repository: FundRepository
) -> None:
    dialog = NavDialog(None, repository.list_holdings()[1], TODAY)
    qtbot.addWidget(dialog)
    dialog._nav.setText("215,50")

    assert dialog.form() == (TODAY, D("215.50"))

    dialog._date.setDate(QDate(2026, 11, 14))
    with pytest.raises(ValueError):
        dialog.form()


def test_edit_exposure_weights_the_summary_by_fund_value(
    page: FundsPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = ExposureRepository(page._repository._conn)
    world, bonds = page._views[0].asset_id, page._views[1].asset_id
    repository.replace(world, [ExposureEntry("Technologie", D(100))], [])
    repository.replace(bonds, [ExposureEntry("États", D(100))], [])

    def fill(self: ExposureDialog) -> QDialog.DialogCode:
        self._sectors.set_entries(
            [ExposureEntry("Technologie", D(50)), ExposureEntry("Santé", D(50))]
        )
        self._zones.set_entries([ExposureEntry("Monde", D(100))])
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExposureDialog, "exec", fill)
    page._selected = world

    page._edit_exposure(page._selected_view())

    assert page._tiles["Zone principale"].text() == f"Monde · 100{NBSP}%"
    legend = page._exposure_card.legend_text(ExposureKind.SECTOR)
    assert "Technologie" in legend and "Santé" in legend and "États" in legend
