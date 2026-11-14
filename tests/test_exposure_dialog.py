from __future__ import annotations

from decimal import Decimal

from pytestqt.qtbot import QtBot

from assets_tracker.domain.exposure import ExposureEntry, ExposureKind
from assets_tracker.ui.exposure_dialog import ExposureDialog, ExposureEditor

D = Decimal
NBSP = " "


def make_dialog(qtbot: QtBot) -> ExposureDialog:
    dialog = ExposureDialog(None, "Air Liquide", [], [])
    qtbot.addWidget(dialog)
    return dialog


def test_empty_dialog_can_be_saved_and_says_nothing_is_filled(qtbot: QtBot) -> None:
    dialog = make_dialog(qtbot)

    assert dialog._save.isEnabled()
    assert dialog._sectors._total.text() == "Non renseigné"
    assert dialog.sectors() == [] and dialog.zones() == []


def test_save_is_disabled_until_each_filled_breakdown_totals_100(qtbot: QtBot) -> None:
    dialog = make_dialog(qtbot)
    editor = dialog._sectors

    editor.add_row("Technologie", D(60))
    assert not dialog._save.isEnabled()
    assert editor._total.text() == f"Reste à répartir : 40,00{NBSP}%"

    editor.add_row("Santé", D(50))
    assert not dialog._save.isEnabled()
    assert editor._total.text() == f"Excédent à retirer : 10,00{NBSP}%"
    assert "100" in dialog._error.text()

    editor._rows[1][1].setText("40")
    assert dialog._save.isEnabled()
    assert editor._total.text() == f"Total 100{NBSP}%"
    assert dialog.sectors() == [
        ExposureEntry("Technologie", D(60)),
        ExposureEntry("Santé", D(40)),
    ]


def test_steppers_complete_with_other_and_remove(qtbot: QtBot) -> None:
    dialog = make_dialog(qtbot)
    editor = dialog._zones
    editor.add_row("Europe", D(0))

    editor._bump(editor._rows[0][1], D(5))
    editor._bump(editor._rows[0][1], D(5))
    assert editor._rows[0][1].text() == "10"
    editor._bump(editor._rows[0][1], D(-5))
    assert editor._rows[0][1].text() == "5"

    editor._complete_with_other()
    assert [(n.text(), w.text()) for n, w, _ in editor._rows] == [("Europe", "5"), ("Autre", "95")]
    assert dialog._save.isEnabled()

    editor._remove(editor._rows[1])
    assert len(editor._rows) == 1 and not dialog._save.isEnabled()


def test_suggestion_adds_a_row_once_and_invalid_weight_blocks_saving(qtbot: QtBot) -> None:
    editor = ExposureEditor(ExposureKind.SECTOR)
    qtbot.addWidget(editor)

    editor._add_suggestion("Technologie")
    editor._add_suggestion("technologie")
    assert len(editor._rows) == 1

    editor._rows[0][1].setText("beaucoup")
    assert editor.problem() is not None
    assert editor._total.text() == "Part invalide"


def test_accept_keeps_the_dialog_open_on_error_and_accepts_when_valid(qtbot: QtBot) -> None:
    dialog = make_dialog(qtbot)
    dialog._sectors.add_row("Santé", D(90))

    dialog.accept()
    assert dialog.result() == 0 and dialog._error.text()

    dialog._sectors._rows[0][1].setText("100")
    dialog.accept()
    assert dialog.result() == 1
