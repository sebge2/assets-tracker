"""Cache des taux de change : un taux par devise et par jour, vers l'euro."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from assets_tracker.domain.fx import BASE_CURRENCY, ExchangeRate, validate_rate


class FxRepository:
    """Réécrire le même jour met le taux à jour ; les jours passés restent en historique."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def record(
        self, currency: str, rate_date: date, rate: Decimal, fetched_at: datetime
    ) -> ExchangeRate:
        currency = currency.strip().upper()
        if not currency or currency == BASE_CURRENCY:
            raise ValueError("Une devise étrangère est attendue.")
        validate_rate(rate)
        with self._conn:
            self._conn.execute(
                "INSERT INTO exchange_rates (currency, rate_date, rate, fetched_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(currency, rate_date) DO UPDATE SET "
                "rate = excluded.rate, fetched_at = excluded.fetched_at",
                (
                    currency,
                    rate_date.isoformat(),
                    str(rate),
                    fetched_at.isoformat(timespec="seconds"),
                ),
            )
        return ExchangeRate(currency, rate_date, rate, fetched_at)

    def latest_rates(self) -> dict[str, ExchangeRate]:
        """Dernier taux connu de chaque devise."""
        rows = self._conn.execute(
            "SELECT e.* FROM exchange_rates e WHERE e.rate_date = "
            "(SELECT MAX(rate_date) FROM exchange_rates WHERE currency = e.currency)"
        ).fetchall()
        return {row["currency"]: _rate(row) for row in rows}

    def latest(self, currency: str) -> ExchangeRate | None:
        return self.latest_rates().get(currency.strip().upper())


def _rate(row: Any) -> ExchangeRate:
    return ExchangeRate(
        row["currency"],
        date.fromisoformat(row["rate_date"]),
        Decimal(row["rate"]),
        datetime.fromisoformat(row["fetched_at"]),
    )
