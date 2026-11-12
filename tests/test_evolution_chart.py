from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QPointF
from pytestqt.qtbot import QtBot

from assets_tracker.ui.evolution_chart import ChartSeries, EvolutionChart, nice_ticks

D = Decimal
NBSP = " "


def test_nice_ticks_use_round_steps_from_zero() -> None:
    assert nice_ticks(D("12037")) == [D(0), D(2500), D(5000), D(7500), D(10000), D(12500)]
    assert nice_ticks(D("100")) == [D(0), D(20), D(40), D(60), D(80), D(100)]
    assert nice_ticks(D("9")) == [D(0), D(2), D(4), D(6), D(8), D(10)]
    assert nice_ticks(D("0")) == [D(0), D("0.2"), D("0.4"), D("0.6"), D("0.8"), D(1)]


def test_nice_ticks_always_cover_the_peak() -> None:
    for peak in ("1", "7", "999", "1000", "1001", "123456", "0.5"):
        ticks = nice_ticks(D(peak))
        assert ticks[0] == 0
        assert ticks[-1] >= D(peak)
        assert len(ticks) <= 8


def make_chart(qtbot: QtBot) -> EvolutionChart:
    chart = EvolutionChart()
    qtbot.addWidget(chart)
    chart.resize(800, 320)
    months = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    chart.set_data(
        months,
        [
            ChartSeries("Livret A", "#5CC8FF", [D(1000), D(1500), D(2000)]),
            ChartSeries("Livret B", "#B38CFF", [None, D(500), D(600)]),
        ],
        [D(1000), D(2000), D(2600)],
        "EUR",
    )
    return chart


def test_every_known_balance_has_a_point(qtbot: QtBot) -> None:
    chart = make_chart(qtbot)

    dots = chart.dots()

    # 3 + 2 soldes de comptes (le compte B n'existe pas en janvier) + 3 totaux
    assert len(dots) == 8
    assert [d.is_total for d in dots] == [False] * 5 + [True] * 3
    assert dots[-1].value == D(2600)


def test_hovering_a_point_gives_its_amount(qtbot: QtBot) -> None:
    chart = make_chart(qtbot)
    account_dot = next(d for d in chart.dots() if d.name == "Livret A" and d.value == D(1500))

    found = chart.dot_at(account_dot.pos + QPointF(3, 2))

    assert found == account_dot
    assert chart.tooltip_text(found) == f"Livret A · février 2026\n1 500,00{NBSP}€"
    assert chart.dot_at(QPointF(5, 5)) is None  # loin de toute courbe


def test_total_wins_when_points_overlap(qtbot: QtBot) -> None:
    chart = make_chart(qtbot)
    # En janvier, le total (1 000 €) se confond avec le compte A (1 000 €).
    january_total = next(d for d in chart.dots() if d.is_total and d.month == date(2026, 1, 1))

    found = chart.dot_at(january_total.pos)

    assert found is not None
    assert found.is_total


def test_mouse_move_highlights_and_leaving_clears(qtbot: QtBot) -> None:
    chart = make_chart(qtbot)
    chart.show()
    target = next(d for d in chart.dots() if d.is_total and d.value == D(2000))

    qtbot.mouseMove(chart, target.pos.toPoint())
    assert chart._hover is not None
    assert chart._hover.value == D(2000)

    chart._hover = None
    qtbot.mouseMove(chart, QPointF(3, 3).toPoint())
    assert chart._hover is None


def test_chart_renders_with_axes(qtbot: QtBot) -> None:
    chart = make_chart(qtbot)
    chart.show()
    assert not chart.grab().isNull()
    assert chart.ticks()[-1] >= D(2600)


def test_single_month_draws_a_message_and_no_dots(qtbot: QtBot) -> None:
    chart = EvolutionChart()
    qtbot.addWidget(chart)
    chart.resize(400, 300)
    chart.set_data([date(2026, 1, 1)], [], [D(10)])

    assert chart.dots() == []
    assert not chart.grab().isNull()
