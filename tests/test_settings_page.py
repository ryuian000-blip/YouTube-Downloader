"""The Settings page, and changing settings by asking an assistant.

Both surfaces write the same file, so a change made in either place has
to show up in the other -- that's the property these tests pin down.
"""

from __future__ import annotations

import asyncio

import pytest

from ytdl_engine.config import load_settings, update_settings


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def settings_page(main_window, qapp):
    main_window._on_show_settings()
    qapp.processEvents()
    return main_window._settings_page


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def test_settings_is_a_page_not_a_dialog(main_window, qapp):
    """This app deliberately has no secondary windows; History and
    Settings are both pages of the one stack."""
    assert main_window._stack.count() == 3
    main_window._on_show_settings()
    qapp.processEvents()
    assert main_window._stack.currentIndex() == 2


def test_back_returns_to_the_download_page(main_window, qapp, settings_page):
    settings_page.back_requested.emit()
    qapp.processEvents()
    assert main_window._stack.currentIndex() == 0


def test_page_content_fits_without_clipping(main_window, qapp, settings_page):
    """The scroll area has its horizontal bar off (this app elides rather
    than scrolls sideways), so anything wider than the viewport is
    silently cut off rather than reachable. A long combo item or an
    unwrappable label set that minimum once already."""
    from PySide6.QtWidgets import QScrollArea

    scroll = settings_page.findChild(QScrollArea)
    overflow = scroll.widget().minimumSizeHint().width() - scroll.viewport().width()
    assert overflow <= 0, f"settings content overflows its viewport by {overflow}px"


# ---------------------------------------------------------------------------
# Editing through the UI
# ---------------------------------------------------------------------------

def test_changing_quality_saves_immediately(qapp, settings_page):
    combo = settings_page._quality_combo
    combo.setCurrentIndex(combo.findData(720))
    qapp.processEvents()
    assert load_settings().max_height == 720


def test_best_available_is_selectable_and_saves_as_no_cap(qapp, settings_page):
    combo = settings_page._quality_combo
    combo.setCurrentIndex(combo.findData(None))
    qapp.processEvents()
    assert load_settings().max_height is None


def test_limits_can_be_set_and_cleared(qapp, settings_page):
    duration = settings_page._duration_combo
    duration.setCurrentIndex(duration.findData(60))
    qapp.processEvents()
    assert load_settings().max_duration_minutes == 60

    duration.setCurrentIndex(duration.findData(None))
    qapp.processEvents()
    assert load_settings().max_duration_minutes is None


def test_turning_off_transcription_disables_the_accuracy_control(qapp, settings_page):
    settings_page._whisper_checkbox.setChecked(False)
    qapp.processEvents()
    assert load_settings().allow_whisper is False
    assert not settings_page._whisper_combo.isEnabled(), (
        "an accuracy setting is meaningless while transcription is off"
    )


def test_reload_picks_up_changes_made_elsewhere(qapp, settings_page):
    """Someone can change these from the CLI or by asking Claude while
    the app is open; reopening the page must not show stale values."""
    update_settings({"max_height": 360, "cache_max_mb": 2000})
    settings_page.reload()
    qapp.processEvents()
    assert settings_page._quality_combo.currentData() == 360
    assert settings_page._cache_combo.currentData() == 2000


def test_loading_the_page_does_not_write_settings(qapp, settings_page, tmp_path):
    """Populating controls fires their change signals; that must not be
    mistaken for the user editing anything."""
    from ytdl_engine.config import settings_path

    settings_path().unlink(missing_ok=True)
    settings_page.reload()
    qapp.processEvents()
    assert not settings_path().exists(), "merely opening Settings wrote a file"


def test_reset_restores_defaults(qapp, settings_page):
    update_settings({"max_height": 360})
    settings_page._reset()
    qapp.processEvents()
    assert load_settings().max_height == 1080
    assert settings_page._quality_combo.currentData() == 1080


def test_settings_page_and_main_page_share_one_download_folder(
    main_window, qapp, settings_page, tmp_path
):
    target = str(tmp_path / "Videos")
    settings_page._save({"download_dir": target})
    settings_page.download_dir_changed.emit(target)
    qapp.processEvents()
    assert str(main_window._output_dir) == target


# ---------------------------------------------------------------------------
# Asking an assistant to change them
# ---------------------------------------------------------------------------

def test_assistant_can_change_settings_and_reports_the_diff():
    import ytdl_mcp

    update_settings({"max_height": 1080})
    result = run(ytdl_mcp.update_settings_tool(changes={"max_height": 720}))
    assert result["changed"]["max_height"] == {"from": 1080, "to": 720}
    assert load_settings().max_height == 720


def test_assistant_changes_show_up_in_the_app(qapp, settings_page):
    import ytdl_mcp

    run(ytdl_mcp.update_settings_tool(changes={"max_height": 480}))
    settings_page.reload()
    qapp.processEvents()
    assert settings_page._quality_combo.currentData() == 480


def test_assistant_cannot_switch_off_the_confirmation_prompt():
    """defaults_confirmed records that the USER saw where downloads go.
    An assistant setting it would be marking its own homework."""
    import ytdl_mcp
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as excinfo:
        run(ytdl_mcp.update_settings_tool(changes={"defaults_confirmed": True}))
    assert "defaults_confirmed" in str(excinfo.value)
    assert load_settings().defaults_confirmed is False


def test_assistant_gets_a_useful_error_for_a_bad_setting_name():
    import ytdl_mcp
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as excinfo:
        run(ytdl_mcp.update_settings_tool(changes={"quality": 720}))
    message = str(excinfo.value)
    assert "quality" in message
    assert "max_height" in message, "the error should list the valid names"


def test_assistant_can_clear_a_cap_with_null():
    import ytdl_mcp

    update_settings({"max_height": 720})
    result = run(ytdl_mcp.update_settings_tool(changes={"max_height": None}))
    assert result["changed"]["max_height"]["to"] is None
    assert load_settings().max_height is None


def test_no_op_changes_are_reported_as_unchanged():
    import ytdl_mcp

    update_settings({"max_height": 720})
    result = run(ytdl_mcp.update_settings_tool(changes={"max_height": 720}))
    assert result["changed"] == {}
    assert "max_height" in result["unchanged"]
