"""The CLI's argument resolution and its JSON contract.

This file is the single biggest coverage gap closed: nothing previously
touched build_parser, cmd_download, cmd_transcript, cmd_frames or
cmd_config, so the surface an assistant actually drives was the least
tested code in the project -- and the thing it most needs to be true,
"an omitted flag uses the user's settings", was true only by accident in
some commands and false in others.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import ytdl_cli
from ytdl_engine.config import update_settings
from ytdl_engine.info import MODE_AUDIO_ONLY, MODE_VIDEO, MODE_VIDEO_ONLY


def parse(argv: list[str]):
    return ytdl_cli.build_parser().parse_args(argv)


def run_main(argv: list[str]) -> tuple[dict, int]:
    """Run the CLI end to end, returning the parsed stdout JSON and code."""
    with pytest.raises(SystemExit) as exit_info:
        ytdl_cli.main(argv)
    return exit_info.value.code


# ---------------------------------------------------------------------------
# The JSON contract: stdout carries JSON and nothing else
# ---------------------------------------------------------------------------

def test_emit_writes_one_json_object_to_stdout_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as info:
        ytdl_cli.emit({"ok": True, "value": 1})
    assert info.value.code == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == {"ok": True, "value": 1}
    assert out.err == "", "stdout-only; nothing may leak to stderr here"


def test_fail_reports_json_on_stdout_and_exits_one(capsys):
    with pytest.raises(SystemExit) as info:
        ytdl_cli.fail("it broke", hint="try this")
    assert info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "error": "it broke", "hint": "try this"}


def test_progress_goes_to_stderr_not_stdout(capsys):
    """yt-dlp's own progress bar on stdout broke `| jq` outright once;
    everything non-JSON has to stay on stderr."""
    ytdl_cli.status("Downloading…")
    out = capsys.readouterr()
    assert out.out == ""
    assert "Downloading" in out.err


def test_an_engine_error_becomes_a_json_failure_not_a_traceback(capsys):
    """An assistant reading a Python traceback learns nothing it can act on."""
    from ytdl_engine.core import EngineError

    with patch.object(ytdl_cli, "get_video_info", side_effect=EngineError("nope")):
        code = run_main(["info", "https://youtu.be/x"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "error": "nope"}


def test_an_unexpected_exception_is_still_json(capsys):
    with patch.object(ytdl_cli, "get_video_info", side_effect=RuntimeError("boom")):
        code = run_main(["info", "https://youtu.be/x"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "RuntimeError" in payload["error"]


# ---------------------------------------------------------------------------
# An omitted flag must use the user's setting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "flag,setting,value,attr,expected",
    [
        (None, "max_height", 720, "height", 720),
        (["--quality", "480"], "max_height", 720, "height", 480),
        (None, "audio_format", "wav", "audio_format", "wav"),
        (["--audio-format", "m4a"], "audio_format", "wav", "audio_format", "m4a"),
    ],
)
def test_download_flags_fall_through_to_settings(flag, setting, value, attr, expected):
    update_settings({setting: value}, allow_user_only=True)
    argv = ["download", "https://youtu.be/x"] + (flag or [])
    captured = {}

    def fake(options, **kwargs):
        captured["options"] = options

        class Result:
            message, real_download, path = "Download complete.", True, Path("x.mp4")
        return Result()

    with patch.object(ytdl_cli, "run_download", fake):
        with pytest.raises(SystemExit):
            ytdl_cli.main(argv)

    assert getattr(captured["options"], attr) == expected


@pytest.mark.parametrize(
    "setting_mode,expected",
    [("video", MODE_VIDEO), ("video_only", MODE_VIDEO_ONLY), ("audio_only", MODE_AUDIO_ONLY)],
)
def test_default_mode_setting_is_honoured(setting_mode, expected):
    update_settings({"default_mode": setting_mode}, allow_user_only=True)
    captured = {}

    def fake(options, **kwargs):
        captured["mode"] = options.mode

        class Result:
            message, real_download, path = "Download complete.", True, Path("x")
        return Result()

    with patch.object(ytdl_cli, "run_download", fake):
        with pytest.raises(SystemExit):
            ytdl_cli.main(["download", "https://youtu.be/x"])

    assert captured["mode"] == expected


@pytest.mark.parametrize("flag,expected", [("video", MODE_VIDEO), ("audio", MODE_AUDIO_ONLY),
                                           ("video-only", MODE_VIDEO_ONLY)])
def test_an_explicit_mode_flag_overrides_the_setting(flag, expected):
    update_settings({"default_mode": "video"}, allow_user_only=True)
    captured = {}

    def fake(options, **kwargs):
        captured["mode"] = options.mode

        class Result:
            message, real_download, path = "Download complete.", True, Path("x")
        return Result()

    with patch.object(ytdl_cli, "run_download", fake):
        with pytest.raises(SystemExit):
            ytdl_cli.main(["download", "https://youtu.be/x", "--mode", flag])

    assert captured["mode"] == expected


def test_an_invalid_default_mode_is_rejected_where_it_is_typed(capsys):
    """`config set default_mode=audio` looks right -- it is the spelling
    the --mode flag uses -- but it is not a valid mode. It used to be
    accepted and then quietly produce an audio-only stream labelled .mp4
    through the MCP surface and a video through the CLI."""
    code = run_main(["config", "set", "default_mode=audio"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "audio_only" in payload["error"], "the error must name the valid values"


def test_transcript_flags_default_to_none_so_settings_govern():
    """These carried concrete argparse defaults, so argparse always passed
    a value and `config set whisper_model=medium` did nothing at all."""
    args = parse(["transcript", "https://youtu.be/x"])
    assert args.lang is None
    assert args.model is None
    assert args.device is None


def test_frame_flags_default_to_none_so_settings_govern():
    args = parse(["frames", "https://youtu.be/x"])
    assert args.max is None
    assert args.width is None
    assert args.quality is None
    assert args.interval is None
    assert args.scene_threshold is None


def test_transcript_passes_the_configured_values_through():
    update_settings(
        {"transcript_language": "fr", "whisper_model": "medium"}, allow_user_only=True
    )
    captured = {}

    def fake(url, **kwargs):
        captured.update(kwargs)

        class T:
            method, language, title = "captions", "fr", "x"

            def slice(self, a, b):
                return self

            def as_dict(self):
                return {"segments": []}
        return T()

    with patch.object(ytdl_cli, "get_transcript", fake):
        with pytest.raises(SystemExit):
            ytdl_cli.main(["transcript", "https://youtu.be/x"])

    # None, not a hard-coded default: the engine resolves it against
    # settings, which is the only place that knows the user's choice.
    assert captured["lang"] is None
    assert captured["whisper_model"] is None


# ---------------------------------------------------------------------------
# scene_threshold: the setting that nothing used to read
# ---------------------------------------------------------------------------

def test_bare_scene_threshold_flag_uses_the_configured_sensitivity():
    update_settings({"scene_threshold": 0.55}, allow_user_only=True)
    captured = {}

    def fake(target, **kwargs):
        captured.update(kwargs)

        class FS:
            def as_dict(self):
                return {"frames": []}
        return FS()

    with patch.object(ytdl_cli, "extract_frames", fake):
        with pytest.raises(SystemExit):
            ytdl_cli.main(["frames", "https://youtu.be/x", "--scene-threshold"])

    assert captured["scene_threshold"] == pytest.approx(0.55)


def test_an_explicit_scene_threshold_still_wins():
    update_settings({"scene_threshold": 0.55}, allow_user_only=True)
    captured = {}

    def fake(target, **kwargs):
        captured.update(kwargs)

        class FS:
            def as_dict(self):
                return {"frames": []}
        return FS()

    with patch.object(ytdl_cli, "extract_frames", fake):
        with pytest.raises(SystemExit):
            ytdl_cli.main(["frames", "https://youtu.be/x", "--scene-threshold", "0.2"])

    assert captured["scene_threshold"] == pytest.approx(0.2)


def test_no_scene_flag_means_interval_mode():
    captured = {}

    def fake(target, **kwargs):
        captured.update(kwargs)

        class FS:
            def as_dict(self):
                return {"frames": []}
        return FS()

    with patch.object(ytdl_cli, "extract_frames", fake):
        with pytest.raises(SystemExit):
            ytdl_cli.main(["frames", "https://youtu.be/x"])

    assert captured["scene_threshold"] is None


# ---------------------------------------------------------------------------
# config subcommands
# ---------------------------------------------------------------------------

def test_config_set_reports_what_changed(capsys):
    code = run_main(["config", "set", "max_height=720"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["settings"]["max_height"] == 720


def test_config_set_rejects_an_unknown_name_and_lists_the_valid_ones(capsys):
    code = run_main(["config", "set", "max_hieght=720"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    # exc.args[0], not str(exc): a KeyError stringifies with quotes around
    # the message, which reads as a typo in JSON output.
    assert not payload["error"].startswith('"')
    assert "max_height" in payload["error"]


def test_config_set_needs_key_equals_value(capsys):
    code = run_main(["config", "set", "max_height"])
    assert code == 1
    assert "key=value" in json.loads(capsys.readouterr().out)["error"]


def test_config_show_reports_where_each_value_came_from(capsys):
    update_settings({"max_height": 720}, allow_user_only=True)
    code = run_main(["config", "show"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sources"]["max_height"] == "settings file"


def test_config_show_reports_an_env_override(capsys, monkeypatch):
    monkeypatch.setenv("YTDL_MAX_HEIGHT", "360")
    code = run_main(["config", "show"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["settings"]["max_height"] == 360
    assert "env" in payload["sources"]["max_height"]


# ---------------------------------------------------------------------------
# Refusals that must arrive before any network call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/playlist?list=PLabc",
        "https://www.youtube.com/@SomeChannel/videos",
        "https://www.youtube.com/channel/UCabc",
        "https://www.youtube.com/results?search_query=cats",
    ],
)
def test_a_multi_video_url_is_refused_without_touching_the_network(capsys, url):
    with patch("yt_dlp.YoutubeDL") as ydl:
        code = run_main(["download", url])
    assert code == 1
    assert ydl.call_count == 0
    payload = json.loads(capsys.readouterr().out)
    assert "single video" in payload["error"] or "one video" in payload["error"]
