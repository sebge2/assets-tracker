"""Champ de filtre placé au-dessus du tableau d'un écran d'instruments."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QLineEdit, QTableWidget, QVBoxLayout, QWidget


class SearchBox(QLineEdit):
    """Filtre par nom ou ISIN, avec bouton d'effacement."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Filtrer par nom ou ISIN")
        self.setAccessibleName("Filtrer la liste par nom ou ISIN")
        self.setClearButtonEnabled(True)


def table_with_search(table: QTableWidget, search: SearchBox, no_match: QLabel) -> QWidget:
    """Regroupe le filtre, le tableau et le message « aucun résultat » dans une colonne."""
    no_match.setText("Aucun résultat pour ce filtre.")
    no_match.setProperty("role", "muted")
    no_match.setVisible(False)
    column = QWidget()
    layout = QVBoxLayout(column)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    layout.addWidget(search)
    layout.addWidget(table)
    layout.addWidget(no_match)
    return column
