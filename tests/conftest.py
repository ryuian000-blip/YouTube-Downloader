"""Shared test fixtures.

These tests were previously scratch scripts living in a temp directory --
which a routine temp cleanup then deleted, taking the whole regression
suite with it. They live in the repo now.

Everything here runs headless (QT_QPA_PLATFORM=offscreen, set in
pytest.ini) and hits no network. Network-touching checks live in
tests/smoke_network.py, which is opt-in and not collected by pytest.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Must be set before PySide6 is imported anywhere, or Qt will try (and on
# a headless machine, fail) to open a real display connection.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    # A distinct app name keeps these tests' QSettings/history out of the
    # real "YouTube Downloader" app-data folder.
    app.setApplicationName("YT Downloader Tests")
    yield app


@pytest.fixture
def thumbnail_image():
    image = QImage(1280, 720, QImage.Format_RGB32)
    image.fill(QColor("#4a5a3a"))
    return image


class FakeScreen:
    """The offscreen platform hard-codes an 800x800 virtual screen,
    unrelated to any real monitor. The window sizes itself against
    ``screen().availableGeometry()`` (see MainWindow._reveal_rest_of_ui),
    and on an 800px-tall fake screen the cap is genuinely smaller than
    this layout's natural height -- so content compresses exactly as
    designed, and geometry assertions would be measuring the harness
    rather than the app. Tests that care about layout patch in a
    realistic desktop instead."""

    def availableGeometry(self):
        return QRect(0, 0, 1920, 1200)


@pytest.fixture
def main_window(qapp, monkeypatch, tmp_path):
    from app.main_window import MainWindow
    from app.theme_manager import ThemeManager

    theme_manager = ThemeManager()
    qapp.setStyleSheet(theme_manager.stylesheet())
    window = MainWindow(theme_manager)
    window.resize(560, 640)
    window.show()
    qapp.processEvents()
    monkeypatch.setattr(window, "screen", lambda: FakeScreen())
    # Never write into the user's real Downloads folder from a test.
    window._output_dir = tmp_path
    yield window
    window.close()
    qapp.processEvents()


def make_video_info(thumbnail, title="Test Video", video_id="abc123", heights=(1080, 720, 360)):
    """A VideoInfo shaped like a real successful fetch."""
    from app.workers import VideoInfo

    raw = {
        "id": video_id,
        "webpage_url": f"https://youtu.be/{video_id}",
        "duration": 754,
        "duration_string": "12:34",
        "formats": [
            {"height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 145_000_000},
            {"height": 720, "vcodec": "avc1", "acodec": "none", "filesize": 78_000_000},
            {"height": 360, "vcodec": "avc1", "acodec": "none", "filesize": 25_000_000},
            {"height": None, "vcodec": "none", "acodec": "mp4a", "filesize": 6_000_000},
        ],
    }
    return VideoInfo(title=title, heights=list(heights), thumbnail=thumbnail, raw=raw)


def settle_animations(window, qapp):
    """Drive every reveal animation to its end state.

    QPropertyAnimation runs on real wall-clock time, so processEvents()
    alone can leave one mid-flight -- and while the docking spacer is
    still collapsing, the thumbnail's heightForWidth-driven size wobbles
    a few px before settling. Asserting on the settled state tests the
    real result instead of a timing-dependent snapshot.
    """
    qapp.processEvents()
    for animation in list(getattr(window, "_reveal_animations", [])):
        animation.setCurrentTime(animation.duration())
    qapp.processEvents()
