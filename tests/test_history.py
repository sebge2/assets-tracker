from __future__ import annotations

from datetime import date
from decimal import Decimal

from pytestqt.qtbot import QtBot

from assets_tracker.domain.history import daily_points, in_period, period_stats
from assets_tracker.ui.quote_history import QuoteHistoryDialog

D = Decimal
TODAY = date(2026, 11, 15)
POINTS = [
    (date(2025, 1, 10), D("80")),
    (date(2026, 6, 1), D("100")),
    (date(2026, 10, 20), D("110")),
    (date(2026, 11, 14), D("121")),
]


def test_daily_points_keep_last_value_of_a_day_and_sort() -> None:
    points = [(date(2026, 1, 2), D(2)), (date(2026, 1, 1), D(1)), (date(2026, 1, 2), D(3))]
    assert daily_points(points) == [(date(2026, 1, 1), D(1)), (date(2026, 1, 2), D(3))]


def test_in_period_windows() -> None:
    assert [p[1] for p in in_period(POINTS, TODAY, 30)] == [D("110"), D("121")]
    assert [p[1] for p in in_period(POINTS, TODAY, 365)] == [D("100"), D("110"), D("121")]
    assert len(in_period(POINTS, TODAY, None)) == 4


def test_in_period_ignores_future_points() -> None:
    assert in_period([(date(2026, 12, 1), D(1))], TODAY, 30) == []


def test_period_stats() -> None:
    stats = period_stats(POINTS)
    assert stats is not None
    assert stats.first == (date(2025, 1, 10), D("80")) and stats.last[1] == D("121")
    assert stats.low[1] == D("80") and stats.high[1] == D("121") and stats.count == 4
    assert stats.change == D("41") and stats.change_pct == D("51.25")


def test_period_stats_edge_cases() -> None:
    assert period_stats([]) is None
    zero = period_stats([(date(2026, 1, 1), D(0)), (date(2026, 1, 2), D(5))])
    assert zero is not None and zero.change_pct is None


def make(qtbot: QtBot, points: list[tuple[date, Decimal]]) -> QuoteHistoryDialog:
    dialog = QuoteHistoryDialog(None, "Fonds", points, TODAY, lambda v: f"{v} €")
    qtbot.addWidget(dialog)
    return dialog


def test_dialog_defaults_to_all_and_switches_period(qtbot: QtBot) -> None:
    dialog = make(qtbot, POINTS)
    assert dialog.period() == "Tout"
    assert dialog._table.rowCount() == 4
    assert dialog._table.item(0, 0).text() == "14/11/2026"  # le plus récent d'abord

    dialog._buttons["1 mois"].click()
    assert dialog.period() == "1 mois"
    assert dialog._table.rowCount() == 2
    assert dialog._stats["Cotations"].text() == "2"
    assert dialog._stats["Variation"].text().startswith("+")


def test_dialog_without_points_in_period_shows_message(qtbot: QtBot) -> None:
    dialog = make(qtbot, [(date(2020, 1, 1), D(5))])
    dialog._buttons["1 mois"].click()
    assert dialog._table.rowCount() == 0
    assert not dialog._empty.isHidden()
    assert dialog._stats["Variation"].text() == "—"
    dialog._buttons["Tout"].click()
    assert dialog._empty.isHidden()
