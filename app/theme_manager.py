"""Resolves the app's (fixed) color scheme.

This used to resolve and persist a System/Light/Dark choice -- QSettings,
OS theme detection, a header button to switch between them, the works.
The app is dark-only now, so all of that is gone. What's left is a thin
wrapper around a single dark ColorTokens instance, kept as its own class
(rather than having every caller just import app.theme.DARK directly) so
main_window.py, splash.py, and app/widgets.py can keep calling
``colors()`` / ``stylesheet()`` without caring whether "the theme" is
computed or fixed.
"""

from __future__ import annotations

from PySide6.QtCore import QObject

from app import theme
from app.theme import ColorTokens


class ThemeManager(QObject):
    """Single source of truth for the app's color tokens. There is only
    one theme (dark), so this never changes at runtime."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def effective_scheme(self) -> str:
        return "dark"

    def colors(self) -> ColorTokens:
        return theme.DARK

    def stylesheet(self) -> str:
        return theme.build_stylesheet(self.colors())
