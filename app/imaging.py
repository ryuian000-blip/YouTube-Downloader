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


def rounded_pixmap(pixmap: QPixmap, radius: int) -> QPixmap:
    """Clip to rounded corners so a thumbnail matches the rest of the
    app's rounded-corner language instead of sitting in a hard-edged box."""
    result = QPixmap(pixmap.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(pixmap.rect()), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result
