"""Icônes du design system (trait de 1,75 px sur grille 24 px), rendues depuis du SVG."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from assets_tracker.ui import theme

_PATHS = {
    "dashboard": (
        '<rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/>'
        '<rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>'
    ),
    "cash": '<rect x="2" y="6" width="20" height="13" rx="2"/><path d="M2 10h20M6 15h4"/>',
    "fund": '<path d="M4 19V9m6 10V5m6 14v-7M3 21h18"/>',
    "bond": '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    "term_note": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h5"/>'
    '<circle cx="15.5" cy="16.5" r="2.5"/><path d="M15.5 15.5v1.2l.8.5"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3'
    'M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
    "stock": '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
    "delete": '<path d="M4 7h16M10 11v5M14 11v5M9 7l1-3h4l1 3M6 7l1 13h10l1-13"/>',
    "back": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "add": '<path d="M12 5v14M5 12h14"/>',
    "commodities": '<path d="M3 18l3-8h12l3 8zM9 10l1-4h4l1 4"/>',
    "simulation": (
        '<path d="M3 6h11M18 6h3M3 12h5M12 12h9M3 18h13M20 18h1"/>'
        '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/>'
        '<circle cx="18" cy="18" r="2"/>'
    ),
}

_SCALE = 2  # rendu en 2x pour les écrans Retina


def _render(svg: str, size: int) -> QPixmap:
    pixmap = QPixmap(size * _SCALE, size * _SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return pixmap


def _stroke_svg(name: str, color: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.75" stroke-linecap="round" '
        f'stroke-linejoin="round">{_PATHS[name]}</svg>'
    )


def icon(
    name: str, color: str = theme.TEXT_MUTED, active_color: str | None = None, size: int = 20
) -> QIcon:
    """Icône au trait ; `active_color` sert à l'état coché (ex. entrée de menu active)."""
    result = QIcon()
    result.addPixmap(_render(_stroke_svg(name, color), size), QIcon.Mode.Normal, QIcon.State.Off)
    if active_color is not None:
        result.addPixmap(
            _render(_stroke_svg(name, active_color), size), QIcon.Mode.Normal, QIcon.State.On
        )
    return result


def logo_pixmap(size: int = 32) -> QPixmap:
    """Monogramme A : barre en flèche verte montante, sur fond sombre."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
        f'<rect x="1" y="1" width="126" height="126" rx="29" fill="{theme.RAISED}" '
        f'stroke="{theme.BORDER_STRONG}" stroke-width="2"/>'
        f'<path d="M32 100L64 26l32 74" fill="none" stroke="{theme.TEXT}" stroke-width="10" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="M42 86l18-16 10 8 26-26M86 52h12v12" fill="none" stroke="{theme.GAIN}" '
        'stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    return _render(svg, size)
