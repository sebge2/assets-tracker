from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.term_note_repository import TermNoteRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind
from assets_tracker.domain.models import TransactionType
from assets_tracker.domain.positions import compute_position
from assets_tracker.domain.term_notes import (
    InterestPayout,
    Repayment,
    TermNoteDetails,
    accrued_interest,
    interest_paid,
    interest_payments,
    interest_total,
    ladder,
    note_totals,
    note_value,
    payment_dates,
    portfolio_prices,
    portfolio_transactions,
    remaining_label,
    repayment_proposal,
    term_label,
    validate_details,
    validate_repayment,
)

D = Decimal
TODAY = date(2026, 11, 14)
CENT = D("0.01")

MATURITY, YEARLY = InterestPayout.MATURITY, InterestPayout.ANNUAL
THREE_YEARS = TermNoteDetails(
    "Crédit Mutuel", D(10000), D("3.2"), date(2025, 3, 15), date(2028, 3, 15), MATURITY
)
ANNUAL = TermNoteDetails(
    "Caisse d'Épargne", D(8000), D("3.5"), date(2026, 1, 10), date(2031, 1, 10), YEARLY
)
DUE = TermNoteDetails(
    "Banque Populaire", D(5000), D("2.8"), date(2024, 9, 2), date(2026, 9, 2), MATURITY
)


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT)


# -- intérêts --


def test_interest_at_maturity_is_prorated_over_365_days() -> None:
    assert cents(interest_total(THREE_YEARS)) == D("960.88")
    assert cents(accrued_interest(THREE_YEARS, TODAY)) == D("533.92")
    assert accrued_interest(THREE_YEARS, date(2025, 3, 15)) == 0
    assert accrued_interest(THREE_YEARS, date(2025, 1, 1)) == 0


def test_annual_payout_pays_a_full_year_each_anniversary() -> None:
    assert payment_dates(ANNUAL)[:2] == [date(2027, 1, 10), date(2028, 1, 10)]
    assert payment_dates(ANNUAL)[-1] == ANNUAL.end
    assert interest_total(ANNUAL) == D(1400)
    assert interest_payments(ANNUAL) == [(date(2027 + i, 1, 10), D(280)) for i in range(4)]
    # Courus depuis la souscription (pas encore d'anniversaire) puis repartis à zéro après.
    assert cents(accrued_interest(ANNUAL, TODAY)) == D("236.27")
    assert interest_paid(ANNUAL, TODAY) == 0
    assert interest_paid(ANNUAL, date(2027, 6, 1)) == D(280)
    assert accrued_interest(ANNUAL, date(2027, 1, 10)) == 0


def test_annual_payout_prorates_a_short_last_period() -> None:
    odd = TermNoteDetails(
        "X", D(1000), D(5), date(2026, 1, 1), date(2027, 7, 2), InterestPayout.ANNUAL
    )
    assert [when for when, _ in interest_payments(odd)] == [date(2027, 1, 1)]
    assert payment_dates(odd) == [date(2027, 1, 1), date(2027, 7, 2)]
    assert interest_total(odd) == D(50) + D(1000) * D(5) / 100 * 182 / 365


def test_anniversary_of_a_leap_day_falls_back_to_february_28() -> None:
    leap = TermNoteDetails(
        "X", D(1000), D(2), date(2024, 2, 29), date(2026, 2, 28), InterestPayout.ANNUAL
    )
    assert payment_dates(leap) == [date(2025, 2, 28), date(2026, 2, 28)]


# -- valeur --


def test_value_is_capital_plus_accrued_interest() -> None:
    value = note_value(THREE_YEARS, TODAY)
    assert cents(value.value) == D("10533.92")
    assert cents(value.to_receive) == D("960.88")
    assert (value.ended, value.repaid) == (False, False)
    assert value.elapsed_days == 609
    assert value.total_days == 1096
    assert cents(value.elapsed_percent) == D("55.57")


def test_a_due_note_keeps_its_last_period_interest_until_repaid() -> None:
    value = note_value(DUE, TODAY)
    assert value.ended and not value.repaid
    assert cents(value.value) == D("5280.00")
    assert cents(value.accrued) == D("280.00")
    assert value.elapsed_percent == 100


def test_a_repaid_note_is_worth_nothing() -> None:
    repayment = Repayment(date(2026, 9, 5), D(5000), D("279.50"))
    value = note_value(DUE, TODAY, repayment)
    assert value.value == 0 and value.repaid
    assert value.paid == D("279.50")
    assert repayment.total == D("5279.50")


def test_repayment_proposal_before_and_after_maturity() -> None:
    assert repayment_proposal(DUE, TODAY) == Repayment(TODAY, D(5000), D("280.00"))
    early = repayment_proposal(THREE_YEARS, TODAY)
    assert early.capital == D(10000) and early.interest == D("533.92")


# -- totaux et échéancier --


def test_totals_ignore_repaid_notes() -> None:
    repaid = Repayment(date(2026, 9, 5), D(5000), D(280))
    totals = note_totals([(THREE_YEARS, None), (ANNUAL, None), (DUE, repaid)], TODAY)
    assert totals.count == 2
    assert totals.capital == D(18000)
    assert cents(totals.accrued) == D("770.19")
    assert cents(totals.value) == D("18770.19")
    assert cents(totals.to_receive) == D("2360.88")
    assert totals.average_rate == (D(10000) * D("3.2") + D(8000) * D("3.5")) / D(18000)


def test_totals_of_nothing() -> None:
    totals = note_totals([], TODAY)
    assert (totals.count, totals.capital, totals.average_rate) == (0, 0, None)


def test_ladder_groups_capital_and_interest_by_year() -> None:
    years = ladder([(THREE_YEARS, None), (ANNUAL, None), (DUE, None)], TODAY)
    assert list(years) == [2026, 2027, 2028, 2029, 2030, 2031]
    assert years[2026] == (D(5000), cents(D(280)))  # bon échu non remboursé : à son échéance
    assert years[2027] == (0, D(280))
    assert years[2028][0] == D(10000)
    assert years[2031][0] == D(8000)


def test_ladder_skips_repaid_notes_and_interest_already_received() -> None:
    repaid = Repayment(date(2026, 9, 5), D(5000), D(280))
    assert ladder([(DUE, repaid)], TODAY) == {}
    later = date(2029, 6, 1)
    years = ladder([(ANNUAL, None)], later)
    assert list(years) == [2030, 2031]


# -- libellés --


def test_labels() -> None:
    assert term_label(THREE_YEARS) == "3 ans"
    assert term_label(ANNUAL) == "5 ans"
    short = TermNoteDetails(
        "X", D(1), D(1), date(2026, 1, 1), date(2026, 7, 1), InterestPayout.MATURITY
    )
    assert term_label(short) == "6 mois"
    assert remaining_label(date(2026, 9, 2), TODAY) == "échu depuis 73 jours"
    assert remaining_label(date(2026, 11, 20), TODAY) == "dans 6 jours"
    assert remaining_label(date(2026, 11, 14), TODAY) == "échéance aujourd'hui"
    assert remaining_label(date(2028, 3, 15), TODAY) == "1 an et 4 mois"


# -- validation --


def test_validation() -> None:
    validate_details("Bon", THREE_YEARS)
    start, end = TODAY, date(2027, 1, 1)
    maturity = InterestPayout.MATURITY
    for name, details, message in (
        (" ", THREE_YEARS, "nom"),
        ("Bon", TermNoteDetails(" ", D(1), D(1), start, end, maturity), "émetteur"),
        ("Bon", TermNoteDetails("X", D(0), D(1), start, end, maturity), "capital"),
        ("Bon", TermNoteDetails("X", D(1), D(-1), start, end, maturity), "taux"),
        ("Bon", TermNoteDetails("X", D(1), D(1), start, start, maturity), "échéance"),
    ):
        with pytest.raises(ValueError, match=f"(?i){message}"):
            validate_details(name, details)
    validate_repayment(THREE_YEARS, Repayment(TODAY, D(1), D(0)))
    with pytest.raises(ValueError, match="(?i)précéder"):
        validate_repayment(THREE_YEARS, Repayment(date(2025, 1, 1), D(1), D(0)))
    with pytest.raises(ValueError, match="(?i)capital"):
        validate_repayment(THREE_YEARS, Repayment(TODAY, D(0), D(0)))
    with pytest.raises(ValueError, match="(?i)négatifs"):
        validate_repayment(THREE_YEARS, Repayment(TODAY, D(1), D(-1)))


# -- vue « portefeuille » --


def test_portfolio_view_is_a_one_unit_position() -> None:
    transactions = portfolio_transactions(7, THREE_YEARS, None)
    position = compute_position(7, transactions)
    assert position.quantity == 1 and position.pru == D(10000)
    prices = portfolio_prices(THREE_YEARS, TODAY, None)
    assert prices[0] == (THREE_YEARS.start, D(10000))
    assert prices[-1][0] == TODAY and cents(prices[-1][1]) == D("10533.92")
    assert [p[0] for p in prices] == sorted({p[0] for p in prices})
    assert date(2025, 3, 31) in [p[0] for p in prices]


def test_repayment_realizes_the_interest() -> None:
    repayment = Repayment(date(2026, 9, 2), D(5000), D(280))
    transactions = portfolio_transactions(3, DUE, repayment)
    assert transactions[1].type is TransactionType.SELL
    position = compute_position(3, transactions)
    assert position.quantity == 0
    assert position.realized_pnl == D(280)
    prices = portfolio_prices(DUE, TODAY, repayment)
    assert prices[-1][0] == repayment.paid_on


def test_due_note_is_priced_with_its_last_interest() -> None:
    prices = portfolio_prices(DUE, TODAY, None)
    # La valeur reste figée à l'échéance tant que le bon n'est pas remboursé.
    assert prices[-1] == (DUE.end, D(5000) + D(5000) * D("2.8") / 100 * 730 / 365)


# -- dépôt --


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "notes.db", key_provider=lambda: b"\x0b" * 32)
    yield connection
    connection.close()


def test_create_and_read_back(conn: Any) -> None:
    repository = TermNoteRepository(conn)
    created = repository.create("Bon 3 ans", THREE_YEARS)
    assert created.asset.id is not None and created.asset.symbol.startswith("BDC-")
    assert created.asset.currency == "EUR"
    assert created.details == THREE_YEARS and created.repayment is None
    assert repository.list_holdings() == [created]
    second = repository.create("Bon 3 ans bis", THREE_YEARS)
    assert second.asset.symbol != created.asset.symbol


def test_holdings_are_ordered_by_maturity(conn: Any) -> None:
    repository = TermNoteRepository(conn)
    repository.create("Long", ANNUAL)
    repository.create("Court", DUE)
    assert [h.asset.name for h in repository.list_holdings()] == ["Court", "Long"]


def test_create_validates(conn: Any) -> None:
    with pytest.raises(ValueError, match="(?i)nom"):
        TermNoteRepository(conn).create(" ", THREE_YEARS)
    assert TermNoteRepository(conn).list_holdings() == []


def test_update(conn: Any) -> None:
    repository = TermNoteRepository(conn)
    created = repository.create("Bon", THREE_YEARS)
    assert created.asset.id is not None
    changed = TermNoteDetails(
        "Autre", D(12000), D(3), date(2025, 4, 1), date(2029, 4, 1), InterestPayout.ANNUAL
    )
    updated = repository.update(created.asset.id, "Renommé", changed)
    assert updated.asset.name == "Renommé" and updated.details == changed
    assert updated.asset.symbol == created.asset.symbol
    with pytest.raises(ValueError, match="(?i)introuvable"):
        repository.update(999, "X", changed)


def test_repay_cancel_and_double_repay(conn: Any) -> None:
    repository = TermNoteRepository(conn)
    created = repository.create("Bon", DUE)
    assert created.asset.id is not None
    repayment = Repayment(date(2026, 9, 5), D(5000), D("279.50"))
    repaid = repository.repay(created.asset.id, repayment)
    assert repaid.repayment == repayment
    with pytest.raises(ValueError, match="(?i)déjà remboursé"):
        repository.repay(created.asset.id, repayment)
    assert repository.cancel_repayment(created.asset.id).repayment is None


def test_repay_validates_and_does_not_write(conn: Any) -> None:
    repository = TermNoteRepository(conn)
    created = repository.create("Bon", DUE)
    assert created.asset.id is not None
    with pytest.raises(ValueError, match="(?i)précéder"):
        repository.repay(created.asset.id, Repayment(date(2020, 1, 1), D(1), D(0)))
    holding = repository.get_holding(created.asset.id)
    assert holding is not None and holding.repayment is None


def test_delete_removes_the_note_and_its_exposures(conn: Any) -> None:
    repository = TermNoteRepository(conn)
    created = repository.create("Bon", DUE)
    assert created.asset.id is not None
    exposure = ExposureRepository(conn)
    exposure.set(created.asset.id, ExposureKind.SECTOR, [ExposureEntry("Finance", D(100))])
    repository.delete(created.asset.id)
    assert repository.list_holdings() == []
    assert exposure.get(created.asset.id, ExposureKind.SECTOR) == []
