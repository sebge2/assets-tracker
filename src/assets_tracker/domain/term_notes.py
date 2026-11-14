"""Bons de caisse : intérêts courus, échéancier, remboursement et valeur dans le portefeuille.

Logique pure (ni Qt, ni SQLite, ni réseau). Un bon n'a pas de cours : sa valeur est son capital
plus les intérêts courus depuis le dernier versement, au prorata journalier sur 365 jours.
Les intérêts sont versés soit à l'échéance (`MATURITY`), soit à chaque anniversaire de la
souscription (`ANNUAL`, dernière période courte au prorata si la durée n'est pas entière).
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from assets_tracker.domain.models import Transaction, TransactionType

ZERO = Decimal(0)
HUNDRED = Decimal(100)
CENT = Decimal("0.01")
DAYS_IN_YEAR = Decimal(365)
DUE_SOON_DAYS = 30  # une échéance à moins de 30 jours est mise en avant

__all__ = [
    "DUE_SOON_DAYS",
    "InterestPayout",
    "NoteTotals",
    "NoteValue",
    "Repayment",
    "TermNoteDetails",
    "accrued_interest",
    "interest_paid",
    "interest_payments",
    "interest_total",
    "interest_to_receive",
    "ladder",
    "next_maturity",
    "note_totals",
    "note_value",
    "payment_dates",
    "portfolio_prices",
    "portfolio_transactions",
    "remaining_label",
    "repayment_proposal",
    "term_label",
    "validate_details",
    "validate_repayment",
]


class InterestPayout(StrEnum):
    MATURITY = "MATURITY"
    ANNUAL = "ANNUAL"

    @property
    def label(self) -> str:
        return {"MATURITY": "à l'échéance", "ANNUAL": "chaque année"}[self.value]


@dataclass(frozen=True, slots=True)
class TermNoteDetails:
    issuer: str
    capital: Decimal  # en euros
    rate: Decimal  # taux annuel en %
    start: date
    end: date
    payout: InterestPayout


@dataclass(frozen=True, slots=True)
class Repayment:
    paid_on: date
    capital: Decimal
    interest: Decimal

    @property
    def total(self) -> Decimal:
        return self.capital + self.interest


@dataclass(frozen=True, slots=True)
class NoteValue:
    """Situation d'un bon à une date."""

    value: Decimal  # capital + intérêts courus (0 une fois remboursé)
    accrued: Decimal
    paid: Decimal  # intérêts déjà versés
    total: Decimal  # intérêts sur toute la durée
    to_receive: Decimal  # intérêts restant à recevoir (courus compris)
    elapsed_days: int
    total_days: int
    ended: bool  # échéance atteinte
    repaid: bool

    @property
    def elapsed_percent(self) -> Decimal:
        if self.total_days <= 0:
            return ZERO
        return Decimal(self.elapsed_days) / Decimal(self.total_days) * HUNDRED


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _anniversary(start: date, years: int) -> date:
    year = start.year + years
    day = min(start.day, calendar.monthrange(year, start.month)[1])
    return date(year, start.month, day)


def _yearly(details: TermNoteDetails) -> Decimal:
    return details.capital * details.rate / HUNDRED


def _prorata(details: TermNoteDetails, days: int) -> Decimal:
    return _yearly(details) * days / DAYS_IN_YEAR


def term_days(details: TermNoteDetails) -> int:
    return (details.end - details.start).days


def payment_dates(details: TermNoteDetails) -> list[date]:
    """Dates de versement des intérêts, la dernière étant toujours l'échéance."""
    if details.payout is InterestPayout.MATURITY:
        return [details.end]
    dates: list[date] = []
    years = 1
    while (when := _anniversary(details.start, years)) < details.end:
        dates.append(when)
        years += 1
    dates.append(details.end)
    return dates


def _period_interest(details: TermNoteDetails, begin: date, finish: date) -> Decimal:
    days = (finish - begin).days
    full_year = _anniversary(begin, 1) == finish
    if details.payout is InterestPayout.ANNUAL and full_year:
        return _yearly(details)
    return _prorata(details, days)


def _periods(details: TermNoteDetails) -> list[tuple[date, date, Decimal]]:
    begin = details.start
    periods = []
    for finish in payment_dates(details):
        periods.append((begin, finish, _period_interest(details, begin, finish)))
        begin = finish
    return periods


def interest_total(details: TermNoteDetails) -> Decimal:
    """Intérêts sur toute la durée du bon."""
    return sum((interest for _, _, interest in _periods(details)), ZERO)


def interest_paid(details: TermNoteDetails, on: date) -> Decimal:
    """Intérêts déjà versés à `on` ; le dernier versement se fait au remboursement, il est exclu."""
    return sum(
        (i for _, finish, i in _periods(details) if finish <= on and finish < details.end), ZERO
    )


def interest_payments(details: TermNoteDetails) -> list[tuple[date, Decimal]]:
    """Versements d'intérêts avant l'échéance (le dernier se fait au remboursement)."""
    return [(finish, i) for _, finish, i in _periods(details) if finish < details.end]


def accrued_interest(details: TermNoteDetails, on: date) -> Decimal:
    """Intérêts courus depuis le dernier versement (0 avant la souscription ou une fois versés)."""
    if on <= details.start:
        return ZERO
    for begin, finish, interest in _periods(details):
        if begin <= on < finish:
            days = (on - begin).days
            return min(interest, _prorata(details, days))
    return ZERO


def interest_to_receive(details: TermNoteDetails, on: date) -> Decimal:
    return interest_total(details) - interest_paid(details, on)


def note_value(
    details: TermNoteDetails, on: date, repayment: Repayment | None = None
) -> NoteValue:
    total_days = term_days(details)
    elapsed = max(0, min(total_days, (on - details.start).days))
    if repayment is not None:
        return NoteValue(
            ZERO,
            ZERO,
            repayment.interest,
            repayment.interest,
            ZERO,
            elapsed,
            total_days,
            True,
            True,
        )
    ended = on >= details.end
    # Échu mais pas remboursé : les intérêts de la dernière période restent à encaisser.
    accrued = _final_period_interest(details) if ended else accrued_interest(details, on)
    total = interest_total(details)
    paid = interest_paid(details, on)
    return NoteValue(
        details.capital + accrued,
        accrued,
        paid,
        total,
        total - paid,
        elapsed,
        total_days,
        ended,
        False,
    )


def _final_period_interest(details: TermNoteDetails) -> Decimal:
    return _periods(details)[-1][2]


def repayment_proposal(details: TermNoteDetails, on: date) -> Repayment:
    """Remboursement attendu à `on` : capital et intérêts de la période en cours (ou finale)."""
    if on >= details.end:
        interest = _final_period_interest(details)
    else:
        interest = accrued_interest(details, on)
    return Repayment(on, details.capital, _round(interest))


def next_maturity(
    notes: Iterable[tuple[str, TermNoteDetails]], today: date
) -> tuple[str, TermNoteDetails] | None:
    upcoming = [item for item in notes if item[1].end >= today]
    return min(upcoming, key=lambda item: item[1].end) if upcoming else None


def remaining_label(end: date, today: date) -> str:
    days = (end - today).days
    if days < 0:
        return f"échu depuis {-days} jour{'s' if -days > 1 else ''}"
    if days == 0:
        return "échéance aujourd'hui"
    if days <= DUE_SOON_DAYS:
        return f"dans {days} jour{'s' if days > 1 else ''}"
    months = round(days / 30.4375)
    years, rest = divmod(months, 12)
    if years == 0:
        return f"{months} mois"
    text = f"{years} an{'s' if years > 1 else ''}"
    return f"{text} et {rest} mois" if rest else text


def term_label(details: TermNoteDetails) -> str:
    years = round(term_days(details) / 365.25)
    if years >= 1:
        return f"{years} an{'s' if years > 1 else ''}"
    months = max(1, round(term_days(details) / 30.4375))
    return f"{months} mois"


@dataclass(frozen=True, slots=True)
class NoteTotals:
    count: int
    capital: Decimal
    value: Decimal
    accrued: Decimal
    to_receive: Decimal
    average_rate: Decimal | None  # taux moyen pondéré par le capital


def note_totals(
    notes: Iterable[tuple[TermNoteDetails, Repayment | None]], on: date
) -> NoteTotals:
    """Totaux des bons non remboursés."""
    count = 0
    capital = value = accrued = to_receive = weighted = ZERO
    for details, repayment in notes:
        if repayment is not None:
            continue
        current = note_value(details, on)
        count += 1
        capital += details.capital
        value += current.value
        accrued += current.accrued
        to_receive += current.to_receive
        weighted += details.capital * details.rate
    return NoteTotals(
        count, capital, value, accrued, to_receive, weighted / capital if capital > 0 else None
    )


def ladder(
    notes: Iterable[tuple[TermNoteDetails, Repayment | None]], today: date
) -> dict[int, tuple[Decimal, Decimal]]:
    """Capital et intérêts encore à recevoir par année (un bon échu compte à son échéance)."""
    years: dict[int, tuple[Decimal, Decimal]] = {}
    for details, repayment in notes:
        if repayment is not None:
            continue
        for _, finish, interest in _periods(details):
            if finish <= today and finish < details.end:
                continue  # versement déjà perçu
            capital = details.capital if finish == details.end else ZERO
            old_capital, old_interest = years.get(finish.year, (ZERO, ZERO))
            years[finish.year] = (old_capital + capital, old_interest + interest)
    return dict(sorted(years.items()))


def validate_details(name: str, details: TermNoteDetails) -> None:
    if not name.strip():
        raise ValueError("Le nom du bon de caisse est obligatoire.")
    if not details.issuer.strip():
        raise ValueError("L'émetteur est obligatoire.")
    if details.capital <= 0:
        raise ValueError("Le capital doit être strictement positif.")
    if details.rate < 0 or details.rate > HUNDRED:
        raise ValueError("Le taux doit être compris entre 0 et 100 %.")
    if details.end <= details.start:
        raise ValueError("L'échéance doit être postérieure à la souscription.")


def validate_repayment(details: TermNoteDetails, repayment: Repayment) -> None:
    if repayment.paid_on < details.start:
        raise ValueError("Le remboursement ne peut pas précéder la souscription.")
    if repayment.capital <= 0:
        raise ValueError("Le capital remboursé doit être strictement positif.")
    if repayment.interest < 0:
        raise ValueError("Les intérêts reçus ne peuvent pas être négatifs.")


# -- intégration à l'écran Portefeuille --------------------------------------


def _month_ends(start: date, stop: date) -> list[date]:
    days = []
    year, month = start.year, start.month
    while True:
        last = date(year, month, calendar.monthrange(year, month)[1])
        if last >= stop:
            break
        days.append(last)
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return days


def portfolio_transactions(
    asset_id: int, details: TermNoteDetails, repayment: Repayment | None
) -> list[Transaction]:
    """Le bon vu comme une position d'un titre : achat du capital, vente au remboursement."""
    one = Decimal(1)
    transactions = [
        Transaction(None, asset_id, TransactionType.BUY, details.start, one, details.capital, ZERO)
    ]
    if repayment is not None:
        transactions.append(
            Transaction(
                None,
                asset_id,
                TransactionType.SELL,
                repayment.paid_on,
                one,
                repayment.total,
                ZERO,
            )
        )
    return transactions


def portfolio_prices(
    details: TermNoteDetails, today: date, repayment: Repayment | None
) -> list[tuple[date, Decimal]]:
    """Valeur du bon (capital + courus) en fin de mois puis aujourd'hui, comme des cours."""
    stop = min(today, repayment.paid_on if repayment else details.end)
    days = [details.start, *_month_ends(details.start, stop)]
    if stop > details.start:
        days.append(stop)
    seen: dict[date, Decimal] = {}
    for day in days:
        if day >= details.end:
            seen[day] = details.capital + _final_period_interest(details)
        else:
            seen[day] = details.capital + accrued_interest(details, day)
    return sorted(seen.items())
