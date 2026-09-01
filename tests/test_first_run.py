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
    payload = run(download_tool(url="https://youtu.be/x"))
    question = payload["ask_the_user"]
    assert "D:" in question and "Videos" in question
    assert "720" in question


def test_best_available_is_described_in_words_not_as_none(download_tool):
    update_settings({"max_height": None})
    payload = run(download_tool(url="https://youtu.be/x"))
    assert payload["quality"] == "best available"
    assert "None" not in payload["ask_the_user"]


def test_confirmed_call_downloads_and_records_the_choice(download_tool, tmp_path):
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl()):
        payload = run(
            download_tool(
                url="https://youtu.be/x", output_dir=str(tmp_path), confirmed=True
            )
        )
    assert "needs_confirmation" not in payload
    assert payload["message"] == "Download complete."
    assert load_settings().defaults_confirmed is True


def test_it_only_asks_once(download_tool, tmp_path):
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl()):
        run(download_tool(url="https://youtu.be/x", output_dir=str(tmp_path), confirmed=True))
        second = run(download_tool(url="https://youtu.be/y", output_dir=str(tmp_path)))
    assert "needs_confirmation" not in second, "a per-download prompt gets clicked through"
    assert second["message"] == "Download complete."


def test_setup_confirm_also_satisfies_the_gate(download_tool, tmp_path):
    """Someone who ran `setup --confirm` has already seen the defaults."""
    update_settings({"defaults_confirmed": True})
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl()):
        payload = run(download_tool(url="https://youtu.be/x", output_dir=str(tmp_path)))
    assert "needs_confirmation" not in payload


def test_gate_reflects_a_per_call_override_not_just_the_defaults(download_tool):
    """If the agent passes an explicit folder/quality, confirm THOSE."""
    payload = run(
        download_tool(url="https://youtu.be/x", output_dir="E:/Elsewhere", quality=480)
    )
    assert "Elsewhere" in payload["download_folder"]
    assert "480" in payload["ask_the_user"]


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
