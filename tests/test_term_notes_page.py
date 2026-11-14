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
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.term_note_repository import TermNoteRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind
from assets_tracker.domain.term_notes import InterestPayout, Repayment, TermNoteDetails
from assets_tracker.ui.term_notes_page import RepayDialog, TermNoteDialog, TermNotesPage

D = Decimal
TODAY = date(2026, 11, 14)
MATURITY = InterestPayout.MATURITY
THREE_YEARS = TermNoteDetails(
    "Crédit Mutuel", D(10000), D("3.2"), date(2025, 3, 15), date(2028, 3, 15), MATURITY
)
DUE = TermNoteDetails(
    "Banque Populaire", D(5000), D("2.8"), date(2024, 9, 2), date(2026, 9, 2), MATURITY
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "notes.db", key_provider=lambda: b"\x0b" * 32)
    yield connection
    connection.close()


def make_page(qtbot: QtBot, conn: Any) -> tuple[TermNotesPage, TermNoteRepository]:
    repository = TermNoteRepository(conn)
    page = TermNotesPage(repository, exposure=ExposureRepository(conn), today=lambda: TODAY)
    qtbot.addWidget(page)
    return page, repository


def cell_text(page: TermNotesPage, row: int, column: int) -> str:
    widget = page._table.cellWidget(row, column)
    assert isinstance(widget, QLabel)
    return widget.text()


def test_empty_page_shows_the_hint(qtbot: QtBot, conn: Any) -> None:
    page, _ = make_page(qtbot, conn)
    assert not page._empty.isHidden()
    assert page._table.isHidden() and page._detail.isHidden()


def test_summary_and_table(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    repository.create("Bon 3 ans", THREE_YEARS)
    repository.create("Bon 2 ans", DUE)
    page.refresh()
    assert page._table.rowCount() == 2
    # Échéance la plus proche d'abord : le bon échu.
    assert "Bon 2 ans" in cell_text(page, 0, 0)
    assert "Échu" in cell_text(page, 0, 1)
    assert "Bon 3 ans" in cell_text(page, 1, 0)
    assert not page._alert.isHidden()
    assert "Bon 2 ans" in page._alert_text.text()
    assert "est échu depuis 73 jours" in page._alert_text.text()
    assert page._subtitle.text() == "2 bons · intérêts calculés au 14/11/2026"
    assert page._next.text().startswith("Prochaine échéance : Bon 3 ans")


def test_selecting_a_row_fills_the_detail(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    repository.create("Bon 3 ans", THREE_YEARS)
    page.refresh()
    assert page._d_name.text() == "Bon 3 ans"
    assert "10" in page._d_value.text() and "533,92" in page._d_value.text()
    assert page._tiles["Durée"].text().startswith("3 ans")
    assert page._tiles["Valeur à l'échéance"].text() != "—"
    assert page._repay_button.text() == "Remboursement anticipé…"
    assert page._cancel_button.isHidden()


def test_due_note_offers_the_repayment(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    created = repository.create("Bon 2 ans", DUE)
    assert created.asset.id is not None
    page.select(created.asset.id)
    page.refresh()
    assert page._repay_button.text() == "Enregistrer le remboursement…"
    assert page._d_badge.text() == "Échu depuis 73 jours — à rembourser"


def test_repaid_note_moves_to_the_end_and_is_closed(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    due = repository.create("Bon 2 ans", DUE)
    repository.create("Bon 3 ans", THREE_YEARS)
    assert due.asset.id is not None
    repository.repay(due.asset.id, Repayment(date(2026, 9, 5), D(5000), D("279.5")))
    page.select(due.asset.id)
    page.refresh()
    assert "Bon 3 ans" in cell_text(page, 0, 0)
    assert "Remboursé le 05/09/2026" in cell_text(page, 1, 1)
    assert page._alert.isHidden()
    assert page._repay_button.isHidden() and not page._cancel_button.isHidden()
    kinds = [kind for kind, *_ in page._operation_rows(page._views[1], TODAY)]
    assert kinds == ["Remboursement", "Intérêts", "Souscription"]


def test_exposure_section_and_summary(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    created = repository.create("Bon 3 ans", THREE_YEARS)
    assert created.asset.id is not None
    ExposureRepository(conn).set(
        created.asset.id, ExposureKind.SECTOR, [ExposureEntry("Finance", D(100))]
    )
    page.refresh()
    assert page._tiles["Secteur principal"].text().startswith("Finance")
    assert not page._exposure_card.isHidden()


# -- dialogues --


def test_note_dialog_summary_updates_and_returns_the_form(qtbot: QtBot) -> None:
    dialog = TermNoteDialog(None, TODAY)
    qtbot.addWidget(dialog)
    assert dialog.summary()["Intérêts à l'échéance"] == "—"
    dialog._name.setText("Bon 3 ans")
    dialog._issuer.setText("Crédit Mutuel")
    dialog._capital.setText("10000")
    dialog._rate.setText("3,2")
    dialog._start.setDate(QDate(2025, 3, 15))
    dialog._end.setDate(QDate(2028, 3, 15))
    assert dialog.summary()["Durée"].startswith("3 ans")
    assert "960,88" in dialog.summary()["Intérêts à l'échéance"]
    assert "10" in dialog.summary()["Valeur à l'échéance"]
    name, details = dialog.form()
    assert name == "Bon 3 ans" and details == THREE_YEARS


def test_note_dialog_stays_open_on_invalid_input(qtbot: QtBot) -> None:
    dialog = TermNoteDialog(None, TODAY)
    qtbot.addWidget(dialog)
    dialog._issuer.setText("CM")
    dialog._capital.setText("1000")
    dialog._rate.setText("3")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "nom" in dialog._error.text().lower()
    dialog._name.setText("Bon")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_repay_dialog_follows_the_date_until_interest_is_edited(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    created = repository.create("Bon 3 ans", THREE_YEARS)
    assert created.asset.id is not None
    page.refresh()
    dialog = RepayDialog(None, page._views[0], TODAY)
    qtbot.addWidget(dialog)
    assert dialog._interest.text() == "533,92"
    assert dialog.form().capital == D(10000)
    dialog._date.setDate(QDate(2026, 11, 15))
    assert dialog._interest.text() != "533,92"
    dialog._interest.setText("500")
    dialog._interest.textEdited.emit("500")
    dialog._date.setDate(QDate(2026, 12, 1))
    assert dialog._interest.text() == "500"
    assert dialog.form().total == D(10500)


def test_repay_dialog_rejects_a_date_before_subscription(qtbot: QtBot, conn: Any) -> None:
    page, repository = make_page(qtbot, conn)
    repository.create("Bon 3 ans", THREE_YEARS)
    page.refresh()
    dialog = RepayDialog(None, page._views[0], TODAY)
    qtbot.addWidget(dialog)
    dialog._date.setDate(QDate(2024, 1, 1))
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "précéder" in dialog._error.text()


def test_repay_through_the_page(qtbot: QtBot, conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    page, repository = make_page(qtbot, conn)
    created = repository.create("Bon 2 ans", DUE)
    assert created.asset.id is not None
    page.select(created.asset.id)
    page.refresh()
    monkeypatch.setattr(RepayDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    page._repay()
    holding = repository.get_holding(created.asset.id)
    assert holding is not None and holding.repayment is not None
    assert holding.repayment.capital == D(5000) and holding.repayment.interest == D("280")


def test_delete_asks_for_confirmation(
    qtbot: QtBot, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page, repository = make_page(qtbot, conn)
    repository.create("Bon 3 ans", THREE_YEARS)
    page.refresh()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    page._delete_note()
    assert len(repository.list_holdings()) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page._delete_note()
    assert repository.list_holdings() == []
