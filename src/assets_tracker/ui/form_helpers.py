"""Briques de formulaire partagées par les dialogues (champ étiqueté, ligne, date)."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDateEdit, QHBoxLayout, QLabel, QVBoxLayout, QWidget


def labeled(text: str, widget: QWidget, hint: str | None = None) -> QWidget:
    """Un champ avec son libellé au-dessus et, si besoin, une indication en dessous."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    label = QLabel(text)
    label.setProperty("role", "muted")
    layout.addWidget(label)
    layout.addWidget(widget)
    if hint:
        note = QLabel(hint)
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        layout.addWidget(note)
    return box


def row(*widgets: QWidget) -> QWidget:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    for widget in widgets:
        layout.addWidget(widget, 1, Qt.AlignmentFlag.AlignTop)
    return box


def section(title: str) -> QLabel:
    label = QLabel(title.upper())
    label.setProperty("role", "caption")
    return label


def date_edit(value: date) -> QDateEdit:
    edit = QDateEdit(QDate(value.year, value.month, value.day))
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    return edit


def to_date(edit: QDateEdit) -> date:
    qdate = edit.date()
    return date(qdate.year(), qdate.month(), qdate.day())
