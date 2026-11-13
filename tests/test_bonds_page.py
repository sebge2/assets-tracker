from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog, QLabel
from pytestqt.qtbot import QtBot

from assets_tracker.db.bond_repository import BondRepository
from assets_tracker.db.connection import connect
from assets_tracker.db.fx_repository import FxRepository
from assets_tracker.domain.bonds import BondDetails, CouponFrequency
from assets_tracker.domain.funds import ValuationSource
from assets_tracker.domain.models import TransactionType
from assets_tracker.ui.bonds_page import BondDialog, BondsPage, QuoteDialog, TradeDialog

D = Decimal
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, 0)
NBSP = " "
NNBSP = " "
OAT = "FR0000000201"
CORPORATE = "XS0000000202"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = connect(tmp_path / "portfolio.db", key_provider=lambda: b"\x0b" * 32)
    yield connection
    connection.close()


@pytest.fixture
def repository(conn: Any) -> BondRepository:
    return BondRepository(conn)


def seed(repository: BondRepository) -> None:
    """Une OAT cotée aujourd'hui (Yahoo), une obligation corporate au cours manuel de 14 jours."""
    oat = repository.create(
        "OAT 3,00 % 2031",
        OAT,
        "EUR",
        BondDetails(
            "État français",
            D(1000),
            D(3),
            CouponFrequency.ANNUAL,
            date(2031, 5, 25),
            ValuationSource.YAHOO,
        ),
    )
    corporate = repository.create(
        "Obligation Corporate Alpha 4,50 % 2029",
        CORPORATE,
        "EUR",
        BondDetails(
            "Alpha Industries",
            D(1000),
            D("4.5"),
            CouponFrequency.SEMIANNUAL,
            date(2029, 9, 12),
            ValuationSource.MANUAL,
        ),
    )
    assert oat.asset.id is not None and corporate.asset.id is not None
    repository.add_transaction(
        oat.asset.id, TransactionType.BUY, date(2026, 3, 12), D(10), D("98.40"), D(0)
    )
    repository.add_transaction(
        corporate.asset.id, TransactionType.BUY, date(2026, 1, 15), D(8), D("100.50"), D(0)
    )
    repository.record_quote(oat.asset.id, TODAY, D("101.20"), NOW, ValuationSource.YAHOO)
    repository.record_quote(
        corporate.asset.id, date(2026, 10, 30), D("99.10"), NOW, ValuationSource.MANUAL
    )


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
    repository: BondRepository,
    quotes: Quotes | None = None,
    rates: Quotes | None = None,
) -> BondsPage:
    page = BondsPage(
        repository,
        FxRepository(repository._conn),
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
def page(qtbot: QtBot, repository: BondRepository) -> BondsPage:
    seed(repository)
    return make_page(qtbot, repository)


# --- page -------------------------------------------------------------------


def test_cards_summarize_the_bonds(page: BondsPage) -> None:
    # 10 × 1 012 + 8 × 991 = 18 048 € pour 17 880 € investis ; coupons 300 + 360
    assert page._value_card._value.text() == f"18{NNBSP}048,00{NBSP}€"
    assert page._gain_card._value.text() == f"+168,00{NBSP}€"
    assert page._gain_card._sub.text() == f"+0,9{NBSP}%"
    assert page._coupon_card._value.text() == f"660,00{NBSP}€"
    assert page._coupon_card._sub.text() == f"Rendement courant 3,66{NBSP}%"


def test_table_has_five_columns_and_no_weight_or_fees(page: BondsPage) -> None:
    headers = [page._table.horizontalHeaderItem(c).text() for c in range(page._table.columnCount())]

    assert headers == [
        "Obligation",
        "Coupon · échéance",
        "Cours (% nominal)",
        "Valeur",
        "+/− latente",
    ]
    assert page._table.rowCount() == 2
    # triées par échéance
    assert "Corporate Alpha" in page._table.cellWidget(0, 0).text()
    assert "OAT 3,00" in page._table.cellWidget(1, 0).text()
    assert "Yahoo · 13/11/2026" in page._table.cellWidget(1, 2).text()
    assert "manuel · 30/10/2026" in page._table.cellWidget(0, 2).text()


def test_old_quote_is_flagged_in_the_table_and_the_alert(page: BondsPage) -> None:
    text = page._alert_text.text()

    assert not page._alert.isHidden()
    assert "Corporate Alpha" in text
    assert "30/10/2026" in text
    assert "14 jours" in text
    assert "ancien" in page._table.cellWidget(0, 2).text()
    assert "ancien" not in page._table.cellWidget(1, 2).text()


def test_ladder_and_next_maturity(page: BondsPage) -> None:
    assert page._ladder._years == [(2029, D(8000)), (2030, D(0)), (2031, D(10000))]
    text = page._maturity_text.text()
    assert "Corporate Alpha" in text
    assert "12/09/2029" in text
    assert "dans 2 ans 9 mois" in text
    assert f"8{NNBSP}000,00{NBSP}€" in text
    assert f"180,00{NBSP}€" in text  # dernier coupon semestriel : 8 × 22,50 €


def test_detail_of_the_selected_bond(page: BondsPage) -> None:
    assert page._d_name.text() == "Obligation Corporate Alpha 4,50 % 2029"
    assert page._d_value.text() == f"7{NNBSP}928,00{NBSP}€"
    assert page._d_gain.text() == f"−112,00{NBSP}€ · −1,4{NBSP}%"
    assert "à mettre à jour" in page._d_source.text()
    tiles = {caption: label.text() for caption, label in page._tiles.items()}
    assert tiles["Titres"] == "8"
    assert tiles["Nominal"] == f"1{NNBSP}000,00{NBSP}€"
    assert tiles["PRU"] == f"100,50{NBSP}%"
    assert tiles["Cours"] == f"99,10{NBSP}%"
    assert tiles["Coupon"] == f"4,50{NBSP}% · semestriel"
    assert tiles["Rendement courant"] == f"4,54{NBSP}%"
    assert tiles["Coupon couru"] == f"61,66{NBSP}€"  # 180 × 62 / 181
    assert tiles["Prochain coupon"] == f"12/03/2027 · 180,00{NBSP}€"
    assert tiles["Échéance"] == "12/09/2029"
    assert tiles["Durée restante"] == "dans 2 ans 9 mois"

    page._table.setCurrentCell(1, 0)

    assert page._d_name.text() == "OAT 3,00 % 2031"
    assert page._d_gain.text() == f"+280,00{NBSP}€ · +2,8{NBSP}%"
    assert page._tiles["Coupon couru"].text() == f"141,37{NBSP}€"  # 300 × 172 / 365
    assert "Yahoo" in page._d_source.text() and "à mettre à jour" not in page._d_source.text()


def test_detail_has_no_repayment_action(page: BondsPage) -> None:
    buttons = [button.text() for button in page._detail.findChildren(type(page._buy_button))]

    assert "Rembourser" not in buttons
    assert {"Acheter", "Vendre", "Mettre à jour le cours…"} <= set(buttons)


def test_detail_lists_the_latest_operations_first(
    page: BondsPage, repository: BondRepository
) -> None:
    oat = repository.list_holdings()[1]
    assert oat.asset.id is not None
    repository.add_transaction(
        oat.asset.id, TransactionType.SELL, date(2026, 6, 1), D(2), D("100"), D("10")
    )

    page._table.setCurrentCell(1, 0)
    page.refresh()

    assert page._operations.count() == 2
    newest = page._operations.itemAt(0).widget()
    assert newest is not None
    assert newest.findChildren(QLabel)[0].text() == "Vente"


def test_empty_page_invites_to_create_a_bond(qtbot: QtBot, repository: BondRepository) -> None:
    page = make_page(qtbot, repository)

    assert not page._empty.isHidden()
    assert page._table.isHidden() and page._detail.isHidden()
    assert page._alert.isHidden() and page._maturity.isHidden() and page._ladder_card.isHidden()
    assert page._value_card._value.text() == f"0,00{NBSP}€"


def test_a_sold_out_bond_is_not_reported_as_outdated(
    page: BondsPage, repository: BondRepository
) -> None:
    corporate = repository.list_holdings()[0]
    assert corporate.asset.id is not None
    repository.add_transaction(
        corporate.asset.id, TransactionType.SELL, date(2026, 11, 1), D(8), D("99"), D(0)
    )

    page.refresh()

    assert page._alert.isHidden()
    assert not page._sell_button.isEnabled()


# --- cours Yahoo ------------------------------------------------------------


def test_yahoo_quote_is_recorded_for_open_bonds_not_valued_today(
    qtbot: QtBot, repository: BondRepository
) -> None:
    seed(repository)
    quotes = Quotes({CORPORATE: D("99.50")})
    page = make_page(qtbot, repository, quotes)

    page.refresh_quotes()

    assert quotes.calls == [CORPORATE]  # l'OAT est déjà cotée aujourd'hui
    corporate = repository.list_holdings()[0]
    assert corporate.valuation is not None
    assert (corporate.valuation.valuation_date, corporate.valuation.value) == (TODAY, D("995.00"))
    assert corporate.details.valuation_source is ValuationSource.YAHOO
    assert page._alert.isHidden()
    assert "Yahoo · 13/11/2026" in page._table.cellWidget(0, 2).text()


def test_yahoo_failure_asks_for_a_manual_quote_and_is_not_retried(
    qtbot: QtBot, repository: BondRepository
) -> None:
    seed(repository)
    quotes = Quotes({})
    page = make_page(qtbot, repository, quotes)

    page.refresh_quotes()
    page.refresh_quotes()

    assert quotes.calls == [CORPORATE]
    assert not page._alert.isHidden()
    assert "Yahoo ne fournit pas de cours pour" in page._alert_text.text()
    assert "Corporate Alpha" in page._alert_text.text()
    corporate = repository.list_holdings()[0]
    assert corporate.valuation is not None
    assert corporate.valuation.valuation_date == date(2026, 10, 30)  # rien n'est écrit

    page.refresh_quotes(force=True)
    assert quotes.calls == [CORPORATE, CORPORATE, OAT]  # « forcer » réinterroge tout


def test_a_provider_error_never_breaks_the_page(qtbot: QtBot, repository: BondRepository) -> None:
    seed(repository)

    def failing(symbol: str) -> Decimal | None:
        raise RuntimeError("réseau indisponible")

    page = BondsPage(
        repository,
        FxRepository(repository._conn),
        today=lambda: TODAY,
        now=lambda: NOW,
        quote_fetcher=failing,
        background=False,
    )
    qtbot.addWidget(page)

    page.refresh_quotes()

    assert not page._alert.isHidden()


def test_manual_quote_clears_the_alert(
    page: BondsPage, repository: BondRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: QuoteDialog) -> QDialog.DialogCode:
        self._price.setText("99,80")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QuoteDialog, "exec", fill)

    page._update_quote(page._selected_view())

    corporate = repository.list_holdings()[0]
    assert corporate.valuation is not None
    assert (corporate.valuation.valuation_date, corporate.valuation.value) == (TODAY, D("998.00"))
    assert corporate.details.valuation_source is ValuationSource.MANUAL
    assert page._alert.isHidden()


def test_quote_dialog_refuses_a_future_date(qtbot: QtBot, page: BondsPage) -> None:
    view = page._selected_view()
    assert view is not None
    dialog = QuoteDialog(None, view.holding, TODAY)
    qtbot.addWidget(dialog)
    dialog._price.setText("100")
    dialog._date.setDate(QDate(2026, 11, 20))

    with pytest.raises(ValueError, match="futur"):
        dialog.form()


# --- nouvelle obligation ----------------------------------------------------


def test_bond_dialog_builds_details_with_yahoo_as_the_default_source(qtbot: QtBot) -> None:
    dialog = BondDialog(None, TODAY)
    qtbot.addWidget(dialog)
    dialog._name.setText("Bund 2,40 % 2033")
    dialog._identifier.setText("de0000000203")
    dialog._issuer.setText("Allemagne")
    dialog._rate.setText("2,4")
    dialog._frequency.setCurrentIndex(1)
    dialog._maturity.setDate(QDate(2033, 2, 15))

    name, identifier, currency, details = dialog.form()

    assert (name, identifier, currency) == ("Bund 2,40 % 2033", "de0000000203", "EUR")
    assert details.valuation_source is ValuationSource.YAHOO
    assert details.frequency is CouponFrequency.SEMIANNUAL
    assert (details.nominal, details.coupon_rate, details.maturity) == (
        D(1000),
        D("2.4"),
        date(2033, 2, 15),
    )


def test_bond_dialog_has_no_yahoo_ticker_field(qtbot: QtBot) -> None:
    dialog = BondDialog(None, TODAY)
    qtbot.addWidget(dialog)

    captions = [label.text() for label in dialog.findChildren(QLabel)]

    assert not any("Ticker Yahoo" in text for text in captions)
    assert "ISIN ou ticker" in captions


def test_new_bond_flow_adds_a_row(
    page: BondsPage, repository: BondRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: BondDialog) -> QDialog.DialogCode:
        self._name.setText("Bund 2,40 % 2033")
        self._identifier.setText("DE0000000203")
        self._rate.setText("2,4")
        self._maturity.setDate(QDate(2033, 2, 15))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BondDialog, "exec", fill)

    page._new_bond()

    assert page._table.rowCount() == 3
    assert page._d_name.text() == "Bund 2,40 % 2033"
    assert len(repository.list_holdings()) == 3


def test_bond_dialog_stays_open_on_invalid_input_and_shows_the_error(qtbot: QtBot) -> None:
    dialog = BondDialog(None, TODAY)
    qtbot.addWidget(dialog)
    dialog._name.setText("Bund 2,40 % 2033")
    dialog._identifier.setText("DE0000000203")

    dialog.accept()  # le taux du coupon manque

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "Taux du coupon" in dialog._error.text()

    dialog._rate.setText("2,4")
    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog._error.text() == ""


def test_new_bond_keeps_the_dialog_and_its_input_after_a_refused_bond(
    page: BondsPage, repository: BondRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def fill(self: BondDialog) -> QDialog.DialogCode:
        if not opened:  # 1re ouverture : l'identifiant existe déjà
            self._name.setText("Bund 2,40 % 2033")
            self._identifier.setText(OAT)
            self._rate.setText("2,4")
            self._maturity.setDate(QDate(2033, 2, 15))
        else:  # la même fenêtre revient avec la saisie : on corrige l'identifiant
            assert self._name.text() == "Bund 2,40 % 2033"
            assert "déjà utilisé" in self._error.text()
            self._identifier.setText("DE0000000203")
        opened.append(self._identifier.text())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BondDialog, "exec", fill)

    page._new_bond()

    assert opened == [OAT, "DE0000000203"]
    assert len(repository.list_holdings()) == 3
    assert page._d_name.text() == "Bund 2,40 % 2033"


def test_quote_dialog_stays_open_on_an_invalid_quote(qtbot: QtBot, page: BondsPage) -> None:
    view = page._selected_view()
    assert view is not None
    dialog = QuoteDialog(None, view.holding, TODAY)
    qtbot.addWidget(dialog)

    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "Cours" in dialog._error.text()

    dialog._price.setText("99,5")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_edit_dialog_is_prefilled_with_every_field_editable(qtbot: QtBot, page: BondsPage) -> None:
    view = page._selected_view()  # l'obligation corporate : 4,5 % semestriel
    assert view is not None
    dialog = BondDialog(None, TODAY, view.holding)
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Modifier l'obligation"
    assert dialog._name.text() == "Obligation Corporate Alpha 4,50 % 2029"
    assert dialog._identifier.text() == CORPORATE
    assert dialog._frequency.currentData() is CouponFrequency.SEMIANNUAL
    assert dialog._nominal.isEnabled() and dialog._currency.isEnabled()
    _, _, currency, details = dialog.form()
    assert currency == "EUR"
    assert details == view.holding.details


def test_edit_can_change_the_nominal_and_currency(
    page: BondsPage, repository: BondRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: BondDialog) -> QDialog.DialogCode:
        self._nominal.setText("500")
        self._currency.setText("usd")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BondDialog, "exec", fill)

    page._edit_bond(page._selected_view())

    corporate = repository.list_holdings()[0]
    assert (corporate.details.nominal, corporate.asset.currency) == (D(500), "USD")
    assert corporate.quote_percent == D("99.1")  # le cours en % ne change pas
    assert page._tiles["PRU"].text() == f"100,50{NBSP}%"
    assert page._tiles["Nominal"].text() == f"500,00{NBSP}$"


def test_edit_flow_updates_the_bond_and_refreshes_the_page(
    page: BondsPage, repository: BondRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: BondDialog) -> QDialog.DialogCode:
        self._name.setText("Alpha 5 % 2030")
        self._rate.setText("5")
        self._maturity.setDate(QDate(2030, 9, 12))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BondDialog, "exec", fill)

    page._edit_bond(page._selected_view())

    corporate = repository.list_holdings()[0]
    assert corporate.asset.name == "Alpha 5 % 2030"
    assert corporate.details.maturity == date(2030, 9, 12)
    assert page._d_name.text() == "Alpha 5 % 2030"
    assert page._tiles["Coupon"].text() == f"5,00{NBSP}% · semestriel"
    assert page._tiles["Échéance"].text() == "12/09/2030"
    assert corporate.position.quantity == D(8)  # les opérations ne bougent pas


def test_edit_keeps_the_dialog_open_when_the_identifier_is_taken(
    page: BondsPage, repository: BondRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def fill(self: BondDialog) -> QDialog.DialogCode:
        if not opened:
            self._identifier.setText(OAT)  # déjà celui de l'OAT
        else:
            assert "déjà utilisé" in self._error.text()
            self._identifier.setText("XS0000000999")
        opened.append(self._identifier.text())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BondDialog, "exec", fill)

    page._edit_bond(page._selected_view())

    assert opened == [OAT, "XS0000000999"]
    assert repository.list_holdings()[0].asset.symbol == "XS0000000999"


def test_detail_has_an_edit_button(page: BondsPage) -> None:
    assert page._edit_button.text() == "Modifier l'obligation…"


# --- achat et vente ---------------------------------------------------------


def oat_dialog(
    qtbot: QtBot, repository: BondRepository, kind: TransactionType = TransactionType.BUY
) -> TradeDialog:
    dialog = TradeDialog(None, repository.list_holdings()[1], kind, TODAY)
    qtbot.addWidget(dialog)
    return dialog


def test_purchase_summary_adds_the_accrued_coupon_to_the_amount_debited(
    qtbot: QtBot, page: BondsPage, repository: BondRepository
) -> None:
    dialog = oat_dialog(qtbot, repository)

    dialog._quantity.setText("5")

    assert dialog._price.text() == "101,20"  # prérempli : dernier cours
    assert dialog._accrued.text() == "70,68"  # 5 × 30 € × 172 / 365, prérempli
    assert dialog.summary_rows() == [
        ("Montant", f"5{NNBSP}060,00{NBSP}€"),
        ("Coupon couru payé", f"+70,68{NBSP}€"),
        ("Total débité", f"5{NNBSP}130,68{NBSP}€"),
    ]
    assert dialog._after.text() == f"Après opération : 15 titres · PRU 99,33{NBSP}%"


def test_sale_summary_keeps_the_accrued_coupon_out_of_the_gain(
    qtbot: QtBot, page: BondsPage, repository: BondRepository
) -> None:
    dialog = oat_dialog(qtbot, repository, TransactionType.SELL)

    dialog._quantity.setText("5")

    assert dialog.summary_rows() == [
        ("Montant brut", f"5{NNBSP}060,00{NBSP}€"),
        ("Coupon couru perçu (hors plus-value)", f"+70,68{NBSP}€"),
        ("Total crédité", f"5{NNBSP}130,68{NBSP}€"),
        ("Plus-value réalisée", f"+140,00{NBSP}€"),  # 5 × (1 012 − 984), coupon exclu
    ]
    assert dialog._after.text() == f"Après opération : 5 titres · PRU 98,40{NBSP}%"


def test_trade_dialog_has_no_repayment_choice(
    qtbot: QtBot, page: BondsPage, repository: BondRepository
) -> None:
    dialog = oat_dialog(qtbot, repository)

    assert [b.text() for b in dialog._kind_buttons.values()] == ["Achat", "Vente"]


def test_an_edited_accrued_coupon_stops_following_the_quantity(
    qtbot: QtBot, page: BondsPage, repository: BondRepository
) -> None:
    dialog = oat_dialog(qtbot, repository)
    dialog._quantity.setText("5")

    dialog._accrued.textEdited.emit("80")
    dialog._accrued.setText("80")
    dialog._quantity.setText("6")

    assert dialog._accrued.text() == "80"
    assert dialog.form().accrued == D(80)


def test_invalid_quantities_disable_the_save_button(
    qtbot: QtBot, page: BondsPage, repository: BondRepository
) -> None:
    dialog = oat_dialog(qtbot, repository)

    for text in ("", "0", "2,5", "abc"):
        dialog._quantity.setText(text)
        assert not dialog._save.isEnabled(), text

    dialog._quantity.setText("2")
    assert dialog._save.isEnabled()


def test_selling_more_than_held_is_refused_with_a_message(
    qtbot: QtBot, page: BondsPage, repository: BondRepository
) -> None:
    dialog = oat_dialog(qtbot, repository, TransactionType.SELL)

    dialog._quantity.setText("11")

    assert not dialog._save.isEnabled()
    assert "supérieure" in dialog._error.text()


def test_trade_flow_records_the_operation_the_accrued_coupon_and_the_quote(
    page: BondsPage, repository: BondRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(self: TradeDialog) -> QDialog.DialogCode:
        self._quantity.setText("2")
        self._price.setText("102")
        self._record.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TradeDialog, "exec", fill)
    page._table.setCurrentCell(1, 0)

    page._trade(TransactionType.BUY)

    oat = repository.list_holdings()[1]
    assert oat.position.quantity == D(12)
    assert oat.valuation is not None and oat.valuation.value == D("1020.00")
    assert oat.details.valuation_source is ValuationSource.MANUAL
    bought = oat.transactions[-1]
    assert oat.accruals[bought.id or 0] == D("28.27")  # 2 × 30 € × 172 / 365
    assert page._d_value.text() == f"12{NNBSP}240,00{NBSP}€"  # 12 titres × 1 020 €


# --- Taux de change -----------------------------------------------------------


def add_dollar_bond(repository: BondRepository) -> None:
    """5 titres en dollars achetés à 98 %, cotés 100 % (nominal 1 000 $), coupon 5 %."""
    details = BondDetails(
        "Trésor US",
        D(1000),
        D(5),
        CouponFrequency.SEMIANNUAL,
        date(2031, 6, 15),
        ValuationSource.MANUAL,
    )
    holding = repository.create("T-Note 2031", "US0000000001", "USD", details)
    assert holding.asset.id is not None
    repository.add_transaction(
        holding.asset.id, TransactionType.BUY, date(2026, 5, 2), D(5), D(98), D(0)
    )
    repository.record_quote(holding.asset.id, TODAY, D(100), NOW, ValuationSource.MANUAL)


def test_summary_converts_foreign_bonds_with_the_cached_rate(
    qtbot: QtBot, repository: BondRepository
) -> None:
    seed(repository)
    add_dollar_bond(repository)
    FxRepository(repository._conn).record("USD", TODAY, D("0.5"), NOW)

    page = make_page(qtbot, repository)

    # 18 048 € + 5 × 1 000 $ × 0,5 = 20 548 € ; plus-value 168 + 5 × 20 $ × 0,5 = 218 €
    assert page._value_card._value.text() == f"20{NNBSP}548,00{NBSP}€"
    assert page._value_card._sub.text().endswith("converti en EUR")
    assert page._gain_card._value.text() == f"+218,00{NBSP}€"
    assert page._coupon_card._value.text() == f"785,00{NBSP}€"  # 660 + 5 × 1 000 $ × 5 % × 0,5


def test_foreign_bonds_without_a_rate_are_left_out_of_the_summary(
    qtbot: QtBot, repository: BondRepository
) -> None:
    seed(repository)
    add_dollar_bond(repository)

    page = make_page(qtbot, repository)

    assert page._value_card._value.text() == f"18{NNBSP}048,00{NBSP}€"
    assert "1 sans taux de change exclue(s)" in page._value_card._sub.text()


def test_refresh_stores_the_rate_once_a_day(qtbot: QtBot, repository: BondRepository) -> None:
    seed(repository)
    add_dollar_bond(repository)
    rates = Quotes({"USD": D("0.5")})
    page = make_page(qtbot, repository, rates=rates)

    page.refresh_quotes()
    page.refresh_quotes()

    assert rates.calls == ["USD"]
    cached = FxRepository(repository._conn).latest("USD")
    assert cached is not None and (cached.rate, cached.rate_date) == (D("0.5"), TODAY)
    assert page._value_card._value.text() == f"20{NNBSP}548,00{NBSP}€"


def test_a_failed_rate_keeps_the_previous_one(qtbot: QtBot, repository: BondRepository) -> None:
    seed(repository)
    add_dollar_bond(repository)
    FxRepository(repository._conn).record("USD", date(2026, 11, 1), D("0.5"), NOW)
    page = make_page(qtbot, repository, rates=Quotes({}))  # Yahoo n'a pas de taux

    page.refresh_quotes(force=True)

    cached = FxRepository(repository._conn).latest("USD")
    assert cached is not None and cached.rate_date == date(2026, 11, 1)
    assert page._value_card._value.text() == f"20{NNBSP}548,00{NBSP}€"
