"""History page: day grouping, collapse, search, and deferred thumbnails.

The deferred-thumbnail tests guard a real symptom: loading every row's
thumbnail synchronously blocked the UI thread long enough (~305ms with 40
entries) that Windows rendered a stale "ghost" frame of the window, which
looked to the user like a second window flashing open and shut.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.workers import MODE_VIDEO
from tests.conftest import make_video_info


def populate_history(window, thumbnail, count, spread_days=4):
    """Write `count` entries straight into the store, dated across a few
    days so the day-grouping headers have something to group."""
    from ytdl_engine import DownloadOptions

    now = datetime.now(timezone.utc)
    for i in range(count):
        info = make_video_info(thumbnail, title=f"Test Video {i}", video_id=f"v{i}")
        window._video_info = info
        window._url_edit.setText(info.raw["webpage_url"])
        window._last_download_options = DownloadOptions(
            url=info.raw["webpage_url"],
            mode=MODE_VIDEO,
            height=1080,
            audio_format="mp3",
            include_subtitles=False,
            embed_thumbnail=False,
            output_dir=Path("."),
            ffmpeg_location=None,
            js_runtime_path=None,
            force_overwrite=False,
        )
        window._record_history_entry()
        window._history._entries[f"v{i}"].downloaded_at = (
            now - timedelta(days=i % spread_days)
        ).isoformat()
    window._history._save()


def drain_thumbnails(page, qapp, timeout=5.0):
    deadline = time.perf_counter() + timeout
    while page._pending_thumbnails and time.perf_counter() < deadline:
        qapp.processEvents()
        time.sleep(0.005)


@pytest.fixture
def history_window(main_window, qapp, monkeypatch, tmp_path):
    # Redirect the store at its own directories so a test run never writes
    # into (or reads leftovers from) the real app-data folder.
    store = main_window._history
    thumbs = tmp_path / "history_thumbnails"
    thumbs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "_dir", tmp_path)
    monkeypatch.setattr(store, "_thumb_dir", thumbs)
    monkeypatch.setattr(store, "_json_path", tmp_path / "history.json")
    store._entries.clear()
    return main_window


def test_history_groups_rows_by_day_with_headers(history_window, qapp, thumbnail_image):
    populate_history(history_window, thumbnail_image, 6)
    history_window._on_show_history()
    qapp.processEvents()

    page = history_window._history_page
    assert len(page._row_entries) == 6
    assert page._headers, "day-grouped history needs at least one header"
    buckets = [bucket for bucket, _row in page._row_entries]
    assert buckets == sorted(buckets, key=buckets.index), "rows should stay grouped"


def test_collapsing_a_group_hides_only_its_own_rows(history_window, qapp, thumbnail_image):
    populate_history(history_window, thumbnail_image, 6)
    history_window._on_show_history()
    qapp.processEvents()

    page = history_window._history_page
    target = page._row_entries[0][0]
    page._on_group_toggled(target, False)
    qapp.processEvents()

    for bucket, row in page._row_entries:
        assert row.isVisible() is (bucket != target)

    page._on_group_toggled(target, True)
    qapp.processEvents()
    assert all(row.isVisible() for _bucket, row in page._row_entries)


def test_search_flattens_grouping_and_filters(history_window, qapp, thumbnail_image):
    populate_history(history_window, thumbnail_image, 6)
    history_window._on_show_history()
    qapp.processEvents()

    page = history_window._history_page
    page._search_edit.setText("Test Video 3")
    qapp.processEvents()

    assert len(page._row_entries) == 1, "search should narrow to the matching video"
    assert not page._headers, "search results are a flat list, not day-grouped"

    page._search_edit.setText("")
    qapp.processEvents()
    assert len(page._row_entries) == 6
    assert page._headers, "clearing the search restores grouping"


def test_reload_resets_search_and_collapse(history_window, qapp, thumbnail_image):
    populate_history(history_window, thumbnail_image, 4)
    history_window._on_show_history()
    qapp.processEvents()

    page = history_window._history_page
    page._search_edit.setText("Video 1")
    qapp.processEvents()
    page._collapsed.add("Today")

    page.reload()
    qapp.processEvents()
    assert page._search_edit.text() == ""
    assert not page._collapsed


def test_thumbnails_load_after_the_page_appears_not_during(
    history_window, qapp, thumbnail_image
):
    populate_history(history_window, thumbnail_image, 20)
    history_window._on_show_history()
    qapp.processEvents()

    page = history_window._history_page
    rows = [row for _bucket, row in page._row_entries]
    assert len(rows) == 20
    assert page._pending_thumbnails, (
        "thumbnails must be queued for later, not decoded during the page switch"
    )

    drain_thumbnails(page, qapp)
    assert not page._pending_thumbnails
    assert all(
        row._thumb_label.pixmap() is not None and not row._thumb_label.pixmap().isNull()
        for row in rows
    ), "every row should end up with its artwork once the queue drains"


def test_rapid_search_does_not_leave_stale_pending_rows(
    history_window, qapp, thumbnail_image
):
    """Each keystroke rebuilds the list; queued thumbnail work for rows
    that no longer exist must be dropped, not run against deleted rows."""
    populate_history(history_window, thumbnail_image, 20)
    history_window._on_show_history()
    qapp.processEvents()

    page = history_window._history_page
    for text in ("Test Video 1", "Test Video 12", "Test", ""):
        page._search_edit.setText(text)
        qapp.processEvents()

    drain_thumbnails(page, qapp)
    assert not page._pending_thumbnails


def test_removing_an_entry_rebuilds_the_list(history_window, qapp, thumbnail_image):
    populate_history(history_window, thumbnail_image, 3)
    history_window._on_show_history()
    qapp.processEvents()

    page = history_window._history_page
    assert len(page._row_entries) == 3

    page._on_remove("v1")
    qapp.processEvents()
    assert len(page._row_entries) == 2
    assert all("v1" != row._entry.video_id for _bucket, row in page._row_entries)
