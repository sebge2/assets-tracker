from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox
from pytestqt.qtbot import QtBot

from assets_tracker.db.connection import connect
from assets_tracker.db.insurance_repository import InsuranceRepository
from assets_tracker.domain.insurance import ContractDetails, ContractKind
from assets_tracker.ui.insurance_page import (
    ContractDialog,
    InsurancePage,
    PaymentDialog,
    StatementDialog,
)

D = Decimal
TODAY = date(2026, 11, 14)
PENSION = ContractDetails(
    ContractKind.PENSION_SAVINGS,
    "Épargne-pension",
    "AG Insurance",
    date(2016, 3, 1),
    ceiling=D(1050),
)
GROUP = ContractDetails(
    ContractKind.GROUP_INSURANCE,
    "Pension complémentaire",
    "Allianz",
    date(2014, 9, 1),
    retirement_on=date(2040, 9, 1),
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "insurance.db", key_provider=lambda: b"\x0b" * 32)
    yield connection
    connection.close()


def make_page(qtbot: QtBot, conn: Any) -> tuple[InsurancePage, InsuranceRepository]:
    repository = InsuranceRepository(conn)
    page = InsurancePage(repository, today=lambda: TODAY)
    qtbot.addWidget(page)
    return page, repository


def cell_text(page: InsurancePage, row: int, column: int) -> str:
    widget = page._table.cellWidget(row, column)
    assert isinstance(widget, QLabel)
    return widget.text()


def test_empty_page_shows_the_hint(qtbot: QtBot, conn: Any) -> None:
    page, _ = make_page(qtbot, conn)
    assert not page._empty.isHidden()
    assert page._table.isHidden() and page._detail.isHidden()


def test_summary_table_and_stale_alert(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    pension = repository.create(PENSION)
    group = repository.create(GROUP)
    repository.add_payment(pension.id, date(2026, 3, 2), D(1050))
    repository.save_statement(pension.id, date(2026, 9, 30), D(14280), D(60000))
    repository.save_statement(group.id, date(2024, 12, 31), D(31650), D(58400))
    page.refresh()
    assert page._table.rowCount() == 2
    assert "Épargne-pension" in cell_text(page, 0, 0)
    assert "60" in cell_text(page, 0, 3)
    assert "ancien" in cell_text(page, 1, 2)
    assert not page._alert.isHidden()
    assert "Pension complémentaire" in page._alert_text.text()
    assert "non inclus" in page._subtitle.text()


def test_no_stale_alert_when_statements_are_recent(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    contract = repository.create(PENSION)
    repository.save_statement(contract.id, date(2026, 9, 30), D(14280), D(60000))
    page.refresh()
    assert page._alert.isHidden()


def test_detail_shows_projection_history_and_ceiling(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    contract = repository.create(PENSION)
    repository.add_payment(contract.id, date(2026, 3, 2), D(1050))
    repository.save_statement(contract.id, date(2025, 12, 31), D(12980), D(55000))
    repository.save_statement(contract.id, date(2026, 9, 30), D(14280), D(60000))
    page.select(contract.id)
    assert page.history_rows() == 2
    assert [v for _, v in page._chart.points()] == [D(55000), D(60000)]
    assert not page._ceiling_box.isHidden()
    assert page._gauge.ratio() == 1.0
    assert "Plafond atteint" in page._d_ceiling_left.text()


def test_group_insurance_has_no_ceiling_gauge(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    contract = repository.create(GROUP)
    page.select(contract.id)
    assert page._ceiling_box.isHidden()
    assert not page._chart_empty.isHidden()


def test_new_contract_through_the_page(
    qtbot: QtBot, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page, repository = make_page(qtbot, conn)
    monkeypatch.setattr(ContractDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(ContractDialog, "form", lambda self: PENSION)
    page._new_contract()
    assert [h.details for h in repository.list_contracts()] == [PENSION]
    assert page._table.rowCount() == 1


def test_update_statement_through_the_page(
    qtbot: QtBot, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page, repository = make_page(qtbot, conn)
    contract = repository.create(PENSION)
    repository.save_statement(contract.id, date(2025, 9, 30), D(12980), D(55000))
    page.select(contract.id)
    monkeypatch.setattr(StatementDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        StatementDialog, "form", lambda self: (date(2026, 9, 30), D(14280), D(60000))
    )
    page._update_statement()
    holding = repository.get(contract.id)
    assert holding is not None and len(holding.statements) == 2
    assert page.history_rows() == 2


def test_delete_statement_asks_for_confirmation(
    qtbot: QtBot, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page, repository = make_page(qtbot, conn)
    contract = repository.create(PENSION)
    holding = repository.save_statement(contract.id, date(2026, 9, 30), D(14280), D(60000))
    page.select(contract.id)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    page._delete_statement(holding.statements[0].id)
    assert page.history_rows() == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page._delete_statement(holding.statements[0].id)
    assert page.history_rows() == 0


def test_statement_dialog_shows_the_change_from_the_previous_projection(
    qtbot: QtBot, conn: Any
) -> None:
    page, repository = make_page(qtbot, conn)
    contract = repository.create(PENSION)
    repository.save_statement(contract.id, date(2025, 9, 30), D(12980), D(55000))
    page.select(contract.id)
    view = page._selected_view()
    assert view is not None
    dialog = StatementDialog(page, view, TODAY)
    qtbot.addWidget(dialog)
    assert dialog.variation_text() == "0,00\u00a0€"
    dialog._projected.setText("57 500")
    assert dialog.variation_text().startswith("+2")
    dialog._projected.setText("")
    assert dialog.variation_text() == "—"


def test_statement_dialog_rejects_negative_values(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    contract = repository.create(PENSION)
    page.select(contract.id)
    view = page._selected_view()
    assert view is not None
    dialog = StatementDialog(page, view, TODAY)
    qtbot.addWidget(dialog)
    dialog._value.setText("-5")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "négative" in dialog._error.text()


def test_payment_dialog_total_follows_the_amount(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    contract = repository.create(PENSION)
    repository.add_payment(contract.id, date(2026, 3, 2), D(1000))
    page.select(contract.id)
    view = page._selected_view()
    assert view is not None
    dialog = PaymentDialog(page, view, TODAY)
    qtbot.addWidget(dialog)
    dialog._amount.setText("50")
    assert dialog.total_text() == "1\u202f050,00\u00a0€"


def test_contract_dialog_hides_ceiling_for_group_insurance(qtbot: QtBot) -> None:
    dialog = ContractDialog(None, TODAY)
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog._ceiling_box.isHidden()
    dialog._kind.setCurrentIndex(1)
    assert dialog._ceiling_box.isHidden()
    dialog._name.setText("Groupe")
    dialog._insurer.setText("Allianz")
    dialog._opened.setDate(QDate(2014, 9, 1))
    details = dialog.form()
    assert details.kind is ContractKind.GROUP_INSURANCE
    assert details.ceiling is None and details.retirement_on is None
