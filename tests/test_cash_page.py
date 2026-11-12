from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QToolButton
from pytestqt.qtbot import QtBot

from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.db.connection import connect
from assets_tracker.domain.cash import (
    CashAccount,
    CashMovement,
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
    repository.add_movement(avenir.id, MovementKind.LOYALTY_BONUS, date(2026, 11, 1), D("12"))
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
    assert table.rowCount() == 3  # deux comptes et la ligne de total
    assert table.item(0, 1).text() == "FR76 •••• •••• 0189"
    assert "2 000,00" in table.item(0, 4).text()
    assert table.item(0, 5).text() == "0,00\u00a0€"  # aucun intérêt saisi en 2026
    # Livret Avenir : 1,5 % de base + 1,5 % de prime (condition remplie depuis 2025-06-01)
    assert "3,00" in table.cellWidget(1, 2).text()
    assert "prime" in table.cellWidget(1, 2).text()
    assert "+37,00" in table.item(1, 5).text()  # 25 d'intérêts + 12 de prime saisis en 2026
    assert table.horizontalHeaderItem(5).text() == "Intérêts 2026"


def test_summary_cards_total_per_currency(page: CashPage) -> None:
    total = page._list._total._value.text()
    assert total.startswith("12") and total.endswith("\u00a0€")
    assert "+37,00" in page._list._interest._value.text()
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

    assert detail._movements.rowCount() > 2  # intérêt, prime et dépôt
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
    assert detail._movements.rowCount() > 2
    detail._set_filter("all")
    assert detail._movements.rowCount() > 2


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


def test_reopened_dialog_shows_the_saved_effective_date(
    qtbot: QtBot, page: CashPage, repo: CashRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = repo.list_accounts()[0]
    assert account.id is not None
    saved = date(2023, 6, 1)
    assert saved < account.opened_on
    repo.update_account(account, base_rate=D("3.00"), bonus=None, effective_date=saved)

    shown: list[date] = []

    def fake_exec(self: AccountDialog) -> int:
        shown.append(self._effective.date().toPython())
        return AccountDialog.DialogCode.Rejected

    monkeypatch.setattr(AccountDialog, "exec", fake_exec)
    page._edit_account(account.id)

    assert shown == [saved]


def test_movement_dialog_offers_interest_and_loyalty_bonus(qtbot: QtBot) -> None:
    dialog = MovementDialog(None, today=TODAY, balance_text="0,00 €")
    qtbot.addWidget(dialog)
    kinds = [dialog._kind.itemData(i) for i in range(dialog._kind.count())]
    assert MovementKind.INTEREST in kinds
    assert MovementKind.LOYALTY_BONUS in kinds

    dialog._kind.setCurrentIndex(kinds.index(MovementKind.LOYALTY_BONUS))
    dialog._amount.setText("4,20")
    assert dialog.form().kind is MovementKind.LOYALTY_BONUS


def test_editing_a_movement_updates_it(
    qtbot: QtBot, page: CashPage, repo: CashRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    click_cell(qtbot, page, 1, 1)
    account_id = page._current_id
    assert account_id is not None
    interest = next(m for m in repo.movements(account_id) if m.kind is MovementKind.INTEREST)
    assert interest.id is not None
    monkeypatch.setattr(MovementDialog, "exec", lambda self: MovementDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        MovementDialog,
        "form",
        lambda self: MovementForm(MovementKind.LOYALTY_BONUS, date(2026, 11, 2), D("30")),
    )

    page._detail.movementEditRequested.emit(interest.id)

    saved = next(m for m in repo.movements(account_id) if m.id == interest.id)
    assert (saved.kind, saved.movement_date, saved.amount) == (
        MovementKind.LOYALTY_BONUS,
        date(2026, 11, 2),
        D("30"),
    )


def test_movement_dialog_is_prefilled_when_editing(qtbot: QtBot) -> None:
    movement = CashMovement(1, 1, MovementKind.INTEREST, date(2026, 3, 4), D("12.5"))
    dialog = MovementDialog(None, today=TODAY, balance_text="0,00 €", movement=movement)
    qtbot.addWidget(dialog)

    form = dialog.form()
    assert (form.kind, form.movement_date, form.amount) == (
        MovementKind.INTEREST,
        date(2026, 3, 4),
        D("12.5"),
    )


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


def test_movement_can_be_deleted_from_the_detail(
    qtbot: QtBot, page: CashPage, repo: CashRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    click_cell(qtbot, page, 0, 1)
    account_id = page._current_id
    assert account_id is not None
    movement = repo.movements(account_id)[0]
    assert movement.id is not None
    monkeypatch.setattr(
        "assets_tracker.ui.cash_page.QMessageBox.question",
        lambda *args: QMessageBox.StandardButton.Yes,
    )

    page._detail.movementDeleteRequested.emit(movement.id)

    assert repo.movements(account_id) == []
    assert page._detail._movements.rowCount() == 0


def test_movement_dialog_returns_a_movement_kind_enum(qtbot: QtBot) -> None:
    dialog = MovementDialog(None, today=TODAY, balance_text="0,00 €")
    qtbot.addWidget(dialog)
    dialog._amount.setText("150")

    form = dialog.form()

    assert form.kind is MovementKind.DEPOSIT


def test_account_dialog_keeps_the_last_effective_date(qtbot: QtBot, repo: CashRepository) -> None:
    account = repo.list_accounts()[0]
    effective_date = date(2026, 5, 1)
    dialog = AccountDialog(
        None,
        account=account,
        base_rate=D("3.00"),
        bonus=None,
        today=TODAY,
        effective_date=effective_date,
    )
    qtbot.addWidget(dialog)

    assert dialog._effective.date().toPython() == effective_date


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


def test_list_ends_with_a_total_row_that_is_not_clickable(qtbot: QtBot, page: CashPage) -> None:
    table = page._list._table

    assert table.item(2, 0).text() == "Total"
    assert table.item(2, 4).text() == "12\u202f037,00\u00a0€"  # 2 000 € + 10 037 €
    assert table.item(2, 5).text() == "+37,00\u00a0€"
    assert table.item(2, 2).text() == "3,00\u00a0%"  # taux moyen pondéré par les soldes

    click_cell(qtbot, page, 2, 4)
    assert page._stack.currentWidget() is page._list


def test_total_rows_are_split_by_currency(page: CashPage, repo: CashRepository) -> None:
    usd = repo.create_account(
        CashAccount(
            None,
            "Compte USD",
            "Chase",
            "DE89370400440532013000",
            "USD",
            date(2026, 1, 1),
            InterestFrequency.MONTHLY,
        ),
        D("2.00"),
    )
    assert usd.id is not None
    repo.add_movement(usd.id, MovementKind.DEPOSIT, date(2026, 2, 3), D("500"))

    page.refresh()
    table = page._list._table

    assert [table.item(r, 0).text() for r in (3, 4)] == ["Total EUR", "Total USD"]
    assert table.item(4, 4).text() == "500,00\u00a0$"


def test_evolution_view_shows_month_by_month_balances_and_total(page: CashPage) -> None:
    page._list._set_mode(1)
    evolution = page._list._evolution

    assert not evolution.isHidden()
    assert page._list._table.isHidden()
    assert len(evolution.months()) == 12  # décembre 2025 à novembre 2026
    table = evolution._table
    assert [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())] == [
        "Mois",
        "Livret A",
        "Livret Avenir",
        "Total",
        "Variation",
    ]
    assert table.item(0, 0).text() == "novembre 2026 (en cours)"
    assert table.item(0, 3).text() == "12\u202f037,00\u00a0€"
    assert "+37,00" in table.item(0, 4).text()  # intérêts et prime du 1er novembre
    last = table.rowCount() - 1
    assert table.item(last, 0).text() == "décembre 2025"
    assert table.item(last, 1).text() == "0,00\u00a0€"  # compte ouvert, encore vide
    assert table.item(last, 3).text() == "10\u202f000,00\u00a0€"
    assert table.item(last, 4).text() == "—"  # premier mois : pas de variation


def test_evolution_period_and_back_to_accounts(page: CashPage) -> None:
    page._list._set_mode(1)
    evolution = page._list._evolution

    evolution._set_period(36)
    assert len(evolution.months()) == 35  # jamais avant l'ouverture du premier compte (2024-01)
    evolution._set_period(0)
    assert len(evolution.months()) == 35

    page._list._set_mode(0)
    assert evolution.isHidden()
    assert not page._list._table.isHidden()


def test_evolution_shows_a_currency_selector_only_with_several_currencies(
    page: CashPage, repo: CashRepository
) -> None:
    evolution = page._list._evolution
    assert evolution._currency.isHidden()

    usd = repo.create_account(
        CashAccount(
            None,
            "Compte USD",
            "Chase",
            "DE89370400440532013000",
            "USD",
            date(2026, 1, 1),
            InterestFrequency.MONTHLY,
        ),
        D("2.00"),
    )
    assert usd.id is not None
    repo.add_movement(usd.id, MovementKind.DEPOSIT, date(2026, 2, 3), D("500"))
    page.refresh()
    page._list._set_mode(1)

    assert not evolution._currency.isHidden()
    evolution._currency.setCurrentText("USD")
    assert [
        evolution._table.horizontalHeaderItem(c).text()
        for c in range(evolution._table.columnCount())
    ] == ["Mois", "Compte USD", "Total", "Variation"]
    assert evolution._table.item(0, 2).text() == "500,00\u00a0$"


def test_monthly_table_is_capped_and_scrolls(page: CashPage) -> None:
    page._list._set_mode(1)
    evolution = page._list._evolution
    table = evolution._table

    assert table.rowCount() == 12
    assert table.height() <= 340  # plafonné : 12 lignes de 44 px ne tiennent pas
    table.resize(table.width(), 340)
    assert table.verticalScrollBar().maximum() > 0
    assert page._list._table.isHidden()
