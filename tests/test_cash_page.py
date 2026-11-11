from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton
from pytestqt.qtbot import QtBot

from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.db.connection import connect
from assets_tracker.domain.cash import (
    CashAccount,
    InterestFrequency,
    MovementKind,
)
from assets_tracker.ui.cash_dialogs import AccountDialog, AccountForm, MovementDialog, MovementForm
from assets_tracker.ui.cash_page import CashPage
from assets_tracker.ui.rate_chart import RateChart

D = Decimal
TODAY = date(2026, 11, 11)
LIVRET_IBAN = "FR7630006000011234567890189"
AVENIR_IBAN = "GB82WEST12345698765432"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x02" * 32)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn: Any) -> CashRepository:
    repository = CashRepository(conn)
    livret = repository.create_account(
        CashAccount(
            None,
            "Livret A",
            "Crédit Mutuel",
            LIVRET_IBAN,
            "EUR",
            date(2024, 1, 1),
            InterestFrequency.ANNUAL,
            12,
            31,
        ),
        D("3.00"),
    )
    assert livret.id is not None
    repository.add_movement(livret.id, MovementKind.DEPOSIT, date(2026, 5, 6), D("2000"))
    avenir = repository.create_account(
        CashAccount(
            None,
            "Livret Avenir",
            "Boursorama",
            AVENIR_IBAN,
            "EUR",
            date(2024, 6, 1),
            InterestFrequency.MONTHLY,
        ),
        D("1.50"),
        bonus=(D("1.50"), 12),
    )
    assert avenir.id is not None
    repository.add_movement(avenir.id, MovementKind.DEPOSIT, date(2025, 10, 12), D("10000"))
    repository.add_movement(avenir.id, MovementKind.INTEREST, date(2026, 11, 1), D("25"))
    return repository


@pytest.fixture
def page(qtbot: QtBot, repo: CashRepository) -> CashPage:
    widget = CashPage(repo, today=lambda: TODAY)
    qtbot.addWidget(widget)
    widget.resize(1100, 800)
    widget.show()
    return widget


def click_cell(qtbot: QtBot, page: CashPage, row: int, column: int) -> None:
    table = page._list._table
    rect = table.visualRect(table.model().index(row, column))
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_list_shows_accounts_with_masked_iban_and_computed_figures(page: CashPage) -> None:
    table = page._list._table
    assert table.rowCount() == 2
    assert table.item(0, 1).text() == "FR76 •••• •••• 0189"
    assert "2 000,00" in table.item(0, 4).text()
    assert "+60,00" in table.item(0, 5).text()  # 3 % de 2 000 €
    # Livret Avenir : 1,5 % de base + 1,5 % de prime (condition remplie depuis 2025-06-01)
    assert "3,00" in table.cellWidget(1, 2).text()
    assert "prime" in table.cellWidget(1, 2).text()
    assert "+300,75" in table.item(1, 5).text()


def test_summary_cards_total_per_currency(page: CashPage) -> None:
    total = page._list._total._value.text()
    assert total.startswith("12") and total.endswith("025,00\u00a0€")  # 2 000 € + 10 025 €
    assert "+360,75" in page._list._interest._value.text()
    assert page._list._average._value.text().startswith("3,00")  # (2000×3 + 10025×3) / 12025


def test_clicking_a_row_opens_the_account_and_back_returns(qtbot: QtBot, page: CashPage) -> None:
    click_cell(qtbot, page, 0, 1)

    assert page._stack.currentWidget() is page._detail
    assert page._detail._title.text() == "Livret A"
    assert page._detail._movements.rowCount() == 1

    page._detail.backRequested.emit()
    assert page._stack.currentWidget() is page._list


def test_detail_has_movement_rate_and_bonus_histories(qtbot: QtBot, page: CashPage) -> None:
    click_cell(qtbot, page, 1, 1)
    detail = page._detail

    assert detail._movements.rowCount() == 2  # intérêt et dépôt
    assert detail._rates.rowCount() == 1  # un seul taux de base : celui d'ouverture
    assert detail._bonuses.rowCount() == 2  # définie puis active
    assert detail._bonus_empty.isHidden()
    # Les lignes les plus récentes sont en premier.
    assert detail._bonuses.item(0, 2).text() == "Active"
    assert detail._bonuses.item(1, 2).text() == "Définie"


def test_account_without_bonus_shows_the_empty_state(qtbot: QtBot, page: CashPage) -> None:
    click_cell(qtbot, page, 0, 1)
    assert not page._detail._bonus_empty.isHidden()
    assert page._detail._bonuses.isHidden()


def test_movement_filters(qtbot: QtBot, page: CashPage) -> None:
    click_cell(qtbot, page, 1, 1)
    detail = page._detail
    detail._set_filter("out")
    assert detail._movements.rowCount() == 0
    detail._set_filter("in")
    assert detail._movements.rowCount() == 2
    detail._set_filter("all")
    assert detail._movements.rowCount() == 2


def test_edit_icon_opens_the_dialog_without_opening_the_account(
    qtbot: QtBot, page: CashPage, repo: CashRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = repo.list_accounts()[0]
    monkeypatch.setattr(AccountDialog, "exec", lambda self: AccountDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        AccountDialog,
        "form",
        lambda self: AccountForm(account, D("2.50"), date(2026, 11, 11), None),
    )

    button = page._list._table.cellWidget(0, 6).findChild(QToolButton)
    assert button.accessibleName() == "Modifier le compte Livret A"
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    assert page._stack.currentWidget() is page._list
    assert account.id is not None
    assert [r.rate for r in repo.base_rates(account.id)] == [D("3.00"), D("2.50")]


def test_new_movement_is_recorded_and_withdrawal_beyond_balance_is_refused(
    qtbot: QtBot, page: CashPage, repo: CashRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    click_cell(qtbot, page, 0, 1)
    account_id = page._current_id
    assert account_id is not None
    monkeypatch.setattr(MovementDialog, "exec", lambda self: MovementDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        MovementDialog,
        "form",
        lambda self: MovementForm(MovementKind.DEPOSIT, date(2026, 11, 10), D("150")),
    )
    page._detail.movementRequested.emit(account_id)
    assert page._detail._movements.rowCount() == 2

    monkeypatch.setattr(
        MovementDialog,
        "form",
        lambda self: MovementForm(MovementKind.WITHDRAWAL, date(2026, 11, 11), D("99999")),
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        "assets_tracker.ui.cash_page.QMessageBox.warning",
        lambda *args: warnings.append(str(args[-1])),
    )
    page._detail.movementRequested.emit(account_id)

    assert warnings and "Solde insuffisant" in warnings[0]
    assert page._detail._movements.rowCount() == 2


def test_account_dialog_builds_a_validated_form(qtbot: QtBot) -> None:
    dialog = AccountDialog(None, account=None, base_rate=None, bonus=None, today=TODAY)
    qtbot.addWidget(dialog)
    dialog._name.setText("Livret Bonus")
    dialog._bank.setText("Banque Test")
    dialog._iban.setText("fr76 3000 6000 0112 3456 7890 189")
    dialog._rate.setText("2,5")
    dialog._monthly_button.setChecked(True)
    dialog._bonus_check.setChecked(True)
    dialog._bonus_rate.setText("1,00")
    dialog._bonus_months.setValue(6)

    form = dialog.form()

    assert form.account.iban == LIVRET_IBAN
    assert form.account.frequency is InterestFrequency.MONTHLY
    assert (form.account.payout_month, form.account.payout_day) == (None, None)
    assert form.base_rate == D("2.5")
    assert form.bonus == (D("1.00"), 6)
    assert form.account.opened_on == TODAY


def test_account_dialog_reports_errors_inline_and_stays_open(qtbot: QtBot) -> None:
    dialog = AccountDialog(None, account=None, base_rate=None, bonus=None, today=TODAY)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._name.setText("Livret")
    dialog._bank.setText("Banque")
    dialog._iban.setText("FR7630006000011234567890188")  # clé de contrôle fausse
    dialog._rate.setText("2")

    dialog._try_accept()

    assert dialog._error.isVisible()
    assert "IBAN" in dialog._error.text()
    assert dialog.result() != AccountDialog.DialogCode.Accepted


def test_movement_dialog_uses_the_validator(qtbot: QtBot) -> None:
    def refuse(form: MovementForm) -> None:
        raise ValueError("Solde insuffisant")

    dialog = MovementDialog(None, today=TODAY, balance_text="100,00 €", validator=refuse)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._amount.setText("500")

    dialog._try_accept()

    assert dialog._error.text() == "Solde insuffisant"
    assert dialog.result() != MovementDialog.DialogCode.Accepted


def test_rate_chart_renders(qtbot: QtBot) -> None:
    chart = RateChart()
    qtbot.addWidget(chart)
    chart.resize(600, 200)
    chart.set_data(
        [
            (date(2024, 6, 1), D("1.00")),
            (date(2025, 1, 1), D("1.50")),
            (date(2025, 6, 1), D("3.00")),
        ],
        TODAY,
    )
    assert not chart.grab().isNull()
