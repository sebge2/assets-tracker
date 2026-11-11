"""Design system sombre : tokens et feuille de style Qt (QSS).

Source de vérité visuelle : le canvas « Assets Tracker Design System ».
"""

from __future__ import annotations

from PySide6.QtGui import QFont
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

ALT_ROW = "#151922"
HOVER_ROW = "#1B2030"
ACCENT_SOFT = "#2A3050"
ACCENT_HOVER = "#95A2FF"
LOSS_SOFT = "#2A1519"
LOSS_BORDER = "#5A2A33"
GAIN_SOFT = "#10261D"
WARNING_SOFT = "#2B2210"

RADIUS_FIELD = 8
RADIUS_BUTTON = 10
RADIUS_CARD = 14

MONO_FONT = "JetBrains Mono, Menlo, Consolas, monospace"


def mono_font(pixel_size: int = 14, *, bold: bool = False) -> QFont:
    """Police à chiffres tabulaires pour tous les montants (taille en pixels, comme le QSS)."""
    font = QFont()
    font.setFamilies([name.strip() for name in MONO_FONT.split(",")])
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    return font


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
QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[variant="danger"] {{
    background: {LOSS_SOFT}; border: 1px solid {LOSS_BORDER}; color: {LOSS};
}}
QPushButton[variant="chip"] {{
    min-height: 32px; padding: 0 16px; border-radius: 17px; background: {SURFACE};
    color: {TEXT_MUTED};
}}
QPushButton[variant="chip"]:checked {{
    background: {ACCENT_SOFT}; border: 1px solid {ACCENT}; color: {TEXT};
}}
QPushButton[nav="true"] {{
    min-height: 44px; padding: 0 12px; border: none; border-radius: {RADIUS_BUTTON}px;
    background: transparent; color: {TEXT_MUTED}; text-align: left;
}}
QPushButton[nav="true"]:hover {{ background: {RAISED}; }}
QPushButton[nav="true"]:checked {{ background: {OVERLAY}; color: {TEXT}; font-weight: 500; }}
QToolButton[role="icon"] {{
    min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px;
    border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_BUTTON}px; background: {RAISED};
}}
QToolButton[role="icon"]:hover {{ background: {OVERLAY}; }}
QFrame#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel[role="title"] {{ font-size: 32px; font-weight: 700; }}
QLabel[role="subtitle"] {{ color: {TEXT_SUBTLE}; font-size: 13px; }}
QLabel[role="error"] {{ color: {LOSS}; }}
QLabel[role="hint"] {{ color: {TEXT_SUBTLE}; font-size: 12px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border: 1px solid {BORDER_STRONG}; border-radius: 5px;
    background: {BG};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QDialog {{ background: {OVERLAY}; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QScrollArea {{ border: none; }}
QTableWidget {{ outline: 0; }}
QTableWidget::item {{ padding: 0 12px; }}
QTableWidget::item:hover {{ background: {HOVER_ROW}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{
    min-height: 36px; padding: 0 12px; background: {BG};
    border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS_FIELD}px;
    selection-background-color: {ACCENT}; selection-color: {BG};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QDateEdit:focus {{ border: 2px solid {ACCENT}; }}
QTableView, QTableWidget {{
    background: {SURFACE}; alternate-background-color: {ALT_ROW};
    border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px;
    gridline-color: transparent; selection-background-color: {OVERLAY};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background: {RAISED}; color: {TEXT_MUTED}; border: none;
    padding: 12px; font-size: 12px; font-weight: 600;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    padding: 10px 16px; color: {TEXT_MUTED}; border: none; border-bottom: 2px solid transparent;
    background: transparent;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QToolTip {{
    background: {OVERLAY}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 6px;
}}
"""


def apply_theme(app: QApplication) -> None:
    """Applique le thème sombre à toute l'application."""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
