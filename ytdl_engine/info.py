"""Metadata: search, video info, and the format/size helpers the GUI's
info chips are built from.

Search goes through yt-dlp's own ``ytsearchN:`` extractor -- no HTML
scraping, no API key, and it rides the same anti-bot handling as
everything else here.
"""

from __future__ import annotations

from typing import Any

import yt_dlp

from .core import (
    EngineError,
    base_opts,
    format_timestamp,
    resolve_runtime_paths,
    run_with_retry,
    tidy_error,
)

MODE_VIDEO = "video"
MODE_VIDEO_ONLY = "video_only"
MODE_AUDIO_ONLY = "audio_only"

FETCH_FAILED_MESSAGE = "Couldn't read that link. Double-check the URL and try again."


# ---------------------------------------------------------------------------
# Chip / size helpers (shared with the GUI)
# ---------------------------------------------------------------------------

def format_duration(info: dict) -> str | None:
    """yt-dlp usually hands back a ready-made ``duration_string``; the
    manual path is the fallback for videos that only carry raw seconds."""
    text = info.get("duration_string")
    if text:
        return str(text)
    seconds = info.get("duration")
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    return format_timestamp(seconds)


def format_filesize(num_bytes: float | None) -> str | None:
    if not num_bytes or num_bytes <= 0:
        return None
    units = ("B", "KB", "MB", "GB")
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit != "GB" else f"{value:.1f} {unit}"
        value /= 1024
    return None


def _stream_size(fmt: dict) -> float:
    """Exact size if yt-dlp knows it, else its estimate, else derive one
    from bitrate x duration (all that's available for some adaptive
    formats)."""
    for key in ("filesize", "filesize_approx"):
        value = fmt.get(key)
        if value:
            return float(value)
    tbr, duration = fmt.get("tbr"), fmt.get("duration")
    if tbr and duration:
        return float(tbr) * 125.0 * float(duration)  # kbit/s -> bytes/s
    return 0.0


def estimate_download_size(raw_info: dict, mode: str, height: int | None) -> float | None:
    """Approximate byte size of what *this app* would actually fetch for
    the given mode/quality.

    Deliberately not ``raw_info["filesize_approx"]``: that describes
    yt-dlp's own default format pick, not the format string this project
    builds (see download.format_string) -- on a 4K video the two can
    differ by well over 100MB, so showing the top-level number next to a
    1080p selection would be plainly wrong. Returns None rather than a
    guess when there is nothing solid to compute from.
    """
    formats = raw_info.get("formats") or []
    if not formats:
        return None
    duration = raw_info.get("duration")
    for fmt in formats:
        fmt.setdefault("duration", duration)

    audio_streams = [
        f for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    video_streams = [
        f for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("height")
    ]
    if height is not None:
        video_streams = [f for f in video_streams if f.get("height") <= height]

    total = 0.0
    if mode in (MODE_VIDEO, MODE_VIDEO_ONLY) and video_streams:
        best_height = max(f["height"] for f in video_streams)
        tier = [f for f in video_streams if f["height"] == best_height]
        total += max((_stream_size(f) for f in tier), default=0.0)
    if mode in (MODE_VIDEO, MODE_AUDIO_ONLY) and audio_streams:
        total += max((_stream_size(f) for f in audio_streams), default=0.0)

    return total or None


def selected_height(raw_info: dict, height: int | None) -> int | None:
    """The height actually delivered for a quality choice -- ``None`` means
    "best available", which resolves to the tallest format on offer."""
    heights = [
        f.get("height") for f in (raw_info.get("formats") or [])
        if f.get("height") and f.get("vcodec") not in (None, "none")
    ]
    if not heights:
        return None
    if height is None:
        return max(heights)
    eligible = [h for h in heights if h <= height]
    return max(eligible) if eligible else None


def available_heights(raw_info: dict) -> list[int]:
    """Descending, deduped list of downloadable video heights."""
    return sorted(
        {
            fmt.get("height")
            for fmt in (raw_info.get("formats") or [])
            if fmt.get("height") and fmt.get("vcodec") not in (None, "none")
        },
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_info(
    url: str,
    js_runtime_path: str | None = None,
    retry: bool = True,
) -> dict:
    """Metadata only (no download). Raises EngineError with a
    user-presentable message on failure."""
    js_runtime_path, _ = resolve_runtime_paths(js_runtime_path, None)
    opts = base_opts(js_runtime_path, skip_download=True)

    def _once() -> dict:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise EngineError(FETCH_FAILED_MESSAGE)
        return info

    if not retry:
        try:
            return _once()
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EngineError(tidy_error(exc, FETCH_FAILED_MESSAGE)) from exc
    return run_with_retry(_once, fallback_message=FETCH_FAILED_MESSAGE)


def fetch_thumbnail_bytes(url: str, js_runtime_path: str | None = None) -> bytes | None:
    """Raw thumbnail bytes, fetched through yt-dlp's own request director.

    Not a bare urllib call: urllib uses Python's default SSL context,
    which on a PyInstaller-frozen macOS build has no access to the system
    trust store and fails every HTTPS request with "certificate verify
    failed". yt-dlp's networking stack already handles this correctly.

    Returns bytes (not a QImage) so this module stays Qt-free -- the GUI
    decodes them itself. Never fatal: a video with unreachable artwork
    should still be downloadable, just without a preview.
    """
    if not url:
        return None
    from yt_dlp.networking.common import Request as YdlRequest

    try:
        with yt_dlp.YoutubeDL(base_opts(js_runtime_path)) as ydl:
            request = YdlRequest(url, headers={"User-Agent": "Mozilla/5.0"})
            with ydl.urlopen(request) as response:
                return response.read()
    except Exception:  # noqa: BLE001 -- artwork is always optional
        return None


def _caption_langs(info: dict, key: str) -> list[str]:
    tracks = info.get(key) or {}
    return sorted(tracks.keys()) if isinstance(tracks, dict) else []


def summarize_info(info: dict) -> dict:
    """The JSON-safe view of a video that the CLI/MCP hand back.

    Deliberately excludes the giant raw format list -- callers that need
    it have the raw dict; callers that are an LLM budgeting context do
    not want 200 format entries.
    """
    duration = info.get("duration")
    chapters = []
    for chapter in info.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        chapters.append(
            {
                "title": chapter.get("title"),
                "start": chapter.get("start_time"),
                "end": chapter.get("end_time"),
                "start_hms": format_timestamp(chapter.get("start_time") or 0),
            }
        )
    manual = _caption_langs(info, "subtitles")
    automatic = _caption_langs(info, "automatic_captions")
    return {
        "id": info.get("id"),
        "title": info.get("title") or "Untitled video",
        "url": info.get("webpage_url") or info.get("original_url"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration_seconds": duration,
        "duration": format_duration(info),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "description": (info.get("description") or "")[:2000] or None,
        "available_heights": available_heights(info),
        "chapters": chapters,
        "has_manual_captions": bool(manual),
        "has_automatic_captions": bool(automatic),
        "caption_languages": sorted(set(manual) | set(automatic)),
        "is_live": bool(info.get("is_live")),
    }


def get_video_info(url: str, js_runtime_path: str | None = None) -> dict:
    """Public entry point: summarized metadata for one video.

    Doesn't retry, matching the GUI's FetchWorker: the failures this hits
    are overwhelmingly deterministic (bad URL, private or removed video),
    so retrying just makes the caller wait through two pointless delays
    before hearing the same answer. Downloads still retry -- one dying
    halfway really is worth another attempt.
    """
    return summarize_info(extract_info(url, js_runtime_path, retry=False))


def search_youtube(
    query: str,
    limit: int = 5,
    js_runtime_path: str | None = None,
) -> list[dict]:
    """Search YouTube via yt-dlp's ytsearchN: extractor.

    Uses extract_flat so this stays one cheap request rather than N full
    per-video extractions -- a search is for *choosing* a video, and
    get_video_info() exists for when the caller has chosen one.
    """
    query = (query or "").strip()
    if not query:
        raise EngineError("Search query is empty.")
    limit = max(1, min(int(limit or 5), 25))

    js_runtime_path, _ = resolve_runtime_paths(js_runtime_path, None)
    opts = base_opts(js_runtime_path, skip_download=True, extract_flat="in_playlist")

    def _once() -> dict:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"ytsearch{limit}:{query}", download=False) or {}

    payload = run_with_retry(_once, fallback_message="YouTube search failed.")

    results: list[dict] = []
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        duration = entry.get("duration")
        results.append(
            {
                "id": video_id,
                "title": entry.get("title") or "Untitled video",
                "url": entry.get("url")
                or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None),
                "channel": entry.get("channel") or entry.get("uploader"),
                "duration_seconds": duration,
                "duration": format_timestamp(duration) if duration else None,
                "view_count": entry.get("view_count"),
                "description": (entry.get("description") or "")[:300] or None,
            }
        )
    return results


def as_video_summary(info: dict) -> dict[str, Any]:
    """Alias kept for readability at call sites that already hold raw info."""
    return summarize_info(info)
