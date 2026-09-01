"""Headless engine behind YouTube Downloader.

Everything YouTube-fragile lives here, with no Qt anywhere, so all three
surfaces share one implementation:

- the PySide6 GUI (``app/workers.py`` wraps these in QThreads)
- the CLI (``ytdl_cli.py``)
- the MCP server (``ytdl_mcp.py``) that lets Claude Code search, download,
  transcribe, and visually inspect videos

When YouTube changes its anti-bot posture (it does, repeatedly -- see the
comments in core.py), the fix goes in one place and every surface gets it.
"""

from .binaries import BinaryStatus, app_root, detect
from .core import (
    EngineError,
    format_timestamp,
    parse_timestamp,
    resolve_runtime_paths,
    strip_ansi,
    ytdlp_version,
)
from .download import (
    ALREADY_DOWNLOADED_MESSAGE,
    DOWNLOAD_COMPLETE_MESSAGE,
    DownloadOptions,
    DownloadResult,
    cache_dir_for,
    cache_root,
    ensure_local_media,
    format_string,
    predict_output_path,
)
# Exported as download_video, NOT as a bare `download`: re-exporting the
# function under that name would overwrite this package's own `download`
# SUBMODULE attribute, so `import ytdl_engine.download as m` would hand
# back the function and every `m.download(...)` call would die with
# AttributeError. (It did, briefly. Hence the rename.)
from .download import download as download_video
from .frames import (
    DEFAULT_MAX_FRAMES,
    DEFAULT_SCENE_THRESHOLD,
    Frame,
    FrameSet,
    extract_frames,
    extract_frames_from_file,
    probe_duration,
)
from .info import (
    MODE_AUDIO_ONLY,
    MODE_VIDEO,
    MODE_VIDEO_ONLY,
    available_heights,
    estimate_download_size,
    extract_info,
    fetch_thumbnail_bytes,
    format_duration,
    format_filesize,
    get_video_info,
    search_youtube,
    selected_height,
    summarize_info,
)
from .transcript import (
    DEFAULT_WHISPER_MODEL,
    Transcript,
    TranscriptSegment,
    get_transcript,
    transcribe_audio_file,
)

__all__ = [
    "ALREADY_DOWNLOADED_MESSAGE",
    "BinaryStatus",
    "DEFAULT_MAX_FRAMES",
    "DEFAULT_SCENE_THRESHOLD",
    "DEFAULT_WHISPER_MODEL",
    "DOWNLOAD_COMPLETE_MESSAGE",
    "DownloadOptions",
    "DownloadResult",
    "EngineError",
    "Frame",
    "FrameSet",
    "MODE_AUDIO_ONLY",
    "MODE_VIDEO",
    "MODE_VIDEO_ONLY",
    "Transcript",
    "TranscriptSegment",
    "ytdlp_version",
    "app_root",
    "available_heights",
    "cache_dir_for",
    "cache_root",
    "detect",
    "download_video",
    "ensure_local_media",
    "estimate_download_size",
    "extract_frames",
    "extract_frames_from_file",
    "extract_info",
    "fetch_thumbnail_bytes",
    "format_duration",
    "format_filesize",
    "format_string",
    "format_timestamp",
    "get_transcript",
    "get_video_info",
    "parse_timestamp",
    "predict_output_path",
    "probe_duration",
    "resolve_runtime_paths",
    "search_youtube",
    "selected_height",
    "strip_ansi",
    "summarize_info",
    "transcribe_audio_file",
]
