from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QDialog, QLabel
from pytestqt.qtbot import QtBot

from assets_tracker.db.connection import connect
from assets_tracker.db.exposure_repository import ExposureRepository
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.db.stock_repository import StockRepository
from assets_tracker.domain.exposure import ExposureEntry, ExposureKind
from assets_tracker.domain.funds import ValuationSource
from assets_tracker.domain.models import TransactionType
from assets_tracker.domain.stocks import StockDetails
from assets_tracker.ui.exposure_dialog import ExposureDialog
from assets_tracker.ui.stocks_page import QuoteDialog, StockDialog, StocksPage, TradeDialog

D = Decimal
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0)
NBSP = " "
NNBSP = " "
AIR_LIQUIDE = "AI.PA"
ASML = "ASML.AS"
APPLE = "AAPL"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0e" * 32)
    yield connection
    connection.close()


@pytest.fixture
def repository(conn: Any) -> StockRepository:
    return StockRepository(conn)


def seed(repository: StockRepository) -> None:
    """Air Liquide cotée aujourd'hui (Yahoo) ; ASML au cours manuel de 14 jours, sans dividende.

    Triées par nom : ASML (ligne 0), puis Air Liquide (ligne 1).
    """
    air = repository.create(
        "Air Liquide",
        AIR_LIQUIDE,
        "EUR",
        StockDetails("Euronext Paris", D("3.20"), ValuationSource.YAHOO),
    )
    asml = repository.create(
        "ASML",
        ASML,
        "EUR",
        StockDetails("Euronext Amsterdam", None, ValuationSource.MANUAL),
    )
    assert air.asset.id is not None and asml.asset.id is not None
    exposure = ExposureRepository(repository._conn)
    exposure.replace(
        air.asset.id, [ExposureEntry("Industrie", D(100))], [ExposureEntry("France", D(100))]
    )
    exposure.replace(asml.asset.id, [ExposureEntry("Technologie", D(100))], [])
    repository.add_transaction(
        air.asset.id, TransactionType.BUY, date(2026, 3, 12), D(12), D("158.40"), D(0)
    )
    repository.add_transaction(
        asml.asset.id, TransactionType.BUY, date(2026, 4, 22), D(2), D("690"), D(5)
    )
    repository.record_quote(
        air.asset.id, date(2026, 11, 12), D("169.80"), NOW, ValuationSource.YAHOO
    )
    repository.record_quote(air.asset.id, TODAY, D("171.20"), NOW, ValuationSource.YAHOO)
    repository.record_quote(
        asml.asset.id, date(2026, 10, 30), D("704"), NOW, ValuationSource.MANUAL
    )


def add_apple(repository: StockRepository) -> None:
    """15 actions Apple en dollars achetées 182,50 $, cotées 228,30 $ ; dividende 1,04 $."""
    apple = repository.create(
        "Apple",
        APPLE,
        "USD",
        StockDetails("NASDAQ", D("1.04"), ValuationSource.YAHOO),
    )
    assert apple.asset.id is not None
    ExposureRepository(repository._conn).replace(
        apple.asset.id, [ExposureEntry("Technologie", D(100))], []
    )
    repository.add_transaction(
        apple.asset.id, TransactionType.BUY, date(2026, 3, 5), D(15), D("182.50"), D(0)
    )
    repository.record_quote(apple.asset.id, TODAY, D("228.30"), NOW, ValuationSource.YAHOO)


class Quotes:
    """Faux fournisseur Yahoo : enregistre les symboles demandés."""

    def __init__(self, prices: dict[str, Decimal | None]) -> None:
        self.prices = prices
        self.calls: list[str] = []

    def __call__(self, symbol: str) -> Decimal | None:
        self.calls.append(symbol)
        return self.prices.get(symbol)


def make_page(
    qtbot: QtBot,
    repository: StockRepository,
    quotes: Quotes | None = None,
    rates: Quotes | None = None,
) -> StocksPage:
    page = StocksPage(
        repository,
        FxRepository(repository._conn),
        exposure=ExposureRepository(repository._conn),
        today=lambda: TODAY,
        now=lambda: NOW,
        quote_fetcher=quotes or Quotes({}),
        rate_fetcher=rates or Quotes({}),
        background=False,
    )
    qtbot.addWidget(page)
    page.resize(1200, 900)
    return page


@pytest.fixture
def page(qtbot: QtBot, repository: StockRepository) -> StocksPage:
    seed(repository)
    return make_page(qtbot, repository)


def row_of(page: StocksPage, name: str) -> int:
    for index in range(page._table.rowCount()):
        if name in page._table.cellWidget(index, 0).text():
            return index
    raise AssertionError(name)


# --- page -------------------------------------------------------------------


def test_cards_summarize_the_stocks(page: StocksPage) -> None:
    # 12 × 171,20 + 2 × 704 = 3 462,40 € pour 3 285,80 € investis ; dividendes 12 × 3,20
    assert page._value_card._value.text() == f"3{NNBSP}462,40{NBSP}€"
    assert page._gain_card._value.text() == f"+176,60{NBSP}€"
    assert page._gain_card._sub.text() == f"+5,4{NBSP}%"
    assert page._dividend_card._value.text() == f"38,40{NBSP}€"
    assert page._dividend_card._sub.text() == f"Rendement 1,11{NBSP}%"


def test_table_has_five_columns_and_no_weight_or_fees(page: StocksPage) -> None:
    headers = [page._table.horizontalHeaderItem(c).text() for c in range(page._table.columnCount())]

    assert headers == ["Action", "Cours", "Valeur", "Dividende", "+/− latente"]
    assert page._table.rowCount() == 2
    assert row_of(page, "ASML") == 0 and row_of(page, "Air Liquide") == 1
    assert "Yahoo · 13/11/2026" in page._table.cellWidget(1, 1).text()
    assert "manuel · 30/10/2026" in page._table.cellWidget(0, 1).text()
    assert "ancien" in page._table.cellWidget(0, 1).text()
    assert "12 actions" in page._table.cellWidget(1, 2).text()
    assert "—" in page._table.cellWidget(0, 3).text()  # ASML : dividende inconnu


def test_exposure_card_weights_sectors_and_zones_by_value(page: StocksPage) -> None:
    sectors = page._exposure_card.legend_text(ExposureKind.SECTOR)
    zones = page._exposure_card.legend_text(ExposureKind.ZONE)

    assert not page._exposure_card.isHidden()
    assert f"Industrie <b>59,3{NBSP}%</b>" in sectors
    assert f"Technologie <b>40,7{NBSP}%</b>" in sectors
    assert sectors.index("Industrie") < sectors.index("Technologie")
    # seule Air Liquide a une zone : ASML tombe dans « Non classé », toujours en dernier
    assert f"France <b>59,3{NBSP}%</b>" in zones
    assert f"Non classé <b>40,7{NBSP}%</b>" in zones
    assert zones.index("France") < zones.index("Non classé")


def test_old_quote_is_flagged_in_the_alert(page: StocksPage) -> None:
    assert not page._alert.isHidden()
    text = page._alert_text.text()
    assert "ASML" in text and "30/10/2026" in text and "14 jours" in text


def test_detail_of_the_selected_stock(page: StocksPage) -> None:
    page._table.setCurrentCell(1, 0)

    assert page._d_name.text() == "Air Liquide"
    assert page._d_sub.text() == "AI.PA · Euronext Paris · Industrie"
    assert page._d_value.text() == f"2{NNBSP}054,40{NBSP}€"
    assert page._d_gain.text() == f"+153,60{NBSP}€ · +8,1{NBSP}%"
    assert page._d_euros.text() == ""  # déjà en euros
    assert page._tiles["Actions"].text() == "12"
    assert page._tiles["PRU"].text() == f"158,40{NBSP}€"
    assert page._tiles["Cours"].text() == f"171,20{NBSP}€"
    assert page._tiles["Variation"].text() == f"+0,8{NBSP}%"  # 169,80 → 171,20
    assert page._tiles["Dividende par action"].text() == f"3,20{NBSP}€"
    assert page._tiles["Rendement"].text() == f"1,87{NBSP}%"
    assert page._tiles["Dividendes annuels"].text() == f"38,40{NBSP}€"
    assert page._tiles["Devise"].text() == "EUR"
    assert page._tiles["Secteur principal"].text() == f"Industrie · 100{NBSP}%"
    assert page._tiles["Zone principale"].text() == f"France · 100{NBSP}%"
    assert page._tiles["Place"].text() == "Euronext Paris"
    assert page._edit_button.text() == "Modifier l'action…"


def test_a_stock_without_two_quotes_has_no_variation(page: StocksPage) -> None:
    page._table.setCurrentCell(0, 0)  # ASML : un seul cours

    assert page._tiles["Variation"].text() == "—"
    assert page._tiles["Dividende par action"].text() == "—"
    assert page._tiles["Rendement"].text() == "—"


def test_detail_lists_the_latest_operations_first(
    qtbot: QtBot, page: StocksPage, repository: StockRepository
) -> None:
    air = repository.list_holdings()[1]
    assert air.asset.id is not None
    repository.add_transaction(
        air.asset.id, TransactionType.SELL, date(2026, 6, 2), D(2), D("163.80"), D("1.50")
    )
    page.refresh()
    page._table.setCurrentCell(1, 0)

    texts = [label.text() for label in page._detail.findChildren(QLabel)]
    sale = texts.index(f"−2 actions · 163,80{NBSP}€")
    assert texts[sale + 2] == f"+326,10{NBSP}€"  # 2 × 163,80 − 1,50
    assert texts.index(f"+12 actions · 158,40{NBSP}€") > sale


def test_empty_page_invites_to_create_a_stock(qtbot: QtBot, repository: StockRepository) -> None:
    page = make_page(qtbot, repository)

    assert not page._empty.isHidden()
    assert page._table.isHidden() and page._detail.isHidden()
    assert page._alert.isHidden() and page._exposure_card.isHidden()
    assert page._value_card._value.text() == f"0,00{NBSP}€"


def test_a_sold_out_stock_is_not_reported_as_outdated(
    qtbot: QtBot, repository: StockRepository
) -> None:
    seed(repository)
    asml = repository.list_holdings()[0]
    assert asml.asset.id is not None
    repository.add_transaction(asml.asset.id, TransactionType.SELL, TODAY, D(2), D(700), D(0))

    page = make_page(qtbot, repository)

    assert page._alert.isHidden()


# --- cours Yahoo ------------------------------------------------------------


def test_yahoo_quote_is_recorded_for_open_stocks_not_valued_today(
    qtbot: QtBot, repository: StockRepository
) -> None:
    seed(repository)
    quotes = Quotes({ASML: D("710.50")})
    page = make_page(qtbot, repository, quotes)

    page.refresh_quotes()

    assert quotes.calls == [ASML]  # Air Liquide est déjà cotée aujourd'hui
    asml = repository.list_holdings()[0]
    assert asml.valuation is not None
    assert (asml.valuation.valuation_date, asml.valuation.value) == (TODAY, D("710.50"))
    assert asml.details.valuation_source is ValuationSource.YAHOO
    assert page._alert.isHidden()


def test_yahoo_failure_asks_for_a_manual_quote_and_is_not_retried(
    qtbot: QtBot, repository: StockRepository
) -> None:
    seed(repository)
    quotes = Quotes({})
    page = make_page(qtbot, repository, quotes)

    page.refresh_quotes()
    page.refresh_quotes()

    assert quotes.calls == [ASML]
    assert "Yahoo ne fournit pas de cours pour" in page._alert_text.text()
    asml = repository.list_holdings()[0]
    assert asml.valuation is not None and asml.valuation.valuation_date == date(2026, 10, 30)


def test_a_provider_error_never_breaks_the_page(qtbot: QtBot, repository: StockRepository) -> None:
    seed(repository)

    def failing(symbol: str) -> Decimal | None:
        raise RuntimeError("réseau indisponible")

    page = StocksPage(
        repository,
        FxRepository(repository._conn),
        exposure=ExposureRepository(repository._conn),
        today=lambda: TODAY,
        now=lambda: NOW,
        quote_fetcher=failing,
        background=False,
    )
    qtbot.addWidget(page)

    page.refresh_quotes()

    assert not page._alert.isHidden()


def test_manual_quote_clears_the_alert(
    page: StocksPage, repository: StockRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: QuoteDialog) -> QDialog.DialogCode:
        self._price.setText("712,40")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QuoteDialog, "exec", fill)

    page._update_quote(page._first_pending())

    asml = repository.list_holdings()[0]
    assert asml.valuation is not None and asml.valuation.value == D("712.40")
    assert asml.details.valuation_source is ValuationSource.MANUAL
    assert page._alert.isHidden()


def test_quote_dialog_refuses_a_future_date_and_a_null_price(
    qtbot: QtBot, page: StocksPage
) -> None:
    dialog = QuoteDialog(None, page._views[0].holding, TODAY)
    qtbot.addWidget(dialog)
    dialog._price.setText("0")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "strictement positif" in dialog._error.text()

    dialog._price.setText("700")
    dialog._date.setDate(dialog._date.date().addDays(1))
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "futur" in dialog._error.text()


# --- dialogues d'action -------------------------------------------------------


def test_stock_dialog_builds_details_with_yahoo_as_the_default_source(qtbot: QtBot) -> None:
    dialog = StockDialog(None)
    qtbot.addWidget(dialog)
    dialog._name.setText("TotalEnergies")
    dialog._ticker.setText("TTE.PA")
    dialog._place.setText("Euronext Paris")

    name, ticker, currency, details = dialog.form()

    assert (name, ticker, currency) == ("TotalEnergies", "TTE.PA", "EUR")
    assert details == StockDetails("Euronext Paris", None, ValuationSource.YAHOO)

    dialog._dividend.setText("3,28")
    assert dialog.form()[3].dividend == D("3.28")


def test_stock_dialog_stays_open_on_an_invalid_dividend(qtbot: QtBot) -> None:
    dialog = StockDialog(None)
    qtbot.addWidget(dialog)
    dialog._dividend.setText("beaucoup")

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "Dividende" in dialog._error.text()

    dialog._dividend.setText("")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_new_stock_flow_adds_a_row(
    page: StocksPage, repository: StockRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: StockDialog) -> QDialog.DialogCode:
        self._name.setText("TotalEnergies")
        self._ticker.setText("tte.pa")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(StockDialog, "exec", fill)

    page._new_stock()

    assert page._table.rowCount() == 3
    assert page._d_name.text() == "TotalEnergies"
    assert len(repository.list_holdings()) == 3


def test_new_stock_keeps_the_dialog_and_its_input_after_a_refused_stock(
    page: StocksPage, repository: StockRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def fill(self: StockDialog) -> QDialog.DialogCode:
        if not opened:  # 1re ouverture : le ticker existe déjà
            self._name.setText("TotalEnergies")
            self._ticker.setText(AIR_LIQUIDE)
        else:  # la même fenêtre revient avec la saisie : on corrige le ticker
            assert self._name.text() == "TotalEnergies"
            assert "déjà utilisé" in self._error.text()
            self._ticker.setText("TTE.PA")
        opened.append(self._ticker.text())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(StockDialog, "exec", fill)

    page._new_stock()

    assert opened == [AIR_LIQUIDE, "TTE.PA"]
    assert len(repository.list_holdings()) == 3


def test_edit_dialog_is_prefilled_and_the_flow_updates_the_stock(
    qtbot: QtBot, page: StocksPage, repository: StockRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = page._views[1]  # Air Liquide
    dialog = StockDialog(None, view.holding)
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Modifier l'action"
    assert dialog._ticker.text() == AIR_LIQUIDE and dialog._dividend.text() == "3,2"
    assert dialog.form()[3] == view.holding.details

    def fill(self: StockDialog) -> QDialog.DialogCode:
        self._dividend.setText("3,5")
        self._currency.setText("usd")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(StockDialog, "exec", fill)
    page._table.setCurrentCell(1, 0)

    page._edit_stock(page._selected_view())

    air = repository.list_holdings()[1]
    assert air.details.dividend == D("3.5") and air.asset.currency == "USD"
    assert air.position.quantity == D(12)  # les opérations sont conservées


# --- achat et vente ---------------------------------------------------------


def air_dialog(
    qtbot: QtBot, repository: StockRepository, kind: TransactionType = TransactionType.BUY
) -> TradeDialog:
    dialog = TradeDialog(None, repository.list_holdings()[1], kind, TODAY)
    qtbot.addWidget(dialog)
    return dialog


def test_purchase_summary_adds_the_fees_to_the_amount_debited(
    qtbot: QtBot, page: StocksPage, repository: StockRepository
) -> None:
    dialog = air_dialog(qtbot, repository)

    dialog._quantity.setText("5")
    dialog._fees.setText("1,90")

    assert dialog._price.text() == "171,20"  # prérempli : dernier cours
    assert dialog.summary_rows() == [
        ("Montant", f"856,00{NBSP}€"),
        ("Frais de courtage", f"+1,90{NBSP}€"),
        ("Total débité", f"857,90{NBSP}€"),
    ]
    assert dialog._after.text() == f"Après opération : 17 actions · PRU 162,28{NBSP}€"


def test_sale_summary_deducts_the_fees_and_shows_the_realized_gain(
    qtbot: QtBot, page: StocksPage, repository: StockRepository
) -> None:
    dialog = air_dialog(qtbot, repository, TransactionType.SELL)

    dialog._quantity.setText("5")
    dialog._fees.setText("1,90")

    assert dialog.summary_rows() == [
        ("Montant brut", f"856,00{NBSP}€"),
        ("Frais de courtage", f"−1,90{NBSP}€"),
        ("Total crédité", f"854,10{NBSP}€"),
        ("Plus-value réalisée", f"+62,10{NBSP}€"),  # 5 × (171,20 − 158,40) − 1,90
    ]
    assert dialog._after.text() == f"Après opération : 7 actions · PRU 158,40{NBSP}€"


def test_invalid_quantities_disable_the_save_button(
    qtbot: QtBot, page: StocksPage, repository: StockRepository
) -> None:
    dialog = air_dialog(qtbot, repository)
    save = dialog._save

    assert not save.isEnabled()  # aucune quantité
    dialog._quantity.setText("2,5")
    assert save.isEnabled()  # les fractions sont permises
    dialog._quantity.setText("0")
    assert not save.isEnabled()
    dialog._quantity.setText("1")
    dialog._fees.setText("-3")
    assert not save.isEnabled()


def test_selling_more_than_held_is_refused_with_a_message(
    qtbot: QtBot, page: StocksPage, repository: StockRepository
) -> None:
    dialog = air_dialog(qtbot, repository, TransactionType.SELL)

    dialog._quantity.setText("13")

    assert not dialog._save.isEnabled()
    assert "supérieure" in dialog._error.text()


def test_trade_flow_records_the_operation_and_the_quote(
    page: StocksPage, repository: StockRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: TradeDialog) -> QDialog.DialogCode:
        self._quantity.setText("2")
        self._price.setText("172")
        self._fees.setText("1,9")
        self._record.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TradeDialog, "exec", fill)
    page._table.setCurrentCell(1, 0)

    page._trade(TransactionType.BUY)

    air = repository.list_holdings()[1]
    assert air.position.quantity == D(14)
    assert air.valuation is not None and air.valuation.value == D("172")
    assert air.details.valuation_source is ValuationSource.MANUAL
    assert air.transactions[-1].fees == D("1.9")
    assert page._d_value.text() == f"2{NNBSP}408,00{NBSP}€"  # 14 actions × 172 €


# --- taux de change -----------------------------------------------------------


def test_summary_converts_foreign_stocks_with_the_cached_rate(
    qtbot: QtBot, repository: StockRepository
) -> None:
    seed(repository)
    add_apple(repository)
    FxRepository(repository._conn).record("USD", TODAY, D("0.5"), NOW)

    page = make_page(qtbot, repository)

    # 3 462,40 € + 15 × 228,30 $ × 0,5 = 5 174,65 € ; plus-value 176,60 + 343,50 = 520,10 €
    assert page._value_card._value.text() == f"5{NNBSP}174,65{NBSP}€"
    assert page._value_card._sub.text().endswith("converti en EUR")
    assert page._gain_card._value.text() == f"+520,10{NBSP}€"
    assert page._dividend_card._value.text() == f"46,20{NBSP}€"  # 38,40 + 15 × 1,04 $ × 0,5
    assert "Technologie <b>" in page._exposure_card.legend_text(ExposureKind.SECTOR)
    page._table.setCurrentCell(row_of(page, "Apple"), 0)
    assert page._d_value.text() == f"3{NNBSP}424,50{NBSP}$"  # la fiche reste en dollars
    assert page._d_euros.text() == f"≈ 1{NNBSP}712,25{NBSP}€ · 1 USD = 0,50{NBSP}€"


def test_foreign_stocks_without_a_rate_are_left_out_of_the_summary(
    qtbot: QtBot, repository: StockRepository
) -> None:
    seed(repository)
    add_apple(repository)

    page = make_page(qtbot, repository)

    assert page._value_card._value.text() == f"3{NNBSP}462,40{NBSP}€"
    assert "1 sans taux de change exclue(s)" in page._value_card._sub.text()
    page._table.setCurrentCell(row_of(page, "Apple"), 0)
    assert "Taux de change inconnu" in page._d_euros.text()


def test_refresh_stores_the_rate_once_a_day(qtbot: QtBot, repository: StockRepository) -> None:
    seed(repository)
    add_apple(repository)
    rates = Quotes({"USD": D("0.5")})
    page = make_page(qtbot, repository, rates=rates)

    page.refresh_quotes()
    page.refresh_quotes()

    assert rates.calls == ["USD"]
    cached = FxRepository(repository._conn).latest("USD")
    assert cached is not None and (cached.rate, cached.rate_date) == (D("0.5"), TODAY)
    assert page._value_card._value.text() == f"5{NNBSP}174,65{NBSP}€"


def test_edit_exposure_flow_saves_and_refreshes(
    page: StocksPage, repository: StockRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: ExposureDialog) -> QDialog.DialogCode:
        self._sectors.set_entries(
            [ExposureEntry("Chimie", D(70)), ExposureEntry("Industrie", D(30))]
        )
        self._zones.set_entries([ExposureEntry("Europe", D(100))])
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExposureDialog, "exec", fill)
    page._table.setCurrentCell(row_of(page, "Air Liquide"), 0)

    page._edit_exposure(page._selected_view())

    section = page._exposure_section
    assert "Chimie" in section.legend_text(ExposureKind.SECTOR)
    assert "Europe" in section.legend_text(ExposureKind.ZONE)
    assert page._tiles["Secteur principal"].text() == f"Chimie · 70{NBSP}%"
    assert "Chimie" in page._exposure_card.legend_text(ExposureKind.SECTOR)


def test_fiche_says_when_nothing_is_filled_in(page: StocksPage) -> None:
    page._table.setCurrentCell(row_of(page, "ASML"), 0)

    assert "non renseignées" in page._exposure_section.legend_text(ExposureKind.ZONE)
    assert page._tiles["Zone principale"].text() == "—"
