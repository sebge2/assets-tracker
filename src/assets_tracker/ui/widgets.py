"""Briques d'interface partagées entre les écrans (cartes, tableaux, cellules)."""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.ui import theme

RIGHT = Qt.AlignmentFlag.AlignRight


def yahoo_quote_url(symbol: str) -> str:
    """Adresse de la page Yahoo Finance d'un instrument."""
    return f"https://finance.yahoo.com/quote/{quote(symbol.strip(), safe='')}"


def source_badge_html(text: str, symbol: str | None, color: str) -> str:
    """Libellé de source ; lien vers la page Yahoo de l'instrument quand `symbol` est connu."""
    if not symbol or not symbol.strip():
        return escape(text)
    return (
        f'<a href="{escape(yahoo_quote_url(symbol), quote=True)}" style="text-decoration: none;">'
        f'<span style="color: {color}; text-decoration: underline;">{escape(text)}</span></a>'
    )


class PageContent(QWidget):
    """Contenu d'une page défilante : s'adapte à la largeur, ne défile que verticalement.

    Sans cela, la largeur minimale cumulée des widgets enfants dépasse vite la fenêtre
    et la zone de défilement ajoute une barre horizontale.
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (API Qt)
        return QSize(0, super().minimumSizeHint().height())


def table_cell(
    text: str,
    *,
    color: str = theme.TEXT,
    mono: bool = False,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setForeground(QColor(color))
    item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if mono:
        item.setFont(theme.mono_font(13))
    return item


def prepare_table(table: QTableWidget, headers: list[str], *, row_height: int) -> None:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )


def fit_height(table: QTableWidget, max_height: int | None = None) -> None:
    """Donne au tableau la hauteur de ses lignes.

    Avec `max_height`, le tableau est plafonné et défile en interne au-delà.
    """
    header = table.horizontalHeader()
    height = max(header.height(), header.sizeHint().height()) + sum(
        table.rowHeight(row) for row in range(table.rowCount())
    )
    height = max(height + 4, 120)
    table.setFixedHeight(height if max_height is None else min(height, max_height))


class Card(QFrame):
    """Indicateur : légende, valeur en police mono, sous-titre."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setProperty("role", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(6)
        self._caption = QLabel(caption.upper())
        self._caption.setProperty("role", "caption")
        self._value = QLabel()
        self._value.setFont(theme.mono_font(22))
        self._sub = QLabel()
        self._sub.setProperty("role", "muted")
        self._sub.setWordWrap(True)
        for widget in (self._caption, self._value, self._sub):
            layout.addWidget(widget)
        layout.addStretch(1)  # le contenu reste en haut quand les cartes d'une rangée diffèrent

    def set(self, value: str, sub: str, color: str = theme.TEXT) -> None:
        self._value.setText(value)
        self._value.setStyleSheet(f"color: {color};")
        self._sub.setText(sub)
