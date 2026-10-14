"""Cancelling a download, and shutting down with one in flight.

Before this existed there was no way to stop a download at all: no
cancel affordance, and no closeEvent, so quitting mid-download destroyed
a running QThread. Both halves are tested here -- the engine's
cooperative stop, and the Qt layer that drives it -- because the useful
property spans the two.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ytdl_engine.core import CancelledError, run_with_retry
from ytdl_engine.download import (
    DownloadOptions,
    _build_progress_hook,
    build_ydl_opts,
    download,
)


def options(tmp_path: Path, **overrides) -> DownloadOptions:
    base = dict(
        url="https://youtu.be/x",
        mode="video",
        height=1080,
        audio_format="mp3",
        include_subtitles=False,
        embed_thumbnail=False,
        output_dir=tmp_path,
        ffmpeg_location=None,
        js_runtime_path=None,
    )
    base.update(overrides)
    return DownloadOptions(**base)


# ---------------------------------------------------------------------------
# The engine's cooperative stop
# ---------------------------------------------------------------------------

def test_progress_hook_raises_yt_dlps_own_cancel_exception():
    """The hook is the only place a cancel can be noticed mid-download.

    It has to raise DownloadCancelled specifically: yt-dlp lets that
    propagate, where a generic exception gets caught and converted into a
    retryable DownloadError.
    """
    from yt_dlp.utils import DownloadCancelled

    hook = _build_progress_hook(None, should_cancel=lambda: True)
    with pytest.raises(DownloadCancelled):
        hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})


def test_progress_hook_is_inert_when_nothing_asked_to_cancel():
    seen = []
    hook = _build_progress_hook(lambda pct, text: seen.append((pct, text)))
    hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
    assert seen and seen[0][0] == pytest.approx(50.0)


def test_cancel_is_checked_even_without_a_progress_callback():
    """A caller may want cancellation without wanting progress updates."""
    from yt_dlp.utils import DownloadCancelled

    hook = _build_progress_hook(None, should_cancel=lambda: True)
    with pytest.raises(DownloadCancelled):
        hook({"status": "downloading"})


def test_a_cancel_before_the_first_attempt_never_starts_a_download(tmp_path):
    with patch("yt_dlp.YoutubeDL") as ydl:
        with pytest.raises(CancelledError):
            download(options(tmp_path), should_cancel=lambda: True)
    assert ydl.call_count == 0, "must not even construct YoutubeDL"


def test_cancelling_is_not_retried(tmp_path):
    """Three attempts and four seconds of sleep would defy the request.

    A 403 is worth retrying with fresh signed URLs; "the user pressed
    Cancel" is not, and retrying it would make the stop take ~6 seconds
    to be honoured.
    """
    calls = []

    def action():
        calls.append(1)
        raise CancelledError("Download cancelled.")

    with patch("ytdl_engine.core.time.sleep") as sleep:
        with pytest.raises(CancelledError):
            run_with_retry(action)
    assert len(calls) == 1, "cancellation must not be re-attempted"
    assert sleep.call_count == 0


def test_a_mid_download_cancel_surfaces_as_cancelled_not_as_a_failure(tmp_path):
    """yt-dlp's DownloadCancelled is translated at the boundary.

    Callers outside ytdl_engine.download should never need to know
    yt-dlp's exception types, and run_with_retry needs to see our own
    CancelledError in order not to retry.
    """
    from yt_dlp.utils import DownloadCancelled

    instance = MagicMock()
    instance.__enter__.return_value.extract_info.side_effect = DownloadCancelled("stop")
    with patch("yt_dlp.YoutubeDL", return_value=instance):
        with pytest.raises(CancelledError):
            download(options(tmp_path))


def test_the_cancel_hook_is_wired_into_the_real_options(tmp_path):
    """Guards against the plumbing existing but nothing connecting it."""
    opts = build_ydl_opts(options(tmp_path), None, lambda: True)
    from yt_dlp.utils import DownloadCancelled

    assert opts["progress_hooks"], "no progress hook installed"
    with pytest.raises(DownloadCancelled):
        opts["progress_hooks"][0]({"status": "downloading"})


# ---------------------------------------------------------------------------
# The Qt layer
# ---------------------------------------------------------------------------

def test_worker_emits_cancelled_rather_than_failed(qapp, tmp_path):
    """A user who changed their mind shouldn't get a red error."""
    from app.workers import DownloadWorker

    worker = DownloadWorker(options(tmp_path))
    worker.cancel()
    results = {"cancelled": 0, "failed": []}
    worker.cancelled.connect(lambda: results.__setitem__("cancelled", 1))
    worker.failed.connect(results["failed"].append)

    with patch("yt_dlp.YoutubeDL") as ydl:
        worker.run()  # synchronous: we want the body, not a thread

    assert results["cancelled"] == 1
    assert results["failed"] == [], "cancelling is not a failure"
    assert ydl.call_count == 0


def test_cancel_sets_the_flag_the_engine_polls(tmp_path):
    from app.workers import DownloadWorker

    worker = DownloadWorker(options(tmp_path))
    assert worker._cancel_requested is False
    worker.cancel()
    assert worker._cancel_requested is True


def test_closing_mid_download_cancels_and_joins_the_worker(main_window):
    """The crash this prevents is a destroyed-while-running QThread."""
    from PySide6.QtGui import QCloseEvent

    worker = MagicMock()
    worker.isRunning.return_value = True
    worker.wait.return_value = True
    main_window._download_worker = worker
    main_window._fetch_worker = None

    main_window.closeEvent(QCloseEvent())

    worker.cancel.assert_called_once()
    worker.wait.assert_called_once()
    worker.terminate.assert_not_called()


def test_a_worker_that_ignores_the_cancel_is_terminated(main_window):
    """Last resort, and only because the alternative is an abort on exit."""
    from PySide6.QtGui import QCloseEvent

    worker = MagicMock()
    worker.isRunning.return_value = True
    worker.wait.return_value = False  # never finishes
    main_window._download_worker = None
    main_window._fetch_worker = worker

    main_window.closeEvent(QCloseEvent())

    # No cancel(): FetchWorker has no cooperative hook to call.
    worker.requestInterruption.assert_called_once()
    worker.terminate.assert_called_once()


def test_closing_with_no_workers_running_is_harmless(main_window):
    from PySide6.QtGui import QCloseEvent

    main_window._download_worker = None
    main_window._fetch_worker = None
    main_window.closeEvent(QCloseEvent())  # must not raise


def test_the_download_button_offers_cancel_while_downloading(main_window):
    """One button, two jobs -- so the label has to be right."""
    main_window._download_in_progress = True
    worker = MagicMock()
    main_window._download_worker = worker

    main_window._on_download_clicked()

    worker.cancel.assert_called_once(), "clicking while busy must cancel"
    main_window._reset_download_button()
    assert main_window._download_btn.text() == "Download"
