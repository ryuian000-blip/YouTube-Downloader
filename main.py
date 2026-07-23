"""Entry point: YouTube Downloader.

Run directly with ``python main.py`` during development. For a packaged
build see ``build.spec`` / the README's PyInstaller command.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.theme_manager import ThemeManager

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("YouTube Downloader")

    theme_manager = ThemeManager()
    app.setStyleSheet(theme_manager.stylesheet())

    icon_path = ASSETS_DIR / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow(theme_manager)
    # Show the real window first -- OS title bar and all -- then play the
    # logo fade as an overlay on top of it. One window for the whole
    # sequence, never a separate splash window swapping into the main one.
    window.show()
    window.play_intro()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
