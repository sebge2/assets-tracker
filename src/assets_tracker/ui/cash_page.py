"""Page « Comptes cash » : liste, fiche d'un compte (mouvements, taux, primes) et saisie."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.cash_repository import CashRepository
from assets_tracker.domain.cash import (
    BonusDefinition,
    BonusEvent,
    BonusEventKind,
    CashAccount,
    CashMovement,
    MovementKind,
    StatementLine,
    add_months,
    balance_of,
    base_rate_history,
    base_rate_on,
    bonus_events,
    bonus_on,
    check_movement_allowed,
    effective_rate_on,
    effective_rate_steps,
    interest_received_in_year,
    mask_iban,
    statement,
    weighted_average_rate,
)
from assets_tracker.ui import icons, theme
from assets_tracker.ui.cash_dialogs import AccountDialog, AccountForm, MovementDialog, MovementForm
from assets_tracker.ui.cash_evolution import EvolutionInput, EvolutionView
from assets_tracker.ui.formatting import (
    frequency_label,
    money,
    percent,
    points,
    signed_money,
)
from assets_tracker.ui.rate_chart import RateChart
from assets_tracker.ui.widgets import RIGHT as _RIGHT
from assets_tracker.ui.widgets import Card as _Card
from assets_tracker.ui.widgets import fit_height as _fit_height
from assets_tracker.ui.widgets import prepare_table as _prepare_table
from assets_tracker.ui.widgets import table_cell as _cell

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Vue calculée d'un compte à une date : tout est recalculé depuis l'historique."""

    account: CashAccount
    balance: Decimal
    base_rate: Decimal
    bonus_rate: Decimal
    effective_rate: Decimal
    year: int
    interest_year: Decimal  # intérêts et primes saisis sur l'année civile en cours


def snapshot_of(repository: CashRepository, account: CashAccount, today: date) -> AccountSnapshot:
    assert account.id is not None
    changes = repository.base_rates(account.id)
    bonuses = repository.bonuses(account.id)
    movements = repository.movements(account.id)
    balance = balance_of(movements)
    base = base_rate_on(changes, today)
    bonus = bonus_on(account.opened_on, bonuses, today)
    effective = effective_rate_on(account.opened_on, changes, bonuses, today)
    return AccountSnapshot(
        account,
        balance,
        base,
        bonus,
        effective,
        today.year,
        interest_received_in_year(movements, today.year),
    )


def _rich_label(html: str, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> QLabel:
    label = QLabel(html)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    label.setContentsMargins(12, 0, 12, 0)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return label


# --- Liste ------------------------------------------------------------------


class AccountsView(QWidget):
    openRequested = Signal(int)
    editRequested = Signal(int)
    newRequested = Signal()

    _COLUMNS = ["Compte", "IBAN", "Taux effectif", "Versement", "Solde", "Intérêts", ""]

    def __init__(self) -> None:
        super().__init__()
        title = QLabel("Comptes cash")
        title.setProperty("role", "title")
        subtitle = QLabel("Chaque dépôt ou retrait est rattaché à un compte.")
        subtitle.setProperty("role", "subtitle")
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(subtitle)
        add = QPushButton("Nouveau compte")
        add.setProperty("variant", "primary")
        add.clicked.connect(self.newRequested)
        header = QHBoxLayout()
        header.addLayout(heading, 1)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)

        self._total = _Card("Total cash")
        self._interest = _Card("Intérêts perçus")
        self._average = _Card("Taux moyen pondéré")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._total, self._interest, self._average):
            cards.addWidget(card, 1)

        self._table = QTableWidget()
        self._table.setAccessibleName("Comptes cash")
        _prepare_table(self._table, self._COLUMNS, row_height=64)
        self._table.setWordWrap(False)
        columns = self._table.horizontalHeader()
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((190, 170, 200, 120, 110, 64), start=1):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            columns.resizeSection(column, width)
        self._table.cellClicked.connect(self._on_cell)
        self._table.cellActivated.connect(self._on_cell)
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self._ids: list[int] = []

        self._empty = QLabel("Aucun compte cash. Créez-en un avec « Nouveau compte ».")
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(24)
        self._evolution = EvolutionView()
        self._mode = 0
        self._has_accounts = False
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        modes = QHBoxLayout()
        modes.setSpacing(8)
        for index, label in enumerate(("Comptes", "Évolution mensuelle")):
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setProperty("variant", "chip")
            chip.setChecked(index == 0)
            chip.clicked.connect(lambda _=False, i=index: self._set_mode(i))
            self._mode_group.addButton(chip)
            modes.addWidget(chip)
        modes.addStretch(1)

        layout.addLayout(header)
        layout.addLayout(cards)
        layout.addLayout(modes)
        layout.addWidget(self._table)
        layout.addWidget(self._empty)
        layout.addWidget(self._evolution)
        layout.addStretch(1)
        self._apply_mode()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _set_mode(self, index: int) -> None:
        self._mode = index
        self._apply_mode()

    def _apply_mode(self) -> None:
        accounts = self._mode == 0
        self._table.setVisible(accounts and self._has_accounts)
        self._empty.setVisible(accounts and not self._has_accounts)
        self._evolution.setVisible(not accounts)

    def set_evolution(self, inputs: list[EvolutionInput], today: date) -> None:
        self._evolution.set_inputs(inputs, today)

    def _on_cell(self, row: int, column: int) -> None:
        if column != len(self._COLUMNS) - 1 and 0 <= row < len(self._ids):
            self.openRequested.emit(self._ids[row])

    def set_snapshots(self, snapshots: list[AccountSnapshot]) -> None:
        self._summarize(snapshots)
        if snapshots:
            self._table.setHorizontalHeaderItem(
                5, QTableWidgetItem(f"Intérêts {snapshots[0].year}")
            )
        self._ids = [s.account.id for s in snapshots if s.account.id is not None]
        totals = self._currency_totals(snapshots)
        self._table.setRowCount(len(snapshots) + len(totals))
        for row, snap in enumerate(snapshots):
            account = snap.account
            self._table.setCellWidget(
                row,
                0,
                _rich_label(
                    f"<b>{account.name}</b><br><span style='color:{theme.TEXT_SUBTLE};"
                    f"font-size:12px'>{account.bank}</span>"
                ),
            )
            self._table.setItem(
                row, 1, _cell(mask_iban(account.iban), color=theme.TEXT_MUTED, mono=True)
            )
            bonus_tag = (
                f"<br><span style='color:{theme.WARNING};font-size:12px'>dont "
                f"{percent(snap.bonus_rate)} de prime</span>"
                if snap.bonus_rate > 0
                else ""
            )
            self._table.setCellWidget(
                row, 2, _rich_label(f"{percent(snap.effective_rate)}{bonus_tag}", _RIGHT)
            )
            self._table.setItem(row, 3, _cell(frequency_label(account), color=theme.TEXT_MUTED))
            self._table.setItem(
                row, 4, _cell(money(snap.balance, account.currency), mono=True, align=_RIGHT)
            )
            self._table.setItem(
                row,
                5,
                _cell(
                    signed_money(snap.interest_year, account.currency),
                    color=theme.GAIN if snap.interest_year > 0 else theme.TEXT_MUTED,
                    mono=True,
                    align=_RIGHT,
                ),
            )
            edit = QToolButton()
            edit.setProperty("role", "icon")
            edit.setIcon(icons.icon("edit"))
            edit.setIconSize(QSize(20, 20))
            edit.setToolTip("Modifier")
            edit.setAccessibleName(f"Modifier le compte {account.name}")
            edit.setCursor(Qt.CursorShape.PointingHandCursor)
            edit.clicked.connect(lambda _=False, i=account.id: self.editRequested.emit(i))
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 12, 0)
            holder_layout.addStretch(1)
            holder_layout.addWidget(edit)
            self._table.setCellWidget(row, len(self._COLUMNS) - 1, holder)
        for offset, (currency, balance, interest, rate) in enumerate(totals):
            label = "Total" if len(totals) == 1 else f"Total {currency}"
            self._fill_total_row(len(snapshots) + offset, label, currency, balance, interest, rate)
        self._has_accounts = bool(snapshots)
        self._apply_mode()
        _fit_height(self._table)

    @staticmethod
    def _currency_totals(
        snapshots: list[AccountSnapshot],
    ) -> list[tuple[str, Decimal, Decimal, Decimal]]:
        """Un total par devise : jamais de somme entre devises."""
        groups: dict[str, list[AccountSnapshot]] = defaultdict(list)
        for snap in snapshots:
            groups[snap.account.currency].append(snap)
        return [
            (
                currency,
                sum((s.balance for s in items), _ZERO),
                sum((s.interest_year for s in items), _ZERO),
                weighted_average_rate((s.balance, s.effective_rate) for s in items),
            )
            for currency, items in sorted(groups.items())
        ]

    def _fill_total_row(
        self,
        row: int,
        label: str,
        currency: str,
        balance: Decimal,
        interest: Decimal,
        rate: Decimal,
    ) -> None:
        self._table.setRowHeight(row, 52)
        cells = {
            0: _cell(label),
            2: _cell(percent(rate), mono=True, align=_RIGHT),
            4: _cell(money(balance, currency), mono=True, align=_RIGHT),
            5: _cell(
                signed_money(interest, currency),
                color=theme.GAIN if interest > 0 else theme.TEXT_MUTED,
                mono=True,
                align=_RIGHT,
            ),
        }
        for column in range(len(self._COLUMNS)):
            self._table.removeCellWidget(row, column)
            item = cells.get(column) or _cell("")
            item.setBackground(QColor(theme.RAISED))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if column in (0, 4):
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self._table.setItem(row, column, item)

    def _summarize(self, snapshots: list[AccountSnapshot]) -> None:
        """Totaux par devise : on n'additionne jamais des devises différentes."""
        balances: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        interests: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        for snap in snapshots:
            balances[snap.account.currency] += snap.balance
            interests[snap.account.currency] += snap.interest_year
        count = len(snapshots)
        plural = "s" if count > 1 else ""
        if not balances:
            balances["EUR"] = _ZERO
            interests["EUR"] = _ZERO
        self._total.set(
            " · ".join(money(v, c) for c, v in sorted(balances.items())), f"{count} compte{plural}"
        )
        total_interest = sum(interests.values(), _ZERO)
        year = snapshots[0].year if snapshots else date.today().year
        self._interest.set(
            " · ".join(signed_money(v, c) for c, v in sorted(interests.items())),
            f"{year} · primes de fidélité incluses",
            theme.GAIN if total_interest > 0 else theme.TEXT,
        )
        if len(balances) > 1:
            self._average.set("—", "devises multiples")
        else:
            average = weighted_average_rate((s.balance, s.effective_rate) for s in snapshots)
            self._average.set(percent(average), "pondéré par les soldes")


# --- Fiche d'un compte ------------------------------------------------------

_MOVEMENT_LABELS = {
    MovementKind.OPENING: "Solde d'ouverture",
    MovementKind.DEPOSIT: "Dépôt",
    MovementKind.WITHDRAWAL: "Retrait",
    MovementKind.INTEREST: "Intérêts",
    MovementKind.LOYALTY_BONUS: "Prime de fidélité",
}


@dataclass(frozen=True, slots=True)
class AccountDetail:
    snapshot: AccountSnapshot
    lines: list[StatementLine]
    interest_12m: Decimal
    bonus_info: str
    chart_steps: list[tuple[date, Decimal]]
    rate_rows: list[tuple[date, Decimal, Decimal | None]]
    bonus_events: list[BonusEvent]
    today: date


def detail_of(repository: CashRepository, account_id: int, today: date) -> AccountDetail | None:
    account = repository.get_account(account_id)
    if account is None:
        return None
    changes = repository.base_rates(account_id)
    bonuses = repository.bonuses(account_id)
    movements: list[CashMovement] = repository.movements(account_id)
    since = add_months(today, -12)
    interest_12m = sum(
        (
            m.amount
            for m in movements
            if m.kind in (MovementKind.INTEREST, MovementKind.LOYALTY_BONUS)
            and since < m.movement_date <= today
        ),
        _ZERO,
    )
    latest: BonusDefinition | None = bonuses[-1] if bonuses else None
    if latest is not None and latest.rate > 0:
        bonus_info = f"Prime de fidélité : {latest.after_months} mois de détention"
    else:
        bonus_info = "Pas de prime de fidélité"
    return AccountDetail(
        snapshot_of(repository, account, today),
        statement(movements),
        interest_12m,
        bonus_info,
        effective_rate_steps(account.opened_on, changes, bonuses, today),
        [(r.effective_date, r.rate, r.delta) for r in base_rate_history(changes)],
        bonus_events(account.opened_on, bonuses, today),
        today,
    )


class AccountDetailView(QWidget):
    backRequested = Signal()
    editRequested = Signal(int)
    movementRequested = Signal(int)
    movementEditRequested = Signal(int)
    movementDeleteRequested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._detail: AccountDetail | None = None
        self._filter = "all"

        back = QToolButton()
        back.setProperty("role", "icon")
        back.setIcon(icons.icon("back"))
        back.setIconSize(QSize(20, 20))
        back.setToolTip("Retour aux comptes")
        back.setAccessibleName("Retour aux comptes")
        back.clicked.connect(self.backRequested)
        self._title = QLabel()
        self._title.setProperty("role", "title")
        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "subtitle")
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(self._title)
        heading.addWidget(self._subtitle)
        edit = QPushButton("Modifier le compte")
        edit.clicked.connect(self._emit_edit)
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(back, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addLayout(heading, 1)
        header.addWidget(edit, 0, Qt.AlignmentFlag.AlignVCenter)

        self._balance = _Card("Solde")
        self._rate = _Card("Taux effectif")
        self._interest = _Card("Intérêts et primes perçus · 12 mois")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._balance, self._rate, self._interest):
            cards.addWidget(card, 1)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_movements_tab(), "Entrées et sorties")
        self._tabs.addTab(self._build_rates_tab(), "Taux d'intérêt")
        self._tabs.addTab(self._build_bonus_tab(), "Primes de fidélité")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(24)
        layout.addLayout(header)
        layout.addLayout(cards)
        layout.addWidget(self._tabs, 1)

    # -- onglets --

    def _build_movements_tab(self) -> QWidget:
        chips = QHBoxLayout()
        chips.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label in (("all", "Tout"), ("in", "Entrées"), ("out", "Sorties")):
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setProperty("variant", "chip")
            chip.setChecked(key == "all")
            chip.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            group.addButton(chip)
            chips.addWidget(chip)
        self._totals = QLabel()
        self._totals.setTextFormat(Qt.TextFormat.RichText)
        self._totals.setFont(theme.mono_font(12))
        add = QPushButton("Nouveau mouvement")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._emit_movement)
        bar = QHBoxLayout()
        bar.addLayout(chips)
        bar.addStretch(1)
        bar.addWidget(self._totals)
        bar.addWidget(add)

        self._movements = QTableWidget()
        self._movements.setAccessibleName("Entrées et sorties du compte")
        _prepare_table(
            self._movements, ["Date", "Opération", "Montant", "Solde après", ""], row_height=48
        )
        self._movements.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._movements.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._movements.horizontalHeader().resizeSection(4, 96)
        self._movements_empty = QLabel("Aucun mouvement pour l'instant.")
        self._movements_empty.setProperty("role", "muted")
        self._movements_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(16)
        layout.addLayout(bar)
        layout.addWidget(self._movements, 1)
        layout.addWidget(self._movements_empty)
        return tab

    def _build_rates_tab(self) -> QWidget:
        caption = QLabel("TAUX EFFECTIF (BASE + PRIME)")
        caption.setProperty("role", "caption")
        self._chart = RateChart()
        card = QFrame()
        card.setProperty("role", "card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.addWidget(caption)
        card_layout.addWidget(self._chart)

        history_caption = QLabel("HISTORIQUE DU TAUX DE BASE")
        history_caption.setProperty("role", "caption")
        self._rates = QTableWidget()
        self._rates.setAccessibleName("Historique du taux de base")
        _prepare_table(
            self._rates, ["Date d'effet", "Taux de base", "Variation", "Événement"], row_height=48
        )
        self._rates.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(card)
        layout.addWidget(history_caption)
        layout.addWidget(self._rates, 1)
        return tab

    def _build_bonus_tab(self) -> QWidget:
        self._bonuses = QTableWidget()
        self._bonuses.setAccessibleName("Historique des primes de fidélité")
        _prepare_table(self._bonuses, ["Date", "Prime", "Statut", "Événement"], row_height=48)
        self._bonuses.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self._bonus_empty = QFrame()
        self._bonus_empty.setProperty("role", "card")
        empty_layout = QVBoxLayout(self._bonus_empty)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        message = QLabel(
            "Ce compte n'a pas de prime de fidélité. Activez-la depuis « Modifier le compte » : "
            "elle apparaîtra ici dès que sa condition est remplie."
        )
        message.setWordWrap(True)
        message.setProperty("role", "muted")
        edit = QPushButton("Modifier le compte")
        edit.clicked.connect(self._emit_edit)
        empty_layout.addWidget(message)
        empty_layout.addWidget(edit, 0, Qt.AlignmentFlag.AlignLeft)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._bonuses, 1)
        layout.addWidget(self._bonus_empty)
        layout.addStretch(0)
        return tab

    # -- données --

    def _emit_edit(self) -> None:
        if self._detail is not None and self._detail.snapshot.account.id is not None:
            self.editRequested.emit(self._detail.snapshot.account.id)

    def _emit_movement(self) -> None:
        if self._detail is not None and self._detail.snapshot.account.id is not None:
            self.movementRequested.emit(self._detail.snapshot.account.id)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._fill_movements()

    def show_detail(self, detail: AccountDetail) -> None:
        self._detail = detail
        snap = detail.snapshot
        account = snap.account
        self._title.setText(account.name)
        self._subtitle.setText(f"{account.bank} · {mask_iban(account.iban)}")
        self._balance.set(money(snap.balance, account.currency), frequency_label(account))
        if snap.bonus_rate > 0:
            split = f"{percent(snap.base_rate)} de base + {percent(snap.bonus_rate)} de prime"
        else:
            split = "Taux de base, sans prime"
        self._rate.set(percent(snap.effective_rate), split)
        self._interest.set(
            signed_money(detail.interest_12m, account.currency),
            detail.bonus_info,
            theme.GAIN if detail.interest_12m > 0 else theme.TEXT,
        )
        self._fill_movements()
        self._fill_rates()
        self._fill_bonuses()

    def _fill_movements(self) -> None:
        assert self._detail is not None
        currency = self._detail.snapshot.account.currency
        lines = self._detail.lines
        total_in = sum(
            (
                line.movement.amount
                for line in lines
                if line.movement.signed_amount > 0
                and line.movement.kind is not MovementKind.OPENING
            ),
            _ZERO,
        )
        total_out = sum(
            (line.movement.amount for line in lines if line.movement.signed_amount < 0), _ZERO
        )
        self._totals.setText(
            f"<span style='color:{theme.GAIN}'>▲ {money(total_in, currency)}</span>"
            f" &nbsp;·&nbsp; <span style='color:{theme.LOSS}'>▼ {money(total_out, currency)}</span>"
        )

        def keep(line: StatementLine) -> bool:
            move = line.movement
            if self._filter == "in":
                return move.signed_amount > 0 and move.kind is not MovementKind.OPENING
            if self._filter == "out":
                return move.signed_amount < 0
            return True

        shown = [line for line in reversed(lines) if keep(line)]
        self._movements.setRowCount(len(shown))
        for row, line in enumerate(shown):
            move = line.movement
            label = _MOVEMENT_LABELS[move.kind]
            if move.kind is MovementKind.OPENING:
                amount, color = money(move.amount, currency), theme.TEXT_MUTED
            elif move.signed_amount > 0:
                amount, color = f"▲ {signed_money(move.amount, currency)}", theme.GAIN
            else:
                amount, color = f"▼ {signed_money(move.signed_amount, currency)}", theme.LOSS
            self._movements.setItem(
                row, 0, _cell(move.movement_date.isoformat(), color=theme.TEXT_MUTED, mono=True)
            )
            self._movements.setItem(row, 1, _cell(label))
            self._movements.setItem(row, 2, _cell(amount, color=color, mono=True, align=_RIGHT))
            self._movements.setItem(
                row,
                3,
                _cell(
                    money(line.balance_after, currency),
                    color=theme.TEXT_MUTED,
                    mono=True,
                    align=_RIGHT,
                ),
            )
            assert move.id is not None
            self._movements.setCellWidget(row, 4, self._movement_actions(move))
        self._movements.setVisible(bool(shown))
        self._movements_empty.setVisible(not shown)

    def _movement_actions(self, move: CashMovement) -> QWidget:
        label = f"{_MOVEMENT_LABELS[move.kind].lower()} du {move.movement_date.isoformat()}"
        movement_id = move.id
        assert movement_id is not None

        def button(icon_name: str, color: str, tooltip: str, name: str) -> QToolButton:
            tool = QToolButton()
            tool.setProperty("role", "icon")
            tool.setIcon(icons.icon(icon_name, color=color))
            tool.setIconSize(QSize(18, 18))
            tool.setToolTip(tooltip)
            tool.setAccessibleName(f"{name} {label}")
            tool.setCursor(Qt.CursorShape.PointingHandCursor)
            return tool

        edit = button("edit", theme.TEXT_MUTED, "Modifier ce mouvement", "Modifier")
        edit.clicked.connect(
            lambda _=False: self.movementEditRequested.emit(movement_id)  # noqa: B023
        )
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(edit)
        if move.kind is not MovementKind.OPENING:
            delete = button("delete", theme.LOSS, "Supprimer ce mouvement", "Supprimer")
            delete.clicked.connect(
                lambda _=False: self.movementDeleteRequested.emit(movement_id)  # noqa: B023
            )
            layout.addWidget(delete)
        return holder

    def _fill_rates(self) -> None:
        assert self._detail is not None
        self._chart.set_data(self._detail.chart_steps, self._detail.today)
        rows = list(reversed(self._detail.rate_rows))
        self._rates.setRowCount(len(rows))
        for row, (when, rate, delta) in enumerate(rows):
            if delta is None:
                note, color, delta_text = "Ouverture", theme.TEXT_MUTED, "—"
            elif delta > 0:
                note, color, delta_text = "Relèvement du taux", theme.GAIN, points(delta)
            elif delta < 0:
                note, color, delta_text = "Baisse du taux", theme.LOSS, points(delta)
            else:
                note, color, delta_text = "Taux confirmé", theme.TEXT_MUTED, points(delta)
            self._rates.setItem(row, 0, _cell(when.isoformat(), color=theme.TEXT_MUTED, mono=True))
            self._rates.setItem(row, 1, _cell(percent(rate), mono=True, align=_RIGHT))
            self._rates.setItem(row, 2, _cell(delta_text, color=color, mono=True, align=_RIGHT))
            self._rates.setItem(row, 3, _cell(note, color=theme.TEXT_MUTED))
        _fit_height(self._rates)

    def _fill_bonuses(self) -> None:
        assert self._detail is not None
        events = list(reversed(self._detail.bonus_events))
        self._bonuses.setRowCount(len(events))
        for row, event in enumerate(events):
            if event.kind is BonusEventKind.ACTIVATED:
                status, color = "Active", theme.WARNING
                note = f"Condition atteinte : {event.after_months} mois de détention"
            elif event.kind is BonusEventKind.DEFINED:
                status, color = "Définie", theme.TEXT_MUTED
                note = f"Prime définie, après {event.after_months} mois de détention"
            else:
                status, color = "Supprimée", theme.LOSS
                note = "Prime supprimée"
            rate = percent(event.rate) if event.rate > 0 else "—"
            self._bonuses.setItem(
                row, 0, _cell(event.event_date.isoformat(), color=theme.TEXT_MUTED, mono=True)
            )
            self._bonuses.setItem(
                row,
                1,
                _cell(
                    f"+{rate}" if event.rate > 0 else rate,
                    color=theme.WARNING,
                    mono=True,
                    align=_RIGHT,
                ),
            )
            self._bonuses.setItem(row, 2, _cell(status, color=color))
            self._bonuses.setItem(row, 3, _cell(note, color=theme.TEXT_MUTED))
        self._bonuses.setVisible(bool(events))
        self._bonus_empty.setVisible(not events)
        if events:
            _fit_height(self._bonuses)


# --- Page -------------------------------------------------------------------


class CashPage(QWidget):
    """Écran « Comptes cash » : orchestre la liste, la fiche et les dialogues."""

    def __init__(
        self,
        repository: CashRepository,
        today: Callable[[], date] = date.today,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._today = today
        self._current_id: int | None = None

        self._list = AccountsView()
        self._detail = AccountDetailView()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._list)
        self._stack.addWidget(self._detail)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._list.openRequested.connect(self.show_detail)
        self._list.editRequested.connect(self._edit_account)
        self._list.newRequested.connect(self._new_account)
        self._detail.backRequested.connect(self.show_list)
        self._detail.editRequested.connect(self._edit_account)
        self._detail.movementRequested.connect(self._new_movement)
        self._detail.movementEditRequested.connect(self._edit_movement)
        self._detail.movementDeleteRequested.connect(self._delete_movement)
        self.refresh()

    # -- navigation --

    def show_list(self) -> None:
        self._current_id = None
        self.refresh()
        self._stack.setCurrentWidget(self._list)

    def show_detail(self, account_id: int) -> None:
        self._current_id = account_id
        self.refresh()

    def refresh(self) -> None:
        today = self._today()
        if self._current_id is not None:
            detail = detail_of(self._repository, self._current_id, today)
            if detail is None:
                self._current_id = None
            else:
                self._detail.show_detail(detail)
                self._stack.setCurrentWidget(self._detail)
        accounts = self._repository.list_accounts()
        snapshots = [snapshot_of(self._repository, account, today) for account in accounts]
        self._list.set_snapshots(snapshots)
        self._list.set_evolution(
            [
                EvolutionInput(account, self._repository.movements(account.id))
                for account in accounts
                if account.id is not None
            ],
            today,
        )

    # -- dialogues --

    def _check_iban_free(self, form: AccountForm) -> None:
        for other in self._repository.list_accounts():
            if other.iban == form.account.iban and other.id != form.account.id:
                raise ValueError(f"Cet IBAN est déjà utilisé par le compte « {other.name} ».")

    def _new_account(self) -> None:
        dialog = AccountDialog(
            self,
            account=None,
            base_rate=None,
            bonus=None,
            today=self._today(),
            validator=self._check_iban_free,
        )
        if dialog.exec() != AccountDialog.DialogCode.Accepted:
            return
        form = dialog.form()
        created = self._repository.create_account(form.account, form.base_rate, form.bonus)
        self._current_id = created.id
        self.refresh()

    def _edit_account(self, account_id: int) -> None:
        account = self._repository.get_account(account_id)
        if account is None:
            return
        today = self._today()
        rates = self._repository.base_rates(account_id)
        bonuses = self._repository.bonuses(account_id)
        # La date d'ouverture n'entre pas en compte : sinon elle masquerait une date d'effet
        # antérieure que l'utilisateur vient d'enregistrer.
        latest_effective = max(
            [
                *(rate.effective_date for rate in rates),
                *(bonus.effective_date for bonus in bonuses),
            ],
            default=account.opened_on,
        )
        dialog = AccountDialog(
            self,
            account=account,
            base_rate=base_rate_on(rates, today),
            bonus=bonuses[-1] if bonuses else None,
            today=today,
            effective_date=latest_effective,
            validator=self._check_iban_free,
        )
        result = dialog.exec()
        if result == AccountDialog.DELETE:
            self._repository.delete_account(account_id)
            self.show_list()
        elif result == AccountDialog.DialogCode.Accepted:
            form = dialog.form()
            self._repository.update_account(
                form.account,
                base_rate=form.base_rate,
                bonus=form.bonus,
                effective_date=form.effective_date,
            )
            self.refresh()

    def _new_movement(self, account_id: int) -> None:
        account = self._repository.get_account(account_id)
        if account is None:
            return
        existing = self._repository.movements(account_id)

        def validate(form: MovementForm) -> None:
            check_movement_allowed(
                existing, CashMovement(None, account_id, form.kind, form.movement_date, form.amount)
            )

        dialog = MovementDialog(
            self,
            today=self._today(),
            balance_text=money(balance_of(existing), account.currency),
            validator=validate,
        )
        if dialog.exec() != MovementDialog.DialogCode.Accepted:
            return
        form = dialog.form()
        try:
            self._repository.add_movement(account_id, form.kind, form.movement_date, form.amount)
        except ValueError as error:
            QMessageBox.warning(self, "Mouvement refusé", str(error))
        self.refresh()

    def _edit_movement(self, movement_id: int) -> None:
        if self._current_id is None:
            return
        account_id = self._current_id
        account = self._repository.get_account(account_id)
        existing = self._repository.movements(account_id)
        target = next((m for m in existing if m.id == movement_id), None)
        if account is None or target is None:
            return
        others = [m for m in existing if m.id != movement_id]

        def validate(form: MovementForm) -> None:
            check_movement_allowed(
                others,
                CashMovement(movement_id, account_id, form.kind, form.movement_date, form.amount),
            )

        dialog = MovementDialog(
            self,
            today=self._today(),
            balance_text=money(balance_of(existing), account.currency),
            movement=target,
            validator=validate,
        )
        if dialog.exec() != MovementDialog.DialogCode.Accepted:
            return
        form = dialog.form()
        try:
            self._repository.update_movement(
                account_id, movement_id, form.kind, form.movement_date, form.amount
            )
        except ValueError as error:
            QMessageBox.warning(self, "Modification refusée", str(error))
        self.refresh()

    def _delete_movement(self, movement_id: int) -> None:
        if self._current_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Supprimer le mouvement",
            "Supprimer définitivement ce mouvement ? Cette action est irréversible.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_movement(self._current_id, movement_id)
        except ValueError as error:
            QMessageBox.warning(self, "Suppression impossible", str(error))
            return
        self.refresh()
