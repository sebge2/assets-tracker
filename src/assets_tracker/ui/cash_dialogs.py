"""Boîtes de dialogue des comptes cash : compte (taux, prime, versement) et mouvement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.domain.cash import (
    BaseRateChange,
    BonusDefinition,
    CashAccount,
    CashMovement,
    InterestFrequency,
    MovementKind,
    format_iban,
    normalize_iban,
)
from assets_tracker.ui.form_helpers import date_edit as _date_edit
from assets_tracker.ui.form_helpers import labeled as _labeled
from assets_tracker.ui.form_helpers import row as _row
from assets_tracker.ui.form_helpers import section as _section
from assets_tracker.ui.form_helpers import to_date as _to_date
from assets_tracker.ui.formatting import MONTHS_FR, format_number, parse_decimal


@dataclass(frozen=True, slots=True)
class AccountForm:
    account: CashAccount
    base_rate: Decimal
    effective_date: date
    bonus: tuple[Decimal, int] | None  # (taux %, mois de détention) ; None = pas de prime


@dataclass(frozen=True, slots=True)
class MovementForm:
    kind: MovementKind
    movement_date: date
    amount: Decimal


class AccountDialog(QDialog):
    """Création ou modification d'un compte cash.

    Modifier le taux ou la prime n'écrase rien : la page ajoute une ligne à l'historique.
    """

    DELETE = 2  # code de retour de `exec()` quand l'utilisateur supprime le compte

    def __init__(
        self,
        parent: QWidget | None,
        *,
        account: CashAccount | None,
        base_rate: Decimal | None,
        bonus: BonusDefinition | None,
        today: date,
        effective_date: date | None = None,
        validator: Callable[[AccountForm], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._account = account
        self._validator = validator
        self.setWindowTitle(
            "Nouveau compte cash" if account is None else f"Modifier {account.name}"
        )
        self.setMinimumWidth(540)

        self._name = QLineEdit(account.name if account else "")
        self._name.setPlaceholderText("Ex. Livret A")
        self._bank = QLineEdit(account.bank if account else "")
        self._bank.setPlaceholderText("Ex. Crédit Mutuel")
        self._iban = QLineEdit(format_iban(account.iban) if account else "")
        self._iban.setPlaceholderText("FR76 0000 0000 0000 0000 0000 000")
        self._currency = QLineEdit(account.currency if account else "EUR")
        self._currency.setMaxLength(3)
        self._currency.setEnabled(account is None)  # la devise est figée après création
        self._rate = QLineEdit(format_number(base_rate) if base_rate is not None else "")
        self._rate.setPlaceholderText("0,00")
        self._effective = _date_edit(effective_date or today)

        self._annual_button = QPushButton("Annuel")
        self._monthly_button = QPushButton("Mensuel")
        group = QButtonGroup(self)
        group.setExclusive(True)
        for button in (self._annual_button, self._monthly_button):
            button.setCheckable(True)
            button.setProperty("variant", "chip")
            group.addButton(button)
        monthly = account is not None and account.frequency is InterestFrequency.MONTHLY
        self._monthly_button.setChecked(monthly)
        self._annual_button.setChecked(not monthly)

        self._month = QComboBox()
        self._month.addItems([name.capitalize() for name in MONTHS_FR])
        self._month.setCurrentIndex(
            (account.payout_month or 12) - 1 if account and account.payout_month else 11
        )
        self._day = QSpinBox()
        self._day.setRange(1, 31)
        self._day.setValue(account.payout_day if account and account.payout_day else 31)
        annual_row = _row(
            _labeled("Mois du versement", self._month), _labeled("Jour du versement", self._day)
        )
        monthly_note = QLabel("Versé le <b>1er de chaque mois</b>.")
        monthly_note.setTextFormat(Qt.TextFormat.RichText)
        self._payout_stack = QStackedWidget()
        self._payout_stack.addWidget(annual_row)
        self._payout_stack.addWidget(monthly_note)
        self._annual_button.toggled.connect(self._sync_payout)
        self._annual_button.toggled.connect(self._fit_later)

        has_bonus = bonus is not None and bonus.rate > 0
        self._bonus_check = QCheckBox("Prime de fidélité")
        self._bonus_check.setChecked(has_bonus)
        self._bonus_rate = QLineEdit(format_number(bonus.rate) if has_bonus and bonus else "1,00")
        self._bonus_months = QSpinBox()
        self._bonus_months.setRange(0, 600)
        self._bonus_months.setSuffix(" mois")
        self._bonus_months.setValue(bonus.after_months if has_bonus and bonus else 12)
        self._bonus_fields = _row(
            _labeled("Taux de la prime (%)", self._bonus_rate),
            _labeled("Condition : détention", self._bonus_months),
        )
        bonus_hint = QLabel(
            "Taux effectif = taux de base + prime. La prime s'ajoute à l'historique à la date "
            "où la condition est remplie."
        )
        bonus_hint.setProperty("role", "hint")
        bonus_hint.setWordWrap(True)
        self._bonus_extra = QWidget()
        extra_layout = QVBoxLayout(self._bonus_extra)
        extra_layout.setContentsMargins(0, 0, 0, 0)
        extra_layout.setSpacing(8)
        extra_layout.addWidget(self._bonus_fields)
        extra_layout.addWidget(bonus_hint)
        self._bonus_check.toggled.connect(self._bonus_extra.setVisible)
        self._bonus_check.toggled.connect(self._fit_later)

        self._error = QLabel()
        self._error.setProperty("role", "error")
        self._error.setWordWrap(True)
        self._error.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)
        layout.addWidget(_section("Compte"))
        layout.addWidget(
            _row(_labeled("Nom du compte", self._name), _labeled("Banque", self._bank))
        )
        layout.addWidget(
            _labeled(
                "IBAN",
                self._iban,
                "Stocké uniquement sur cet ordinateur, dans la base chiffrée. "
                "Affiché masqué dans la liste.",
            )
        )
        layout.addWidget(_labeled("Devise", self._currency))
        layout.addWidget(_section("Intérêts"))
        date_label = "Date d'ouverture" if account is None else "Date d'effet"
        layout.addWidget(
            _row(
                _labeled(
                    "Taux de base annuel (%)",
                    self._rate,
                    "Changer le taux ajoute une ligne à l'historique : l'ancien est conservé.",
                ),
                _labeled(date_label, self._effective),
            )
        )
        layout.addWidget(
            _labeled("Fréquence de versement", _row(self._annual_button, self._monthly_button))
        )
        layout.addWidget(self._payout_stack)
        layout.addWidget(self._bonus_check)
        layout.addWidget(self._bonus_extra)
        layout.addWidget(self._error)

        buttons = QHBoxLayout()
        if account is not None:
            delete = QPushButton("Supprimer")
            delete.setProperty("variant", "danger")
            delete.clicked.connect(self._confirm_delete)
            buttons.addWidget(delete)
        buttons.addStretch(1)
        box = QDialogButtonBox()
        cancel = box.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        save = box.addButton("Enregistrer", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setProperty("variant", "primary")
        save.setDefault(True)
        assert cancel is not None
        box.accepted.connect(self._try_accept)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)

        self._sync_payout()
        self._bonus_extra.setVisible(has_bonus)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (API Qt)
        super().showEvent(event)
        self._fit_height()

    def _fit_later(self) -> None:
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self) -> None:
        """Les libellés à retour à la ligne faussent `sizeHint()` : on impose la hauteur réelle."""
        layout = self.layout()
        if layout is None:
            return
        width = max(self.width(), self.minimumWidth())
        needed = layout.totalHeightForWidth(width)
        self.setMinimumHeight(needed)
        if self.height() < needed or self.height() > needed + 40:
            self.resize(self.width(), needed)

    def _sync_payout(self) -> None:
        self._payout_stack.setCurrentIndex(0 if self._annual_button.isChecked() else 1)

    def _confirm_delete(self) -> None:
        assert self._account is not None
        answer = QMessageBox.question(
            self,
            "Supprimer le compte",
            f"Supprimer « {self._account.name} » avec tout son historique (taux, primes, "
            "mouvements) ? Cette action est irréversible.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.done(self.DELETE)

    def form(self) -> AccountForm:
        """Construit le formulaire validé ; lève `ValueError` avec un message lisible."""
        effective = _to_date(self._effective)
        monthly = self._monthly_button.isChecked()
        account = CashAccount(
            id=self._account.id if self._account else None,
            name=self._name.text(),
            bank=self._bank.text(),
            iban=normalize_iban(self._iban.text()),
            currency=self._currency.text().strip().upper(),
            opened_on=self._account.opened_on if self._account else effective,
            frequency=InterestFrequency.MONTHLY if monthly else InterestFrequency.ANNUAL,
            payout_month=None if monthly else self._month.currentIndex() + 1,
            payout_day=None if monthly else self._day.value(),
        )
        base_rate = parse_decimal(self._rate.text(), "Taux de base")
        bonus: tuple[Decimal, int] | None = None
        if self._bonus_check.isChecked():
            bonus = (
                parse_decimal(self._bonus_rate.text(), "Taux de la prime"),
                self._bonus_months.value(),
            )
        # Valide les bornes des taux via les objets du domaine.
        BaseRateChange(effective, base_rate)
        if bonus is not None:
            BonusDefinition(effective, *bonus)
        return AccountForm(account, base_rate, effective, bonus)

    def _try_accept(self) -> None:
        try:
            form = self.form()
            if self._validator is not None:
                self._validator(form)
        except ValueError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self.accept()


class MovementDialog(QDialog):
    """Saisie d'un mouvement (dépôt, retrait, intérêts, prime de fidélité, solde d'ouverture)."""

    _KINDS = (
        ("Dépôt", MovementKind.DEPOSIT),
        ("Retrait", MovementKind.WITHDRAWAL),
        ("Intérêts", MovementKind.INTEREST),
        ("Prime de fidélité", MovementKind.LOYALTY_BONUS),
        ("Solde d'ouverture", MovementKind.OPENING),
    )

    def __init__(
        self,
        parent: QWidget | None,
        *,
        today: date,
        balance_text: str,
        movement: CashMovement | None = None,
        validator: Callable[[MovementForm], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._validator = validator
        self.setWindowTitle("Nouveau mouvement" if movement is None else "Modifier le mouvement")
        self.setMinimumWidth(420)

        self._kind = QComboBox()
        for label, kind in self._KINDS:
            self._kind.addItem(label, kind)
        self._amount = QLineEdit()
        self._amount.setPlaceholderText("0,00")
        self._date = _date_edit(movement.movement_date if movement else today)
        if movement is not None:
            self._kind.setCurrentIndex(self._kind.findData(movement.kind))
            self._amount.setText(format_number(movement.amount))
        self._error = QLabel()
        self._error.setProperty("role", "error")
        self._error.setWordWrap(True)
        self._error.hide()

        balance = QLabel(f"Solde actuel : {balance_text}")
        balance.setProperty("role", "muted")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)
        layout.addWidget(_labeled("Opération", self._kind))
        layout.addWidget(_row(_labeled("Montant", self._amount), _labeled("Date", self._date)))
        layout.addWidget(balance)
        layout.addWidget(self._error)
        box = QDialogButtonBox()
        box.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        save = box.addButton("Enregistrer", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setProperty("variant", "primary")
        save.setDefault(True)
        box.accepted.connect(self._try_accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def form(self) -> MovementForm:
        amount = parse_decimal(self._amount.text(), "Montant")
        if amount <= 0:
            raise ValueError("Le montant doit être strictement positif.")
        # MovementKind hérite de str : Qt restitue donc la donnée utilisateur sous
        # forme de chaîne. Recréer l'enum préserve le contrat de MovementForm.
        return MovementForm(MovementKind(self._kind.currentData()), _to_date(self._date), amount)

    def _try_accept(self) -> None:
        try:
            form = self.form()
            if self._validator is not None:
                self._validator(form)
        except ValueError as error:
            self._error.setText(str(error))
            self._error.show()
            return
        self.accept()
