"""Choix des actifs repris dans la répartition par secteur et par zone du Portefeuille."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from assets_tracker.domain.exposure import AssetKey, ExposedAsset, asset_key
from assets_tracker.ui.formatting import ASSET_LABELS, compact_money

_KEY_ROLE = Qt.ItemDataRole.UserRole


class ExposureAssetsDialog(QDialog):
    """Une case par actif : décochée, l'actif est exclu de la répartition."""

    def __init__(
        self,
        parent: QWidget | None,
        assets: Sequence[ExposedAsset],
        excluded: Collection[AssetKey],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Actifs de la répartition")
        self.setMinimumWidth(480)
        title = QLabel("Actifs repris dans la répartition")
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 20px;")
        hint = QLabel(
            "Décochez un actif pour l'exclure des secteurs et zones ; "
            "les pourcentages sont recalculés sur les actifs restants."
        )
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)

        self._list = QListWidget()
        self._list.setAccessibleName("Actifs de la répartition")
        ordered = sorted(assets, key=lambda a: (a.instrument.value, -a.value, a.name.casefold()))
        for asset in ordered:
            key = asset_key(asset)
            item = QListWidgetItem(
                f"{asset.name} · {ASSET_LABELS[asset.instrument]} · "
                f"{compact_money(asset.value, 'EUR')}"
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Unchecked if key in excluded else Qt.CheckState.Checked
            )
            item.setData(_KEY_ROLE, key)
            self._list.addItem(item)

        include_all = QPushButton("Tout inclure")
        include_all.clicked.connect(lambda _=False: self.set_all(True))
        exclude_all = QPushButton("Tout exclure")
        exclude_all.clicked.connect(lambda _=False: self.set_all(False))
        bulk = QHBoxLayout()
        bulk.setSpacing(8)
        bulk.addWidget(include_all)
        bulk.addWidget(exclude_all)
        bulk.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Appliquer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(bulk)
        layout.addWidget(self._list, 1)
        layout.addWidget(buttons)

    def set_all(self, included: bool) -> None:
        state = Qt.CheckState.Checked if included else Qt.CheckState.Unchecked
        for index in range(self._list.count()):
            self._list.item(index).setCheckState(state)

    def excluded(self) -> set[AssetKey]:
        """Clés des actifs décochés."""
        found: set[AssetKey] = set()
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.checkState() is Qt.CheckState.Unchecked:
                found.add(item.data(_KEY_ROLE))
        return found
