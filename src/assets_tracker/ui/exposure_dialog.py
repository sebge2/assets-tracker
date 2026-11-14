"""Édition des secteurs et zones géographiques d'un actif (action, obligation ou fonds)."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.domain.exposure import (
    HUNDRED,
    KIND_LABELS,
    OTHER,
    ZERO,
    ExposureEntry,
    ExposureKind,
    entries_total,
    suggestions,
    validate_entries,
)
from assets_tracker.ui import theme
from assets_tracker.ui.formatting import NBSP, format_number, parse_decimal

STEP = Decimal(5)
_TITLES = {ExposureKind.SECTOR: "Secteurs", ExposureKind.ZONE: "Zones géographiques"}
_ADD_LABELS = {ExposureKind.SECTOR: "Ajouter un secteur", ExposureKind.ZONE: "Ajouter une zone"}


def weight_text(weight: Decimal) -> str:
    """`35`, `12,5` : sans zéros inutiles ; vide pour zéro."""
    if weight == ZERO:
        return ""
    text = format_number(weight, 2)
    return text.rstrip("0").rstrip(",") if "," in text else text


class ExposureEditor(QWidget):
    """Liste de lignes « nom + pourcentage » d'un seul type, avec son total."""

    changed = Signal()

    def __init__(self, kind: ExposureKind, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self._rows: list[tuple[QLineEdit, QLineEdit, QWidget]] = []
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(8)

        self._chips = QHBoxLayout()
        self._chips.setSpacing(6)
        for name in suggestions(kind)[:5]:
            chip = QPushButton(f"+ {name}")
            chip.clicked.connect(lambda _=False, n=name: self._add_suggestion(n))
            self._chips.addWidget(chip)
        self._chips.addStretch(1)

        self._add = QPushButton(_ADD_LABELS[kind])
        self._add.clicked.connect(lambda _=False: self.add_row("", ZERO))
        self._complete = QPushButton(f"Compléter avec « {OTHER} »")
        self._complete.clicked.connect(lambda _=False: self._complete_with_other())
        self._total = QLabel()
        actions = QHBoxLayout()
        actions.addWidget(self._add)
        actions.addWidget(self._complete)
        actions.addStretch(1)
        actions.addWidget(self._total)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)
        layout.addLayout(self._rows_layout)
        layout.addLayout(self._chips)
        layout.addLayout(actions)
        layout.addStretch(1)
        self._refresh_total()

    # -- lignes --

    def set_entries(self, entries: list[ExposureEntry]) -> None:
        for _, _, frame in self._rows:
            self._rows_layout.removeWidget(frame)
            frame.deleteLater()
        self._rows.clear()
        for entry in entries:
            self.add_row(entry.name, entry.weight)
        self._refresh_total()

    def add_row(self, name: str, weight: Decimal) -> None:
        frame = QWidget()
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText(f"Nom du {KIND_LABELS[self.kind]}")
        name_edit.setAccessibleName(f"Nom du {KIND_LABELS[self.kind]}")
        weight_edit = QLineEdit(weight_text(weight))
        weight_edit.setFixedWidth(80)
        weight_edit.setAccessibleName("Part en pourcentage")
        minus, plus, remove = QPushButton("−"), QPushButton("+"), QPushButton("✕")
        minus.setAccessibleName("Retirer 5 %")
        plus.setAccessibleName("Ajouter 5 %")
        remove.setAccessibleName("Supprimer la ligne")
        for button in (minus, plus):
            button.setFixedWidth(34)
        minus.clicked.connect(lambda _=False: self._bump(weight_edit, -STEP))
        plus.clicked.connect(lambda _=False: self._bump(weight_edit, STEP))
        entry = (name_edit, weight_edit, frame)
        remove.clicked.connect(lambda _=False: self._remove(entry))
        name_edit.textChanged.connect(lambda _="": self._refresh_total())
        weight_edit.textChanged.connect(lambda _="": self._refresh_total())
        line = QHBoxLayout(frame)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(name_edit, 1)
        line.addWidget(minus)
        line.addWidget(weight_edit)
        line.addWidget(QLabel("%"))
        line.addWidget(plus)
        line.addWidget(remove)
        self._rows.append(entry)
        self._rows_layout.addWidget(frame)
        self._refresh_total()

    def _remove(self, entry: tuple[QLineEdit, QLineEdit, QWidget]) -> None:
        if entry not in self._rows:
            return
        self._rows.remove(entry)
        self._rows_layout.removeWidget(entry[2])
        entry[2].deleteLater()
        self._refresh_total()

    def _bump(self, edit: QLineEdit, delta: Decimal) -> None:
        try:
            current = parse_decimal(edit.text(), "Part") if edit.text().strip() else ZERO
        except ValueError:
            current = ZERO
        value = min(max(current + delta, ZERO), HUNDRED)
        edit.setText(weight_text(value))

    def _add_suggestion(self, name: str) -> None:
        if any(n.text().strip().casefold() == name.casefold() for n, _, _ in self._rows):
            return
        self.add_row(name, ZERO)

    def _complete_with_other(self) -> None:
        try:
            rest = HUNDRED - entries_total(self.entries())
        except ValueError:
            return
        if rest <= ZERO:
            return
        for name_edit, weight_edit, _ in self._rows:
            if name_edit.text().strip().casefold() == OTHER.casefold():
                try:
                    current = parse_decimal(weight_edit.text(), "Part")
                except ValueError:
                    current = ZERO
                weight_edit.setText(weight_text(current + rest))
                return
        self.add_row(OTHER, rest)

    # -- lecture --

    def entries(self) -> list[ExposureEntry]:
        """Lignes saisies (les lignes vides sont ignorées) ; `ValueError` si invalide."""
        result: list[ExposureEntry] = []
        for name_edit, weight_edit, _ in self._rows:
            name, text = name_edit.text().strip(), weight_edit.text().strip()
            if not name and not text:
                continue
            weight = parse_decimal(text, f"Part de « {name or '?'} »") if text else ZERO
            result.append(ExposureEntry(name, weight))
        return result

    def problem(self) -> str | None:
        try:
            validate_entries(self.kind, self.entries())
        except ValueError as error:
            return str(error)
        return None

    def _refresh_total(self) -> None:
        try:
            entries = self.entries()
        except ValueError:
            self._total.setText("Part invalide")
            self._total.setStyleSheet(f"color: {theme.LOSS};")
            self.changed.emit()
            return
        total = entries_total(entries)
        pct = f"{NBSP}%"
        if not entries:
            text, color = "Non renseigné", theme.TEXT_SUBTLE
        elif total == HUNDRED:
            text, color = f"Total 100{pct}", theme.GAIN
        elif total < HUNDRED:
            text = f"Reste à répartir : {format_number(HUNDRED - total, 2)}{pct}"
            color = theme.WARNING
        else:
            text = f"Excédent à retirer : {format_number(total - HUNDRED, 2)}{pct}"
            color = theme.LOSS
        self._total.setText(text)
        self._total.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.changed.emit()


class ExposureDialog(QDialog):
    """Deux onglets (secteurs, zones). Enregistrer reste grisé tant qu'un total n'est pas 100 %."""

    def __init__(
        self,
        parent: QWidget | None,
        asset_name: str,
        sectors: list[ExposureEntry],
        zones: list[ExposureEntry],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Secteurs et zones")
        self.setMinimumWidth(580)
        self._sectors = ExposureEditor(ExposureKind.SECTOR)
        self._zones = ExposureEditor(ExposureKind.ZONE)
        self._sectors.set_entries(sectors)
        self._zones.set_entries(zones)
        tabs = QTabWidget()
        tabs.addTab(self._sectors, _TITLES[ExposureKind.SECTOR])
        tabs.addTab(self._zones, _TITLES[ExposureKind.ZONE])

        title = QLabel(f"Secteurs et zones · {asset_name}")
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        hint = QLabel(
            "Chaque répartition doit totaliser 100 %, ou rester vide si elle est inconnue. "
            "Pour un fonds ou une action diversifiée, répartissez selon la composition publiée."
        )
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        self._error = QLabel()
        self._error.setProperty("role", "error")
        self._error.setWordWrap(True)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        self._save = self._buttons.button(QDialogButtonBox.StandardButton.Save)
        self._save.setText("Enregistrer")
        self._save.setProperty("variant", "primary")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(tabs)
        layout.addWidget(self._error)
        layout.addWidget(self._buttons)
        self._sectors.changed.connect(self._update_state)
        self._zones.changed.connect(self._update_state)
        self._update_state()

    def _update_state(self) -> None:
        problem = self._sectors.problem() or self._zones.problem()
        self._save.setEnabled(problem is None)
        self._error.setText(problem or "")

    def sectors(self) -> list[ExposureEntry]:
        return validate_entries(ExposureKind.SECTOR, self._sectors.entries())

    def zones(self) -> list[ExposureEntry]:
        return validate_entries(ExposureKind.ZONE, self._zones.entries())

    def accept(self) -> None:
        try:
            self.sectors()
            self.zones()
        except ValueError as error:
            self._error.setText(str(error))
            return
        super().accept()
