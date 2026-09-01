"""Settings: precedence, coercion, persistence, and the limits they drive.

Every test points the settings file at tmp_path so a run can never read
or write the real one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ytdl_engine import EngineError
from ytdl_engine.config import (
    Settings,
    describe_sources,
    known_fields,
    load_settings,
    save_settings,
    settings_path,
    update_settings,
)
from ytdl_engine.download import check_limits


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setenv("YTDL_SETTINGS_FILE", str(target))
    # Clear any real env overrides so tests aren't affected by the shell.
    for name in known_fields():
        monkeypatch.delenv(f"YTDL_{name.upper()}", raising=False)
    return target


# ---------------------------------------------------------------------------
# Defaults and precedence
# ---------------------------------------------------------------------------

def test_defaults_apply_with_no_file(config_file):
    settings = load_settings()
    assert not config_file.exists()
    assert settings.max_height == 1080
    assert settings.default_mode == "video"
    assert settings.max_duration_minutes is None  # guardrails off by default
    assert settings.allow_whisper is True


def test_settings_file_overrides_defaults(config_file):
    config_file.write_text(json.dumps({"max_height": 720}), encoding="utf-8")
    assert load_settings().max_height == 720


def test_env_overrides_the_file(config_file, monkeypatch):
    config_file.write_text(json.dumps({"max_height": 720}), encoding="utf-8")
    monkeypatch.setenv("YTDL_MAX_HEIGHT", "360")
    assert load_settings().max_height == 360
    assert describe_sources()["max_height"] == "env YTDL_MAX_HEIGHT"


def test_sources_report_where_each_value_came_from(config_file, monkeypatch):
    config_file.write_text(json.dumps({"max_height": 720}), encoding="utf-8")
    monkeypatch.setenv("YTDL_WHISPER_MODEL", "medium")
    sources = describe_sources()
    assert sources["max_height"] == "settings file"
    assert sources["whisper_model"] == "env YTDL_WHISPER_MODEL"
    assert sources["audio_format"] == "default"


def test_settings_path_honours_its_env_override(config_file):
    assert settings_path() == config_file


# ---------------------------------------------------------------------------
# Coercion -- env vars are strings, JSON is typed, both must land correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [("720", 720), ("none", None), ("null", None), ("", None)],
)
def test_env_int_and_none_coercion(config_file, monkeypatch, raw, expected):
    monkeypatch.setenv("YTDL_MAX_HEIGHT", raw)
    assert load_settings().max_height == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("1", True), ("yes", True), ("false", False), ("0", False)],
)
def test_env_bool_coercion(config_file, monkeypatch, raw, expected):
    monkeypatch.setenv("YTDL_ALLOW_WHISPER", raw)
    assert load_settings().allow_whisper is expected


def test_float_coercion(config_file, monkeypatch):
    monkeypatch.setenv("YTDL_SCENE_THRESHOLD", "0.45")
    assert load_settings().scene_threshold == pytest.approx(0.45)


def test_corrupt_settings_file_falls_back_to_defaults(config_file):
    """A stray comma shouldn't take the whole app down."""
    config_file.write_text("{ this is not json", encoding="utf-8")
    assert load_settings().max_height == 1080


def test_unknown_keys_in_the_file_are_ignored(config_file):
    config_file.write_text(
        json.dumps({"max_height": 720, "leftover_setting": "x"}), encoding="utf-8"
    )
    assert load_settings().max_height == 720


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def test_update_settings_persists_and_returns_the_new_view(config_file):
    updated = update_settings({"max_height": "480", "download_dir": "D:/Videos"})
    assert updated.max_height == 480
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["max_height"] == 480
    assert saved["download_dir"] == "D:/Videos"


def test_update_settings_rejects_unknown_keys(config_file):
    with pytest.raises(KeyError) as excinfo:
        update_settings({"nonsense": 1})
    assert "nonsense" in str(excinfo.value)
    assert not config_file.exists(), "a rejected update must not write anything"


def test_update_does_not_bake_env_overrides_into_the_file(config_file, monkeypatch):
    """Saving while an env override is active must not make that
    one-off value permanent."""
    monkeypatch.setenv("YTDL_WHISPER_MODEL", "large")
    update_settings({"max_height": 720})
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["whisper_model"] == "small", "env value should not be persisted"


def test_update_preserves_previously_saved_values(config_file):
    update_settings({"max_height": 720})
    update_settings({"audio_format": "wav"})
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["max_height"] == 720
    assert saved["audio_format"] == "wav"


# ---------------------------------------------------------------------------
# Download folder resolution
# ---------------------------------------------------------------------------

def test_download_dir_defaults_to_the_downloads_folder(config_file):
    assert load_settings().resolved_download_dir() == Path.home() / "Downloads"


def test_download_dir_is_configurable(config_file):
    update_settings({"download_dir": str(Path.home() / "Videos")})
    assert load_settings().resolved_download_dir() == Path.home() / "Videos"


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def test_no_limits_means_nothing_is_blocked(config_file):
    check_limits({"duration": 60 * 60 * 10, "filesize_approx": 50 * 1024**3})


def test_duration_limit_blocks_and_names_the_setting(config_file):
    settings = Settings(max_duration_minutes=5)
    with pytest.raises(EngineError) as excinfo:
        check_limits({"duration": 22 * 60}, settings)
    message = str(excinfo.value)
    assert "22:00" in message
    assert "max_duration_minutes" in message, "a refusal must say what to change"


def test_filesize_limit_blocks_and_names_the_setting(config_file):
    settings = Settings(max_filesize_mb=100)
    with pytest.raises(EngineError) as excinfo:
        check_limits({"filesize_approx": 500 * 1024 * 1024}, settings)
    assert "max_filesize_mb" in str(excinfo.value)


def test_limits_allow_content_within_bounds(config_file):
    settings = Settings(max_duration_minutes=60, max_filesize_mb=1000)
    check_limits({"duration": 22 * 60, "filesize_approx": 100 * 1024 * 1024}, settings)


def test_missing_metadata_does_not_trip_limits(config_file):
    """An unknown duration/size shouldn't be treated as infinite."""
    settings = Settings(max_duration_minutes=5, max_filesize_mb=10)
    check_limits({}, settings)


# ---------------------------------------------------------------------------
# The settings actually reach the call sites
# ---------------------------------------------------------------------------

def test_frames_use_configured_defaults(config_file, monkeypatch):
    """extract_frames_from_file must read frame_max/frame_width from
    settings, not from constants in its own signature."""
    from ytdl_engine import frames as frames_mod

    update_settings({"frame_max": 7, "frame_width": 640})
    captured = {}

    def fake_run(args, timeout=900):
        captured["args"] = args
        raise EngineError("stop here -- we only need the arguments")

    monkeypatch.setattr(frames_mod, "_run_ffmpeg", fake_run)
    monkeypatch.setattr(frames_mod, "_ffmpeg_binary", lambda: Path("ffmpeg"))

    video = config_file.parent / "video.mp4"
    video.write_bytes(b"not really a video")
    with pytest.raises(EngineError):
        frames_mod.extract_frames_from_file(video, config_file.parent / "out")

    args = captured["args"]
    assert "scale=640:-2" in " ".join(args), "configured frame width should be used"
    # max_frames + 1: one extra frame is requested so truncation is detectable
    assert "8" in args, "configured frame cap should be used"


def test_transcript_respects_allow_whisper_being_off(config_file, monkeypatch):
    from ytdl_engine import transcript as transcript_mod

    update_settings({"allow_whisper": False})
    monkeypatch.setattr(
        transcript_mod, "extract_info", lambda url, js=None: {"id": "x", "title": "T"}
    )
    monkeypatch.setattr(transcript_mod, "_captions_from_info", lambda *a, **k: None)

    with pytest.raises(EngineError) as excinfo:
        transcript_mod.get_transcript("https://youtu.be/x")
    assert "allow_whisper" in str(excinfo.value)
