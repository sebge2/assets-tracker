from __future__ import annotations

from decimal import Decimal

import pytest

from assets_tracker.domain.projection import (
    ProjectionAssumptions,
    milestone_years,
    project,
)

D = Decimal
CENT = D("0.01")


def assumptions(**overrides: object) -> ProjectionAssumptions:
    fields: dict[str, object] = {
        "initial": D(25000),
        "monthly_contribution": D(500),
        "annual_rate_percent": D(6),
        "years": 20,
    }
    fields.update(overrides)
    return ProjectionAssumptions(**fields)  # type: ignore[arg-type]


def test_year_by_year_values_follow_annual_compounding() -> None:
    points = project(assumptions())

    # V(n+1) = V(n) × 1,06 + 6 000
    assert [p.balance.quantize(CENT) for p in points[:5]] == [
        D("25000.00"),
        D("32500.00"),
        D("40450.00"),
        D("48877.00"),
        D("57809.62"),
    ]


def test_twenty_year_reference_case() -> None:
    last = project(assumptions())[-1]

    assert last.year == 20
    assert last.contributed == D(145000)  # 25 000 + 20 × 6 000
    assert last.balance.quantize(CENT) == D("300891.93")
    assert last.interest.quantize(CENT) == D("155891.93")
    assert last.balance_without_contributions.quantize(CENT) == D("80178.39")


def test_without_contributions_scenario_is_pure_compounding() -> None:
    points = project(assumptions(monthly_contribution=D(0)))

    assert [p.balance for p in points] == [p.balance_without_contributions for p in points]
    assert points[3].balance.quantize(CENT) == D("29775.40")
    assert points[-1].contributed == D(25000)


def test_zero_rate_only_accumulates_contributions() -> None:
    points = project(assumptions(annual_rate_percent=D(0), years=3))

    assert [p.balance for p in points] == [D(25000), D(31000), D(37000), D(43000)]
    assert all(p.interest == 0 for p in points)


def test_starting_from_nothing() -> None:
    points = project(assumptions(initial=D(0), annual_rate_percent=D(10), years=2))

    assert [p.balance.quantize(CENT) for p in points] == [D("0.00"), D("6000.00"), D("12600.00")]


def test_fractional_rate_is_kept_exact() -> None:
    first = project(assumptions(annual_rate_percent=D("3.5"), years=1))[1]
    assert first.balance == D(25000) * D("1.035") + D(6000)


def test_interest_is_balance_minus_contributions() -> None:
    for point in project(assumptions()):
        assert point.interest == point.balance - point.contributed


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("initial", D(-1), "capital"),
        ("monthly_contribution", D(-1), "versement"),
        ("annual_rate_percent", D(-1), "rendement"),
        ("annual_rate_percent", D(101), "rendement"),
        ("years", 0, "durée"),
        ("years", 61, "durée"),
    ],
)
def test_invalid_assumptions_are_refused(field: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        assumptions(**{field: value})


def test_milestone_years() -> None:
    assert milestone_years(20) == [5, 10, 15, 20]
    assert milestone_years(12) == [5, 10, 12]
    assert milestone_years(3) == [3]
    assert milestone_years(5) == [5]
