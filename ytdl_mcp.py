#!/usr/bin/env python3
"""MCP server exposing YouTube Downloader to Claude Code (and any other
MCP client).

Gives an assistant that cannot watch videos the ability to: search
YouTube, read a video's metadata, pull a timestamped transcript, download
the file, and extract frames as images it can actually look at.

Register it (user scope, so it works from any project):

    claude mcp add youtube-downloader --scope user -- \
        "<repo>/.venv/Scripts/python.exe" "<repo>/ytdl_mcp.py"

Everything heavy runs in a worker thread: the MCP server's event loop must
stay responsive, and yt-dlp/ffmpeg are blocking. Tools return plain dicts
(auto-serialized) and raise ToolError with a readable sentence on failure
-- an assistant reading a Python traceback learns nothing useful.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parent))

# MCP SDK 2.x renamed FastMCP to MCPServer. Failures are raised as
# ToolError specifically: a plain exception gets masked to "Error
# executing tool <name>" with the real reason thrown away, and MCPError
# would surface as a JSON-RPC protocol error instead of a tool result the
# assistant can read and react to. ToolError keeps the message intact.
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from ytdl_engine import (  # noqa: E402
    DEFAULT_MAX_FRAMES,
    DEFAULT_WHISPER_MODEL,
    MODE_AUDIO_ONLY,
    MODE_VIDEO,
    MODE_VIDEO_ONLY,
    DownloadOptions,
    EngineError,
    ytdlp_version,
    detect,
    extract_frames,
    get_transcript,
    get_video_info,
    parse_timestamp,
    search_youtube,
)
from ytdl_engine.config import load_settings, settings_path  # noqa: E402
from ytdl_engine.download import download as run_download  # noqa: E402

mcp = MCPServer(
    name="youtube-downloader",
    version="1.0.0",
    instructions=(
        "Search, download, transcribe, and visually inspect YouTube videos.\n\n"
        "Typical flow for 'what does this video say/show?':\n"
        "  1. search_youtube (only if you don't already have a URL)\n"
        "  2. get_video_info — check duration and whether captions exist "
        "before committing to anything expensive\n"
        "  3. get_transcript — this answers most questions on its own. For a "
        "long video, pass start/end to read one section at a time rather than "
        "pulling the whole thing into context\n"
        "  4. extract_frames — ONLY for moments that actually need eyes (a "
        "chart, UI, diagram, or something the words don't convey). Use the "
        "transcript's timestamps to pick the range, then read the returned "
        "image paths with your file-reading tool.\n\n"
        "Downloading a video for the user is a separate, explicit task: use "
        "download_video for that, not as a step before transcripts or frames "
        "(both fetch what they need on their own, into a temp cache).\n\n"
        "Defaults (quality cap, download folder, frame and transcript "
        "settings, optional size/duration limits) come from the user's own "
        "configuration -- call get_settings rather than guessing where a "
        "file will land or what quality it will be. Omitting an argument "
        "uses their setting; pass one explicitly only to override a single "
        "call, and point them at `ytdl_cli.py config set` for a lasting "
        "change rather than changing it on their behalf."
    ),
)


def _fail(message: str) -> ToolError:
    return ToolError(message)


async def _run(func, *args, **kwargs) -> Any:
    """Run blocking engine work off the event loop, converting engine
    failures into clean MCP errors."""
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except EngineError as exc:
        raise _fail(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _fail(f"{type(exc).__name__}: {exc}") from exc


@mcp.tool(
    structured_output=True,
    title="Search YouTube",
    description=(
        "Search YouTube and return matching videos with title, URL, channel, "
        "duration, and view count. Use this when the user names a video or "
        "topic but gives no link. Returns metadata only — nothing is "
        "downloaded."
    ),
)
async def search_youtube_tool(
    query: Annotated[str, Field(description="What to search for.")],
    limit: Annotated[int, Field(description="How many results (1-25).", ge=1, le=25)] = 5,
) -> dict[str, Any]:
    results = await _run(search_youtube, query, limit)
    return {"query": query, "count": len(results), "results": results}


@mcp.tool(
    structured_output=True,
    title="Get video info",
    description=(
        "Metadata for one YouTube video: title, channel, duration, chapters, "
        "available quality levels, and whether captions exist. Cheap and "
        "fast. Worth calling first on an unfamiliar video — duration tells "
        "you whether to slice the transcript, and the caption flags tell you "
        "whether a transcript will be instant (captions) or slow (local "
        "Whisper transcription)."
    ),
)
async def get_video_info_tool(
    url: Annotated[str, Field(description="YouTube video URL or ID.")],
) -> dict[str, Any]:
    return await _run(get_video_info, url)


@mcp.tool(
    structured_output=True,
    title="Get transcript",
    description=(
        "Timestamped transcript of a YouTube video. Uses YouTube's own "
        "captions when available (instant); otherwise downloads the audio "
        "and transcribes it locally with Whisper (minutes, and needs "
        "faster-whisper installed).\n\n"
        "For a long video, pass start/end to read one section at a time — a "
        "40-minute talk is a lot of tokens at once. Timestamps accept "
        "'4:10', '1:02:03', or plain seconds. Segments overlapping the "
        "window are included, so you get the sentence already in progress.\n\n"
        "The returned timestamps are what you feed to extract_frames when "
        "something needs looking at rather than reading."
    ),
)
async def get_transcript_tool(
    url: Annotated[str, Field(description="YouTube video URL or ID.")],
    start: Annotated[
        str | None, Field(description="Only segments from this time, e.g. '4:10'.")
    ] = None,
    end: Annotated[
        str | None, Field(description="Only segments up to this time, e.g. '6:00'.")
    ] = None,
    format: Annotated[
        Literal["segments", "text"],
        Field(description="'segments' for structured data, 'text' for readable lines."),
    ] = "text",
    lang: Annotated[
        str | None, Field(description="Preferred caption language. Omit for the configured default.")
    ] = None,
    force_whisper: Annotated[
        bool,
        Field(description="Ignore YouTube captions and transcribe locally instead."),
    ] = False,
    whisper_model: Annotated[
        str | None,
        Field(description="Whisper model size if local transcription runs. Omit for the configured default."),
    ] = None,
) -> dict[str, Any]:
    transcript = await _run(
        get_transcript,
        url,
        lang=lang,
        force_whisper=force_whisper,
        whisper_model=whisper_model,
    )
    window = transcript.slice(parse_timestamp(start), parse_timestamp(end))
    payload: dict[str, Any] = {
        "title": window.title,
        "video_id": window.video_id,
        "method": window.method,
        "language": window.language,
        "duration_seconds": window.duration_seconds,
        "segment_count": len(window.segments),
    }
    if window.note:
        payload["note"] = window.note
    if format == "text":
        payload["text"] = window.as_text()
    else:
        payload["segments"] = [s.as_dict() for s in window.segments]
    return payload


@mcp.tool(
    structured_output=True,
    title="Extract frames",
    description=(
        "Extract still frames from a YouTube video (or a local video file) "
        "as JPEG images, and return their file paths. READ THOSE PATHS WITH "
        "YOUR FILE-READING TOOL to actually see them — this tool returns "
        "paths, not pixels.\n\n"
        "This is how you inspect something a transcript can't convey: a "
        "chart, a UI, code on screen, a diagram, someone's slides.\n\n"
        "Pick a mode:\n"
        "  • start+end — zoom into one moment (best default; get the "
        "timestamp from the transcript first)\n"
        "  • interval — one frame every N seconds across the whole video\n"
        "  • scene_threshold (~0.3) — only frames where the picture changed, "
        "good for slide decks and cut-heavy edits\n\n"
        "Capped at max_frames (default 50); the response says whether it hit "
        "the cap. Prefer a narrow range over a big sweep: 5 frames you look "
        "at beat 50 you skim. The video is cached, so later calls on the "
        "same video are fast."
    ),
)
async def extract_frames_tool(
    target: Annotated[
        str, Field(description="YouTube URL/ID, or a path to a local video file.")
    ],
    start: Annotated[str | None, Field(description="Start time, e.g. '4:10'.")] = None,
    end: Annotated[str | None, Field(description="End time, e.g. '4:30'.")] = None,
    interval: Annotated[
        float | None, Field(description="Seconds between frames.", gt=0)
    ] = None,
    scene_threshold: Annotated[
        float | None,
        Field(description="Scene-change sensitivity, ~0.3. Overrides interval.", gt=0, lt=1),
    ] = None,
    max_frames: Annotated[
        int | None,
        Field(description="Maximum frames to return. Omit for the configured default.", ge=1, le=200),
    ] = None,
    width: Annotated[
        int | None,
        Field(description="Frame width in pixels. Omit for the configured default.", ge=160, le=1920),
    ] = None,
    quality: Annotated[
        int | None,
        Field(description="Video height to download for extraction. Omit for the configured default.", ge=144, le=2160),
    ] = None,
) -> dict[str, Any]:
    frame_set = await _run(
        extract_frames,
        target,
        interval=interval,
        scene_threshold=scene_threshold,
        start=parse_timestamp(start),
        end=parse_timestamp(end),
        max_frames=max_frames,
        width=width,
        height=quality,
    )
    payload = frame_set.as_dict()
    payload["hint"] = (
        "Read these paths with your file-reading tool to view the images."
    )
    return payload


@mcp.tool(
    structured_output=True,
    title="Download video",
    description=(
        "Download a YouTube video (or just its audio) to a real folder the "
        "user keeps. Use this when the user actually wants the file — not as "
        "a setup step for get_transcript or extract_frames, which fetch what "
        "they need themselves into a temp cache.\n\n"
        "Defaults to the user's Downloads folder. Returns the final path."
    ),
)
async def download_video_tool(
    url: Annotated[str, Field(description="YouTube video URL or ID.")],
    quality: Annotated[
        int | None,
        Field(
            description=(
                "Max height, e.g. 1080. Omit to use the user's configured "
                "quality cap (see get_settings)."
            ),
            ge=144,
            le=4320,
        ),
    ] = None,
    mode: Annotated[
        Literal["video", "video-only", "audio"] | None,
        Field(description="'video' (with sound), 'video-only', or 'audio'."),
    ] = None,
    audio_format: Annotated[
        Literal["mp3", "m4a", "wav"] | None,
        Field(description="Format when mode is 'audio'."),
    ] = None,
    output_dir: Annotated[
        str | None,
        Field(
            description=(
                "Where to save. Omit to use the user's configured download "
                "folder -- which is the same folder the desktop app saves to."
            )
        ),
    ] = None,
    subtitles: Annotated[bool, Field(description="Also fetch English subtitles.")] = False,
) -> dict[str, Any]:
    settings = load_settings()
    mode_map = {
        "video": MODE_VIDEO,
        "video-only": MODE_VIDEO_ONLY,
        "audio": MODE_AUDIO_ONLY,
    }
    # Every omitted argument falls through to the user's settings, so
    # their configured cap/folder genuinely governs what an agent does.
    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else settings.resolved_download_dir()
    )
    options = DownloadOptions(
        url=url,
        mode=mode_map.get(mode or "", settings.default_mode),
        height=quality if quality is not None else settings.max_height,
        audio_format=audio_format or settings.audio_format,
        include_subtitles=subtitles,
        embed_thumbnail=False,
        output_dir=target_dir,
        ffmpeg_location=None,
        js_runtime_path=None,
        force_overwrite=False,
    )
    result = await _run(run_download, options, settings=settings)
    return {
        "message": result.message,
        "real_download": result.real_download,
        "path": str(result.path) if result.path else None,
        "output_dir": str(target_dir),
        "quality_cap": options.height,
    }


@mcp.tool(
    structured_output=True,
    title="Get settings",
    description=(
        "The user's configured defaults: download folder, quality cap, "
        "frame and transcript settings, and any duration/size limits.\n\n"
        "Read this before telling the user where a file went or what "
        "quality they'll get, instead of assuming. These are the user's "
        "choices — you can override a single call by passing an explicit "
        "argument, but if they want a lasting change, tell them to run "
        "`python ytdl_cli.py config set <key>=<value>` (or pick a folder "
        "in the desktop app) rather than changing it for them."
    ),
)
async def get_settings_tool() -> dict[str, Any]:
    settings = load_settings()
    return {
        "settings": settings.as_dict(),
        "effective_download_dir": str(settings.resolved_download_dir()),
        "settings_file": str(settings_path()),
        "how_to_change": "python ytdl_cli.py config set max_height=720",
    }


@mcp.tool(
    structured_output=True,
    title="Check setup",
    description=(
        "Verify ffmpeg, deno, and the yt-dlp stack are all present and "
        "current. Run this first if downloads or transcripts start failing — "
        "YouTube changes break stale yt-dlp builds, and this reports the "
        "installed version plus anything missing."
    ),
)
async def check_setup_tool() -> dict[str, Any]:
    binaries = await _run(detect)
    payload: dict[str, Any] = {
        "ready": binaries.is_download_ready,
        "yt_dlp_version": ytdlp_version(),
        "ffmpeg": str(binaries.ffmpeg) if binaries.ffmpeg else None,
        "js_runtime": str(binaries.js_runtime) if binaries.js_runtime else None,
        "missing": binaries.missing,
    }
    try:
        import yt_dlp_ejs  # noqa: F401

        payload["yt_dlp_ejs"] = "installed"
    except ImportError:
        payload["ready"] = False
        payload["yt_dlp_ejs"] = "MISSING — downloads will 403. pip install yt-dlp-ejs"
    try:
        import faster_whisper  # noqa: F401

        payload["faster_whisper"] = "installed"
    except ImportError:
        payload["faster_whisper"] = (
            "not installed — transcripts work for videos with captions, but "
            "caption-less videos need: pip install faster-whisper"
        )
    return payload


if __name__ == "__main__":
    mcp.run(transport="stdio")
