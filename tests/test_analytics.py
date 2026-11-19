from datetime import date
from decimal import Decimal

from assets_tracker.domain.analytics import (
    analyze,
    annualized,
    capital_invested,
    gains_by_type,
    performance,
    realized_between,
    units_from,
)
from assets_tracker.domain.cash import CashAccount, CashMovement, MovementKind
from assets_tracker.domain.models import InstrumentType, Transaction, TransactionType
from assets_tracker.domain.portfolio import CashSource, PortfolioInput, PricedSource

D = Decimal


def _account(currency: str = "EUR") -> CashAccount:
    return CashAccount(
        1,
        "Livret",
        "Banque",
        "BE68539007547034",
        currency,
        date(2023, 1, 1),
        payout_month=12,
        payout_day=31,
    )


def _move(kind: MovementKind, day: date, amount: str) -> CashMovement:
    return CashMovement(None, 1, kind, day, D(amount))


def _tx(kind: TransactionType, day: date, qty: str, price: str) -> Transaction:
    return Transaction(None, 7, kind, day, D(qty), D(price))


def _data() -> PortfolioInput:
    cash = CashSource(
        _account(),
        [
            _move(MovementKind.OPENING, date(2023, 1, 1), "1000"),
            _move(MovementKind.INTEREST, date(2023, 12, 31), "30"),
        ],
    )
    stock = PricedSource(
        InstrumentType.STOCK,
        [
            _tx(TransactionType.BUY, date(2023, 1, 10), "10", "100"),
            _tx(TransactionType.SELL, date(2024, 3, 1), "5", "150"),
        ],
        [
            (date(2023, 1, 10), D(100)),
            (date(2023, 12, 31), D(120)),
            (date(2024, 3, 1), D(150)),
            (date(2024, 12, 31), D(160)),
        ],
        name="ACME",
        distributions=[(date(2024, 6, 1), D(20))],
        income=[(date(2024, 6, 1), D(20))],
    )
    return PortfolioInput([cash], [stock])


def test_year_gain_matches_value_change_minus_contributions() -> None:
    units = units_from(_data())
    result = performance(units, date(2023, 12, 31), date(2024, 12, 31))
    # début : 1030 cash + 1200 actions ; fin : 1030 + 5*160 = 1830
    assert result.start == D(2230)
    assert result.end == D(1830)
    assert result.contributions == D(-750)  # vente de 5 × 150
    assert result.distributions == D(20)
    assert result.gain == D(1830) - D(2230) + D(750) + D(20)


def test_realized_and_latent_split() -> None:
    units = units_from(_data())
    assert realized_between(units, date(2023, 12, 31), date(2024, 12, 31)) == D(250) + D(20)
    assert realized_between(units, date(2022, 12, 31), date(2023, 12, 31)) == D(30)


def test_gains_by_type_and_capital() -> None:
    units = units_from(_data())
    gains = gains_by_type(units, date(2023, 12, 31), date(2024, 12, 31))
    assert gains[InstrumentType.STOCK] == D(800) - D(1200) + D(750) + D(20)
    assert gains.get(InstrumentType.CASH) is None
    assert capital_invested(units, date(2023, 12, 31), date(2024, 12, 31)) == D(2230) - D(750)


def test_twr_ignores_contributions() -> None:
    cash = CashSource(
        _account(),
        [
            _move(MovementKind.OPENING, date(2023, 1, 1), "1000"),
            _move(MovementKind.DEPOSIT, date(2023, 6, 15), "1000"),
        ],
    )
    result = performance(
        units_from(PortfolioInput([cash], [])), date(2022, 12, 31), date(2023, 12, 31)
    )
    assert result.twr == D(0)
    assert result.gain == D(0)


def test_foreign_currency_cash_is_ignored() -> None:
    cash = CashSource(_account("USD"), [_move(MovementKind.OPENING, date(2023, 1, 1), "1000")])
    assert units_from(PortfolioInput([cash], [])) == []


def test_analyze_covers_every_year() -> None:
    result = analyze(units_from(_data()), date(2025, 3, 1))
    assert result is not None
    assert [y.year for y in result.years] == [2023, 2024, 2025]
    assert result.types[0] is InstrumentType.CASH or result.types[0] is InstrumentType.STOCK
    assert result.years[1].income[InstrumentType.STOCK] == D(20)
    assert result.annualized is not None


def test_analyze_without_activity_is_none() -> None:
    assert analyze([], date(2025, 1, 1)) is None


def test_annualized() -> None:
    value = annualized(D(21), date(2022, 12, 31), date(2024, 12, 31))
    assert value is not None
    assert abs(value - D(10)) < D("0.1")
    assert annualized(None, date(2023, 1, 1), date(2024, 1, 1)) is None


def test_bond_coupons_follow_the_quantity_held() -> None:
    from assets_tracker.domain.bonds import BondDetails, CouponFrequency, coupon_payments
    from assets_tracker.domain.funds import ValuationSource

    details = BondDetails(
        "Etat", D(1000), D(4), CouponFrequency.ANNUAL, date(2030, 6, 1), ValuationSource.MANUAL
    )
    trades = [
        _tx(TransactionType.BUY, date(2023, 1, 10), "10", "1000"),
        _tx(TransactionType.SELL, date(2025, 1, 1), "4", "1000"),
    ]
    payments = coupon_payments(details, trades, date(2026, 1, 1))
    assert payments == [
        (date(2023, 6, 1), D(400)),
        (date(2024, 6, 1), D(400)),
        (date(2025, 6, 1), D(240)),
    ]


def test_term_note_annual_interest_is_a_distribution() -> None:
    from assets_tracker.domain.term_notes import (
        InterestPayout,
        Repayment,
        TermNoteDetails,
        interest_income,
    )

    details = TermNoteDetails(
        "Banque", D(1000), D(3), date(2023, 3, 1), date(2026, 3, 1), InterestPayout.ANNUAL
    )
    paid, income = interest_income(details, None, date(2025, 6, 1))
    assert paid == [(date(2024, 3, 1), D(30)), (date(2025, 3, 1), D(30))]
    assert income == paid
    repayment = Repayment(date(2026, 3, 1), D(1000), D(30))
    paid, income = interest_income(details, repayment, date(2026, 6, 1))
    assert len(paid) == 2
    assert income[-1] == (date(2026, 3, 1), D(30))
