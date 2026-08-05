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
# A history row's thumbnail is a standalone tile, not half of a card the
# way the poster's is -- so it rounds on all four corners, at a radius
# between CONTROL and CARD that suits its size (116x65, versus the
# poster's much larger full-bleed band).
RADIUS_HISTORY_THUMB = 10

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
# The poster layout gives the fetched video its own band, so its title is
# the largest thing on screen after the window title -- big enough to read
# as a heading, still short of SIZE_TITLE so it can't compete with it.
SIZE_POSTER_TITLE = 16
SIZE_CHIP = 11


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

    /* Pill background for the download-status overlay floating on the
       poster thumbnail (see PosterThumbnail.setOverlay in widgets.py).
       Keyed by objectName, not role: role keeps toggling between
       status/statusError/statusWarning/statusSuccess above for TEXT
       color as the download progresses, but the dark pill underneath it
       -- needed so the text stays legible over an arbitrary, unpredictable
       thumbnail image -- stays constant across all of those, and QSS
       composes both rules onto the same widget at once (this one
       contributes background/shape, the role rule above contributes
       color) since neither declares a property the other one does. */
    QLabel#progressOverlay {{
        background-color: rgba(0, 0, 0, 160);
        border-radius: {RADIUS_CONTROL}px;
        padding: 3px 9px;
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
    /* Media card (poster layout)                                       */
    /* -------------------------------------------------------------- */
    /* The thumbnail band and the title block below it are one rounded
       object. Qt does NOT clip a child widget to its parent's
       border-radius, so the card itself carries no background at the top
       -- the band's pixmap is clipped to rounded TOP corners in code (see
       imaging.rounded_pixmap's top/bottom flags) and the title block below
       paints the rounded BOTTOM corners itself. */
    QFrame#mediaCard {{
        background-color: transparent;
        border: none;
    }}

    /* The radius here rounds the label's own BACKGROUND, which is a
       separate thing from the rounded clipping applied to its pixmap in
       imaging.rounded_pixmap(). Both are needed: without this rule the
       label painted a fully square SURFACE_ALT rectangle behind the
       correctly-rounded pixmap, and that square background showed through
       at the top-left and top-right as two small hard-edged notches.
       The fill itself has to stay -- it is what renders the empty band
       when a video's thumbnail can't be reached, and that state still
       needs to hold the card's shape. */
    QLabel#posterThumb {{
        background-color: {c.SURFACE_ALT};
        border: none;
        border-top-left-radius: {RADIUS_CARD}px;
        border-top-right-radius: {RADIUS_CARD}px;
    }}

    QWidget#posterMeta {{
        background-color: {c.SURFACE};
        border: 1px solid {c.BORDER};
        border-top: none;
        border-bottom-left-radius: {RADIUS_CARD}px;
        border-bottom-right-radius: {RADIUS_CARD}px;
    }}

    QLabel[role="posterTitle"] {{
        font-size: {SIZE_POSTER_TITLE}px;
        font-weight: 650;
        color: {c.TEXT_PRIMARY};
    }}

    /* Duration / resolution / size, next to the title. Monospace digits so
       the row doesn't jitter as the size estimate changes with quality. */
    QLabel[role="chip"] {{
        background-color: {c.SURFACE_ALT};
        border: 1px solid {c.BORDER};
        border-radius: 6px;
        padding: 2px 7px;
        font-size: {SIZE_CHIP}px;
        color: {c.TEXT_MUTED};
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

    /* UrlLineEdit's failed-fetch state (see app/widgets.py). The :focus
       variant has to be declared explicitly and after the plain :focus
       rule above -- without it, focusing the field (exactly when the
       user is looking at it, reading the error) would fall through to
       the plain rule and the red border would vanish. */
    QLineEdit[state="error"] {{
        border: 1px solid {c.ERROR};
        color: {c.ERROR};
    }}
    QLineEdit[state="error"]:focus {{
        border: 1px solid {c.ERROR};
        color: {c.ERROR};
        background-color: {c.SURFACE};
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
    /* The CLOSED control is app.widgets.AnimatedComboBox, which paints its
       own background, border and chevron (the native drop-down arrow is a
       platform pixmap QSS can tint but not reshape, and it read as
       pasted-in next to everything else here). These rules are the
       pre-apply_theme() fallback for that one frame before colors arrive.
       The POPUP below is the part QSS genuinely owns. */
    QComboBox {{
        background-color: {c.SURFACE_ALT};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: {SPACE_SM}px {SPACE_MD}px;
        font-size: {SIZE_BODY}px;
        color: {c.TEXT_PRIMARY};
        min-height: 20px;
    }}
    QComboBox:disabled {{
        background-color: {c.DISABLED_BG};
        color: {c.DISABLED_TEXT};
        border: 1px solid {c.DISABLED_BORDER};
    }}
    /* Zero-width, because AnimatedComboBox draws the chevron itself and a
       native sub-control here would sit on top of it. */
    QComboBox::drop-down {{
        border: none;
        width: 0px;
    }}

    /* The open list. Note app.widgets._ComboItemDelegate must be installed
       on the view for these item rules to take effect at all -- Windows'
       native style otherwise draws its own row chrome (fixed heights, a
       blue selection bar) that no stylesheet can override. */
    QComboBox QAbstractItemView {{
        background-color: {c.SURFACE};
        border: 1px solid {c.BORDER};
        border-radius: {RADIUS_CONTROL}px;
        selection-background-color: transparent;
        outline: none;
        padding: {SPACE_XS}px;
        margin-top: {SPACE_XS}px;
    }}
    QComboBox QAbstractItemView::item {{
        border-radius: 6px;
        padding: 0px {SPACE_SM}px;
        margin: 1px 0px;
        color: {c.TEXT_MUTED};
        border: none;
    }}
    /* Hover and keyboard-highlight get the same treatment, so arrowing
       through the list looks identical to moving the mouse down it. */
    QComboBox QAbstractItemView::item:hover,
    QComboBox QAbstractItemView::item:selected {{
        background-color: {c.ACCENT_TINT};
        color: {c.TEXT_PRIMARY};
    }}
    /* The value currently in effect stays marked in accent even while the
       cursor is elsewhere in the list. */
    QComboBox QAbstractItemView::item:checked {{
        color: {c.ACCENT};
        font-weight: 600;
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

    /* The poster layout's scrubber: spans the media card edge-to-edge
       directly beneath the artwork, so it drops the pill radius, the
       border, and a couple of pixels of height. This override exists
       because the rule above pins both min-height and max-height, and QSS
       beats setFixedHeight() -- AnimatedProgressBar.setFlush() sets this
       object name precisely so this rule can take effect. */
    QProgressBar#flushProgressBar {{
        border: none;
        border-radius: 0px;
        background-color: {c.SURFACE_ALT};
        min-height: 5px;
        max-height: 5px;
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
