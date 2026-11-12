"""Comptes cash : taux, primes de fidélité, mouvements et intérêts (logique pure)."""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

ZERO = Decimal(0)
_CENT = Decimal("0.01")
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")


class InterestFrequency(StrEnum):
    MONTHLY = "MONTHLY"  # versé le 1er de chaque mois
    ANNUAL = "ANNUAL"  # versé à un mois et un jour fixés


class MovementKind(StrEnum):
    OPENING = "OPENING"  # solde repris à l'ouverture
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    INTEREST = "INTEREST"
    LOYALTY_BONUS = "LOYALTY_BONUS"


class BonusEventKind(StrEnum):
    DEFINED = "DEFINED"  # la prime est définie (ou modifiée)
    ACTIVATED = "ACTIVATED"  # la condition de détention est remplie
    REMOVED = "REMOVED"  # la prime est supprimée


def normalize_iban(raw: str) -> str:
    """Retire les espaces et passe en majuscules."""
    return "".join(raw.split()).upper()


def is_valid_iban(iban: str) -> bool:
    """Vérifie le format et la clé de contrôle (modulo 97) d'un IBAN normalisé."""
    if not _IBAN_RE.match(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1


def format_iban(iban: str) -> str:
    """Groupe l'IBAN par blocs de 4 caractères."""
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def mask_iban(iban: str) -> str:
    """`FR76 •••• •••• 0143` : seuls le préfixe et les 4 derniers caractères restent visibles."""
    return f"{iban[:4]} •••• •••• {iban[-4:]}"


def add_months(start: date, months: int) -> date:
    """Ajoute des mois en ramenant le jour à la fin du mois si nécessaire."""
    index = start.year * 12 + (start.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass(frozen=True, slots=True)
class CashAccount:
    id: int | None
    name: str
    bank: str
    iban: str  # normalisé (sans espaces, majuscules)
    currency: str
    opened_on: date
    frequency: InterestFrequency = InterestFrequency.ANNUAL
    payout_month: int | None = None  # versement annuel : mois (1-12)
    payout_day: int | None = None  # versement annuel : jour du mois

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Le nom du compte est obligatoire.")
        if not self.bank.strip():
            raise ValueError("La banque est obligatoire.")
        if not is_valid_iban(self.iban):
            raise ValueError("L'IBAN est invalide.")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("La devise doit comporter 3 lettres (ex. EUR).")
        if self.frequency is InterestFrequency.MONTHLY:
            if self.payout_month is not None or self.payout_day is not None:
                raise ValueError("Un versement mensuel n'a ni mois ni jour : c'est le 1er du mois.")
            return
        if self.payout_month is None or self.payout_day is None:
            raise ValueError("Un versement annuel demande un mois et un jour.")
        if not 1 <= self.payout_month <= 12:
            raise ValueError("Le mois de versement doit être compris entre 1 et 12.")
        if not 1 <= self.payout_day <= calendar.monthrange(2023, self.payout_month)[1]:
            raise ValueError("Le jour de versement n'existe pas dans ce mois.")


@dataclass(frozen=True, slots=True)
class BaseRateChange:
    effective_date: date
    rate: Decimal  # taux annuel en %, ex. Decimal("3.00")

    def __post_init__(self) -> None:
        if not ZERO <= self.rate <= 100:
            raise ValueError("Le taux doit être compris entre 0 et 100 %.")


@dataclass(frozen=True, slots=True)
class BonusDefinition:
    """Prime de fidélité : taux additionnel après `after_months` mois de détention.

    Un taux nul retire la prime à partir de `effective_date`.
    """

    effective_date: date
    rate: Decimal
    after_months: int

    def __post_init__(self) -> None:
        if not ZERO <= self.rate <= 100:
            raise ValueError("Le taux de la prime doit être compris entre 0 et 100 %.")
        if self.after_months < 0:
            raise ValueError("La condition de détention ne peut pas être négative.")


@dataclass(frozen=True, slots=True)
class CashMovement:
    id: int | None
    account_id: int
    kind: MovementKind
    movement_date: date
    amount: Decimal  # toujours positif ; le sens dépend de `kind`

    def __post_init__(self) -> None:
        if self.amount <= ZERO:
            raise ValueError("Le montant doit être strictement positif.")

    @property
    def signed_amount(self) -> Decimal:
        return -self.amount if self.kind is MovementKind.WITHDRAWAL else self.amount


@dataclass(frozen=True, slots=True)
class StatementLine:
    movement: CashMovement
    balance_after: Decimal


@dataclass(frozen=True, slots=True)
class RateChangeRow:
    effective_date: date
    rate: Decimal
    delta: Decimal | None  # variation en points vs le taux précédent (None = ouverture)


@dataclass(frozen=True, slots=True)
class BonusEvent:
    event_date: date
    kind: BonusEventKind
    rate: Decimal
    after_months: int


# --- Taux -------------------------------------------------------------------


def base_rate_on(changes: Iterable[BaseRateChange], on: date) -> Decimal:
    """Taux de base en vigueur à une date (0 avant le premier taux)."""
    applicable = [c for c in changes if c.effective_date <= on]
    if not applicable:
        return ZERO
    return max(enumerate(applicable), key=lambda pair: (pair[1].effective_date, pair[0]))[1].rate


def bonus_activation_date(opened_on: date, definition: BonusDefinition) -> date:
    """Date à laquelle la condition de détention d'une prime est remplie."""
    return max(definition.effective_date, add_months(opened_on, definition.after_months))


def bonus_on(opened_on: date, definitions: Iterable[BonusDefinition], on: date) -> Decimal:
    """Prime en vigueur à une date : définition la plus récente, si sa condition est remplie."""
    applicable = [d for d in definitions if d.effective_date <= on]
    if not applicable:
        return ZERO
    latest = max(enumerate(applicable), key=lambda pair: (pair[1].effective_date, pair[0]))[1]
    if latest.rate > ZERO and on >= bonus_activation_date(opened_on, latest):
        return latest.rate
    return ZERO


def effective_rate_on(
    opened_on: date,
    changes: Sequence[BaseRateChange],
    definitions: Sequence[BonusDefinition],
    on: date,
) -> Decimal:
    """Taux effectif = taux de base + prime de fidélité si la condition est remplie."""
    return base_rate_on(changes, on) + bonus_on(opened_on, definitions, on)


def effective_rate_steps(
    opened_on: date,
    changes: Sequence[BaseRateChange],
    definitions: Sequence[BonusDefinition],
    today: date,
) -> list[tuple[date, Decimal]]:
    """Points de changement du taux effectif jusqu'à `today`, pour l'historique en escalier."""
    dates = {c.effective_date for c in changes}
    for definition in definitions:
        dates.add(definition.effective_date)
        dates.add(bonus_activation_date(opened_on, definition))
    return [
        (when, effective_rate_on(opened_on, changes, definitions, when))
        for when in sorted(d for d in dates if d <= today)
    ]


def base_rate_history(changes: Iterable[BaseRateChange]) -> list[RateChangeRow]:
    """Historique chronologique du taux de base avec la variation en points."""
    ordered = sorted(enumerate(changes), key=lambda pair: (pair[1].effective_date, pair[0]))
    rows: list[RateChangeRow] = []
    previous: Decimal | None = None
    for _, change in ordered:
        delta = None if previous is None else change.rate - previous
        rows.append(RateChangeRow(change.effective_date, change.rate, delta))
        previous = change.rate
    return rows


_BONUS_ORDER = {BonusEventKind.DEFINED: 0, BonusEventKind.REMOVED: 0, BonusEventKind.ACTIVATED: 1}


def bonus_events(
    opened_on: date, definitions: Iterable[BonusDefinition], today: date
) -> list[BonusEvent]:
    """Historique chronologique des primes : définition, activation, suppression."""
    events: list[BonusEvent] = []
    for definition in definitions:
        if definition.effective_date > today:
            continue
        if definition.rate == ZERO:
            events.append(
                BonusEvent(
                    definition.effective_date, BonusEventKind.REMOVED, ZERO, definition.after_months
                )
            )
            continue
        events.append(
            BonusEvent(
                definition.effective_date,
                BonusEventKind.DEFINED,
                definition.rate,
                definition.after_months,
            )
        )
        activation = bonus_activation_date(opened_on, definition)
        if activation <= today:
            events.append(
                BonusEvent(
                    activation, BonusEventKind.ACTIVATED, definition.rate, definition.after_months
                )
            )
    return sorted(events, key=lambda e: (e.event_date, _BONUS_ORDER[e.kind]))


# --- Mouvements et soldes ---------------------------------------------------

_KIND_ORDER = {
    MovementKind.OPENING: 0,
    MovementKind.DEPOSIT: 1,
    MovementKind.INTEREST: 2,
    MovementKind.LOYALTY_BONUS: 3,
    MovementKind.WITHDRAWAL: 4,
}


def statement(movements: Iterable[CashMovement]) -> list[StatementLine]:
    """Relevé chronologique avec le solde après chaque mouvement."""
    ordered = sorted(movements, key=lambda m: (m.movement_date, _KIND_ORDER[m.kind], m.id or 0))
    balance = ZERO
    lines: list[StatementLine] = []
    for movement in ordered:
        balance += movement.signed_amount
        lines.append(StatementLine(movement, balance))
    return lines


def balance_of(movements: Iterable[CashMovement]) -> Decimal:
    return sum((m.signed_amount for m in movements), ZERO)


def check_movement_allowed(existing: Sequence[CashMovement], candidate: CashMovement) -> None:
    """Refuse un mouvement qui rendrait le solde négatif à un moment quelconque."""
    for line in statement([*existing, candidate]):
        if line.balance_after < ZERO:
            raise ValueError(
                "Solde insuffisant : ce mouvement rendrait le solde négatif "
                f"au {line.movement.movement_date.isoformat()}."
            )


# --- Intérêts ---------------------------------------------------------------


def estimated_annual_interest(balance: Decimal, rate_percent: Decimal) -> Decimal:
    """Intérêts annuels estimés au solde et taux actuels (estimation, sans capitalisation)."""
    return (balance * rate_percent / 100).quantize(_CENT, rounding=ROUND_HALF_UP)


def accrued_interest(
    movements: Iterable[CashMovement],
    start: date,
    end: date,
    annual_rate: Callable[[date], Decimal],
) -> Decimal:
    """Intérêt brut au prorata journalier, sur le solde de fin de journée."""
    if end < start:
        return ZERO
    by_date: dict[date, list[CashMovement]] = {}
    opening_balance = ZERO
    for movement in movements:
        if movement.movement_date < start:
            opening_balance += movement.signed_amount
        elif movement.movement_date <= end:
            by_date.setdefault(movement.movement_date, []).append(movement)
    balance = opening_balance
    accrued = ZERO
    current = start
    while current <= end:
        balance += sum((m.signed_amount for m in by_date.get(current, [])), ZERO)
        accrued += balance * annual_rate(current) / Decimal("36500")
        current += timedelta(days=1)
    return accrued.quantize(_CENT, rounding=ROUND_HALF_UP)


def weighted_average_rate(positions: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    """Taux moyen pondéré par les soldes, à partir de paires (solde, taux %)."""
    total = ZERO
    weighted = ZERO
    for balance, rate in positions:
        total += balance
        weighted += balance * rate
    if total == ZERO:
        return ZERO
    return (weighted / total).quantize(_CENT, rounding=ROUND_HALF_UP)
