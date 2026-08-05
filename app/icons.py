"""Painter-drawn icons.

No SVG/PNG assets: every icon here is stroked directly with QPainter, for
the same reason the controls in app/widgets.py paint themselves -- it
guarantees identical rendering on every machine, it recolors freely from
ColorTokens (a bundled PNG would need one file per color state), and it
stays crisp at any DPI without shipping @2x variants.

Each function strokes into an arbitrary rect, so callers size the icon by
the rect they pass. Coordinates are authored on a 24x24 grid and mapped
onto that rect by _pt(), which keeps every icon visually consistent with
the others regardless of the size it's drawn at.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen


def _pt(rect: QRectF, x: float, y: float) -> QPointF:
    """Map a point on the 24x24 authoring grid into ``rect``."""
    return QPointF(
        rect.left() + rect.width() * x / 24.0,
        rect.top() + rect.height() * y / 24.0,
    )


def _pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def draw_arrow_right(p: QPainter, rect: QRectF, color: QColor, width: float = 1.9) -> None:
    """Fetch. A shaft plus a chevron head."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(_pen(color, width))
    p.setBrush(Qt.NoBrush)
    p.drawLine(_pt(rect, 5, 12), _pt(rect, 17.5, 12))
    path = QPainterPath(_pt(rect, 12.5, 6.5))
    path.lineTo(_pt(rect, 18, 12))
    path.lineTo(_pt(rect, 12.5, 17.5))
    p.drawPath(path)
    p.restore()


def draw_clock(p: QPainter, rect: QRectF, color: QColor, width: float = 1.7) -> None:
    """History."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(_pen(color, width))
    p.setBrush(Qt.NoBrush)
    r = QRectF(_pt(rect, 3.2, 3.2), _pt(rect, 20.8, 20.8))
    p.drawEllipse(r)
    path = QPainterPath(_pt(rect, 12, 7))
    path.lineTo(_pt(rect, 12, 12.4))
    path.lineTo(_pt(rect, 15.6, 14.6))
    p.drawPath(path)
    p.restore()


def draw_folder(p: QPainter, rect: QRectF, color: QColor, width: float = 1.7) -> None:
    """Save-to destination."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(_pen(color, width))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(_pt(rect, 3, 7.5))
    path.lineTo(_pt(rect, 3, 18.5))
    path.lineTo(_pt(rect, 21, 18.5))
    path.lineTo(_pt(rect, 21, 9.5))
    path.lineTo(_pt(rect, 11.5, 9.5))
    path.lineTo(_pt(rect, 9.5, 7.5))
    path.closeSubpath()
    p.drawPath(path)
    p.restore()


def draw_chevron_down(p: QPainter, rect: QRectF, color: QColor, width: float = 1.8) -> None:
    """Dropdown affordance. Callers rotate the painter to flip it open."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(_pen(color, width))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(_pt(rect, 6, 9.75))
    path.lineTo(_pt(rect, 12, 15.25))
    path.lineTo(_pt(rect, 18, 9.75))
    p.drawPath(path)
    p.restore()


def draw_spinner(p: QPainter, rect: QRectF, color: QColor, angle: float, width: float = 2.2) -> None:
    """Busy indicator: a 270-degree arc swept to ``angle`` degrees. Used by
    IconButton while a fetch is in flight, in place of its normal glyph."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = _pen(color, width)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    arc = QRectF(_pt(rect, 4.5, 4.5), _pt(rect, 19.5, 19.5))
    # Qt angles are in 1/16th of a degree, counter-clockwise from 3 o'clock.
    p.drawArc(arc, int(-angle * 16), int(-270 * 16))
    p.restore()
