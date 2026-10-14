"""First-run experience: the confirmation gate and the setup/doctor report.

The gate is the one thing standing between an agent and silently writing
a file the user never chose the shape of, so it gets tested as behaviour,
not as documentation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ytdl_engine.config import load_settings, update_settings


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def download_tool():
    import ytdl_mcp

    # _confirmation_offered is module state that outlives a single test:
    # once any test has tripped the gate, a later test's "first" call
    # would find the prompt already offered and behave differently
    # depending on execution order. Reset it so each test starts from a
    # genuinely fresh conversation.
    ytdl_mcp._confirmation_offered = False
    return ytdl_mcp.download_video_tool


def mock_ydl(result=None):
    instance = MagicMock()
    instance.__enter__.return_value.extract_info.return_value = result or {
        "requested_downloads": [{"__real_download": True, "filepath": "out.mp4"}]
    }
    return instance


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_first_download_asks_instead_of_downloading(download_tool):
    with patch("yt_dlp.YoutubeDL") as ydl:
        payload = run(download_tool(url="https://youtu.be/x"))
    assert payload["needs_confirmation"] is True
    assert ydl.call_count == 0, "nothing should be downloaded before the user agrees"
    assert "ask_the_user" in payload
    assert str(load_settings().resolved_download_dir()) == payload["download_folder"]


def test_the_question_names_the_folder_and_quality(download_tool):
    update_settings({"max_height": 720, "download_dir": "D:/Videos"})
    with patch("yt_dlp.YoutubeDL") as ydl:
        payload = run(download_tool(url="https://youtu.be/x"))
    question = payload["ask_the_user"]
    assert "D:" in question and "Videos" in question
    assert "720" in question
    assert ydl.call_count == 0


def test_best_available_is_described_in_words_not_as_none(download_tool):
    update_settings({"max_height": None})
    with patch("yt_dlp.YoutubeDL") as ydl:
        payload = run(download_tool(url="https://youtu.be/x"))
    assert payload["quality"] == "best available"
    assert "None" not in payload["ask_the_user"]
    assert ydl.call_count == 0


def test_confirming_an_asked_question_downloads_and_records_the_choice(
    download_tool, tmp_path
):
    """The normal two-step: the tool asks, the user agrees, it downloads."""
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl()):
        asked = run(download_tool(url="https://youtu.be/x", output_dir=str(tmp_path)))
        assert asked["needs_confirmation"] is True
        payload = run(
            download_tool(
                url="https://youtu.be/x", output_dir=str(tmp_path), confirmed=True
            )
        )
    assert "needs_confirmation" not in payload
    assert payload["message"] == "Download complete."
    assert load_settings().defaults_confirmed is True


def test_a_first_call_cannot_confirm_itself(download_tool, tmp_path):
    """confirmed=true on the very first call shows the user nothing.

    `confirmed` is a parameter the model fills in, so by itself it only
    attests that an assistant says the user agreed. What the gate can
    enforce is that the defaults were actually handed back first -- into
    the conversation, where the user can see them. A call that arrives
    already-confirmed, before anything was ever asked, must get the
    question rather than a file on disk.
    """
    with patch("yt_dlp.YoutubeDL") as ydl:
        payload = run(
            download_tool(
                url="https://youtu.be/x", output_dir=str(tmp_path), confirmed=True
            )
        )
    assert payload["needs_confirmation"] is True
    assert ydl.call_count == 0, "a self-confirmed first call must not download"
    assert load_settings().defaults_confirmed is False


def test_it_only_asks_once(download_tool, tmp_path):
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl()):
        run(download_tool(url="https://youtu.be/x", output_dir=str(tmp_path)))
        run(download_tool(url="https://youtu.be/x", output_dir=str(tmp_path), confirmed=True))
        second = run(download_tool(url="https://youtu.be/y", output_dir=str(tmp_path)))
    assert "needs_confirmation" not in second, "a per-download prompt gets clicked through"
    assert second["message"] == "Download complete."


def test_setup_confirm_also_satisfies_the_gate(download_tool, tmp_path):
    """Someone who ran `setup --confirm` has already seen the defaults."""
    # allow_user_only mirrors what `ytdl_cli setup --confirm` passes: the
    # user ran that command themselves, and that is the consent.
    update_settings({"defaults_confirmed": True}, allow_user_only=True)
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl()):
        payload = run(download_tool(url="https://youtu.be/x", output_dir=str(tmp_path)))
    assert "needs_confirmation" not in payload


def test_an_assistant_cannot_flip_the_gate_through_update_settings(download_tool):
    """The guard lives in config.update_settings, not on one caller.

    It used to sit in the MCP server only, so `ytdl_cli config set
    defaults_confirmed=true` -- one Bash call away for any agent that has
    both tools -- walked straight past it.
    """
    import ytdl_engine.config as config

    with pytest.raises(config.UserOnlySettingError):
        config.update_settings({"defaults_confirmed": True})
    with pytest.raises(config.UserOnlySettingError):
        config.update_settings({"cache_dir": "C:/Users/someone/Documents"})
    assert load_settings().defaults_confirmed is False


def test_gate_reflects_a_per_call_override_not_just_the_defaults(download_tool):
    """If the agent passes an explicit folder/quality, confirm THOSE."""
    with patch("yt_dlp.YoutubeDL") as ydl:
        payload = run(
            download_tool(url="https://youtu.be/x", output_dir="E:/Elsewhere", quality=480)
        )
    assert "Elsewhere" in payload["download_folder"]
    assert "480" in payload["ask_the_user"]
    assert ydl.call_count == 0, "must not reach the network to answer this"


# ---------------------------------------------------------------------------
# setup / doctor reporting
# ---------------------------------------------------------------------------

def test_environment_report_explains_and_fixes_each_problem(monkeypatch):
    """A diagnostic that says "ffmpeg not found" and stops just moves the
    problem to a search engine."""
    import ytdl_cli
    from ytdl_engine.binaries import BinaryStatus

    monkeypatch.setattr(
        ytdl_cli, "detect", lambda: BinaryStatus(ffmpeg=None, ffprobe=None, js_runtime=None)
    )
    report = ytdl_cli._environment_report()

    assert report["ready"] is False
    names = {p["what"] for p in report["problems"]}
    assert {"ffmpeg", "ffprobe", "deno"} <= names
    for problem in report["problems"]:
        assert problem["why"].strip(), f"{problem['what']} should say why it matters"
        assert problem["fix"].strip(), f"{problem['what']} should say how to fix it"


def test_a_healthy_environment_reports_no_problems(monkeypatch):
    import ytdl_cli
    from ytdl_engine.binaries import BinaryStatus

    monkeypatch.setattr(
        ytdl_cli,
        "detect",
        lambda: BinaryStatus(
            ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"), js_runtime=Path("deno")
        ),
    )
    report = ytdl_cli._environment_report()
    # yt-dlp-ejs is a real dependency of this project, so in a working
    # dev environment there should be nothing to report.
    assert report["problems"] == []
    assert report["ready"] is True


def test_install_hints_are_platform_specific(monkeypatch):
    import ytdl_cli

    monkeypatch.setattr(ytdl_cli.sys, "platform", "win32")
    assert "winget" in ytdl_cli._install_hint("ffmpeg")

    monkeypatch.setattr(ytdl_cli.sys, "platform", "darwin")
    assert "brew" in ytdl_cli._install_hint("ffmpeg")


def test_agent_entry_dispatches_cli_and_mcp(monkeypatch):
    """The shipped console binary routes `mcp` to the server and
    everything else to the CLI."""
    import agent_entry

    called = {}
    monkeypatch.setattr(
        "ytdl_mcp.main", lambda: called.__setitem__("mcp", True), raising=False
    )
    agent_entry.main(["mcp"])
    assert called.get("mcp")

    monkeypatch.setattr(
        "ytdl_cli.main", lambda argv: called.__setitem__("cli", argv), raising=False
    )
    agent_entry.main(["doctor"])
    assert called.get("cli") == ["doctor"]


def test_agent_entry_help_mentions_how_to_connect(capsys):
    import agent_entry

    with pytest.raises(SystemExit):
        agent_entry.main([])
    out = capsys.readouterr().out
    assert "claude mcp add" in out, "help should show the one command that matters"
    assert "setup" in out


# ---------------------------------------------------------------------------
# Warning the user before a slow transcript
# ---------------------------------------------------------------------------

def test_info_flags_a_video_with_captions_as_fast():
    from ytdl_engine.info import summarize_info

    payload = summarize_info({"id": "x", "automatic_captions": {"en": [{"ext": "json3"}]}})
    assert payload["transcript_cost"].startswith("fast")


def test_info_warns_when_a_transcript_will_need_local_work():
    """Without this the caller can't distinguish seconds from minutes,
    and a silent multi-minute model download reads as a hang."""
    from ytdl_engine.info import summarize_info

    payload = summarize_info({"id": "x"})
    cost = payload["transcript_cost"]
    assert cost.startswith("slow")
    assert "model" in cost, "should mention the one-time model download"


# ---------------------------------------------------------------------------
# Output encoding
# ---------------------------------------------------------------------------

def test_cli_emits_utf8_even_under_a_legacy_console_codepage(tmp_path):
    """Windows consoles default to a legacy codepage, and Python encodes
    stdout with it -- so any video whose title or description contained a
    character outside it (em dashes, emoji, non-Latin scripts) raised
    UnicodeEncodeError mid-write and killed the command. The JSON
    contract must not depend on regional settings.
    """
    import json
    import os
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parent.parent
    settings_file = tmp_path / "settings.json"
    # A folder name full of characters cp1252 cannot represent.
    settings_file.write_text(
        json.dumps({"download_dir": "D:/Vidéos/日本語/–emdash"}), encoding="utf-8"
    )

    env = dict(os.environ)
    env["YTDL_SETTINGS_FILE"] = str(settings_file)
    env["PYTHONIOENCODING"] = "cp1252"  # reproduce the failing condition

    proc = subprocess.run(
        [_sys.executable, str(repo / "ytdl_cli.py"), "config", "show"],
        capture_output=True,
        env=env,
        cwd=str(repo),
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    payload = json.loads(proc.stdout.decode("utf-8"))
    assert "日本語" in payload["settings"]["download_dir"]


def test_force_utf8_is_safe_when_streams_cannot_be_reconfigured(monkeypatch):
    """A frozen windowed process has no real stdio; this must not raise."""
    import ytdl_cli

    class Dumb:
        pass

    monkeypatch.setattr(ytdl_cli.sys, "stdout", Dumb())
    monkeypatch.setattr(ytdl_cli.sys, "stderr", Dumb())
    ytdl_cli._force_utf8_stdio()
