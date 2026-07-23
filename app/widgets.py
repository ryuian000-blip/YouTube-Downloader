"""Custom-painted, animated form controls: buttons, radio buttons, and
checkboxes.

Why these exist instead of QPushButton/QRadioButton/QCheckBox + QSS: QSS
can restyle a lot of a native control, but not reliably all of it. On
Windows' native ("vista"/"11") QStyle, the checked-state override for
QRadioButton::indicator (meant to render as a ring -- a colored border
with a hollow center) instead painted as a solid filled square, and the
control's own sizeHint() -- which QSS cannot touch at all -- was too
narrow for its label, truncating text ("Video only (no sound)" -> "no
sounc"). This is the same failure mode already seen twice elsewhere in
this app (see app/splash.py and app/theme.py's docstrings): native-style
QSS overrides of a sub-control's *shape*, as opposed to simple colors,
aren't guaranteed to be honored.

The fix that actually guarantees identical, correct rendering on every
machine is to stop asking QStyle to draw these controls at all: paint
them ourselves in paintEvent(), and drive every state transition (hover,
press, check) with QPropertyAnimation, so each control gets a real,
smooth, physically-eased animation instead of an instant QSS state swap.

Pattern follows the standard PySide6 approach for animated custom
widgets: a QObject-owned Qt ``Property`` as the animatable value, a
QPropertyAnimation that interpolates it, and the property's setter
calling ``update()`` so every interpolated frame actually repaints --
the same technique as pythonguis.com/tutorials/pyside6-animated-widgets'
"AnimatedToggle" reference example.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
)

from app.theme import ColorTokens

HOVER_MS = 140
PRESS_MS = 90
CHECK_MS = 180


def _mix(c1: QColor, c2: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(c1.red() + (c2.red() - c1.red()) * t),
        round(c1.green() + (c2.green() - c1.green()) * t),
        round(c1.blue() + (c2.blue() - c1.blue()) * t),
    )


class AnimatedButton(QPushButton):
    """Drop-in QPushButton replacement with an animated hover/press color
    transition and a subtle press "settle" scale, instead of QSS's
    instant color swap between :hover/:pressed states."""

    def __init__(self, text: str = "", primary: bool = False, parent=None) -> None:
        super().__init__(text, parent)
        self._primary = primary
        self._radius = 8
        self._colors: ColorTokens | None = None
        self._hover_t = 0.0  # 0 = idle, 1 = hovered
        self._press_t = 0.0  # 0 = up, 1 = pressed
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._hover_anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_anim.setDuration(HOVER_MS)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._press_anim = QPropertyAnimation(self, b"pressProgress", self)
        self._press_anim.setDuration(PRESS_MS)
        self._press_anim.setEasingCurve(QEasingCurve.OutCubic)

    def setBorderRadius(self, radius: int) -> None:
        self._radius = radius
        self.update()

    def apply_theme(self, c: ColorTokens) -> None:
        self._colors = c
        self.update()

    # -- animatable properties -------------------------------------------

    def _get_hover(self) -> float:
        return self._hover_t

    def _set_hover(self, value: float) -> None:
        self._hover_t = value
        self.update()

    hoverProgress = Property(float, _get_hover, _set_hover)

    def _get_press(self) -> float:
        return self._press_t

    def _set_press(self, value: float) -> None:
        self._press_t = value
        self.update()

    pressProgress = Property(float, _get_press, _set_press)

    # -- state transitions -------------------------------------------------

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt override)
        super().setEnabled(enabled)
        if not enabled:
            self._hover_anim.stop()
            self._press_anim.stop()
            self._hover_t = 0.0
            self._press_t = 0.0
            self.update()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self.isEnabled():
            self._hover_anim.stop()
            self._hover_anim.setStartValue(self._hover_t)
            self._hover_anim.setEndValue(1.0)
            self._hover_anim.start()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if self.isEnabled() and event.button() == Qt.LeftButton:
            self._press_anim.stop()
            self._press_anim.setStartValue(self._press_t)
            self._press_anim.setEndValue(1.0)
            self._press_anim.start()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._press_anim.stop()
        self._press_anim.setStartValue(self._press_t)
        self._press_anim.setEndValue(0.0)
        self._press_anim.start()

    # -- painting -----------------------------------------------------------

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text())
        pad_x = 32
        h = fm.height() + (24 if self._primary else 18)
        w = text_w + pad_x * 2
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        # Text is drawn with plain drawText(rect, ...), which *clips*
        # rather than elides when the rect is too narrow -- so unlike a
        # native QPushButton, if a layout ever compressed this below its
        # sizeHint, the label would silently lose its right edge instead
        # of just looking a little cramped. Pin the minimum to the
        # preferred size so a layout with too little room has to make
        # that visible (e.g. by growing the window) rather than quietly
        # clipping text.
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        c = self._colors
        if c is None:
            super().paintEvent(event)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self._radius if self._radius else rect.height() / 2

        if not self.isEnabled():
            bg = QColor(c.DISABLED_BG)
            border: QColor | None = QColor(c.DISABLED_BORDER)
            text_color = QColor(c.DISABLED_TEXT)
        elif self._primary:
            base = QColor(c.ACCENT)
            hover = QColor(c.ACCENT_HOVER)
            pressed = QColor(c.ACCENT_PRESSED)
            bg = _mix(_mix(base, hover, self._hover_t), pressed, self._press_t)
            border = None
            text_color = QColor(c.ON_ACCENT)
        else:
            base = QColor(c.SURFACE_ALT)
            hover = QColor(c.BORDER)
            bg = _mix(base, hover, max(self._hover_t, self._press_t))
            border = QColor(c.ACCENT if self._hover_t > 0.3 else c.BORDER)
            text_color = QColor(c.TEXT_PRIMARY)

        # A very slight press "settle" so a click always reads as tactile,
        # even for the default (non-primary) button where hover/pressed
        # backgrounds are close in value.
        scale = 1.0 - 0.015 * self._press_t
        p.translate(rect.center())
        p.scale(scale, scale)
        p.translate(-rect.center())

        p.setPen(Qt.NoPen if border is None else QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, radius, radius)

        p.setPen(QPen(text_color))
        font = self.font()
        font.setBold(self._primary)
        p.setFont(font)
        p.drawText(rect, Qt.AlignCenter, self.text())
        p.end()


class AnimatedRadioButton(QRadioButton):
    """Drop-in QRadioButton replacement. Paints its own indicator (a
    circle, always -- guaranteed by drawEllipse(), unlike the native-style
    QSS override that rendered as a filled square) with the checked state
    animated as the inner dot growing/shrinking, and a correct sizeHint()
    that actually measures the label so long option text no longer clips."""

    INDICATOR = 18

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._colors: ColorTokens | None = None
        self._fill_t = 1.0 if self.isChecked() else 0.0
        self._hover_t = 0.0
        self.setCursor(Qt.PointingHandCursor)

        self._fill_anim = QPropertyAnimation(self, b"fillProgress", self)
        self._fill_anim.setDuration(CHECK_MS)
        self._fill_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._hover_anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_anim.setDuration(HOVER_MS)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.toggled.connect(self._on_toggled)

    def apply_theme(self, c: ColorTokens) -> None:
        self._colors = c
        self.update()

    def _get_fill(self) -> float:
        return self._fill_t

    def _set_fill(self, value: float) -> None:
        self._fill_t = value
        self.update()

    fillProgress = Property(float, _get_fill, _set_fill)

    def _get_hover(self) -> float:
        return self._hover_t

    def _set_hover(self, value: float) -> None:
        self._hover_t = value
        self.update()

    hoverProgress = Property(float, _get_hover, _set_hover)

    def _on_toggled(self, checked: bool) -> None:
        self._fill_anim.stop()
        self._fill_anim.setStartValue(self._fill_t)
        self._fill_anim.setEndValue(1.0 if checked else 0.0)
        self._fill_anim.start()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt override)
        # Mirrors AnimatedButton.setEnabled: Qt won't deliver a real
        # leaveEvent just because a widget got disabled out from under a
        # still-hovering cursor, so without this a radio disabled while
        # hovered would keep painting its hover ring indefinitely.
        super().setEnabled(enabled)
        if not enabled:
            self._hover_anim.stop()
            self._hover_t = 0.0
            self.update()

    def hitButton(self, pos) -> bool:  # noqa: N802 (Qt override)
        # Default QRadioButton only treats the *indicator sub-control's*
        # native-style rect as clickable; since we no longer use that
        # native geometry at all, the whole control (indicator + label)
        # should be clickable, matching how it always visually behaved.
        return self.contentsRect().contains(pos)

    # 1.5px pen centered on the ring's path needs 1px of clearance from
    # the widget's own left edge, or its outward half gets clipped (see
    # paintEvent) -- baked in here too so sizeHint/minimumSizeHint keep
    # matching what paintEvent actually draws.
    LEFT_INSET = 1

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text())
        spacing = 8
        w = self.LEFT_INSET + self.INDICATOR + spacing + text_w + 4
        h = max(self.INDICATOR, fm.height()) + 8
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        # See AnimatedButton.minimumSizeHint -- same reasoning: this is
        # exactly the control whose label got silently truncated by a
        # layout that compressed it below its needed width, so its
        # minimum must equal its preferred size.
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        c = self._colors
        if c is None:
            super().paintEvent(event)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = self.contentsRect()
        d = self.INDICATOR
        cy = rect.center().y()
        # The 1.5px ring pen is centered on the path, so half its width
        # extends outward from it. Flush against rect.left() (== 0, no
        # contents margin), that outward half falls at a negative
        # x-coordinate -- off the widget entirely -- and gets clipped,
        # leaving the ring visibly cut open on the left instead of a
        # closed circle.
        indicator_rect = QRectF(rect.left() + self.LEFT_INSET, cy - d / 2, d, d)

        border_color = QColor(c.ACCENT if self._fill_t > 0.5 else c.BORDER)
        if self._hover_t > 0 and self._fill_t < 0.5:
            border_color = _mix(QColor(c.BORDER), QColor(c.ACCENT), self._hover_t)

        p.setPen(QPen(border_color, 1.5))
        p.setBrush(QColor(c.SURFACE if self.isEnabled() else c.SURFACE_ALT))
        p.drawEllipse(indicator_rect)

        if self._fill_t > 0:
            inner_d = d * 0.5 * self._fill_t
            inner_rect = QRectF(0, 0, inner_d, inner_d)
            inner_rect.moveCenter(indicator_rect.center())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c.ACCENT))
            p.drawEllipse(inner_rect)

        text_color = QColor(c.TEXT_PRIMARY if self.isEnabled() else c.DISABLED_TEXT)
        p.setPen(QPen(text_color))
        text_left = rect.left() + self.LEFT_INSET + d + 8
        text_rect = QRectF(text_left, rect.top(), rect.width() - text_left, rect.height())
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        p.end()


class AnimatedCheckBox(QCheckBox):
    """Drop-in QCheckBox replacement: a rounded-square indicator that
    fills with an animated color wash on check, plus a checkmark that
    draws itself in stroke-by-stroke rather than popping in instantly."""

    BOX = 18

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._colors: ColorTokens | None = None
        self._fill_t = 1.0 if self.isChecked() else 0.0
        self._hover_t = 0.0
        self.setCursor(Qt.PointingHandCursor)

        self._fill_anim = QPropertyAnimation(self, b"fillProgress", self)
        self._fill_anim.setDuration(CHECK_MS)
        self._fill_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._hover_anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_anim.setDuration(HOVER_MS)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.toggled.connect(self._on_toggled)

    def apply_theme(self, c: ColorTokens) -> None:
        self._colors = c
        self.update()

    def _get_fill(self) -> float:
        return self._fill_t

    def _set_fill(self, value: float) -> None:
        self._fill_t = value
        self.update()

    fillProgress = Property(float, _get_fill, _set_fill)

    def _get_hover(self) -> float:
        return self._hover_t

    def _set_hover(self, value: float) -> None:
        self._hover_t = value
        self.update()

    hoverProgress = Property(float, _get_hover, _set_hover)

    def _on_toggled(self, checked: bool) -> None:
        self._fill_anim.stop()
        self._fill_anim.setStartValue(self._fill_t)
        self._fill_anim.setEndValue(1.0 if checked else 0.0)
        self._fill_anim.start()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt override)
        # See AnimatedRadioButton.setEnabled for why this is needed.
        super().setEnabled(enabled)
        if not enabled:
            self._hover_anim.stop()
            self._hover_t = 0.0
            self.update()

    def hitButton(self, pos) -> bool:  # noqa: N802 (Qt override)
        return self.contentsRect().contains(pos)

    # Same reasoning as AnimatedRadioButton.LEFT_INSET: the box's own
    # border pen is centered on its path, so it needs clearance from the
    # widget's left edge or its outward half gets clipped.
    LEFT_INSET = 1

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text())
        spacing = 8
        w = self.LEFT_INSET + self.BOX + spacing + text_w + 4
        h = max(self.BOX, fm.height()) + 8
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        c = self._colors
        if c is None:
            super().paintEvent(event)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = self.contentsRect()
        d = self.BOX
        cy = rect.center().y()
        box = QRectF(rect.left() + self.LEFT_INSET, cy - d / 2, d, d)
        radius = 4

        border_color = QColor(c.ACCENT if self._fill_t > 0.5 else c.BORDER)
        if self._hover_t > 0 and self._fill_t < 0.5:
            border_color = _mix(QColor(c.BORDER), QColor(c.ACCENT), self._hover_t)

        bg = _mix(QColor(c.SURFACE if self.isEnabled() else c.SURFACE_ALT), QColor(c.ACCENT), self._fill_t)
        p.setPen(QPen(border_color, 1.5))
        p.setBrush(bg)
        p.drawRoundedRect(box, radius, radius)

        if self._fill_t > 0.15:
            # Checkmark drawn as two segments; how much of each has been
            # stroked so far is driven by the same fill progress that
            # colors the box in, so the mark visibly "writes itself" over
            # the animation instead of appearing all at once.
            t = min(1.0, (self._fill_t - 0.15) / 0.85)
            p1 = QPointF(box.left() + d * 0.26, box.top() + d * 0.55)
            p2 = QPointF(box.left() + d * 0.42, box.top() + d * 0.72)
            p3 = QPointF(box.left() + d * 0.76, box.top() + d * 0.30)

            pen = QPen(QColor(c.ON_ACCENT), 2.0)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)

            if t <= 0.5:
                seg_t = t / 0.5
                mid = QPointF(
                    p1.x() + (p2.x() - p1.x()) * seg_t,
                    p1.y() + (p2.y() - p1.y()) * seg_t,
                )
                p.drawLine(p1, mid)
            else:
                p.drawLine(p1, p2)
                seg_t = (t - 0.5) / 0.5
                mid = QPointF(
                    p2.x() + (p3.x() - p2.x()) * seg_t,
                    p2.y() + (p3.y() - p2.y()) * seg_t,
                )
                p.drawLine(p2, mid)

        text_color = QColor(c.TEXT_PRIMARY if self.isEnabled() else c.DISABLED_TEXT)
        p.setPen(QPen(text_color))
        text_left = rect.left() + self.LEFT_INSET + d + 8
        text_rect = QRectF(text_left, rect.top(), rect.width() - text_left, rect.height())
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        p.end()


class AnimatedProgressBar(QProgressBar):
    """Drop-in QProgressBar replacement: a fully rounded pill-shaped
    track and fill.

    QProgressBar::chunk has the same problem the radio/checkbox
    indicators did -- its border-radius QSS is a sub-control override
    that Windows' native style doesn't reliably honor, so instead of a
    smooth rounded bar it rendered as a flat, square block. Painting it
    ourselves guarantees the same rounded shape everywhere. Value changes
    also now animate (the fill eases to the new percentage over a couple
    hundred ms) instead of snapping instantly, so rapid progress updates
    during a download read as motion rather than a flicker."""

    HEIGHT = 10

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._colors: ColorTokens | None = None
        self._display_value = float(self.value())
        self.setTextVisible(False)
        self.setFixedHeight(self.HEIGHT)

        self._anim = QPropertyAnimation(self, b"displayValue", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self.valueChanged.connect(self._animate_to)

    def apply_theme(self, c: ColorTokens) -> None:
        self._colors = c
        self.update()

    def _get_display_value(self) -> float:
        return self._display_value

    def _set_display_value(self, value: float) -> None:
        self._display_value = value
        self.update()

    displayValue = Property(float, _get_display_value, _set_display_value)

    def _animate_to(self, value: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._display_value)
        self._anim.setEndValue(float(value))
        self._anim.start()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        c = self._colors
        if c is None:
            super().paintEvent(event)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect())
        radius = rect.height() / 2

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c.SURFACE_ALT))
        p.drawRoundedRect(rect, radius, radius)

        lo, hi = self.minimum(), self.maximum()
        span = max(1, hi - lo)
        frac = max(0.0, min(1.0, (self._display_value - lo) / span))
        if frac > 0:
            # Clip to the *full* rounded-track shape rather than drawing
            # a separately-rounded chunk rect -- that way the fill's left
            # edge always matches the track's curve exactly (including
            # right at 100%, where the fill's own right edge should be
            # rounded too), with a plain straight leading edge in between,
            # which is how every pill progress bar looks.
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.setClipPath(path)
            p.setBrush(QColor(c.ACCENT))
            p.drawRect(QRectF(rect.left(), rect.top(), rect.width() * frac, rect.height()))

        p.end()
