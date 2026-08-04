"""Color tokens and the QSS stylesheet for the app.

Palette is a warm theme in the spirit of Anthropic's own product design
(claude.ai): near-black neutrals and one accent color used for everything
that would normally be Anthropic's signature terracotta orange -- here, a
sage green.

The app used to also ship a light variant with a System/Light/Dark picker,
resolved and persisted by ``app.theme_manager.ThemeManager``. That's gone
now -- dark is the only theme -- so this module defines exactly one
ColorTokens instance (DARK) and nowhere else in the codebase should a hex
value be typed literally.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorTokens:
    BG: str             # window / page background
    SURFACE: str         # card background
    SURFACE_ALT: str     # input / dropdown idle background, inside cards
    BORDER: str           # hairline borders and dividers

    TEXT_PRIMARY: str     # primary text
    TEXT_MUTED: str       # secondary text, section labels, status copy

    ACCENT: str            # sage green (stands in for Anthropic's orange)
    ACCENT_HOVER: str      # hover state
    ACCENT_PRESSED: str    # active / pressed state
    ACCENT_TINT: str       # pale sage wash -- selected radio/checkbox fill
    ON_ACCENT: str         # text/icon color on top of ACCENT or ACCENT_HOVER

    ERROR: str
    WARNING: str

    DISABLED_BG: str
    DISABLED_TEXT: str
    DISABLED_BORDER: str

    @property
    def SUCCESS(self) -> str:
        return self.ACCENT


# ---------------------------------------------------------------------------
# Dark theme -- warm near-black, cream text, sage accent (the only theme)
# ---------------------------------------------------------------------------

DARK = ColorTokens(
    BG="#1e1e1a",
    SURFACE="#28281f",
    SURFACE_ALT="#302f26",
    BORDER="#3d3c30",
    TEXT_PRIMARY="#efeee6",
    TEXT_MUTED="#a7a495",
    ACCENT="#8fa876",
    ACCENT_HOVER="#9fbb87",   # LIGHTER on hover, on purpose: against a dark
                               # background, hover states read as "more
                               # emphasis" by moving away from bg, not
                               # toward it.
    ACCENT_PRESSED="#7c9463",
    ACCENT_TINT="#33402c",
    ON_ACCENT="#141413",       # dark text, not white -- see REBUILD-PYSIDE6.md
                               # for the contrast math (white on this sage
                               # green fails WCAG contrast; dark text clears
                               # ~7:1 in both hover and non-hover states).
    ERROR="#e08a72",
    WARNING="#d8b06a",
    DISABLED_BG="#26261e",
    DISABLED_TEXT="#5c5a4d",
    DISABLED_BORDER="#302f26",
)


# ---------------------------------------------------------------------------
# Spacing (4px baseline grid)
# ---------------------------------------------------------------------------

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 16
SPACE_LG = 24

# ---------------------------------------------------------------------------
# Corners
# ---------------------------------------------------------------------------

RADIUS_CARD = 18
RADIUS_CONTROL = 8

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------

FONT_FAMILY = "Segoe UI"
FONT_FAMILY_FALLBACK = "Segoe UI, -apple-system, Helvetica Neue, Arial, sans-serif"

SIZE_TITLE = 21
SIZE_SECTION_LABEL = 12
SIZE_BODY = 13
SIZE_BUTTON = 13
SIZE_STATUS = 12


def build_stylesheet(c: ColorTokens) -> str:
    """Returns the full application QSS for the given color tokens."""
    return f"""
    /* Deliberately no background-color here. A blanket rule on the base
       QWidget type paints an opaque {{BG}} rectangle behind *every*
       widget -- including plain QLabels sitting on top of a card, whose
       card is a different (lighter, in light mode; barely-different, in
       dark mode) shade than the window. That mismatch is invisible when
       BG and SURFACE are close in lightness, but in dark mode the gap is
       wide enough to show up as a visible box/seam behind label text.
       Only widgets that are actually meant to paint a surface (cards,
       inputs, buttons, the header bar) get a background-color rule below;
       everything else stays transparent and shows its parent through. */
    QWidget {{
        color: {c.TEXT_PRIMARY};
        font-family: {FONT_FAMILY_FALLBACK};
        font-size: {SIZE_BODY}px;
    }}

    QMainWindow, #centralWidget {{
        background-color: {c.BG};
    }}

    QLabel, QCheckBox, QRadioButton {{
        background-color: transparent;
    }}

    /* -------------------------------------------------------------- */
    /* Cards                                                           */
    /* -------------------------------------------------------------- */
    QFrame[card="true"] {{
        background-color: {c.SURFACE};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CARD}px;
    }}

    /* -------------------------------------------------------------- */
    /* Dialogs (QMessageBox)                                            */
    /* -------------------------------------------------------------- */
    /* Without an explicit background-color, QMessageBox falls back to
       the platform's native dialog background (white on Windows) -- the
       same gap the base QWidget rule above deliberately leaves open (see
       its comment), except here there's no card underneath to show
       through instead, so it rendered as a stark white box dropped on
       top of the rest of the dark UI, with a native blue "?" icon that
       doesn't belong to this palette at all -- the code that builds the
       already-downloaded confirmation also explicitly asks for no icon,
       rather than trying to reskin one that's drawn by the platform
       style and won't take an accent-color tint via QSS anyway. */
    QMessageBox {{
        background-color: {c.SURFACE};
    }}
    QMessageBox QLabel {{
        color: {c.TEXT_PRIMARY};
        font-size: {SIZE_BODY}px;
    }}
    QMessageBox QPushButton {{
        min-width: 72px;
    }}

    /* -------------------------------------------------------------- */
    /* Labels                                                          */
    /* -------------------------------------------------------------- */
    QLabel[role="title"] {{
        font-size: {SIZE_TITLE}px;
        font-weight: 700;
        color: {c.TEXT_PRIMARY};
    }}

    QLabel[role="sectionLabel"] {{
        font-size: {SIZE_SECTION_LABEL}px;
        font-weight: 700;
        color: {c.TEXT_MUTED};
        letter-spacing: 1px;
    }}

    QLabel[role="videoTitle"] {{
        font-size: {SIZE_BODY}px;
        font-weight: 600;
        color: {c.TEXT_PRIMARY};
    }}

    QLabel[role="status"] {{
        font-size: {SIZE_STATUS}px;
        color: {c.TEXT_MUTED};
    }}

    QLabel[role="statusError"] {{
        font-size: {SIZE_STATUS}px;
        color: {c.ERROR};
    }}

    QLabel[role="statusWarning"] {{
        font-size: {SIZE_STATUS}px;
        color: {c.WARNING};
    }}

    QLabel[role="statusSuccess"] {{
        font-size: {SIZE_STATUS}px;
        color: {c.SUCCESS};
        font-weight: 600;
    }}

    QLabel[role="body"] {{
        font-size: {SIZE_BODY}px;
        color: {c.TEXT_PRIMARY};
    }}

    QLabel[role="path"] {{
        font-size: {SIZE_BODY}px;
        color: {c.TEXT_MUTED};
    }}

    /* The clickable video-url link on each History row -- default
       rich-text link blue would clash with this palette. */
    QLabel a {{
        color: {c.ACCENT};
    }}

    /* The fetched video's thumbnail, next to its title -- so someone can
       recognize the specific video at a glance rather than only reading
       a title. The pixmap set on this label is already clipped to
       rounded corners in code (QSS border-radius doesn't clip a label's
       *pixmap*, only its own background/border), so this radius exists
       to make the frame drawn around it match, not to do the clipping. */
    QLabel#thumbnailLabel {{
        background-color: {c.SURFACE_ALT};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CONTROL}px;
    }}

    /* -------------------------------------------------------------- */
    /* Line edit / URL field                                           */
    /* -------------------------------------------------------------- */
    QLineEdit {{
        background-color: {c.SURFACE_ALT};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: {SPACE_SM}px {SPACE_MD}px;
        font-size: {SIZE_BODY}px;
        color: {c.TEXT_PRIMARY};
        selection-background-color: {c.ACCENT_TINT};
        selection-color: {c.TEXT_PRIMARY};
    }}
    QLineEdit:focus {{
        border: 1px solid {c.ACCENT};
        background-color: {c.SURFACE};
    }}
    QLineEdit:disabled {{
        background-color: {c.DISABLED_BG};
        color: {c.DISABLED_TEXT};
        border: 1px solid {c.DISABLED_BORDER};
    }}

    /* -------------------------------------------------------------- */
    /* Buttons                                                          */
    /* -------------------------------------------------------------- */
    /* Fetch Info / Download / Change... are app.widgets.AnimatedButton,
       not plain QPushButton -- they paint their own background, border,
       and animated hover/press states in paintEvent() and get their
       colors from ColorTokens directly via apply_theme(), not from this
       QSS. The rules below only apply to a plain QPushButton that hasn't
       had apply_theme() called yet (AnimatedButton falls back to this
       for a single frame before theming is pushed to it), or to any
       future plain QPushButton added elsewhere in the app. */
    QPushButton {{
        background-color: {c.SURFACE_ALT};
        color: {c.TEXT_PRIMARY};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: {SPACE_SM}px {SPACE_MD}px;
        font-size: {SIZE_BUTTON}px;
        font-weight: 400;
    }}
    QPushButton:hover {{
        background-color: {c.BORDER};
    }}
    QPushButton:pressed {{
        background-color: {c.BORDER};
    }}
    QPushButton:disabled {{
        background-color: {c.DISABLED_BG};
        color: {c.DISABLED_TEXT};
        border: 1px solid {c.DISABLED_BORDER};
    }}

    QPushButton[role="primary"] {{
        background-color: {c.ACCENT};
        color: {c.ON_ACCENT};
        border: none;
        font-weight: 700;
    }}
    QPushButton[role="primary"]:hover {{
        background-color: {c.ACCENT_HOVER};
    }}
    QPushButton[role="primary"]:pressed {{
        background-color: {c.ACCENT_PRESSED};
    }}
    QPushButton[role="primary"]:disabled {{
        background-color: {c.DISABLED_BG};
        color: {c.DISABLED_TEXT};
    }}

    /* Pill-shaped buttons: radius = half of each button's fixed height,
       set explicitly here rather than computed, since QSS can't read a
       widget's own height. Keep in sync with the setFixedHeight() calls
       in main_window.py (Fetch Info: 36px -> 18px radius, Download: 44px
       -> 22px radius). */
    QPushButton#fetchButton {{
        border-radius: 18px;
        padding-left: {SPACE_MD}px;
        padding-right: {SPACE_MD}px;
    }}
    QPushButton#downloadButton {{
        border-radius: 22px;
        font-size: 14px;
    }}

    /* -------------------------------------------------------------- */
    /* Dropdowns                                                        */
    /* -------------------------------------------------------------- */
    QComboBox {{
        background-color: {c.SURFACE_ALT};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: {SPACE_SM}px {SPACE_MD}px;
        font-size: {SIZE_BODY}px;
        color: {c.TEXT_PRIMARY};
        min-height: 20px;
    }}
    QComboBox:hover {{
        border: 1px solid {c.ACCENT};
    }}
    QComboBox:disabled {{
        background-color: {c.DISABLED_BG};
        color: {c.DISABLED_TEXT};
        border: 1px solid {c.DISABLED_BORDER};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {c.SURFACE};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CONTROL}px;
        selection-background-color: {c.ACCENT_TINT};
        selection-color: {c.TEXT_PRIMARY};
        outline: none;
        padding: {SPACE_XS}px;
    }}

    /* -------------------------------------------------------------- */
    /* Radio buttons / checkboxes                                       */
    /* -------------------------------------------------------------- */
    /* MODE and EXTRAS controls are app.widgets.AnimatedRadioButton /
       AnimatedCheckBox, not the plain Qt widgets -- real rendering is
       fully custom-painted (see app/widgets.py's module docstring for
       why: the ::indicator:checked override below is what rendered as a
       solid filled square instead of a ring on Windows' native style).
       This block is now only the pre-apply_theme() fallback. */
    QRadioButton, QCheckBox {{
        font-size: {SIZE_BODY}px;
        color: {c.TEXT_PRIMARY};
        spacing: {SPACE_SM}px;
    }}
    QRadioButton::indicator, QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c.BORDER};
        background-color: {c.SURFACE_ALT};
    }}
    QRadioButton::indicator {{
        border-radius: 8px;
    }}
    QCheckBox::indicator {{
        border-radius: 4px;
    }}
    QRadioButton::indicator:hover, QCheckBox::indicator:hover {{
        border: 1px solid {c.ACCENT};
    }}
    QRadioButton::indicator:checked {{
        border: 5px solid {c.ACCENT};
        background-color: {c.SURFACE};
    }}
    QCheckBox::indicator:checked {{
        border: 1px solid {c.ACCENT};
        background-color: {c.ACCENT};
    }}
    QRadioButton:disabled, QCheckBox:disabled {{
        color: {c.DISABLED_TEXT};
    }}

    /* -------------------------------------------------------------- */
    /* Progress bar                                                     */
    /* -------------------------------------------------------------- */
    QProgressBar {{
        background-color: {c.SURFACE_ALT};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CONTROL}px;
        text-align: center;
        color: {c.TEXT_MUTED};
        font-size: {SIZE_STATUS}px;
        min-height: 10px;
        max-height: 10px;
    }}
    QProgressBar::chunk {{
        background-color: {c.ACCENT};
        border-radius: {RADIUS_CONTROL}px;
    }}

    /* -------------------------------------------------------------- */
    /* Scroll area (only if ever needed)                                */
    /* -------------------------------------------------------------- */
    /* QScrollArea's own background-color rule doesn't reach its internal
       viewport widget (a separate child QWidget Qt creates to actually
       paint scrolled content) -- without targeting it explicitly too, the
       viewport keeps its default palette background, showing up as a
       stray light box behind/below scrolled content in dark mode. */
    QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
        border: none;
        background-color: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {c.BORDER};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    """
