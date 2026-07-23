"""Startup splash: logo fades in, holds, then a two-stage exit reveals the
app. No scaling or movement anywhere -- the logo sits at a fixed size the
entire time and only its opacity ever animates, which reads as clean and
deliberate rather than a "zoom" effect competing for attention.

This is an *overlay widget inside the main window*, not a separate
top-level window. An earlier version was a standalone frameless QWidget
that showed on its own, then hid itself while a completely different
window (the main one, with the OS title bar) appeared -- which reads as
two windows swapping rather than one window starting up. Overlaying inside
the already-shown main window keeps it to a single OS window for the whole
sequence: the frame/title bar is there from the first frame, and the logo
is just an in-window transition over content that was already built
underneath it.

Choreography, not just easing (per Material Design's motion guidelines --
see the "Choreography" and "Duration & easing" docs at material.io/m2 and
m3.material.io): fading the logo and the background curtain on one shared
opacity value looked "correct" (right easing curves, ease-out entrance /
ease-in exit) but was still wrong, because partway through the exit both
a half-transparent logo AND a half-transparent hint of the real UI were
on screen at once -- overlapping semi-transparent elements read as messy
regardless of the easing curve. The fix is the "fade-through" pattern:
the outgoing element (logo) fades out *completely* before the incoming
one (real content, via the background curtain lifting) starts to appear,
so the two never overlap mid-transition.

That exit is built from two *sequential* stages, never simultaneous: the
logo's own QGraphicsOpacityEffect fades it out first, and only once
that's fully done does the curtain fade separately. The curtain
deliberately does *not* use a second QGraphicsOpacityEffect for this --
nesting one opacity effect inside a widget tree that's a child of another
effect-bearing widget doesn't composite reliably everywhere (confirmed
while building this: it rendered fine on its own, but with the logo's
effect also present in the same tree, the logo silently failed to
paint at all during the exit instead of fading). The curtain fade is a
plain RGBA stylesheet animation instead -- no QGraphicsEffect involved,
so there's nothing to nest.

Build-time decision (per REBUILD-PYSIDE6.md, which left this as an
implementer's call): QPropertyAnimation over QGraphicsOpacityEffect rather
than frame-by-frame Pillow compositing -- vsync-friendly by construction,
no pre-rendered frame sequence needed.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QSequentialAnimationGroup,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QVBoxLayout, QWidget

from app.theme_manager import ThemeManager


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# Entrance: logo fades in alone, ease-out (Material's "deceleration"
# curve -- enters at full speed, settles gently). No scale change.
ENTER_MS = 380

# Hold at full opacity so the wordmark is actually readable.
HOLD_MS = 550

# Exit stage 1: the logo alone fades out, ease-in (Material's
# "acceleration" curve -- exiting elements get out of the way quickly).
# The background curtain is untouched here, still fully opaque, so there
# is never a moment where a translucent logo and the UI underneath are
# both visible at once.
LOGO_EXIT_MS = 200

# Exit stage 2: only once the logo has fully disappeared does the opaque
# background curtain itself fade away, revealing the real window content.
CURTAIN_EXIT_MS = 240

LOGO_MAX = 168  # fixed size -- never scaled up or down during the animation


class SplashOverlay(QWidget):
    """Sits on top of its parent, filling it completely, until the intro
    animation finishes -- then removes itself. Parent is responsible for
    keeping this widget's geometry in sync (see MainWindow.resizeEvent)."""

    finished = Signal()

    def __init__(
        self,
        assets_dir: Path,
        theme_manager: ThemeManager,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._assets_dir = assets_dir
        self._master_pixmap: QPixmap | None = None
        self.setObjectName("splashOverlay")
        # Plain QWidget ignores QSS `background-color` unless told to
        # paint a styled background -- without this, only the logo label
        # actually draws anything, and the overlay is invisible as a fill,
        # letting the real content underneath show straight through even
        # while this overlay is at full opacity.
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        self._logo_label = QLabel(self)
        self._logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._logo_label)

        # Opacity effect for the logo alone -- the only QGraphicsEffect
        # anywhere in this widget's tree (see module docstring for why a
        # second one on the curtain doesn't work).
        self._logo_effect = QGraphicsOpacityEffect(self._logo_label)
        self._logo_effect.setOpacity(0.0)
        self._logo_label.setGraphicsEffect(self._logo_effect)

        self._curtain_alpha = 255  # 0-255, animated directly via stylesheet
        self._apply_style()
        self._apply_logo()

        self._group = self._build_sequence()

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def _build_sequence(self) -> QSequentialAnimationGroup:
        group = QSequentialAnimationGroup(self)

        # -- Entrance: fade in only, ease-out. No scale change.
        enter_fade = QPropertyAnimation(self._logo_effect, b"opacity", self)
        enter_fade.setDuration(ENTER_MS)
        enter_fade.setStartValue(0.0)
        enter_fade.setEndValue(1.0)
        enter_fade.setEasingCurve(QEasingCurve.OutCubic)

        # -- Exit stage 1: logo alone fades out, ease-in. No scale change.
        logo_exit_fade = QPropertyAnimation(self._logo_effect, b"opacity", self)
        logo_exit_fade.setDuration(LOGO_EXIT_MS)
        logo_exit_fade.setStartValue(1.0)
        logo_exit_fade.setEndValue(0.0)
        logo_exit_fade.setEasingCurve(QEasingCurve.InCubic)

        # -- Exit stage 2: only starts once stage 1 has fully finished
        # (they're siblings in a *sequential* group) -- the logo is
        # completely invisible before the curtain begins to lift.
        curtain_exit = QVariantAnimation(self)
        curtain_exit.setDuration(CURTAIN_EXIT_MS)
        curtain_exit.setStartValue(255)
        curtain_exit.setEndValue(0)
        curtain_exit.setEasingCurve(QEasingCurve.InCubic)
        curtain_exit.valueChanged.connect(self._set_curtain_alpha)

        group.addAnimation(enter_fade)
        group.addPause(HOLD_MS)
        group.addAnimation(logo_exit_fade)
        group.addAnimation(curtain_exit)
        group.finished.connect(self._on_finished)
        return group

    def _set_curtain_alpha(self, alpha: int) -> None:
        self._curtain_alpha = alpha
        self._apply_style()

    # ------------------------------------------------------------------

    def _logo_path(self) -> Path:
        # The lockup (icon + "youtube" / "downloader" wordmark) is baked-in
        # artwork, pre-tinted for a dark background rather than
        # recolorable via QSS -- see assets/logo_on_dark.png. There used
        # to be a light-theme counterpart selected here too, back when the
        # app had a light theme to select for.
        return self._assets_dir / "logo_on_dark.png"

    def _apply_logo(self) -> None:
        if self._master_pixmap is None:
            path = self._logo_path()
            if path.exists():
                self._master_pixmap = QPixmap(str(path))
            else:
                self._master_pixmap = QPixmap()

        if self._master_pixmap.isNull():
            self._logo_label.setText("YouTube\nDownloader")
            c = self._theme_manager.colors()
            self._logo_label.setStyleSheet(
                f"color: {c.TEXT_PRIMARY}; font-size: 16px; font-weight: 700;"
            )
            return

        # Rendered once at a fixed size and never rescaled afterwards --
        # scaling a pixmap up/down every frame during a zoom animation is
        # what made the old version look soft/cheap. Sampling at the
        # screen's actual device pixel ratio (then tagging the pixmap with
        # that ratio) keeps it crisp on HiDPI/Retina displays instead of
        # rendering at 1x and letting Qt stretch it.
        dpr = self.devicePixelRatioF() or 1.0
        target = max(1, int(LOGO_MAX * dpr))
        pixmap = self._master_pixmap.scaled(
            target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        pixmap.setDevicePixelRatio(dpr)
        self._logo_label.setPixmap(pixmap)

    def _apply_style(self) -> None:
        c = self._theme_manager.colors()
        r, g, b = _hex_to_rgb(c.BG)
        # Flat fill, no card border/radius -- this covers the whole
        # window's content area, so it should read as "the window's
        # content hasn't loaded yet," not as a floating card on top of it.
        # rgba() (not a plain hex) so the curtain-exit animation can drive
        # alpha directly -- see _set_curtain_alpha / the module docstring
        # on why this isn't a second QGraphicsOpacityEffect instead.
        self.setStyleSheet(
            f"#splashOverlay {{ background-color: rgba({r}, {g}, {b}, {self._curtain_alpha}); }}"
        )

    def _on_finished(self) -> None:
        self.hide()
        self.deleteLater()
        self.finished.emit()

    def play(self) -> None:
        self.show()
        self.raise_()
        self._group.start()
