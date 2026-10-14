"""The MCP tools' payloads and error handling.

Six of the eight tools had no collected test, including `_run` -- the
EngineError -> ToolError mapping that every tool's error message depends
on. The assertions here are about what an assistant actually consumes:
the keys in the payload, and whether a failure arrives as a sentence it
can relay or as a masked "Error executing tool".
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

import ytdl_mcp
from ytdl_engine.config import update_settings
from ytdl_engine.core import EngineError
from ytdl_engine.transcript import Transcript, TranscriptSegment


def run(coro):
    return asyncio.run(coro)


def transcript(**overrides) -> Transcript:
    base = dict(
        title="A talk",
        video_id="abc123",
        method="captions",
        language="en",
        segments=[
            TranscriptSegment(start=0.0, end=5.0, text="hello"),
            TranscriptSegment(start=250.0, end=255.0, text="the interesting bit"),
            TranscriptSegment(start=600.0, end=605.0, text="goodbye"),
        ],
        note=None,
        device=None,
    )
    base.update(overrides)
    return Transcript(**base)


# ---------------------------------------------------------------------------
# _run: the error mapping every tool inherits
# ---------------------------------------------------------------------------

def test_an_engine_error_keeps_its_message():
    """A plain exception gets masked to "Error executing tool <name>" with
    the real reason discarded; ToolError keeps the sentence intact."""
    from mcp.server.mcpserver.exceptions import ToolError

    def boom():
        raise EngineError("deno is missing, run doctor")

    with pytest.raises(ToolError) as info:
        run(ytdl_mcp._run(boom))
    assert "deno is missing, run doctor" in str(info.value)


def test_an_unexpected_exception_is_named_rather_than_swallowed():
    from mcp.server.mcpserver.exceptions import ToolError

    def boom():
        raise ValueError("odd")

    with pytest.raises(ToolError) as info:
        run(ytdl_mcp._run(boom))
    assert "ValueError" in str(info.value) and "odd" in str(info.value)


def test_run_returns_the_value_on_success():
    assert run(ytdl_mcp._run(lambda x: x * 2, 21)) == 42


# ---------------------------------------------------------------------------
# get_transcript
# ---------------------------------------------------------------------------

def test_transcript_defaults_to_readable_text_not_structured_segments():
    """The default is 'text': it is what answers a question about a video,
    and it costs far fewer tokens than one dict per cue."""
    with patch.object(ytdl_mcp, "get_transcript", return_value=transcript()):
        payload = run(ytdl_mcp.get_transcript_tool(url="https://youtu.be/x"))
    assert "text" in payload and "segments" not in payload
    assert "hello" in payload["text"]
    assert payload["segment_count"] == 3
    assert payload["method"] == "captions"
    assert payload["title"] == "A talk"


def test_transcript_segments_format_returns_segments_and_no_text():
    with patch.object(ytdl_mcp, "get_transcript", return_value=transcript()):
        payload = run(
            ytdl_mcp.get_transcript_tool(url="https://youtu.be/x", format="segments")
        )
    assert "segments" in payload and "text" not in payload
    assert len(payload["segments"]) == 3


def test_transcript_start_and_end_actually_narrow_the_window():
    """Timestamps arrive as "4:10", not 250 -- every caller is either a
    human reading a player or an assistant copying from a transcript."""
    with patch.object(ytdl_mcp, "get_transcript", return_value=transcript()):
        payload = run(
            ytdl_mcp.get_transcript_tool(
                url="https://youtu.be/x", format="segments", start="4:00", end="5:00"
            )
        )
    assert payload["segment_count"] == 1
    assert payload["segments"][0]["text"] == "the interesting bit"


def test_transcript_omits_optional_keys_when_there_is_nothing_to_say():
    with patch.object(ytdl_mcp, "get_transcript", return_value=transcript()):
        payload = run(ytdl_mcp.get_transcript_tool(url="https://youtu.be/x"))
    assert "note" not in payload
    assert "device" not in payload


def test_transcript_reports_the_device_when_whisper_ran():
    """"Why was that slow" needs an answer in the payload."""
    with patch.object(
        ytdl_mcp,
        "get_transcript",
        return_value=transcript(method="whisper", device="cuda", note="took a while"),
    ):
        payload = run(ytdl_mcp.get_transcript_tool(url="https://youtu.be/x"))
    assert payload["device"] == "cuda"
    assert payload["note"] == "took a while"


def test_transcript_failure_surfaces_the_engine_message():
    from mcp.server.mcpserver.exceptions import ToolError

    with patch.object(
        ytdl_mcp, "get_transcript", side_effect=EngineError("no captions, no whisper")
    ):
        with pytest.raises(ToolError, match="no captions, no whisper"):
            run(ytdl_mcp.get_transcript_tool(url="https://youtu.be/x"))


# ---------------------------------------------------------------------------
# extract_frames
# ---------------------------------------------------------------------------

def test_frames_payload_tells_the_assistant_to_read_the_paths():
    """The tool returns paths, not pixels, and an assistant that lists
    them instead of reading them has learned nothing."""
    from ytdl_engine.frames import Frame, FrameSet

    frame_set = FrameSet(
        frames=[Frame(path=Path("/tmp/frame_001_0-10.jpg"), timestamp=10.0)],
        output_dir=Path("/tmp"),
        mode="interval",
        truncated=False,
        note=None,
        source=Path("/tmp/clip.mp4"),
    )
    with patch.object(ytdl_mcp, "extract_frames", return_value=frame_set):
        payload = run(ytdl_mcp.extract_frames_tool(target="https://youtu.be/x"))
    assert "hint" in payload and "file-reading tool" in payload["hint"]
    assert payload["frames"]


def test_frames_failure_surfaces_the_engine_message():
    from mcp.server.mcpserver.exceptions import ToolError

    with patch.object(
        ytdl_mcp, "extract_frames", side_effect=EngineError("ffmpeg produced no frames")
    ):
        with pytest.raises(ToolError, match="no frames"):
            run(ytdl_mcp.extract_frames_tool(target="https://youtu.be/x"))


# ---------------------------------------------------------------------------
# get_video_info / search
# ---------------------------------------------------------------------------

def test_video_info_is_passed_through():
    info = {"title": "A talk", "duration": 1320, "transcript_cost": "instant"}
    with patch.object(ytdl_mcp, "get_video_info", return_value=info):
        payload = run(ytdl_mcp.get_video_info_tool(url="https://youtu.be/x"))
    assert payload["title"] == "A talk"
    assert payload["transcript_cost"] == "instant"


def test_search_reports_its_count_alongside_the_results():
    results = [{"title": "one"}, {"title": "two"}]
    with patch.object(ytdl_mcp, "search_youtube", return_value=results):
        payload = run(ytdl_mcp.search_youtube_tool(query="claude code"))
    assert payload["count"] == 2
    assert payload["results"] == results


def test_search_failure_surfaces_the_engine_message():
    from mcp.server.mcpserver.exceptions import ToolError

    with patch.object(
        ytdl_mcp, "search_youtube", side_effect=EngineError("Search query is empty.")
    ):
        with pytest.raises(ToolError, match="Search query is empty"):
            run(ytdl_mcp.search_youtube_tool(query=" "))


# ---------------------------------------------------------------------------
# get_settings / check_setup
# ---------------------------------------------------------------------------

def test_get_settings_reports_the_effective_folder_not_just_the_raw_setting():
    """download_dir is None by default, and "None" is not an answer an
    assistant can tell the user."""
    update_settings({"download_dir": None}, allow_user_only=True)
    payload = run(ytdl_mcp.get_settings_tool())
    assert payload["settings"]["download_dir"] is None
    assert payload["effective_download_dir"], "must resolve to a real path"
    assert "Downloads" in payload["effective_download_dir"]
    assert "how_to_change" in payload


def test_get_settings_reflects_a_change(tmp_path):
    update_settings({"download_dir": str(tmp_path), "max_height": 360},
                    allow_user_only=True)
    payload = run(ytdl_mcp.get_settings_tool())
    assert payload["settings"]["max_height"] == 360
    assert str(tmp_path) in payload["effective_download_dir"]


def test_check_setup_requires_the_js_runtime_to_call_itself_ready():
    """Without deno, every download 403s. This reported ready=true in that
    state, while the CLI's doctor -- reading `missing` instead -- said the
    opposite about the same machine."""
    from ytdl_engine.binaries import BinaryStatus

    no_deno = BinaryStatus(
        ffmpeg=Path("ffmpeg.exe"), ffprobe=Path("ffprobe.exe"), js_runtime=None
    )
    with patch.object(ytdl_mcp, "detect", return_value=no_deno):
        payload = run(ytdl_mcp.check_setup_tool())
    assert payload["ready"] is False
    assert any("deno" in m.lower() for m in payload["missing"])


def test_check_setup_is_ready_when_everything_is_present():
    from ytdl_engine.binaries import BinaryStatus

    all_there = BinaryStatus(
        ffmpeg=Path("ffmpeg.exe"),
        ffprobe=Path("ffprobe.exe"),
        js_runtime=Path("deno.exe"),
    )
    with patch.object(ytdl_mcp, "detect", return_value=all_there):
        payload = run(ytdl_mcp.check_setup_tool())
    assert payload["ready"] is True


# ---------------------------------------------------------------------------
# update_settings
# ---------------------------------------------------------------------------

def test_update_settings_reports_before_and_after():
    update_settings({"max_height": 1080}, allow_user_only=True)
    payload = run(ytdl_mcp.update_settings_tool(changes={"max_height": 720}))
    assert payload["changed"]["max_height"] == {"from": 1080, "to": 720}


def test_update_settings_lists_values_that_were_already_set():
    update_settings({"max_height": 720}, allow_user_only=True)
    payload = run(ytdl_mcp.update_settings_tool(changes={"max_height": 720}))
    assert payload["unchanged"] == ["max_height"]


def test_update_settings_rejects_an_unknown_name():
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="Unknown setting"):
        run(ytdl_mcp.update_settings_tool(changes={"max_hieght": 720}))


def test_update_settings_rejects_an_invalid_value_and_names_the_valid_ones():
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        run(ytdl_mcp.update_settings_tool(changes={"default_mode": "audio"}))
    assert "audio_only" in str(info.value)


def test_update_settings_refuses_a_user_only_setting_with_an_actionable_message():
    """Not a bug report -- a boundary. The message has to say what to ask
    the user to do instead."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        run(ytdl_mcp.update_settings_tool(changes={"cache_dir": "C:/Users/x/Documents"}))
    message = str(info.value)
    assert "cache_dir" in message
    assert "user" in message.lower()


def test_update_settings_refuses_an_empty_change_set():
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="No changes"):
        run(ytdl_mcp.update_settings_tool(changes={}))


def test_an_assistant_can_still_change_an_ordinary_setting(tmp_path):
    """The guard must not have swept up the documented capabilities."""
    payload = run(
        ytdl_mcp.update_settings_tool(changes={"download_dir": str(tmp_path)})
    )
    assert str(tmp_path) in payload["effective_download_dir"]
