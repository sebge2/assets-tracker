from __future__ import annotations

from datetime import date
from decimal import Decimal

from assets_tracker.domain.cash import (
    CashAccount,
    CashMovement,
    InterestFrequency,
    MovementKind,
)
from assets_tracker.domain.models import InstrumentType, Transaction, TransactionType
from assets_tracker.domain.portfolio import (
    CashSource,
    PortfolioInput,
    PricedSource,
    allocation,
    build_portfolio,
    change,
    unit_price_on,
    value_on,
    window,
)

D = Decimal
BUY, SELL = TransactionType.BUY, TransactionType.SELL
IBAN = "FR7630006000011234567890189"
TODAY = date(2026, 11, 13)


def account(currency: str = "EUR", opened: date = date(2026, 1, 10)) -> CashAccount:
    return CashAccount(1, "Livret", "Banque", IBAN, currency, opened, InterestFrequency.MONTHLY)


def deposit(day: date, amount: str, kind: MovementKind = MovementKind.DEPOSIT) -> CashMovement:
    return CashMovement(None, 1, kind, day, D(amount))


def gold(prices: list[tuple[date, Decimal]] | None = None) -> PricedSource:
    return PricedSource(
        InstrumentType.COMMODITY,
        [
            Transaction(1, 7, BUY, date(2026, 3, 5), D(100), D(60), D(0)),
            Transaction(2, 7, SELL, date(2026, 6, 5), D(20), D(70), D(0)),
        ],
        prices if prices is not None else [(date(2026, 11, 13), D(72))],
    )


def test_unit_price_uses_last_quote_then_last_trade() -> None:
    source = gold([(date(2026, 8, 1), D(65)), (date(2026, 11, 13), D(72))])
    assert unit_price_on(source, date(2026, 11, 13)) == D(72)
    assert unit_price_on(source, date(2026, 9, 1)) == D(65)
    assert unit_price_on(source, date(2026, 7, 1)) == D(70)  # dernière transaction (vente)
    assert unit_price_on(source, date(2026, 4, 1)) == D(60)
    assert unit_price_on(source, date(2026, 1, 1)) is None


def test_value_on_follows_the_quantity_held_that_day() -> None:
    source = gold([])
    assert value_on(source, date(2026, 1, 1)) == 0
    assert value_on(source, date(2026, 4, 1)) == D(6000)  # 100 × 60
    assert value_on(source, date(2026, 7, 1)) == D(5600)  # 80 × 70


def test_summary_splits_value_gain_and_realized() -> None:
    data = PortfolioInput([CashSource(account(), [deposit(date(2026, 1, 10), "1000")])], [gold()])
    summary = build_portfolio(data, TODAY).summary

    assert summary.total == D(1000) + D(80 * 72)  # 6 760
    assert summary.unrealized == D(80 * 72 - 80 * 60)  # 960
    assert summary.unrealized_percent == D(960) / D(4800) * 100
    assert summary.realized == D(200)  # 20 × (70 − 60)


def test_allocation_is_sorted_and_percentages_sum_to_100() -> None:
    slices = allocation(
        {
            InstrumentType.CASH: D(1000),
            InstrumentType.COMMODITY: D(3000),
            InstrumentType.STOCK: D(0),
        }
    )
    assert [s.asset_type for s in slices] == [InstrumentType.COMMODITY, InstrumentType.CASH]
    assert [s.percent for s in slices] == [D(75), D(25)]
    assert allocation({}) == []


def test_foreign_currency_cash_is_excluded_and_reported() -> None:
    usd = CashSource(account("USD"), [deposit(date(2026, 1, 10), "500")])
    portfolio = build_portfolio(PortfolioInput([usd], []), TODAY)

    assert portfolio.summary.total == 0
    assert portfolio.slices == []
    assert portfolio.foreign_cash == ("USD",)


def test_no_gain_percentage_without_invested_assets() -> None:
    data = PortfolioInput([CashSource(account(), [deposit(date(2026, 1, 10), "1000")])], [])
    summary = build_portfolio(data, TODAY).summary
    assert summary.unrealized == 0
    assert summary.unrealized_percent is None


def test_monthly_totals_start_at_the_first_activity() -> None:
    data = PortfolioInput(
        [
            CashSource(
                account(),
                [deposit(date(2026, 1, 10), "1000"), deposit(date(2026, 9, 2), "500")],
            )
        ],
        [gold([])],
    )
    portfolio = build_portfolio(data, TODAY)

    assert portfolio.months[0] == date(2026, 1, 1)
    assert portfolio.months[-1] == date(2026, 11, 1)
    assert len(portfolio.months) == len(portfolio.totals) == 11
    assert portfolio.totals[0] == D(1000)  # janvier : cash seul
    assert portfolio.totals[2] == D(1000) + D(6000)  # mars : or acheté
    assert portfolio.totals[5] == D(1000) + D(5600)  # juin : vente de 20 g
    assert portfolio.totals[8] == D(1500) + D(5600)  # septembre : nouveau dépôt
    assert portfolio.totals[-1] == portfolio.summary.total


def test_empty_portfolio_has_no_history() -> None:
    portfolio = build_portfolio(PortfolioInput([], []), TODAY)
    assert portfolio.months == [] and portfolio.totals == [] and portfolio.slices == []
    assert portfolio.summary.total == 0


def test_window_keeps_the_last_months_and_all_on_zero() -> None:
    months = [date(2026, m, 1) for m in range(1, 7)]
    totals = [D(m) for m in range(1, 7)]

    assert window(months, totals, 3) == (months[-3:], totals[-3:])
    assert window(months, totals, 12) == (months, totals)  # pas avant le premier mois
    assert window(months, totals, 0) == (months, totals)


def test_change_gives_amount_and_percentage() -> None:
    assert change([D(100), D(110), D(125)]) == (D(25), D(25))
    assert change([D(0), D(50)]) == (D(50), None)
    assert change([D(10)]) == (0, None)
