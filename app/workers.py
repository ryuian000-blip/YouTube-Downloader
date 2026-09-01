"""Background QThread workers -- the GUI's thin Qt skin over ytdl_engine.

Never touch a Qt widget from these -- everything crosses back to the GUI
thread via signal/slot (Qt queues the delivery automatically because the
receiving QObject, MainWindow, lives on the main thread). This is the
direct equivalent of the original Tkinter app's ``self.after(0, ...)``
pattern, per the rebuild brief.

All the YouTube-fragile logic (client selection, the JS-runtime wiring,
format strings, the fresh-extract retry loop, ANSI stripping) now lives in
``ytdl_engine`` so the GUI, the CLI (ytdl_cli.py), and the MCP server
(ytdl_mcp.py) share one implementation -- when YouTube shifts again, the
fix lands once. What remains here is exactly the Qt part: threads,
signals, and QImage decoding.

The module-level names re-exported below are the GUI's import surface
(main_window.py, history_view.py) and are kept stable on purpose.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from ytdl_engine import (
    DownloadOptions,
    EngineError,
    MODE_AUDIO_ONLY,
    MODE_VIDEO,
    MODE_VIDEO_ONLY,
    available_heights,
    estimate_download_size,
    extract_info,
    fetch_thumbnail_bytes,
    format_duration,
    format_filesize,
    predict_output_path,
    selected_height,
)
# Imported from the submodule, NOT as `from ytdl_engine import download`:
# the package re-exports a *function* named `download`, so that form would
# bind the function and any `engine_download.download(...)` call would
# raise AttributeError at download time.
from ytdl_engine.download import download as run_download
from ytdl_engine.info import FETCH_FAILED_MESSAGE

# Re-exported for the GUI's existing imports. Listed explicitly rather
# than left implicit so it's obvious these are a public surface other
# modules depend on, not incidental imports.
__all__ = [
    "DownloadOptions",
    "DownloadWorker",
    "FetchWorker",
    "MODE_AUDIO_ONLY",
    "MODE_VIDEO",
    "MODE_VIDEO_ONLY",
    "VideoInfo",
    "estimate_download_size",
    "format_duration",
    "format_filesize",
    "predict_output_path",
    "selected_height",
]


# ---------------------------------------------------------------------------
# Fetch (metadata only)
# ---------------------------------------------------------------------------

class VideoInfo:
    """What FetchWorker hands the GUI.

    Holds a QImage, not a QPixmap: QPixmap depends on the platform's paint
    engine and isn't safe to construct off the GUI thread, but this object
    is built entirely on FetchWorker's background thread and handed across
    via a Qt signal. QImage has no such restriction -- it's just pixel
    data -- so the conversion to QPixmap happens later, in MainWindow,
    once this has crossed back to the GUI thread.
    """

    __slots__ = ("title", "heights", "thumbnail", "raw")

    def __init__(
        self,
        title: str,
        heights: list[int],
        thumbnail: QImage | None = None,
        raw: dict | None = None,
    ) -> None:
        self.title = title
        self.heights = heights
        self.thumbnail = thumbnail
        self.raw = raw or {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"VideoInfo(title={self.title!r}, heights={self.heights!r})"


class FetchWorker(QThread):
    succeeded = Signal(object)   # VideoInfo
    failed = Signal(str)

    def __init__(self, url: str, js_runtime_path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._js_runtime_path = js_runtime_path

    def run(self) -> None:
        try:
            # retry=False keeps the GUI's feel unchanged: a typo'd URL
            # should report back immediately, not after two silent retry
            # delays. DownloadWorker still retries -- a download failing
            # halfway is worth re-attempting, a bad link isn't.
            info = extract_info(self._url, self._js_runtime_path, retry=False)
        except EngineError as exc:
            self.failed.emit(str(exc) or FETCH_FAILED_MESSAGE)
            return
        except Exception:  # noqa: BLE001 -- never let a thread die silently
            self.failed.emit(FETCH_FAILED_MESSAGE)
            return

        thumbnail = None
        data = fetch_thumbnail_bytes(info.get("thumbnail"), self._js_runtime_path)
        if data:
            image = QImage.fromData(data)
            if not image.isNull():
                thumbnail = image

        self.succeeded.emit(
            VideoInfo(
                title=info.get("title") or "Untitled video",
                heights=available_heights(info),
                thumbnail=thumbnail,
                raw=info,
            )
        )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

class DownloadWorker(QThread):
    progress = Signal(float, str)   # percent 0-100, status text
    succeeded = Signal(str)         # final message
    failed = Signal(str)

    def __init__(self, options: DownloadOptions, parent=None) -> None:
        super().__init__(parent)
        self._opts = options

    def run(self) -> None:
        try:
            result = run_download(
                self._opts,
                on_progress=lambda pct, text: self.progress.emit(pct, text),
            )
        except EngineError as exc:
            self.failed.emit(str(exc) or "Download failed.")
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc).splitlines()[0] if str(exc) else "Download failed.")
            return
        self.succeeded.emit(result.message)
