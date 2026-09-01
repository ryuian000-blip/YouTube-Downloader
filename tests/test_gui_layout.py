"""GUI regression tests: the poster layout, sizing, and progress overlay.

Each test here corresponds to a bug that actually shipped once. Names say
what broke, so a future failure explains itself.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from app import theme
from app.workers import MODE_AUDIO_ONLY, MODE_VIDEO
from tests.conftest import make_video_info, settle_animations


def chips(window):
    return [
        window._chip_row.itemAt(i).widget().text()
        for i in range(window._chip_row.count())
        if window._chip_row.itemAt(i).widget()
    ]


def test_only_url_row_visible_before_fetch(main_window):
    assert main_window._url_edit.isVisible()
    assert not main_window._media_card.isVisible()
    assert not main_window._controls_card.isVisible()
    assert main_window._top_spacer.height() > 0, (
        "the spacer should hold the URL row centred before the first fetch"
    )


def test_fetch_reveals_the_rest_and_populates_chips(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    for name in ("_media_card", "_controls_card", "_dest_widget", "_download_btn"):
        assert getattr(main_window, name).isVisible(), f"{name} should be revealed"

    assert "12:34" in chips(main_window), "duration chip missing"
    assert "1080p" in chips(main_window), "resolution chip should show the best height"


def test_chips_follow_the_quality_dropdown(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    main_window._quality_combo.setCurrentIndex(main_window._quality_combo.findData(720))
    qapp.processEvents()

    assert "720p" in chips(main_window)
    assert "1080p" not in chips(main_window)


def test_mode_switch_drives_state_and_animates(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)
    assert main_window._current_mode() == MODE_VIDEO

    main_window._mode_control.setCurrentIndex(2)  # Audio only
    qapp.processEvents()

    assert main_window._current_mode() == MODE_AUDIO_ONLY
    assert main_window._mode_control._slide.state().name == "Running", (
        "the segmented control's thumb should animate between segments"
    )
    assert not main_window._quality_combo.isEnabled()
    assert main_window._audio_format_combo.isEnabled()


def test_poster_thumbnail_is_full_bleed_16_9_with_top_corners_only(
    main_window, qapp, thumbnail_image
):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    pixmap = main_window._thumbnail_label.pixmap()
    card_width = main_window._media_card.width()
    assert abs(pixmap.width() - card_width) <= 2, "the band should span the full card"
    assert abs(pixmap.height() - round(pixmap.width() * 9 / 16)) <= 2, "band should be 16:9"

    image = pixmap.toImage()
    assert image.pixelColor(1, 1).alpha() == 0, "top corners should be rounded"
    assert image.pixelColor(1, image.height() - 2).alpha() == 255, (
        "bottom corners must stay square so the card reads as one shape"
    )


def test_progress_bar_sits_flush_under_the_artwork(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    thumb = main_window._thumbnail_label
    bar = main_window._progress_bar
    gap = bar.mapTo(main_window, bar.rect().topLeft()).y() - (
        thumb.mapTo(main_window, thumb.rect().bottomLeft()).y()
    )
    assert 0 <= gap <= 2, f"scrubber should sit flush beneath the artwork (gap={gap})"
    assert abs(bar.width() - main_window._media_card.width()) <= 2
    assert bar.height() == bar.FLUSH_HEIGHT


def test_destination_row_is_path_then_download_button(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    dest = main_window._dest_button.geometry()
    download = main_window._download_btn.geometry()
    assert dest.left() < download.left(), "path should sit left of the Download button"
    assert dest.top() == download.top(), "both should share one row"


def test_window_has_equal_margins_and_no_scroll_area(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    from PySide6.QtWidgets import QScrollArea

    # Scoped to the download page on purpose: the History page has its own
    # QScrollArea and is supposed to.
    assert not main_window._page_content.findChildren(QScrollArea), (
        "the download page should fit exactly, with no scroll area at all"
    )

    margins = main_window._outer_layout.contentsMargins()
    assert margins.left() == margins.right() == margins.top() == margins.bottom() == theme.SPACE_LG

    content = main_window._page_content
    bottom_gap = content.height() - (
        main_window._dest_widget.geometry().bottom() + 1
    )
    assert abs(bottom_gap - theme.SPACE_LG) <= 2, (
        f"bottom padding should match the other sides (got {bottom_gap})"
    )


# ---------------------------------------------------------------------------
# Progress status overlay
# ---------------------------------------------------------------------------

def test_progress_overlay_is_parented_to_the_thumbnail(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    label = main_window._progress_status_label
    assert label.parentWidget() is main_window._thumbnail_label
    assert isinstance(label.graphicsEffect(), QGraphicsDropShadowEffect), (
        "the overlay needs its shadow to stay legible over any thumbnail"
    )
    assert label.objectName() == "progressOverlay"


def test_progress_status_never_shifts_the_layout(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    before = (
        main_window._controls_card.geometry().top(),
        main_window._dest_widget.geometry().top(),
        main_window.size(),
    )
    main_window._show_progress_status("Downloading… 42%  (6.25MiB/s, ETA 00:13)", "status")
    qapp.processEvents()
    main_window._on_download_succeeded("Download complete.")
    qapp.processEvents()
    after = (
        main_window._controls_card.geometry().top(),
        main_window._dest_widget.geometry().top(),
        main_window.size(),
    )
    assert before == after, "progress text floats over the artwork; it must move nothing"


def test_progress_overlay_stays_one_line_however_long_the_text(
    main_window, qapp, thumbnail_image
):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    label = main_window._progress_status_label
    main_window._show_progress_status("50%", "status")
    qapp.processEvents()
    single_line_height = label.height()

    long_text = (
        "Downloading… 33%  (6.25MiB/s, ETA 00:13) extremely long extra text that "
        "would never fit on one line no matter how wide the thumbnail band gets"
    )
    main_window._show_progress_status(long_text, "status")
    qapp.processEvents()

    assert label.height() <= single_line_height + 1, "the overlay must never wrap"
    assert label.text() != long_text, "overflowing text should be elided"
    assert label.toolTip() == long_text, "the full text belongs in the tooltip"


def test_progress_overlay_hides_when_cleared(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)

    main_window._show_progress_status("Downloading… 10%", "status")
    qapp.processEvents()
    assert main_window._progress_status_label.isVisible()

    main_window._show_progress_status("", "status")
    qapp.processEvents()
    assert not main_window._progress_status_label.isVisible()


def test_fetching_a_new_video_clears_the_previous_result(main_window, qapp, thumbnail_image):
    """The reported bug: a finished download's "Download complete." stayed
    pinned over the *next* video's artwork, describing nothing."""
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image, title="Video A"))
    settle_animations(main_window, qapp)
    main_window._progress_bar.setValue(100)
    main_window._on_download_succeeded("Download complete.")
    qapp.processEvents()
    assert main_window._progress_status_label.isVisible()

    main_window._on_fetch_succeeded(
        make_video_info(thumbnail_image, title="Video B", video_id="def456")
    )
    qapp.processEvents()

    assert not main_window._progress_status_label.isVisible()
    assert main_window._progress_bar.value() == 0
    assert main_window._video_title_label.text() == "Video B"


def test_failed_download_message_also_clears_on_new_fetch(main_window, qapp, thumbnail_image):
    main_window._on_fetch_succeeded(make_video_info(thumbnail_image))
    settle_animations(main_window, qapp)
    main_window._on_download_failed("Something went wrong.")
    qapp.processEvents()
    assert main_window._progress_status_label.isVisible()

    main_window._on_fetch_succeeded(
        make_video_info(thumbnail_image, title="Next", video_id="ghi789")
    )
    qapp.processEvents()
    assert not main_window._progress_status_label.isVisible()


# ---------------------------------------------------------------------------
# URL field error state
# ---------------------------------------------------------------------------

def test_url_field_shows_errors_in_place_and_keeps_the_real_url(main_window, qapp):
    edit = main_window._url_edit
    edit.setText("https://youtu.be/realurl")
    assert edit.url() == "https://youtu.be/realurl"
    assert not edit.isError()

    message = "Couldn't read that link. Double-check the URL and try again."
    main_window._on_fetch_failed(message)
    qapp.processEvents()

    assert edit.text() == message
    assert edit.property("state") == "error"
    assert edit.url() == "https://youtu.be/realurl", (
        ".url() must return the real URL, never the error message the field is showing"
    )

    # Focus events are dispatched directly rather than via setFocus():
    # under the offscreen platform the window is never truly activated, so
    # setFocus() doesn't always deliver a focus event. Sending them
    # exercises UrlLineEdit's own focusInEvent/focusOutEvent overrides,
    # which is the behavior under test.
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    edit.focusInEvent(QFocusEvent(QEvent.FocusIn, Qt.MouseFocusReason))
    qapp.processEvents()
    assert edit.text() == "https://youtu.be/realurl", "focusing should reveal the URL"
    assert edit.isError(), "...but focusing alone shouldn't clear the error"

    edit.focusOutEvent(QFocusEvent(QEvent.FocusOut, Qt.MouseFocusReason))
    qapp.processEvents()
    assert edit.text() == message


def test_second_failure_does_not_overwrite_the_stored_url(main_window, qapp):
    edit = main_window._url_edit
    edit.setText("https://youtu.be/realurl")
    main_window._on_fetch_failed("First failure.")
    main_window._on_fetch_failed("Second failure.")
    qapp.processEvents()
    assert edit.url() == "https://youtu.be/realurl"


def test_segmented_control_focus_ring_is_not_clipped(main_window, qapp):
    control = main_window._mode_control
    control.setFocus()
    qapp.processEvents()
    shot = control.grab().toImage()

    def row_has_accent(y):
        for x in range(shot.width()):
            px = shot.pixelColor(x, y)
            if px.alpha() and px.green() > px.red() + 15 and px.green() > px.blue() + 15:
                return True
        return False

    assert row_has_accent(0) or row_has_accent(1), "focus ring should reach the top edge"
    assert row_has_accent(shot.height() - 1) or row_has_accent(shot.height() - 2), (
        "focus ring should reach the bottom edge"
    )
