from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.connection import connect
from assets_tracker.db.valuation_repository import ValuationRepository
from assets_tracker.domain.models import InstrumentType

D = Decimal
COMMODITY, FUND, STOCK = InstrumentType.COMMODITY, InstrumentType.FUND, InstrumentType.STOCK
KEY = b"\x08" * 32


def add_asset(conn: Any, symbol: str) -> int:
    cursor = conn.execute("INSERT INTO assets (symbol, name) VALUES (?, ?)", (symbol, symbol))
    conn.commit()
    return int(cursor.lastrowid)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: KEY)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn: Any) -> ValuationRepository:
    return ValuationRepository(conn)


def test_records_one_value_per_asset_and_day(repo: ValuationRepository, conn: Any) -> None:
    gold = add_asset(conn, "XAU")
    repo.record(COMMODITY, gold, date(2026, 11, 13), D("71.85"), datetime(2026, 11, 13, 9, 0))
    repo.record(COMMODITY, gold, date(2026, 11, 13), D("72.10"), datetime(2026, 11, 13, 15, 30))

    history = repo.history(COMMODITY, gold)

    assert len(history) == 1  # le même jour est mis à jour, pas dupliqué
    assert history[0].value == D("72.10")
    assert history[0].fetched_at == datetime(2026, 11, 13, 15, 30)


def test_history_keeps_every_day_in_order(repo: ValuationRepository, conn: Any) -> None:
    gold = add_asset(conn, "XAU")
    for day, value in ((13, "72"), (11, "70"), (12, "71")):
        repo.record(COMMODITY, gold, date(2026, 11, day), D(value), datetime(2026, 11, day, 9, 0))

    assert [v.value for v in repo.history(COMMODITY, gold)] == [D(70), D(71), D(72)]
    latest = repo.latest(COMMODITY, gold)
    assert latest is not None and latest.valuation_date == date(2026, 11, 13)


def test_past_days_are_never_modified_by_a_new_day(repo: ValuationRepository, conn: Any) -> None:
    gold = add_asset(conn, "XAU")
    repo.record(COMMODITY, gold, date(2026, 11, 12), D("70"), datetime(2026, 11, 12, 9, 0))
    repo.record(COMMODITY, gold, date(2026, 11, 13), D("72"), datetime(2026, 11, 13, 9, 0))

    yesterday = repo.on_day(COMMODITY, gold, date(2026, 11, 12))

    assert yesterday is not None and yesterday.value == D(70)


def test_unknown_asset_or_day_gives_none(repo: ValuationRepository, conn: Any) -> None:
    gold = add_asset(conn, "XAU")
    assert repo.latest(COMMODITY, gold) is None
    assert repo.on_day(COMMODITY, gold, date(2026, 11, 13)) is None
    assert repo.history(COMMODITY, gold) == []


def test_type_and_asset_are_part_of_the_key(repo: ValuationRepository, conn: Any) -> None:
    gold = add_asset(conn, "XAU")
    fund = add_asset(conn, "CW8")
    day = date(2026, 11, 13)
    stamp = datetime(2026, 11, 13, 9, 0)
    repo.record(COMMODITY, gold, day, D("71"), stamp)
    repo.record(FUND, fund, day, D("412"), stamp)
    repo.record(STOCK, gold, day, D("5"), stamp)  # même actif, autre type : ligne distincte

    assert repo.latest(COMMODITY, gold).value == D(71)  # type: ignore[union-attr]
    assert repo.latest(FUND, fund).value == D(412)  # type: ignore[union-attr]
    assert repo.latest(STOCK, gold).value == D(5)  # type: ignore[union-attr]
    assert repo.latest(FUND, gold) is None


def test_valued_on_lists_assets_already_valued_that_day(
    repo: ValuationRepository, conn: Any
) -> None:
    gold, silver, fund = add_asset(conn, "XAU"), add_asset(conn, "XAG"), add_asset(conn, "CW8")
    today, yesterday = date(2026, 11, 13), date(2026, 11, 12)
    stamp = datetime(2026, 11, 13, 9, 0)
    repo.record(COMMODITY, gold, today, D("71"), stamp)
    repo.record(COMMODITY, silver, yesterday, D("28"), stamp)
    repo.record(FUND, fund, today, D("412"), stamp)

    assert repo.valued_on(COMMODITY, today) == {gold}
    assert repo.valued_on(FUND, today) == {fund}
    assert repo.valued_on(COMMODITY, yesterday) == {silver}


def test_value_is_stored_as_text_and_must_be_positive(repo: ValuationRepository, conn: Any) -> None:
    gold = add_asset(conn, "XAU")
    repo.record(COMMODITY, gold, date(2026, 11, 13), D("71.85"), datetime(2026, 11, 13, 9, 0))

    kind = conn.execute("SELECT typeof(value) AS t FROM valuations").fetchone()["t"]
    assert kind == "text"
    with pytest.raises(ValueError, match="strictement positive"):
        repo.record(COMMODITY, gold, date(2026, 11, 14), D(0), datetime(2026, 11, 14, 9, 0))
    assert len(repo.history(COMMODITY, gold)) == 1


def test_unknown_asset_type_is_refused_by_the_database(conn: Any) -> None:
    gold = add_asset(conn, "XAU")
    with pytest.raises(Exception, match="CHECK"):
        conn.execute(
            "INSERT INTO valuations VALUES ('WINE', ?, '2026-11-13', '10', '2026-11-13T09:00:00')",
            (gold,),
        )


def test_deleting_an_asset_removes_its_valuations(repo: ValuationRepository, conn: Any) -> None:
    gold = add_asset(conn, "XAU")
    repo.record(COMMODITY, gold, date(2026, 11, 13), D("71"), datetime(2026, 11, 13, 9, 0))

    conn.execute("DELETE FROM assets WHERE id = ?", (gold,))
    conn.commit()

    assert conn.execute("SELECT count(*) AS n FROM valuations").fetchone()["n"] == 0


def test_migration_turns_existing_commodity_prices_into_valuations(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.db"
    first = connect(path, key_provider=lambda: KEY)
    gold = add_asset(first, "XAU")
    stock = add_asset(first, "AAPL")
    first.execute(
        "INSERT INTO commodity_details (asset_id, family, form, unit) "
        "VALUES (?, 'PRECIOUS', 'PHYSICAL', 'g')",
        (gold,),
    )
    first.execute(
        "INSERT INTO prices (asset_id, price, fetched_at) "
        "VALUES (?, '71.85', '2026-11-12T21:03:41')",
        (gold,),
    )
    first.execute(
        "INSERT INTO prices (asset_id, price, fetched_at) VALUES (?, '190', '2026-11-12T21:03:41')",
        (stock,),
    )
    first.execute("PRAGMA user_version = 1")  # base créée avant la table des valorisations
    first.commit()
    first.close()

    second = connect(path, key_provider=lambda: KEY)
    try:
        repo = ValuationRepository(second)
        migrated = repo.latest(COMMODITY, gold)
        assert migrated is not None
        assert migrated.value == D("71.85")
        assert migrated.valuation_date == date(2026, 11, 12)
        assert migrated.fetched_at == datetime(2026, 11, 12, 21, 3, 41)
        assert repo.latest(STOCK, stock) is None  # seules les matières premières sont reprises
        assert second.execute("SELECT count(*) AS n FROM prices").fetchone()["n"] == 2  # intacte
        assert int(second.execute("PRAGMA user_version").fetchone()[0]) == 2
    finally:
        second.close()

    third = connect(path, key_provider=lambda: KEY)  # rouvrir ne duplique rien
    try:
        assert len(ValuationRepository(third).history(COMMODITY, gold)) == 1
    finally:
        third.close()
