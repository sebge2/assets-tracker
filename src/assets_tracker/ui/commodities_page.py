"""Page « Matières premières » : positions, répartition, fiche, achat, vente, modification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import QObject, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.db.commodity_repository import CommodityRepository, Holding
from assets_tracker.domain.commodities import (
    CommodityDetails,
    CommodityFamily,
    CommodityForm,
    Valuation,
    family_shares,
    valuation,
)
from assets_tracker.domain.models import Position, Transaction, TransactionType
from assets_tracker.domain.valuation import assets_to_refresh
from assets_tracker.market.commodities import fetch_holding_price
from assets_tracker.ui import icons, theme
from assets_tracker.ui.commodity_chart import PositionChart
from assets_tracker.ui.commodity_dialogs import (
    EditPositionDialog,
    NewPositionDialog,
    NewPositionForm,
    TradeDialog,
    TradeForm,
)
from assets_tracker.ui.formatting import (
    FAMILY_LABELS,
    format_number,
    format_quantity,
    money,
    relative_age,
    short_datetime,
    signed_money,
)
from assets_tracker.ui.widgets import (
    RIGHT,
    Card,
    PageContent,
    fit_height,
    prepare_table,
    table_cell,
)

CURRENCY = "EUR"
REFRESH_EVERY_MS = 15 * 60 * 1000
WIDE_LAYOUT = 1340  # largeur de page à partir de laquelle la fiche passe à droite
DETAIL_WIDTH = 380
FAMILY_COLORS = {
    CommodityFamily.PRECIOUS: theme.SERIES[4],
    CommodityFamily.ENERGY: theme.SERIES[2],
    CommodityFamily.INDUSTRIAL: theme.SERIES[0],
    CommodityFamily.AGRICULTURAL: theme.SERIES[3],
}

PriceFetcher = Callable[[CommodityDetails], Decimal | None]


@dataclass(frozen=True, slots=True)
class HoldingView:
    """Une matière valorisée : tout est recalculé depuis ses transactions et son dernier cours."""

    holding: Holding
    position: Position
    valuation: Valuation | None  # None sans cours connu
    stale: bool  # cours ancien ou dernière actualisation en échec

    @property
    def asset_id(self) -> int:
        assert self.holding.asset.id is not None
        return self.holding.asset.id


def build_view(holding: Holding, today: date, failed: set[int]) -> HoldingView:
    """Valorise une matière ; sa valorisation est « en cache » si elle date d'un jour passé."""
    position = holding.position
    price = holding.price
    priced = valuation(position, price.price) if price is not None else None
    stale = price is not None and (price.valuation_date < today or holding.asset.id in failed)
    return HoldingView(holding, position, priced, stale)


def per_unit(details: CommodityDetails) -> str:
    return "" if details.unit == "parts" else f" / {details.unit}"


def gain_color(value: Decimal) -> str:
    return theme.GAIN if value > 0 else theme.LOSS if value < 0 else theme.TEXT_MUTED


# --- Actualisation des cours hors du thread Qt -------------------------------


def fetch_prices(
    requests: list[tuple[int, CommodityDetails]], fetcher: PriceFetcher
) -> dict[int, Decimal | None]:
    """Un cours (ou None) par actif ; une erreur du fournisseur ne bloque jamais les autres."""
    results: dict[int, Decimal | None] = {}
    for asset_id, details in requests:
        try:
            results[asset_id] = fetcher(details)
        except Exception:
            results[asset_id] = None
    return results


class _TaskSignals(QObject):
    done = Signal(object)


class _PriceTask(QRunnable):
    """Interroge le réseau dans un thread du pool ; la base n'est jamais touchée ici."""

    def __init__(self, requests: list[tuple[int, CommodityDetails]], fetcher: PriceFetcher) -> None:
        super().__init__()
        self.signals = _TaskSignals()
        self._requests = requests
        self._fetcher = fetcher

    def run(self) -> None:
        self.signals.done.emit(fetch_prices(self._requests, self._fetcher))


# --- Répartition --------------------------------------------------------------


class AllocationBar(QWidget):
    """Barre segmentée : une couleur par famille, proportionnelle à sa valeur."""

    def __init__(self) -> None:
        super().__init__()
        self._segments: list[tuple[str, Decimal]] = []
        self.setFixedHeight(12)

    def set_segments(self, segments: list[tuple[str, Decimal]]) -> None:
        self._segments = segments
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        total = sum((share for _, share in self._segments), Decimal(0))
        painter.setBrush(QColor(theme.RAISED))
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        if total <= 0:
            return
        gap = 2.0
        usable = self.width() - gap * (len(self._segments) - 1)
        x = 0.0
        for index, (color, share) in enumerate(self._segments):
            width = usable * float(share / total)
            painter.setBrush(QColor(color))
            first, last = index == 0, index == len(self._segments) - 1
            painter.drawRoundedRect(
                QRectF(x, 0, width, self.height()), 6 if first or last else 0, 6
            )
            x += width + gap


# --- Page ---------------------------------------------------------------------


class CommoditiesPage(QWidget):
    """Écran « Matières premières ».

    Le réseau n'est utilisé que pour actualiser les cours (module `market`), hors du thread Qt ;
    un échec conserve le dernier cours connu, affiché avec sa date et le badge « Cache ».
    """

    _COLUMNS = ["Matière", "Détention", "PRU", "Cours", "Valeur", "+/− value"]

    def __init__(
        self,
        repository: CommodityRepository,
        *,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = datetime.now,
        price_fetcher: PriceFetcher = fetch_holding_price,
        background: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._today = today
        self._now = now
        self._price_fetcher = price_fetcher
        self._background = background
        self._selected: int | None = None
        self._family: CommodityFamily | None = None
        self._failed: set[int] = set()
        self._refreshing = False
        self._started = False
        self._tasks: list[_PriceTask] = []
        self._views: list[HoldingView] = []
        self._shown: list[HoldingView] = []
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_EVERY_MS)
        self._timer.timeout.connect(self.refresh_prices)

        title = QLabel("Matières premières")
        title.setProperty("role", "title")
        subtitle = QLabel(
            "Métaux, énergie, agricole : en physique ou via des ETC. "
            "Cours indicatifs, convertis en euros."
        )
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(subtitle)
        self._refresh_button = QPushButton("Actualiser les cours")
        self._refresh_button.clicked.connect(lambda _=False: self.refresh_prices(force=True))
        add = QPushButton("Nouvelle position")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._new_position)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addLayout(heading, 1)
        header.addWidget(self._refresh_button, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)

        self._value_card = Card("Valeur totale")
        self._gain_card = Card("Plus-value latente")
        self._price_card = Card("Cours")
        cards = QHBoxLayout()
        cards.setSpacing(16)
        for card in (self._value_card, self._gain_card, self._price_card):
            cards.addWidget(card, 1)

        self._bar = AllocationBar()
        self._legend = QLabel()
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setProperty("role", "muted")
        self._legend.setWordWrap(True)
        caption = QLabel("RÉPARTITION PAR FAMILLE")
        caption.setProperty("role", "caption")
        self._allocation = QFrame()
        self._allocation.setProperty("role", "card")
        allocation_layout = QVBoxLayout(self._allocation)
        allocation_layout.setContentsMargins(20, 16, 20, 16)
        allocation_layout.setSpacing(12)
        allocation_layout.addWidget(caption)
        allocation_layout.addWidget(self._bar)
        allocation_layout.addWidget(self._legend)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        self._chips: dict[CommodityFamily | None, QPushButton] = {}
        for family, label in [(None, "Tout"), *FAMILY_LABELS.items()]:
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setProperty("variant", "chip")
            chip.setChecked(family is None)
            chip.clicked.connect(lambda _=False, f=family: self._set_family(f))
            self._chip_group.addButton(chip)
            self._chips[family] = chip
            chips.addWidget(chip)
        chips.addStretch(1)

        self._table = QTableWidget()
        self._table.setAccessibleName("Positions en matières premières")
        prepare_table(self._table, self._COLUMNS, row_height=64)
        self._table.setWordWrap(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        columns = self._table.horizontalHeader()
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate((110, 140, 170, 110, 100), start=1):
            columns.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            columns.resizeSection(column, width)
        self._table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.currentCellChanged.connect(self._on_row_changed)

        self._empty = QLabel(
            "Aucune matière première. Ajoutez une position avec « Nouvelle position »."
        )
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._detail = self._build_detail()
        self._body = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._body.setSpacing(16)
        self._body.addWidget(self._table, 1, Qt.AlignmentFlag.AlignTop)
        self._body.addWidget(self._detail, 0, Qt.AlignmentFlag.AlignTop)

        content = PageContent()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(24)
        layout.addLayout(header)
        layout.addLayout(cards)
        layout.addWidget(self._allocation)
        layout.addLayout(chips)
        layout.addLayout(self._body)
        layout.addWidget(self._empty)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.refresh()

    # -- fiche --

    def _build_detail(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        frame.setMinimumWidth(280)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        self._d_name = QLabel()
        self._d_name.setProperty("role", "title")
        self._d_name.setStyleSheet("font-size: 22px;")
        self._d_sub = QLabel()
        self._d_sub.setProperty("role", "subtitle")
        self._d_form = QLabel()
        self._d_form.setStyleSheet(
            f"background: {theme.OVERLAY}; color: {theme.TEXT_MUTED}; border-radius: 10px; "
            "padding: 2px 10px; font-size: 12px; font-weight: 600;"
        )
        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(self._d_name)
        names.addWidget(self._d_sub)
        top.addLayout(names, 1)
        top.addWidget(self._d_form, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        self._d_price = QLabel()
        self._d_price.setFont(theme.mono_font(26))
        self._d_price_note = QLabel()
        self._d_price_note.setProperty("role", "muted")
        layout.addWidget(self._d_price)
        layout.addWidget(self._d_price_note)

        chart_caption = QLabel("PRIX D'ACHAT, PRU ET COURS")
        chart_caption.setProperty("role", "caption")
        self._chart = PositionChart()
        legend = QLabel(
            f"<span style='color:{theme.ACCENT}'>■</span> Prix connus &nbsp;&nbsp; "
            f"<span style='color:{theme.WARNING}'>■</span> Prix de revient"
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setProperty("role", "muted")
        layout.addWidget(chart_caption)
        layout.addWidget(self._chart)
        layout.addWidget(legend)

        self._tiles: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setSpacing(10)
        for index, key in enumerate(("Détention", "PRU", "Valeur", "+/− value")):
            grid.addWidget(self._tile(key), index // 2, index % 2)
        grid.addWidget(self._tile("extra"), 2, 0, 1, 2)
        layout.addLayout(grid)

        self._buy_button = QPushButton("Ajouter un achat")
        self._buy_button.clicked.connect(lambda: self._trade(TransactionType.BUY))
        self._sell_button = QPushButton("Vendre")
        self._sell_button.clicked.connect(lambda: self._trade(TransactionType.SELL))
        self._edit_button = QToolButton()
        self._edit_button.setProperty("role", "icon")
        self._edit_button.setIcon(icons.icon("edit"))
        self._edit_button.setIconSize(QSize(20, 20))
        self._edit_button.setToolTip("Modifier")
        self._edit_button.setAccessibleName("Modifier la position")
        self._edit_button.clicked.connect(self._edit)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self._buy_button)
        actions.addWidget(self._sell_button)
        actions.addStretch(1)
        actions.addWidget(self._edit_button)
        layout.addLayout(actions)
        return frame

    def _tile(self, key: str) -> QFrame:
        tile = QFrame()
        tile.setStyleSheet(
            f"background: {theme.RAISED}; border: 1px solid {theme.BORDER}; border-radius: 10px;"
        )
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        label = QLabel(key if key != "extra" else "")
        label.setStyleSheet(f"color: {theme.TEXT_SUBTLE}; font-size: 12px; border: none;")
        value = QLabel()
        value.setFont(theme.mono_font(14))
        value.setStyleSheet("border: none;")
        value.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value)
        self._tiles[key] = value
        self._tiles[f"{key}:label"] = label
        return tile

    # -- données --

    def refresh(self) -> None:
        now = self._now()
        self._views = [
            build_view(holding, self._today(), self._failed)
            for holding in self._repository.list_holdings()
        ]
        self._shown = [
            v
            for v in self._views
            if self._family is None or v.holding.details.family is self._family
        ]
        ids = [v.asset_id for v in self._shown]
        if self._selected not in ids:
            self._selected = ids[0] if ids else None
        self._summarize(now)
        self._fill_table()
        self._show_selected()
        has_any = bool(self._views)
        self._table.setVisible(bool(self._shown))
        self._detail.setVisible(self._selected is not None)
        self._empty.setVisible(not has_any)
        self._allocation.setVisible(bool(self._views))

    def _summarize(self, now: datetime) -> None:
        priced = [v for v in self._views if v.valuation is not None and v.position.quantity > 0]
        total = sum((v.valuation.value for v in priced if v.valuation), Decimal(0))
        cost = sum((v.valuation.cost for v in priced if v.valuation), Decimal(0))
        gain = total - cost
        missing = sum(1 for v in self._views if v.valuation is None and v.position.quantity > 0)
        count = len(self._views)
        if priced:
            self._value_card.set(
                money(total, CURRENCY),
                f"{count} position{'s' if count > 1 else ''}"
                + (f" · {missing} sans cours" if missing else ""),
            )
            percent = gain / cost * 100 if cost else Decimal(0)
            self._gain_card.set(
                signed_money(gain, CURRENCY),
                f"{'+' if percent > 0 else ''}{format_number(percent, 1)} % depuis l'achat",
                gain_color(gain),
            )
        else:
            self._value_card.set("—", "Aucun cours connu" if count else "Aucune position")
            self._gain_card.set("—", "")
        prices = [v.holding.price.fetched_at for v in self._views if v.holding.price is not None]
        stale = sum(1 for v in self._views if v.stale)
        if prices:
            age = relative_age((now - max(prices)).total_seconds())
            self._price_card.set(
                age,
                f"{stale} cours en cache" if stale else "Tous les cours sont à jour",
            )
        else:
            self._price_card.set("Jamais actualisé", "Cliquez sur « Actualiser les cours »")
        by_family: dict[CommodityFamily, Decimal] = {}
        for v in priced:
            if v.valuation:
                family = v.holding.details.family
                by_family[family] = by_family.get(family, Decimal(0)) + v.valuation.value
        shares = family_shares(by_family)
        self._bar.set_segments([(FAMILY_COLORS[f], share) for f, share in shares])
        self._legend.setText(
            " &nbsp;&nbsp;&nbsp; ".join(
                f"<span style='color:{FAMILY_COLORS[f]}'>■</span> {FAMILY_LABELS[f]} "
                f"<span style='color:{theme.TEXT}'>{format_number(share, 1)} %</span>"
                for f, share in shares
            )
        )

    def _fill_table(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._shown))
        for row, view in enumerate(self._shown):
            self._fill_row(row, view)
        if self._selected is not None:
            for row, view in enumerate(self._shown):
                if view.asset_id == self._selected:
                    self._table.selectRow(row)
        self._table.blockSignals(False)
        fit_height(self._table)

    def _fill_row(self, row: int, view: HoldingView) -> None:
        holding, position = view.holding, view.position
        details = holding.details
        color = FAMILY_COLORS[details.family]
        kind = "Physique" if details.form is CommodityForm.PHYSICAL else "ETC"
        self._table.setCellWidget(
            row,
            0,
            _rich(
                f"<span style='color:{color}'>■</span>&nbsp; <b>{holding.asset.name}</b><br>"
                f"<span style='color:{theme.TEXT_SUBTLE};font-size:12px'>"
                f"{kind} · {holding.asset.symbol}</span>"
            ),
        )
        self._table.setItem(
            row,
            1,
            table_cell(
                f"{format_quantity(position.quantity)} {details.unit}", mono=True, align=RIGHT
            ),
        )
        self._table.setItem(
            row,
            2,
            table_cell(
                f"{money(position.pru, CURRENCY)}{per_unit(details)}",
                color=theme.TEXT_MUTED,
                mono=True,
                align=RIGHT,
            ),
        )
        if holding.price is None:
            price_html = (
                f"—<br><span style='color:{theme.TEXT_SUBTLE};font-size:11px'>Pas de cours</span>"
            )
        else:
            note = short_datetime(holding.price.fetched_at)
            badge = f" <span style='color:{theme.WARNING}'>· Cache</span>" if view.stale else ""
            price_html = (
                f"{money(holding.price.price, CURRENCY)}{per_unit(details)}<br>"
                f"<span style='color:{theme.TEXT_SUBTLE};font-size:11px'>{note}{badge}</span>"
            )
        self._table.setCellWidget(row, 3, _rich(price_html, RIGHT, mono=True))
        valued = view.valuation
        self._table.setItem(
            row,
            4,
            table_cell(
                "—" if valued is None else money(valued.value, CURRENCY), mono=True, align=RIGHT
            ),
        )
        if valued is None:
            gain_html = "—"
        else:
            sign = "+" if (valued.unrealized_percent or 0) > 0 else ""
            percent = (
                "—"
                if valued.unrealized_percent is None
                else f"{sign}{format_number(valued.unrealized_percent, 1)} %"
            )
            gain_html = (
                f"<span style='color:{gain_color(valued.unrealized)}'>"
                f"{signed_money(valued.unrealized, CURRENCY)}<br>"
                f"<span style='font-size:12px'>{percent}</span></span>"
            )
        self._table.setCellWidget(row, 5, _rich(gain_html, RIGHT, mono=True))

    def _show_selected(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        holding, position = view.holding, view.position
        details = holding.details
        kind = "Physique" if details.form is CommodityForm.PHYSICAL else "ETC"
        self._d_name.setText(holding.asset.name)
        self._d_sub.setText(f"{kind} · {holding.asset.symbol} · {FAMILY_LABELS[details.family]}")
        self._d_form.setText(kind)
        if holding.price is None:
            self._d_price.setText("—")
            self._d_price_note.setText("Pas de cours connu")
        else:
            self._d_price.setText(f"{money(holding.price.price, CURRENCY)}{per_unit(details)}")
            note = f"Cours du {short_datetime(holding.price.fetched_at)}"
            self._d_price_note.setText(note + (" · en cache" if view.stale else ""))
            self._d_price_note.setStyleSheet(
                f"color: {theme.WARNING if view.stale else theme.TEXT_MUTED};"
            )
        if len(holding.history) >= 2:  # valorisations quotidiennes historisées
            points = [(v.valuation_date, v.price) for v in holding.history]
        else:  # pas encore d'historique : prix de vos transactions et dernier cours
            points = [(t.trade_date, t.unit_price) for t in holding.transactions]
            if holding.price is not None:
                points.append((holding.price.valuation_date, holding.price.price))
        self._chart.set_data(points, position.pru if position.quantity > 0 else None)
        self._tiles["Détention"].setText(f"{format_quantity(position.quantity)} {details.unit}")
        self._tiles["PRU"].setText(f"{money(position.pru, CURRENCY)}{per_unit(details)}")
        valued = view.valuation
        self._tiles["Valeur"].setText("—" if valued is None else money(valued.value, CURRENCY))
        gain_text = "—" if valued is None else signed_money(valued.unrealized, CURRENCY)
        self._tiles["+/− value"].setText(gain_text)
        self._tiles["+/− value"].setStyleSheet(
            f"border: none; color: {gain_color(valued.unrealized) if valued else theme.TEXT_MUTED};"
        )
        if details.form is CommodityForm.PHYSICAL:
            self._tiles["extra:label"].setText("Première acquisition")
            first = holding.transactions[0].trade_date.isoformat() if holding.transactions else "—"
            self._tiles["extra"].setText(first)
        else:
            self._tiles["extra:label"].setText("Courtier")
            self._tiles["extra"].setText(details.broker or "non renseigné")
        self._sell_button.setEnabled(position.quantity > 0)

    def _selected_view(self) -> HoldingView | None:
        for view in self._shown:
            if view.asset_id == self._selected:
                return view
        return None

    # -- interactions --

    def _set_family(self, family: CommodityFamily | None) -> None:
        self._family = family
        self.refresh()

    def _on_row_changed(self, row: int, *_: int) -> None:
        if 0 <= row < len(self._shown):
            self._selected = self._shown[row].asset_id
            self._show_selected()

    def _new_position(self) -> None:
        def validate(form: NewPositionForm) -> None:
            if any(h.asset.symbol == form.symbol for h in self._repository.list_holdings()):
                raise ValueError(f"Une matière porte déjà le symbole « {form.symbol} ».")

        dialog = NewPositionDialog(self, today=self._today(), validator=validate)
        if dialog.exec() != NewPositionDialog.DialogCode.Accepted:
            return
        form = dialog.form()
        first = Transaction(
            None,
            0,
            TransactionType.BUY,
            form.first.trade_date,
            form.first.quantity,
            form.first.unit_price,
            form.first.fees,
        )
        created = self._repository.create_position(form.name, form.symbol, form.details, first)
        self._selected = created.asset.id
        self._family = None
        self._chips[None].setChecked(True)
        self.refresh()
        self.refresh_prices()

    def _trade(self, kind: TransactionType) -> None:
        view = self._selected_view()
        if view is None:
            return
        asset_id = view.asset_id

        def validate(form: TradeForm) -> None:
            self._repository.check_transaction(
                asset_id, form.kind, form.trade_date, form.quantity, form.unit_price, form.fees
            )

        dialog = TradeDialog(
            self, kind=kind, holding=view.holding, today=self._today(), validator=validate
        )
        if dialog.exec() != TradeDialog.DialogCode.Accepted:
            return
        form = dialog.form()
        self._repository.add_transaction(
            asset_id, form.kind, form.trade_date, form.quantity, form.unit_price, form.fees
        )
        self.refresh()

    def _edit(self) -> None:
        view = self._selected_view()
        if view is None:
            return
        asset_id = view.asset_id
        dialog = EditPositionDialog(self, repository=self._repository, asset_id=asset_id)
        result = dialog.exec()
        if result == EditPositionDialog.DELETE:
            self._repository.delete_position(asset_id)
            self._selected = None
            self.refresh()
        elif dialog.modified:
            self.refresh()

    # -- cours --

    def refresh_prices(self, force: bool = False) -> None:
        """Actualise les cours : une seule fois par jour et par matière, sauf `force`.

        Une matière déjà valorisée aujourd'hui n'est pas redemandée au fournisseur ; un échec
        garde le dernier cours connu (badge « Cache ») et sera retenté au prochain passage.
        """
        if self._refreshing:
            return
        quoted = [
            h
            for h in self._repository.list_holdings()
            if h.details.quote_symbol and h.asset.id is not None
        ]
        due = set(
            assets_to_refresh(
                [h.asset.id for h in quoted if h.asset.id is not None],
                self._repository.valued_on(self._today()),
                force=force,
            )
        )
        requests = [(h.asset.id, h.details) for h in quoted if h.asset.id in due and h.asset.id]
        if not requests:
            return
        self._refreshing = True
        self._refresh_button.setEnabled(False)
        self._refresh_button.setText("Actualisation…")
        if not self._background:
            self._finish_refresh(fetch_prices(requests, self._price_fetcher))
            return
        task = _PriceTask(requests, self._price_fetcher)
        task.signals.done.connect(self._finish_refresh)
        self._tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _finish_refresh(self, results: object) -> None:
        assert isinstance(results, dict)
        stamp = self._now()
        for asset_id, price in results.items():
            if price is None:
                self._failed.add(asset_id)
            else:
                self._failed.discard(asset_id)
                self._repository.record_price(asset_id, price, self._today(), stamp)
        self._tasks.clear()
        self._refreshing = False
        self._refresh_button.setEnabled(True)
        self._refresh_button.setText("Actualiser les cours")
        self.refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._apply_direction()

    def _apply_direction(self) -> None:
        """Fiche à droite du tableau si la fenêtre est assez large, sinon dessous."""
        wide = self.width() >= WIDE_LAYOUT
        direction = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self._body.direction() == direction:
            return
        self._body.setDirection(direction)
        if wide:
            self._detail.setFixedWidth(DETAIL_WIDTH)
        else:
            self._detail.setMinimumWidth(280)
            self._detail.setMaximumWidth(16777215)
        self._body.setStretchFactor(self._table, 1)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (API Qt)
        super().showEvent(event)
        if self._background and not self._started:
            self._started = True
            self._timer.start()
            QTimer.singleShot(0, self.refresh_prices)


def _rich(
    html: str, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft, *, mono: bool = False
) -> QLabel:
    label = QLabel(html)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    label.setContentsMargins(12, 0, 12, 0)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    if mono:
        label.setFont(theme.mono_font(13))
    return label
