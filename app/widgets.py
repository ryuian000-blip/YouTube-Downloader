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
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStyledItemDelegate,
    QWidget,
)

from app import icons
from app.theme import ColorTokens

HOVER_MS = 140
PRESS_MS = 90
CHECK_MS = 180
SLIDE_MS = 260


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


class AnimatedSegmentedControl(QWidget):
    """The MODE picker: one pill-shaped track with an accent "thumb" that
    *slides* between segments instead of three separate radio buttons
    blinking on and off.

    The slide is the whole point -- a radio group communicates the change
    only by which dot is filled, whereas a thumb travelling from the old
    segment to the new one shows the relationship between them, so the eye
    tracks the selection rather than re-finding it. Label colors crossfade
    off the same animated value, so text lightens into ON_ACCENT exactly as
    the thumb arrives underneath it rather than snapping a frame early.
    """

    selectionChanged = Signal(int)

    PAD = 3          # inset of the thumb from the track edge
    H_PADDING = 14   # per-segment horizontal breathing room, used by sizeHint
    MIN_SEGMENT = 62  # narrowest a segment may get before the window must grow

    def __init__(self, options: list[str], parent=None) -> None:
        super().__init__(parent)
        self._options = list(options)
        self._colors: ColorTokens | None = None
        self._index = 0
        self._thumb_t = 0.0          # animated, in segment units (0 .. n-1)
        self._hover_index = -1
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._slide = QPropertyAnimation(self, b"thumbPosition", self)
        self._slide.setDuration(SLIDE_MS)
        # OutBack overshoots very slightly at the end, which reads as the
        # thumb settling into place rather than stopping dead.
        self._slide.setEasingCurve(QEasingCurve.OutBack)

    def apply_theme(self, c: ColorTokens) -> None:
        self._colors = c
        self.update()

    # -- animatable property ------------------------------------------------

    def _get_thumb(self) -> float:
        return self._thumb_t

    def _set_thumb(self, value: float) -> None:
        self._thumb_t = value
        self.update()

    thumbPosition = Property(float, _get_thumb, _set_thumb)

    # -- selection ----------------------------------------------------------

    def currentIndex(self) -> int:  # noqa: N802 (Qt naming)
        return self._index

    def setCurrentIndex(self, index: int, animate: bool = True) -> None:  # noqa: N802
        index = max(0, min(len(self._options) - 1, index))
        if index == self._index:
            return
        self._index = index
        self._slide.stop()
        if animate:
            self._slide.setStartValue(self._thumb_t)
            self._slide.setEndValue(float(index))
            self._slide.start()
        else:
            self._set_thumb(float(index))
        self.selectionChanged.emit(index)

    # -- geometry -----------------------------------------------------------

    def _segment_width(self) -> float:
        if not self._options:
            return 0.0
        return (self.width() - self.PAD * 2) / len(self._options)

    def _index_at(self, x: float) -> int:
        seg = self._segment_width()
        if seg <= 0:
            return 0
        idx = int((x - self.PAD) // seg)
        return max(0, min(len(self._options) - 1, idx))

    # -- interaction --------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setCurrentIndex(self._index_at(event.position().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        idx = self._index_at(event.position().x())
        if idx != self._hover_index:
            self._hover_index = idx
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:
        # Arrow keys move the selection, matching how a native radio group
        # behaves -- without this the control is reachable by Tab but dead.
        if event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.setCurrentIndex(self._index - 1)
        elif event.key() in (Qt.Key_Right, Qt.Key_Down):
            self.setCurrentIndex(self._index + 1)
        else:
            super().keyPressEvent(event)

    # -- sizing -------------------------------------------------------------

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        # Equal-width segments sized to the longest label, which is what
        # makes a segmented control read as one balanced object rather than
        # three buttons of random width.
        widest = max((fm.horizontalAdvance(o) for o in self._options), default=0)
        w = (widest + self.H_PADDING * 2) * len(self._options) + self.PAD * 2
        return QSize(int(w), max(38, fm.height() + 18))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        # Deliberately NOT sizeHint() here, unlike AnimatedButton and
        # AnimatedRadioButton. Those pin their minimum to their preferred
        # size because drawText() clips rather than elides. This control
        # elides instead (see paintEvent), so it can be squeezed safely --
        # and it has to be, because "3 x the longest label" is by
        # definition the widest a three-option row can be, and pinning the
        # minimum there forced the whole window ~140px wider than the
        # layout was designed around.
        fm = self.fontMetrics()
        w = self.MIN_SEGMENT * len(self._options) + self.PAD * 2
        return QSize(int(w), max(38, fm.height() + 18))

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        c = self._colors
        if c is None or not self._options:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        enabled = self.isEnabled()

        p.setPen(QPen(QColor(c.BORDER if enabled else c.DISABLED_BORDER), 1))
        p.setBrush(QColor(c.SURFACE_ALT if enabled else c.DISABLED_BG))
        p.drawRoundedRect(rect, radius, radius)

        seg = self._segment_width()
        thumb_h = rect.height() - self.PAD * 2

        # Hover tint on an unselected segment, drawn under the thumb.
        if enabled and self._hover_index >= 0 and abs(self._hover_index - self._thumb_t) > 0.5:
            hx = rect.left() + self.PAD + seg * self._hover_index
            hover_rect = QRectF(hx, rect.top() + self.PAD, seg, thumb_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c.BORDER))
            p.drawRoundedRect(hover_rect, thumb_h / 2, thumb_h / 2)

        if enabled:
            tx = rect.left() + self.PAD + seg * self._thumb_t
            thumb = QRectF(tx, rect.top() + self.PAD, seg, thumb_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c.ACCENT))
            p.drawRoundedRect(thumb, thumb_h / 2, thumb_h / 2)

        font = self.font()
        for i, text in enumerate(self._options):
            # How much of the thumb is currently over this segment (1 = fully
            # covered) drives the label's color, so the crossfade is exactly
            # in step with the slide.
            overlap = max(0.0, min(1.0, 1.0 - abs(i - self._thumb_t)))
            if not enabled:
                color = QColor(c.DISABLED_TEXT)
            else:
                color = _mix(QColor(c.TEXT_MUTED), QColor(c.ON_ACCENT), overlap)
            font.setBold(overlap > 0.5)
            p.setFont(font)
            p.setPen(QPen(color))
            cell = QRectF(rect.left() + self.PAD + seg * i, rect.top(), seg, rect.height())
            # Elide rather than let drawText() clip: this control's minimum
            # width is well below its preferred width (see minimumSizeHint),
            # so a squeezed segment has to degrade to "Video +…" instead of
            # losing its last characters with no visible indication.
            fm = QFontMetrics(font)
            label = fm.elidedText(text, Qt.ElideRight, max(0, int(cell.width()) - 10))
            p.drawText(cell, Qt.AlignCenter, label)

        if self.hasFocus() and enabled:
            p.setPen(QPen(QColor(c.ACCENT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)

        p.end()


class _ComboItemDelegate(QStyledItemDelegate):
    """Setting *any* QStyledItemDelegate on a QComboBox's view is what makes
    Windows' native style stop drawing its own item chrome and start
    honoring the QSS in app/theme.py -- without it the popup keeps native
    row heights and a blue selection bar no stylesheet can reach."""

    ROW_HEIGHT = 32

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt override)
        size = super().sizeHint(option, index)
        size.setHeight(self.ROW_HEIGHT)
        return size


class AnimatedComboBox(QComboBox):
    """Quality / audio-format dropdown.

    Paints its own closed state for the same reason every other control in
    this module does: the native combo draws a platform arrow and frame that
    QSS can tint but not reshape, which looked pasted-in next to the rest of
    the app. Here the border eases to ACCENT on hover/focus and the chevron
    physically flips over while the popup is open, so the closed control and
    the open list read as one object.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._colors: ColorTokens | None = None
        self._hover_t = 0.0
        self._open_t = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        self.setItemDelegate(_ComboItemDelegate(self))
        self.view().setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The popup is not just the list: Qt wraps it in a container widget
        # that draws its own native frame, which renders as a pale border
        # around the styled dark list. Clear that frame directly -- QSS
        # can't select the container, since it's a private Qt class.
        #
        # Note this is setFrameShape and NOT a stylesheet on the container:
        # a stylesheet set on a parent cascades into its children, so
        # "background: transparent" there also blanked the list's own
        # SURFACE background and the popup rendered as floating text on
        # whatever happened to be behind the window.
        container = self.view().parentWidget()
        if isinstance(container, QFrame):
            container.setFrameShape(QFrame.NoFrame)

        self._hover_anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_anim.setDuration(HOVER_MS)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._open_anim = QPropertyAnimation(self, b"openProgress", self)
        self._open_anim.setDuration(180)
        self._open_anim.setEasingCurve(QEasingCurve.OutCubic)

    def apply_theme(self, c: ColorTokens) -> None:
        self._colors = c
        self.update()

    def _get_hover(self) -> float:
        return self._hover_t

    def _set_hover(self, value: float) -> None:
        self._hover_t = value
        self.update()

    hoverProgress = Property(float, _get_hover, _set_hover)

    def _get_open(self) -> float:
        return self._open_t

    def _set_open(self, value: float) -> None:
        self._open_t = value
        self.update()

    openProgress = Property(float, _get_open, _set_open)

    def _animate(self, anim: QPropertyAnimation, current: float, target: float) -> None:
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.start()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self.isEnabled():
            self._animate(self._hover_anim, self._hover_t, 1.0)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._animate(self._hover_anim, self._hover_t, 0.0)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt override)
        # See AnimatedRadioButton.setEnabled -- Qt sends no leaveEvent when a
        # widget is disabled under a resting cursor, so the hover state has
        # to be cleared by hand or it stays lit forever.
        super().setEnabled(enabled)
        if not enabled:
            self._hover_anim.stop()
            self._hover_t = 0.0
            self.update()

    def showPopup(self) -> None:  # noqa: N802 (Qt override)
        # Qt sizes the popup to its widest item, so a list of short entries
        # ("720p") opens visibly narrower than the control it drops out of.
        # Pinning it to the control's own width keeps the closed and open
        # states reading as one object.
        self.view().setMinimumWidth(self.width())
        super().showPopup()
        self._animate(self._open_anim, self._open_t, 1.0)

    def hidePopup(self) -> None:  # noqa: N802 (Qt override)
        super().hidePopup()
        self._animate(self._open_anim, self._open_t, 0.0)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        c = self._colors
        if c is None:
            super().paintEvent(event)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled = self.isEnabled()

        if not enabled:
            bg, border, text_color = (
                QColor(c.DISABLED_BG), QColor(c.DISABLED_BORDER), QColor(c.DISABLED_TEXT)
            )
        else:
            bg = QColor(c.SURFACE_ALT)
            lit = max(self._hover_t, self._open_t)
            border = _mix(QColor(c.BORDER), QColor(c.ACCENT), lit)
            text_color = QColor(c.TEXT_PRIMARY)

        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, 8, 8)

        chevron_box = 20.0
        pad = 12.0
        text_rect = QRectF(
            rect.left() + pad, rect.top(),
            rect.width() - pad * 2 - chevron_box, rect.height(),
        )
        fm = QFontMetrics(self.font())
        p.setPen(QPen(text_color))
        p.drawText(
            text_rect, Qt.AlignVCenter | Qt.AlignLeft,
            fm.elidedText(self.currentText(), Qt.ElideRight, int(text_rect.width())),
        )

        chevron_rect = QRectF(
            rect.right() - pad - chevron_box, rect.center().y() - chevron_box / 2,
            chevron_box, chevron_box,
        )
        p.save()
        # Rotate about the glyph's own center so it flips in place.
        p.translate(chevron_rect.center())
        p.rotate(180.0 * self._open_t)
        p.translate(-chevron_rect.center())
        icons.draw_chevron_down(
            p, chevron_rect,
            QColor(c.DISABLED_TEXT if not enabled else c.TEXT_MUTED),
        )
        p.restore()
        p.end()


class IconButton(QAbstractButton):
    """Small circular button carrying a painted glyph instead of a label --
    used for Fetch (accent) and History (ghost) in the poster layout's top
    row, where a text button would eat width the URL field needs.

    ``setBusy(True)`` swaps the glyph for a spinning arc, which is what
    replaces the old "Fetching…" button text now that there is no text.
    """

    def __init__(self, glyph: str, primary: bool = False, diameter: int = 36, parent=None) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self._primary = primary
        self._colors: ColorTokens | None = None
        self._hover_t = 0.0
        self._press_t = 0.0
        self._spin = 0.0
        self._busy = False
        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.PointingHandCursor)

        self._hover_anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_anim.setDuration(HOVER_MS)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._press_anim = QPropertyAnimation(self, b"pressProgress", self)
        self._press_anim.setDuration(PRESS_MS)
        self._press_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._spin_anim = QPropertyAnimation(self, b"spinAngle", self)
        self._spin_anim.setDuration(900)
        self._spin_anim.setStartValue(0.0)
        self._spin_anim.setEndValue(360.0)
        self._spin_anim.setLoopCount(-1)
        self._spin_anim.setEasingCurve(QEasingCurve.Linear)

    def apply_theme(self, c: ColorTokens) -> None:
        self._colors = c
        self.update()

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

    def _get_spin(self) -> float:
        return self._spin

    def _set_spin(self, value: float) -> None:
        self._spin = value
        self.update()

    spinAngle = Property(float, _get_spin, _set_spin)

    def setBusy(self, busy: bool) -> None:  # noqa: N802 (Qt naming)
        if busy == self._busy:
            return
        self._busy = busy
        if busy:
            self._spin_anim.start()
        else:
            self._spin_anim.stop()
        self.update()

    def isBusy(self) -> bool:  # noqa: N802 (Qt naming)
        return self._busy

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

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt override)
        super().setEnabled(enabled)
        if not enabled:
            self._hover_anim.stop()
            self._press_anim.stop()
            self._hover_t = 0.0
            self._press_t = 0.0
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        c = self._colors
        if c is None:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        enabled = self.isEnabled()

        if not enabled:
            bg, border, glyph_color = (
                QColor(c.DISABLED_BG), QColor(c.DISABLED_BORDER), QColor(c.DISABLED_TEXT)
            )
        elif self._primary:
            bg = _mix(
                _mix(QColor(c.ACCENT), QColor(c.ACCENT_HOVER), self._hover_t),
                QColor(c.ACCENT_PRESSED), self._press_t,
            )
            border = None
            glyph_color = QColor(c.ON_ACCENT)
        else:
            bg = _mix(QColor(c.SURFACE_ALT), QColor(c.BORDER), max(self._hover_t, self._press_t))
            border = _mix(QColor(c.BORDER), QColor(c.ACCENT), self._hover_t)
            glyph_color = _mix(QColor(c.TEXT_MUTED), QColor(c.TEXT_PRIMARY), self._hover_t)

        scale = 1.0 - 0.06 * self._press_t
        p.translate(rect.center())
        p.scale(scale, scale)
        p.translate(-rect.center())

        p.setPen(Qt.NoPen if border is None else QPen(border, 1))
        p.setBrush(bg)
        p.drawEllipse(rect)

        inset = rect.width() * 0.24
        glyph_rect = rect.adjusted(inset, inset, -inset, -inset)
        if self._busy:
            icons.draw_spinner(p, glyph_rect.adjusted(-2, -2, 2, 2), glyph_color, self._spin)
        elif self._glyph == "arrow":
            icons.draw_arrow_right(p, glyph_rect, glyph_color)
        elif self._glyph == "clock":
            icons.draw_clock(p, glyph_rect, glyph_color)
        elif self._glyph == "folder":
            icons.draw_folder(p, glyph_rect, glyph_color)
        p.end()


class PosterThumbnail(QLabel):
    """The full-bleed 16:9 artwork band at the top of the media card.

    Re-renders from its own resizeEvent rather than being driven by the
    window's, because its width changes for reasons the window never sees --
    most obviously the scroll area's vertical scrollbar appearing, which
    silently narrows the card by ~10px and would otherwise leave a pixmap
    rendered for the old width hanging over the card's edge.

    Height comes from heightForWidth() so the layout itself maintains the
    16:9 ratio; setting a fixed height from inside resizeEvent would fight
    the layout and can oscillate.
    """

    ASPECT = 9 / 16

    def __init__(self, radius: int, parent=None) -> None:
        super().__init__(parent)
        self._image = None
        self._radius = radius
        self.setAlignment(Qt.AlignCenter)
        policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumHeight(1)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        return max(1, round(width * self.ASPECT))

    def sizeHint(self) -> QSize:
        width = self.width() or 480
        return QSize(width, self.heightForWidth(width))

    def setImage(self, image) -> None:  # noqa: N802 (Qt naming)
        self._image = image
        self._render()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        from PySide6.QtGui import QPixmap
        from app.imaging import fit_pixmap, rounded_pixmap

        width, height = max(1, self.width()), max(1, self.height())
        if self._image is None or self._image.isNull():
            # Never fatal -- an unreachable thumbnail host leaves an empty
            # SURFACE_ALT band rather than collapsing the card's shape.
            self.clear()
            return
        pixmap = QPixmap.fromImage(self._image)
        pixmap = fit_pixmap(pixmap, QSize(width, height))
        # Top corners only: the title block below paints the matching bottom
        # corners, so the two together read as a single rounded card.
        self.setPixmap(rounded_pixmap(pixmap, self._radius, top=True, bottom=False))


class ElidedLabel(QLabel):
    """QLabel that shortens its text with an ellipsis instead of forcing the
    layout wider. The save-to path is arbitrary-length user data, so without
    this a deep folder pushes the whole destination row past the window."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        # QLabel's own minimum is the full untruncated string, which would
        # let an unusually deep save path drag the whole window wider --
        # the exact thing eliding exists to prevent.
        base = super().minimumSizeHint()
        return QSize(48, base.height())

    def setText(self, text: str) -> None:  # noqa: N802 (Qt override)
        self._full_text = text
        self.setToolTip(text)
        self._apply_elide()

    def fullText(self) -> str:  # noqa: N802 (Qt naming)
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full_text, Qt.ElideMiddle, max(0, self.width())))
