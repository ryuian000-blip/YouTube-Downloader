"""Pixmap helpers shared between the main download view and the history
view, both of which render YouTube thumbnails the same way."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap


def fit_pixmap(pixmap: QPixmap, size: QSize) -> QPixmap:
    """Scale to fill ``size`` completely (may overshoot one dimension)
    then center-crop the overshoot away, instead of letterboxing -- a
    thumbnail crop reads as normal; a thumbnail with bars around it reads
    as broken."""
    scaled = pixmap.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - size.width()) // 2)
    y = max(0, (scaled.height() - size.height()) // 2)
    return scaled.copy(x, y, size.width(), size.height())


def rounded_pixmap(
    pixmap: QPixmap, radius: int, top: bool = True, bottom: bool = True
) -> QPixmap:
    """Clip to rounded corners so a thumbnail matches the rest of the
    app's rounded-corner language instead of sitting in a hard-edged box.

    ``top``/``bottom`` select which pair of corners actually gets rounded.
    The poster layout needs top-only: the thumbnail is the upper half of a
    single rounded media card whose lower half is the title block, so
    rounding the thumbnail's bottom corners would cut a notch into the
    middle of that card. Qt does not clip a child widget to its parent's
    border-radius, so this has to happen on the pixmap itself.
    """
    result = QPixmap(pixmap.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)

    rect = QRectF(pixmap.rect())
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    # Square off the unwanted end by unioning a plain rect over it.
    if not top:
        square = QPainterPath()
        square.addRect(QRectF(rect.left(), rect.top(), rect.width(), rect.height() / 2))
        path = path.united(square)
    if not bottom:
        square = QPainterPath()
        square.addRect(
            QRectF(rect.left(), rect.center().y(), rect.width(), rect.height() / 2)
        )
        path = path.united(square)

    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result
