"""Design system sombre : tokens et feuille de style Qt (QSS).

Source de vérité visuelle : le canvas « Assets Tracker Design System ».
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

# --- Tokens -----------------------------------------------------------------
BG = "#0B0D12"
SURFACE = "#12151C"
RAISED = "#181C26"
OVERLAY = "#202533"
BORDER = "#232834"
BORDER_STRONG = "#2E3446"

TEXT = "#E8EAF0"
TEXT_MUTED = "#A3ACBF"
TEXT_SUBTLE = "#8590A6"

ACCENT = "#7C8CFF"
GAIN = "#3DDC97"
LOSS = "#FF7A8A"
WARNING = "#F5B84B"

RADIUS_FIELD = 8
RADIUS_BUTTON = 10
RADIUS_CARD = 14

MONO_FONT = "JetBrains Mono, Menlo, Consolas, monospace"


def variation_color(value: object) -> str:
    """Couleur sémantique d'une variation (Decimal, int ou float)."""
    if value > 0:  # type: ignore[operator]
        return GAIN
    if value < 0:  # type: ignore[operator]
        return LOSS
    return TEXT_MUTED


STYLESHEET = f"""
* {{ color: {TEXT}; font-size: 14px; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QLabel[role="muted"] {{ color: {TEXT_MUTED}; }}
QLabel[role="caption"] {{ color: {TEXT_MUTED}; font-size: 12px; font-weight: 600; }}
QLabel[role="metric"] {{ font-family: {MONO_FONT}; font-size: 30px; }}
QFrame[role="card"] {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px;
}}
QPushButton {{
    min-height: 36px; padding: 0 18px; border-radius: {RADIUS_BUTTON}px;
    background: {RAISED}; border: 1px solid {BORDER_STRONG};
}}
QPushButton:hover {{ background: {OVERLAY}; }}
QPushButton:disabled {{ color: {TEXT_SUBTLE}; }}
QPushButton[variant="primary"] {{
    background: {ACCENT}; border: none; color: {BG}; font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background: #95A2FF; }}
QPushButton[variant="danger"] {{
    background: #2A1519; border: 1px solid #5A2A33; color: {LOSS};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{
    min-height: 36px; padding: 0 12px; background: {BG};
    border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_FIELD}px;
    selection-background-color: {ACCENT}; selection-color: {BG};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QDateEdit:focus {{ border: 2px solid {ACCENT}; }}
QTableView, QTableWidget {{
    background: {SURFACE}; alternate-background-color: #151922;
    border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px;
    gridline-color: transparent; selection-background-color: {OVERLAY};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background: {RAISED}; color: {TEXT_MUTED}; border: none;
    padding: 12px 16px; font-size: 12px; font-weight: 600;
}}
QTabBar::tab {{ padding: 10px 16px; color: {TEXT_MUTED}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QToolTip {{ background: {OVERLAY}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 6px; }}
"""


def apply_theme(app: QApplication) -> None:
    """Applique le thème sombre à toute l'application."""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
