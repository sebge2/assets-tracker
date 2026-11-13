from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox, QToolButton
from pytestqt.qtbot import QtBot

from assets_tracker.db.commodity_repository import CommodityRepository
from assets_tracker.db.connection import connect
from assets_tracker.domain.commodities import CommodityDetails, CommodityFamily, CommodityForm
from assets_tracker.domain.models import Transaction, TransactionType
from assets_tracker.ui.commodities_page import CommoditiesPage
from assets_tracker.ui.commodity_chart import PositionChart
from assets_tracker.ui.commodity_dialogs import (
    EditPositionDialog,
    NewPositionDialog,
    NewPositionForm,
    TradeDialog,
    TradeForm,
)
from assets_tracker.ui.main_window import MainWindow

D = Decimal
BUY, SELL = TransactionType.BUY, TransactionType.SELL
NOW = datetime(2026, 11, 12, 21, 10, 0)
TODAY = NOW.date()
NBSP = " "
NNBSP = " "


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x07" * 32)
    yield connection
    connection.close()


def gold_details() -> CommodityDetails:
    return CommodityDetails(
        CommodityFamily.PRECIOUS, CommodityForm.PHYSICAL, "g",
        quote_symbol="GC=F", quote_currency="USD", quote_unit="oz",
    )  # fmt: skip


def oil_details() -> CommodityDetails:
    return CommodityDetails(
        CommodityFamily.ENERGY, CommodityForm.ETC, "parts", broker="Compte-titres",
        quote_symbol="CL=F", quote_currency="USD",
    )  # fmt: skip


def buy(day: str, qty: str, price: str, fees: str = "0") -> Transaction:
    return Transaction(None, 0, BUY, date.fromisoformat(day), D(qty), D(price), D(fees))


@pytest.fixture
def repo(conn: Any) -> CommodityRepository:
    repository = CommodityRepository(conn)
    gold = repository.create_position(
        "Or", "XAU", gold_details(), buy("2025-03-14", "60", "61.2", "18")
    )
    assert gold.asset.id is not None
    repository.add_transaction(gold.asset.id, BUY, date(2025, 9, 22), D(40), D("63.75"), D(0))
    repository.set_price(gold.asset.id, D("71.85"), NOW - timedelta(minutes=4))
    oil = repository.create_position(
        "Pétrole WTI", "CL=F", oil_details(), buy("2025-02-11", "80", "32", "2")
    )
    assert oil.asset.id is not None
    repository.add_transaction(oil.asset.id, BUY, date(2025, 8, 19), D(40), D("30.15"), D(0))
    repository.set_price(oil.asset.id, D("29.8"), NOW - timedelta(days=2))
    return repository


class Quotes:
    """Fournisseur de cours factice : jamais de réseau en test."""

    def __init__(self, prices: dict[str, Decimal | None]) -> None:
        self.prices = prices
        self.calls: list[str] = []

    def __call__(self, details: CommodityDetails) -> Decimal | None:
        assert details.quote_symbol is not None
        self.calls.append(details.quote_symbol)
        return self.prices.get(details.quote_symbol)


@pytest.fixture
def quotes() -> Quotes:
    return Quotes({"GC=F": D("72.00"), "CL=F": None})


@pytest.fixture
def page(qtbot: QtBot, repo: CommodityRepository, quotes: Quotes) -> CommoditiesPage:
    widget = CommoditiesPage(
        repo, today=lambda: TODAY, now=lambda: NOW, price_fetcher=quotes, background=False
    )
    qtbot.addWidget(widget)
    widget.resize(1300, 900)
    widget.show()
    return widget


def click_cell(qtbot: QtBot, page: CommoditiesPage, row: int, column: int) -> None:
    table = page._table
    rect = table.visualRect(table.model().index(row, column))
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


# --- Liste et indicateurs -----------------------------------------------------


def test_list_shows_each_position_with_computed_figures(page: CommoditiesPage) -> None:
    table = page._table

    assert table.rowCount() == 2
    assert "Or" in table.cellWidget(0, 0).text()
    assert "Physique · XAU" in table.cellWidget(0, 0).text()
    assert table.item(0, 1).text() == "100 g"
    assert table.item(0, 2).text() == f"62,40{NBSP}€{NBSP}/{NBSP}g"  # (3 690 + 2 550) / 100
    assert table.item(0, 4).text() == f"7{NNBSP}185,00{NBSP}€"
    assert "+945,00" in table.cellWidget(0, 5).text()
    assert "Pétrole WTI" in table.cellWidget(1, 0).text()
    assert table.item(1, 1).text() == "120 parts"
    assert "−192,00" in table.cellWidget(1, 5).text()


def test_cards_summarize_value_gain_and_price_freshness(page: CommoditiesPage) -> None:
    assert page._value_card._value.text() == f"10{NNBSP}761,00{NBSP}€"
    assert page._gain_card._value.text() == f"+753,00{NBSP}€"  # 10 761 − (6 240 + 3 768)
    assert page._price_card._value.text() == "Il y a 4 min"
    assert page._price_card._sub.text() == "1 cours en cache"


def test_allocation_by_family(page: CommoditiesPage) -> None:
    legend = page._legend.text()
    assert "Métaux précieux" in legend and "Énergie" in legend
    assert f"66,8{NBSP}%" in legend  # 7 185 / 10 761


def test_family_filter(page: CommoditiesPage) -> None:
    page._set_family(CommodityFamily.ENERGY)
    assert page._table.rowCount() == 1
    assert page._d_name.text() == "Pétrole WTI"

    page._set_family(None)
    assert page._table.rowCount() == 2


def test_stale_price_is_flagged_as_cache(page: CommoditiesPage) -> None:
    assert "Cache" not in page._table.cellWidget(0, 3).text()
    assert "Cache" in page._table.cellWidget(1, 3).text()  # cours vieux de 2 jours


def test_selecting_a_row_shows_its_sheet(qtbot: QtBot, page: CommoditiesPage) -> None:
    assert page._d_name.text() == "Or"
    assert page._tiles["Détention"].text() == "100 g"
    assert page._tiles["extra:label"].text() == "Première acquisition"
    assert page._tiles["extra"].text() == "2025-03-14"

    click_cell(qtbot, page, 1, 1)

    assert page._d_name.text() == "Pétrole WTI"
    assert page._tiles["Détention"].text() == "120 parts"
    assert page._tiles["extra"].text() == "Compte-titres"
    assert page._tiles["extra:label"].text() == "Courtier"


def test_chart_uses_transaction_prices_and_current_price(page: CommoditiesPage) -> None:
    assert page._chart.can_draw()  # deux achats + le dernier cours
    assert not page._chart.grab().isNull()


def test_chart_needs_at_least_two_prices(qtbot: QtBot) -> None:
    chart = PositionChart()
    qtbot.addWidget(chart)
    chart.resize(300, 200)
    chart.set_data([(date(2026, 1, 1), D(10))], D(10))
    assert not chart.can_draw()
    assert not chart.grab().isNull()


def test_empty_state(qtbot: QtBot, conn: Any) -> None:
    empty = CommoditiesPage(CommodityRepository(conn), background=False)
    qtbot.addWidget(empty)
    empty.show()

    assert not empty._empty.isHidden()
    assert empty._table.isHidden()
    assert empty._detail.isHidden()
    assert empty._value_card._value.text() == "—"


# --- Cours --------------------------------------------------------------------


def test_refresh_stores_new_prices_and_keeps_cache_on_failure(
    page: CommoditiesPage, repo: CommodityRepository, quotes: Quotes
) -> None:
    page.refresh_prices(force=True)

    assert sorted(quotes.calls) == ["CL=F", "GC=F"]
    gold, oil = repo.list_holdings()
    assert gold.price is not None and gold.price.price == D("72.00")
    assert gold.price.fetched_at == NOW
    assert oil.price is not None and oil.price.price == D("29.8")  # dernier cours conservé
    assert oil.asset.id in page._failed
    assert "Cache" in page._table.cellWidget(1, 3).text()
    assert page._price_card._value.text() == "À l'instant"
    assert page._refresh_button.isEnabled()


def test_provider_exception_never_breaks_the_page(qtbot: QtBot, repo: CommodityRepository) -> None:
    def boom(details: CommodityDetails) -> Decimal | None:
        raise RuntimeError("réseau coupé")

    widget = CommoditiesPage(
        repo, today=lambda: TODAY, now=lambda: NOW, price_fetcher=boom, background=False
    )
    qtbot.addWidget(widget)

    widget.refresh_prices(force=True)

    assert widget._table.rowCount() == 2
    assert len(widget._failed) == 2
    assert repo.list_holdings()[0].price is not None


def test_no_request_without_positions(qtbot: QtBot, conn: Any, quotes: Quotes) -> None:
    empty = CommoditiesPage(CommodityRepository(conn), price_fetcher=quotes, background=False)
    qtbot.addWidget(empty)
    empty.refresh_prices()
    assert quotes.calls == []


def test_background_refresh_updates_the_page(
    qtbot: QtBot, repo: CommodityRepository, quotes: Quotes
) -> None:
    widget = CommoditiesPage(
        repo, today=lambda: TODAY, now=lambda: NOW, price_fetcher=quotes, background=True
    )
    qtbot.addWidget(widget)

    widget.refresh_prices(force=True)
    qtbot.waitUntil(lambda: not widget._refreshing, timeout=3000)

    assert repo.list_holdings()[0].price is not None
    assert repo.list_holdings()[0].price.price == D("72.00")  # type: ignore[union-attr]


# --- Cache quotidien et historique -------------------------------------------


def test_automatic_refresh_only_asks_for_assets_not_valued_today(
    page: CommoditiesPage, quotes: Quotes
) -> None:
    page.refresh_prices()  # l'or est déjà valorisé aujourd'hui : seul le pétrole est interrogé

    assert quotes.calls == ["CL=F"]


def test_a_successful_valuation_is_not_asked_again_the_same_day(
    page: CommoditiesPage, quotes: Quotes
) -> None:
    quotes.prices["CL=F"] = D("30.10")

    page.refresh_prices()
    page.refresh_prices()
    page.refresh_prices()

    assert quotes.calls == ["CL=F"]  # une seule fois, les appels suivants lisent le cache
    assert "Cache" not in page._table.cellWidget(1, 3).text()  # le pétrole est maintenant du jour


def test_a_failed_valuation_is_retried_at_the_next_pass(
    page: CommoditiesPage, quotes: Quotes
) -> None:
    page.refresh_prices()  # le pétrole échoue (aucun cours)
    page.refresh_prices()

    assert quotes.calls == ["CL=F", "CL=F"]


def test_the_refresh_button_forces_a_new_valuation(
    qtbot: QtBot, page: CommoditiesPage, quotes: Quotes
) -> None:
    qtbot.mouseClick(page._refresh_button, Qt.MouseButton.LeftButton)

    assert sorted(quotes.calls) == ["CL=F", "GC=F"]  # même l'or, déjà valorisé aujourd'hui


def test_each_day_adds_one_row_of_history_and_past_days_stay_untouched(
    qtbot: QtBot, repo: CommodityRepository, quotes: Quotes
) -> None:
    clock = [NOW]
    widget = CommoditiesPage(
        repo,
        today=lambda: clock[0].date(),
        now=lambda: clock[0],
        price_fetcher=quotes,
        background=False,
    )
    qtbot.addWidget(widget)
    widget.show()
    gold = repo.list_holdings()[0]
    assert len(gold.history) == 1  # la valorisation du 12 novembre

    clock[0] = NOW + timedelta(days=1, hours=-12)  # le lendemain matin
    widget.refresh()
    assert "Cache" in widget._table.cellWidget(0, 3).text()  # la valeur d'hier est un cache
    widget.refresh_prices()  # nouvelle journée : l'or est de nouveau interrogé

    gold = repo.list_holdings()[0]
    assert [(h.valuation_date, h.price) for h in gold.history] == [
        (date(2026, 11, 12), D("71.85")),  # hier, figé
        (date(2026, 11, 13), D("72.00")),
    ]
    assert gold.price is not None and gold.price.valuation_date == date(2026, 11, 13)
    assert "Cache" not in widget._table.cellWidget(0, 3).text()
    assert widget._chart.can_draw()  # la courbe utilise l'historique quotidien


# --- Achat, vente, modification, création -------------------------------------


def test_buy_flow_recomputes_the_pru(
    page: CommoditiesPage, repo: CommodityRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(TradeDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        TradeDialog,
        "form",
        lambda self: TradeForm(BUY, date(2026, 11, 12), D(50), D("71.2"), D(35)),
    )

    page._buy_button.click()

    gold = repo.list_holdings()[0]
    assert gold.position.quantity == D(150)
    assert round(gold.position.pru, 2) == D("65.57")
    assert page._tiles["Détention"].text() == "150 g"


def test_sell_flow_keeps_the_pru_and_updates_the_quantity(
    page: CommoditiesPage, repo: CommodityRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(TradeDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        TradeDialog,
        "form",
        lambda self: TradeForm(SELL, date(2026, 11, 12), D(20), D("71.85"), D(4)),
    )

    page._sell_button.click()

    gold = repo.list_holdings()[0]
    assert gold.position.quantity == D(80)
    assert gold.position.pru == D("62.4")
    assert gold.position.realized_pnl == D(185)


def test_sell_is_disabled_once_the_position_is_closed(
    page: CommoditiesPage, repo: CommodityRepository
) -> None:
    gold = repo.list_holdings()[0]
    assert gold.asset.id is not None
    repo.add_transaction(gold.asset.id, SELL, date(2026, 1, 1), D(100), D(70), D(0))

    page.refresh()

    assert not page._sell_button.isEnabled()
    assert page._table.item(0, 1).text() == "0 g"


def test_new_position_flow_creates_selects_and_fetches_its_price(
    page: CommoditiesPage,
    repo: CommodityRepository,
    quotes: Quotes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copper = CommodityDetails(
        CommodityFamily.INDUSTRIAL, CommodityForm.ETC, "parts", broker="CT",
        quote_symbol="HG=F", quote_currency="USD",
    )  # fmt: skip
    form = NewPositionForm(
        "Cuivre", "HG=F", copper, TradeForm(BUY, date(2026, 11, 12), D(55), D("41.1"), D(0))
    )
    monkeypatch.setattr(NewPositionDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(NewPositionDialog, "form", lambda self: form)
    quotes.prices["HG=F"] = D("46.2")

    page._new_position()

    names = [h.asset.name for h in repo.list_holdings()]
    assert "Cuivre" in names
    assert page._d_name.text() == "Cuivre"
    assert "HG=F" in quotes.calls
    assert page._table.rowCount() == 3


def test_edit_flow_can_delete_a_position(
    page: CommoditiesPage, repo: CommodityRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(EditPositionDialog, "exec", lambda self: EditPositionDialog.DELETE)

    page._edit_button.click()

    assert [h.asset.name for h in repo.list_holdings()] == ["Pétrole WTI"]
    assert page._d_name.text() == "Pétrole WTI"


# --- Dialogues ----------------------------------------------------------------


def test_trade_dialog_previews_a_purchase(qtbot: QtBot, repo: CommodityRepository) -> None:
    holding = repo.list_holdings()[0]
    dialog = TradeDialog(None, kind=BUY, holding=holding, today=TODAY)
    qtbot.addWidget(dialog)

    assert dialog._price.text() == "71,85"  # préremplie avec le cours actuel
    dialog._quantity.setText("50")
    dialog._price.setText("71,20")

    values = dialog.results_values()
    assert values[0] == f"3{NNBSP}560,00{NBSP}€"  # 50 × 71,20, prix tout compris
    assert values[1] == "150 g"
    assert values[2].endswith(f"65,33{NBSP}€{NBSP}/{NBSP}g")


def test_trade_dialog_previews_a_sale_and_refuses_oversell(
    qtbot: QtBot, repo: CommodityRepository
) -> None:
    holding = repo.list_holdings()[0]
    dialog = TradeDialog(None, kind=SELL, holding=holding, today=TODAY)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._quantity.setText("20")
    dialog._price.setText("71,85")

    values = dialog.results_values()
    assert values[0] == f"1{NNBSP}437,00{NBSP}€"  # 20 × 71,85
    assert values[1] == f"+189,00{NBSP}€"  # 1 437 − 20 × 62,40
    assert values[3] == "80 g"

    dialog._quantity.setText("101")
    assert dialog.results_values()[0] == "—"
    dialog._accept()
    assert dialog._error.isVisible()
    assert "supérieure" in dialog._error.text()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_trade_dialog_rejects_bad_numbers_inline(qtbot: QtBot, repo: CommodityRepository) -> None:
    dialog = TradeDialog(None, kind=BUY, holding=repo.list_holdings()[0], today=TODAY)
    qtbot.addWidget(dialog)
    dialog.show()

    dialog._quantity.setText("abc")
    dialog._accept()

    assert dialog._error.isVisible()
    assert "Quantité" in dialog._error.text()


def test_trade_dialog_uses_the_repository_check_for_past_dates(
    qtbot: QtBot, repo: CommodityRepository
) -> None:
    holding = repo.list_holdings()[0]
    assert holding.asset.id is not None
    asset_id = holding.asset.id

    def check(form: TradeForm) -> None:
        repo.check_transaction(
            asset_id, form.kind, form.trade_date, form.quantity, form.unit_price, form.fees
        )

    dialog = TradeDialog(None, kind=SELL, holding=holding, today=date(2024, 1, 1), validator=check)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._quantity.setText("10")
    dialog._price.setText("70")

    dialog._accept()  # vente datée avant le premier achat

    assert dialog._error.isVisible()
    assert "négative" in dialog._error.text()


def test_new_position_dialog_physical_uses_metal_presets(qtbot: QtBot) -> None:
    dialog = NewPositionDialog(None, today=TODAY)
    qtbot.addWidget(dialog)
    dialog._material.setCurrentText("Argent")
    dialog._p_quantity.setText("2")
    dialog._p_unit.setCurrentText("kg")
    dialog._p_price.setText("780")

    form = dialog.form()

    assert (form.name, form.symbol) == ("Argent", "XAG")
    assert form.details.quote_symbol == "SI=F"
    assert (form.details.quote_currency, form.details.quote_unit) == ("USD", "oz")
    assert form.details.unit == "kg"
    assert form.first.quantity == D(2) and form.first.fees == D(0)
    assert dialog.results_values()[0] == f"1{NNBSP}560,00{NBSP}€"


def test_new_position_dialog_custom_metal_has_no_automatic_quote(qtbot: QtBot) -> None:
    dialog = NewPositionDialog(None, today=TODAY)
    qtbot.addWidget(dialog)
    dialog._material.setCurrentText("Rhodium")
    dialog._p_symbol.setText("XRH")
    dialog._p_quantity.setText("1")
    dialog._p_price.setText("150")

    form = dialog.form()

    assert form.details.quote_symbol is None
    assert form.details.quote_unit is None


def test_new_position_dialog_etc_form(qtbot: QtBot) -> None:
    dialog = NewPositionDialog(None, today=TODAY)
    qtbot.addWidget(dialog)
    dialog._etc_button.setChecked(True)
    dialog._e_name.setText("Pétrole WTI")
    dialog._e_family.setCurrentIndex(list(CommodityFamily).index(CommodityFamily.ENERGY))
    dialog._e_symbol.setText("CL=F")
    dialog._e_quantity.setText("120")
    dialog._e_price.setText("31,40")
    dialog._e_broker.setText("Compte-titres")

    form = dialog.form()

    assert form.details.form is CommodityForm.ETC
    assert form.details.unit == "parts"
    assert form.details.quote_symbol == "CL=F"
    assert form.details.family is CommodityFamily.ENERGY
    assert form.first.unit_price == D("31.4")


def test_new_position_dialog_validation_stays_inline(qtbot: QtBot) -> None:
    dialog = NewPositionDialog(None, today=TODAY)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._material.setCurrentText("Rhodium")
    dialog._p_symbol.setText("")
    dialog._p_quantity.setText("1")
    dialog._p_price.setText("150")

    dialog._accept()

    assert dialog._error.isVisible()
    assert "symbole" in dialog._error.text()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_edit_dialog_lists_and_corrects_transactions(
    qtbot: QtBot, repo: CommodityRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold = repo.list_holdings()[0]
    assert gold.asset.id is not None
    dialog = EditPositionDialog(None, repository=repo, asset_id=gold.asset.id)
    qtbot.addWidget(dialog)

    assert dialog._table.rowCount() == 2
    assert dialog._table.item(0, 0).text() == "2025-09-22"  # la plus récente d'abord

    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    dialog._delete_transaction(repo.transactions(gold.asset.id)[1].id)

    assert dialog._table.rowCount() == 1
    assert dialog.modified
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(str(a[-1])))
    dialog._delete_transaction(repo.transactions(gold.asset.id)[0].id)  # la dernière : refusée
    assert warnings and "au moins une transaction" in warnings[0]


def test_edit_dialog_saves_the_information(qtbot: QtBot, repo: CommodityRepository) -> None:
    gold = repo.list_holdings()[0]
    assert gold.asset.id is not None
    dialog = EditPositionDialog(None, repository=repo, asset_id=gold.asset.id)
    qtbot.addWidget(dialog)
    dialog._name.setText("Or fin")

    dialog._accept()

    saved = repo.get_holding(gold.asset.id)
    assert saved is not None
    assert saved.asset.name == "Or fin"
    assert saved.details.quote_symbol == "GC=F"  # le cours spot d'un métal physique est conservé
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_edit_dialog_rejects_a_symbol_already_used(qtbot: QtBot, repo: CommodityRepository) -> None:
    gold = repo.list_holdings()[0]
    assert gold.asset.id is not None
    dialog = EditPositionDialog(None, repository=repo, asset_id=gold.asset.id)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._symbol.setText("CL=F")

    dialog._accept()

    assert dialog._error.isVisible()
    assert "déjà le symbole" in dialog._error.text()


def test_edit_dialog_row_buttons_are_labelled(qtbot: QtBot, repo: CommodityRepository) -> None:
    gold = repo.list_holdings()[0]
    assert gold.asset.id is not None
    dialog = EditPositionDialog(None, repository=repo, asset_id=gold.asset.id)
    qtbot.addWidget(dialog)

    buttons = dialog._table.cellWidget(0, 5).findChildren(QToolButton)

    assert [b.accessibleName() for b in buttons] == [
        "Modifier cette transaction",
        "Supprimer cette transaction",
    ]


# --- Navigation ---------------------------------------------------------------


def test_main_window_has_a_commodities_entry(qtbot: QtBot, conn: Any) -> None:
    window = MainWindow(conn)
    qtbot.addWidget(window)
    window.show()

    assert [b.text() for b in window._nav_buttons][2] == "Matières premières"
    qtbot.mouseClick(window._nav_buttons[2], Qt.MouseButton.LeftButton)

    assert window._stack.currentWidget() is window.commodities_page
    assert window._nav_buttons[2].isChecked()
