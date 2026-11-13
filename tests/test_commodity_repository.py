from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from assets_tracker.db.commodity_repository import CommodityRepository
from assets_tracker.db.connection import connect
from assets_tracker.domain.commodities import CommodityDetails, CommodityFamily, CommodityForm
from assets_tracker.domain.models import Transaction, TransactionType

D = Decimal
BUY, SELL = TransactionType.BUY, TransactionType.SELL


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x06" * 32)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn: Any) -> CommodityRepository:
    return CommodityRepository(conn)


def gold_details() -> CommodityDetails:
    return CommodityDetails(
        CommodityFamily.PRECIOUS,
        CommodityForm.PHYSICAL,
        "g",
        quote_symbol="GC=F",
        quote_currency="USD",
        quote_unit="oz",
    )


def etc_details() -> CommodityDetails:
    return CommodityDetails(
        CommodityFamily.ENERGY, CommodityForm.ETC, "parts", broker="Compte-titres",
        quote_symbol="CL=F", quote_currency="USD",
    )  # fmt: skip


def first_buy(qty: str = "60", price: str = "61.2", fees: str = "18") -> Transaction:
    return Transaction(None, 0, BUY, date(2025, 3, 14), D(qty), D(price), D(fees))


def test_create_position_stores_asset_details_and_first_purchase(repo: CommodityRepository) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy())

    assert holding.asset.id is not None
    assert (holding.asset.name, holding.asset.symbol, holding.asset.currency) == (
        "Or",
        "XAU",
        "EUR",
    )
    assert holding.details == gold_details()
    assert len(holding.transactions) == 1
    assert holding.position.quantity == D(60)
    assert holding.position.pru == (D(60) * D("61.2") + D(18)) / D(60)
    assert holding.price is None
    assert repo.list_holdings() == [holding]


def test_only_commodities_are_listed(repo: CommodityRepository, conn: Any) -> None:
    conn.execute("INSERT INTO assets (symbol, name, currency) VALUES ('AAPL', 'Apple', 'USD')")
    conn.commit()
    repo.create_position("Or", "XAU", gold_details(), first_buy())

    assert [h.asset.symbol for h in repo.list_holdings()] == ["XAU"]


def test_symbol_must_be_unique_and_names_required(repo: CommodityRepository) -> None:
    repo.create_position("Or", "XAU", gold_details(), first_buy())

    with pytest.raises(ValueError, match="déjà le symbole"):
        repo.create_position("Or bis", "XAU", gold_details(), first_buy())
    with pytest.raises(ValueError, match="nom"):
        repo.create_position(" ", "XAG", gold_details(), first_buy())
    with pytest.raises(ValueError, match="symbole"):
        repo.create_position("Argent", "", gold_details(), first_buy())
    assert len(repo.list_holdings()) == 1  # rien n'a été créé à moitié


def test_a_position_cannot_start_with_a_sale(repo: CommodityRepository) -> None:
    sale = Transaction(None, 0, SELL, date(2025, 3, 14), D(1), D(10), D(0))
    with pytest.raises(ValueError, match="commence par un achat"):
        repo.create_position("Or", "XAU", gold_details(), sale)


def test_buy_and_sell_are_recomputed_into_the_position(repo: CommodityRepository) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy("100", "62.4", "0"))
    asset_id = holding.asset.id
    assert asset_id is not None

    repo.add_transaction(asset_id, SELL, date(2025, 6, 1), D(20), D("71.85"), D(4))

    position = repo.get_holding(asset_id).position  # type: ignore[union-attr]
    assert position.quantity == D(80)
    assert position.pru == D("62.4")
    assert position.realized_pnl == D(185)


def test_cannot_sell_more_than_held_or_before_buying(repo: CommodityRepository) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy("10", "60", "0"))
    asset_id = holding.asset.id
    assert asset_id is not None

    with pytest.raises(ValueError, match="négative"):
        repo.add_transaction(asset_id, SELL, date(2025, 4, 1), D(11), D(70), D(0))
    with pytest.raises(ValueError, match="négative"):
        repo.add_transaction(asset_id, SELL, date(2025, 1, 1), D(1), D(70), D(0))  # avant l'achat
    assert len(repo.transactions(asset_id)) == 1


def test_amounts_are_stored_as_text(repo: CommodityRepository, conn: Any) -> None:
    repo.create_position("Or", "XAU", gold_details(), first_buy("0.5", "61.25", "1.5"))
    row = conn.execute(
        "SELECT typeof(quantity) AS q, typeof(unit_price) AS p, typeof(fees) AS f FROM transactions"
    ).fetchone()
    assert (row["q"], row["p"], row["f"]) == ("text", "text", "text")


def test_update_transaction_recomputes_and_validates_the_whole_history(
    repo: CommodityRepository,
) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy("10", "60", "0"))
    asset_id = holding.asset.id
    assert asset_id is not None
    sale = repo.add_transaction(asset_id, SELL, date(2025, 5, 1), D(8), D(70), D(0))
    assert sale.id is not None
    first_id = repo.transactions(asset_id)[0].id
    assert first_id is not None

    repo.update_transaction(sale.id, SELL, date(2025, 5, 1), D(5), D(75), D(1))
    assert repo.get_holding(asset_id).position.quantity == D(5)  # type: ignore[union-attr]

    with pytest.raises(ValueError, match="négative"):  # l'achat devient trop petit pour la vente
        repo.update_transaction(first_id, BUY, date(2025, 3, 14), D(2), D(60), D(0))
    assert repo.get_holding(asset_id).position.quantity == D(5)  # type: ignore[union-attr]


def test_delete_transaction_keeps_history_consistent(repo: CommodityRepository) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy("10", "60", "0"))
    asset_id = holding.asset.id
    assert asset_id is not None
    second = repo.add_transaction(asset_id, BUY, date(2025, 4, 1), D(5), D(62), D(0))
    sale = repo.add_transaction(asset_id, SELL, date(2025, 5, 1), D(12), D(70), D(0))
    first_id = repo.transactions(asset_id)[0].id
    assert second.id is not None and sale.id is not None and first_id is not None

    with pytest.raises(ValueError, match="négative"):
        repo.delete_transaction(second.id)  # la vente de 12 ne passerait plus
    repo.delete_transaction(sale.id)
    repo.delete_transaction(second.id)
    with pytest.raises(ValueError, match="au moins une transaction"):
        repo.delete_transaction(first_id)
    assert len(repo.transactions(asset_id)) == 1


def test_update_position_changes_identity_and_details(repo: CommodityRepository) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy())
    asset_id = holding.asset.id
    assert asset_id is not None
    other = repo.create_position("Pétrole", "CL=F", etc_details(), first_buy("10", "30", "0"))

    updated = CommodityDetails(
        CommodityFamily.PRECIOUS, CommodityForm.PHYSICAL, "kg",
        quote_symbol="GC=F", quote_currency="USD", quote_unit="oz",
    )  # fmt: skip
    repo.update_position(asset_id, " Or fin ", "XAU2", updated)

    saved = repo.get_holding(asset_id)
    assert saved is not None
    assert (saved.asset.name, saved.asset.symbol, saved.details.unit) == ("Or fin", "XAU2", "kg")
    assert other.asset.id is not None
    with pytest.raises(ValueError, match="déjà le symbole"):
        repo.update_position(asset_id, "Or", "CL=F", updated)


def test_prices_are_upserted_and_kept(repo: CommodityRepository) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy())
    asset_id = holding.asset.id
    assert asset_id is not None

    repo.set_price(asset_id, D("71.85"), datetime(2026, 11, 12, 21, 3, 41))
    repo.set_price(asset_id, D("72.10"), datetime(2026, 11, 13, 9, 0, 0))

    record = repo.price(asset_id)
    assert record is not None
    assert record.price == D("72.10")
    assert record.fetched_at == datetime(2026, 11, 13, 9, 0, 0)
    assert repo.list_holdings()[0].price == record


def test_deleting_a_position_removes_everything(repo: CommodityRepository, conn: Any) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy())
    asset_id = holding.asset.id
    assert asset_id is not None
    repo.set_price(asset_id, D("71.85"), datetime(2026, 11, 12, 21, 3, 41))

    repo.delete_position(asset_id)

    assert repo.list_holdings() == []
    for table in ("assets", "commodity_details", "transactions", "prices"):
        assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0


def test_each_valuation_day_is_kept_in_the_history(repo: CommodityRepository) -> None:
    holding = repo.create_position("Or", "XAU", gold_details(), first_buy())
    asset_id = holding.asset.id
    assert asset_id is not None
    assert repo.valued_on(date(2026, 11, 12)) == set()

    repo.record_price(asset_id, D("71.85"), date(2026, 11, 12), datetime(2026, 11, 12, 21, 3))
    repo.record_price(asset_id, D("72.10"), date(2026, 11, 13), datetime(2026, 11, 13, 9, 0))

    saved = repo.get_holding(asset_id)
    assert saved is not None
    assert [(h.valuation_date, h.price) for h in saved.history] == [
        (date(2026, 11, 12), D("71.85")),
        (date(2026, 11, 13), D("72.10")),
    ]
    assert saved.price == saved.history[-1]
    assert repo.valued_on(date(2026, 11, 13)) == {asset_id}
    assert repo.valued_on(date(2026, 11, 14)) == set()
