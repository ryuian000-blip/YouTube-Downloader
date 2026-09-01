"""Downloading: the format-string logic, the retry loop, and the
"did a file actually get written" check the GUI's already-downloaded
warning depends on.

The GUI (app/workers.py), the CLI, and the MCP server all call
``download()`` -- so a YouTube change that breaks downloading is fixed
once, here.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path


from .config import Settings, load_settings
from .core import (
    EngineError,
    ProgressCallback,
    base_opts,
    format_timestamp,
    resolve_runtime_paths,
    run_with_retry,
    strip_ansi,
)
from .info import MODE_AUDIO_ONLY, MODE_VIDEO, MODE_VIDEO_ONLY

DOWNLOAD_COMPLETE_MESSAGE = "Download complete."
ALREADY_DOWNLOADED_MESSAGE = "Already downloaded -- no new file was saved."


def check_limits(info: dict, settings: Settings | None = None) -> None:
    """Enforce the optional duration/size guardrails before downloading.

    Both are off unless the user turns them on (see config.Settings), so
    this is a no-op for most people. Raises EngineError naming the limit
    and how to change it -- a refusal the user can't act on is worse than
    no refusal.
    """
    settings = settings or load_settings()

    limit_minutes = settings.max_duration_minutes
    duration = info.get("duration")
    if limit_minutes and isinstance(duration, (int, float)) and duration > limit_minutes * 60:
        raise EngineError(
            f"Video is {format_timestamp(duration)}, over the "
            f"{limit_minutes}-minute limit (max_duration_minutes). "
            "Raise or clear that setting to download it."
        )

    limit_mb = settings.max_filesize_mb
    if limit_mb:
        size = info.get("filesize_approx") or info.get("filesize")
        if isinstance(size, (int, float)) and size > limit_mb * 1024 * 1024:
            raise EngineError(
                f"Video is about {size / (1024 * 1024):.0f}MB, over the "
                f"{limit_mb}MB limit (max_filesize_mb). "
                "Raise or clear that setting to download it."
            )


@dataclass
class DownloadOptions:
    url: str
    mode: str  # MODE_VIDEO | MODE_VIDEO_ONLY | MODE_AUDIO_ONLY
    height: int | None  # None = "Best available"
    audio_format: str  # "mp3" | "m4a" | "wav"
    include_subtitles: bool
    embed_thumbnail: bool
    output_dir: Path
    ffmpeg_location: str | None
    js_runtime_path: str | None
    # True once the caller has confirmed "yes, download it again" past the
    # already-downloaded warning -- without this, yt-dlp silently skips
    # writing a file that already exists at the destination and *still*
    # reports success, which is exactly the confusing behavior the
    # warning exists to head off.
    force_overwrite: bool = False


@dataclass
class DownloadResult:
    message: str
    real_download: bool
    path: Path | None
    info: dict


def format_string(mode: str, height: int | None) -> str:
    height_filter = f"[height<={height}]" if height else ""
    if mode == MODE_VIDEO:
        return f"bv*{height_filter}+ba/b{height_filter}"
    if mode == MODE_VIDEO_ONLY:
        return f"bv*{height_filter}"
    return "bestaudio/best"


def predict_output_path(
    raw_info: dict, output_dir: Path, mode: str, audio_format: str
) -> Path:
    """Best-effort prediction of where a download would land, using
    yt-dlp's own filename templating against an info dict already
    retrieved -- no network call, so it's cheap enough to call before the
    user confirms a download, purely to check "does this already exist".

    yt-dlp itself already refuses to redownload (and re-report success on)
    an existing file -- see ``download()``'s use of requested_downloads /
    __real_download for the authoritative, after-the-fact version of this
    same check. This one exists so callers can warn *before* starting a
    download that will turn out to do nothing.

    Not guaranteed exact (yt-dlp has narrow special cases, e.g. forcing an
    .mkv container when embedding a thumbnail into a webm), but right for
    the two extensions this project actually produces.
    """
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        base = ydl.prepare_filename(raw_info)
    final_ext = audio_format if mode == MODE_AUDIO_ONLY else "mp4"
    return Path(base).with_suffix(f".{final_ext}")


def _build_progress_hook(on_progress: ProgressCallback | None):
    def hook(d: dict) -> None:
        if on_progress is None:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            pct = max(0.0, min(100.0, downloaded / total * 100)) if total else 0.0
            speed = strip_ansi(d.get("_speed_str", "")).strip()
            eta = strip_ansi(d.get("_eta_str", "")).strip()
            label = f"Downloading… {pct:.0f}%"
            if speed:
                label += f"  ({speed}"
                label += f", ETA {eta})" if eta else ")"
            on_progress(pct, label)
        elif status == "finished":
            on_progress(99.0, "Processing…")

    return hook


def build_ydl_opts(o: DownloadOptions, on_progress: ProgressCallback | None = None) -> dict:
    opts = base_opts(
        o.js_runtime_path,
        format=format_string(o.mode, o.height),
        outtmpl=str(Path(o.output_dir) / "%(title)s.%(ext)s"),
        progress_hooks=[_build_progress_hook(on_progress)],
    )
    if o.ffmpeg_location:
        opts["ffmpeg_location"] = o.ffmpeg_location
    if o.force_overwrite:
        opts["overwrites"] = True

    postprocessors: list[dict] = []
    if o.mode in (MODE_VIDEO, MODE_VIDEO_ONLY):
        opts["merge_output_format"] = "mp4"
    if o.mode == MODE_AUDIO_ONLY:
        postprocessors.append(
            {"key": "FFmpegExtractAudio", "preferredcodec": o.audio_format}
        )
    if o.embed_thumbnail:
        opts["writethumbnail"] = True
        postprocessors.append({"key": "EmbedThumbnail"})
    if o.include_subtitles:
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = ["en"]
        if o.mode != MODE_AUDIO_ONLY:
            postprocessors.append({"key": "FFmpegEmbedSubtitle"})
    opts["postprocessors"] = postprocessors
    return opts


def _final_path(result: dict) -> Path | None:
    """Where the finished file actually landed.

    requested_downloads[].filepath is yt-dlp's own post-postprocessing
    answer -- more reliable than re-deriving the name, which would have to
    replicate every container/extension special case.
    """
    for entry in (result or {}).get("requested_downloads") or []:
        path = entry.get("filepath") or entry.get("_filename")
        if path:
            return Path(path)
    path = (result or {}).get("filepath")
    return Path(path) if path else None


def download(
    options: DownloadOptions,
    on_progress: ProgressCallback | None = None,
    on_retry: ProgressCallback | None = None,
    settings: Settings | None = None,
    enforce_limits: bool = True,
) -> DownloadResult:
    """Download one video. Raises EngineError (already user-presentable)
    after all retry attempts are exhausted.

    enforce_limits=False is for callers that already showed the user what
    they were about to fetch and got a yes -- the GUI, where the size is
    on screen next to the button. Agent surfaces leave it on.
    """
    settings = settings or load_settings()
    o = options
    o.js_runtime_path, o.ffmpeg_location = resolve_runtime_paths(
        o.js_runtime_path, o.ffmpeg_location
    )
    Path(o.output_dir).mkdir(parents=True, exist_ok=True)
    ydl_opts = build_ydl_opts(o, on_progress)

    if enforce_limits and (settings.max_duration_minutes or settings.max_filesize_mb):
        # Metadata-only pass first: refusing *after* pulling 2GB would
        # defeat the entire point of a size limit.
        from .info import extract_info

        check_limits(extract_info(o.url, o.js_runtime_path, retry=False), settings)

    def _retry_notice(next_attempt: int, max_attempts: int) -> None:
        message = f"Retrying… (attempt {next_attempt} of {max_attempts})"
        if on_retry:
            on_retry(0.0, message)
        elif on_progress:
            on_progress(0.0, message)

    def _once() -> dict:
        import yt_dlp

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # extract_info(download=True), not the simpler download() --
            # download() only returns a retcode, but extract_info hands
            # back the processed info dict, the only place yt-dlp records
            # whether it *actually* wrote a file (requested_downloads /
            # __real_download below). Without that, a silently skipped
            # "already downloaded" file and a real one both look like
            # success.
            return ydl.extract_info(o.url, download=True) or {}

    result = run_with_retry(
        _once, on_retry=_retry_notice, fallback_message="Download failed."
    )

    requested = result.get("requested_downloads") or []
    if requested:
        real_download = any(d.get("__real_download") for d in requested)
    else:
        # No requested_downloads entries at all is unusual for a
        # successful single-video run; assume a real download rather than
        # surface a confusing "already downloaded" that may not be true.
        real_download = bool(result.get("__real_download", True))

    if real_download or o.force_overwrite:
        message = DOWNLOAD_COMPLETE_MESSAGE
    else:
        message = ALREADY_DOWNLOADED_MESSAGE

    return DownloadResult(
        message=message,
        real_download=real_download,
        path=_final_path(result),
        info=result,
    )


# ---------------------------------------------------------------------------
# Cache (used by the CLI/MCP so transcript + frames don't fetch twice)
# ---------------------------------------------------------------------------

def cache_root(settings: Settings | None = None) -> Path:
    """Scratch space for agent-driven work.

    Defaults to the system temp dir, not the user's Downloads folder:
    these are working copies an agent pulled in order to read a video,
    not files the user asked to keep. Override with the ``cache_dir``
    setting if temp is small or on a slow disk. The GUI's own downloads
    are unaffected either way.
    """
    settings = settings or load_settings()
    root = (
        Path(settings.cache_dir).expanduser()
        if settings.cache_dir
        else Path(tempfile.gettempdir()) / "ytdl-agent-cache"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_dir_for(video_id: str, settings: Settings | None = None) -> Path:
    safe = "".join(c for c in (video_id or "video") if c.isalnum() or c in "-_")[:64]
    path = cache_root(settings) / (safe or "video")
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_size_bytes(settings: Settings | None = None) -> int:
    root = cache_root(settings)
    total = 0
    for entry in root.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def clear_cache(settings: Settings | None = None) -> int:
    """Delete every cached working copy. Returns bytes reclaimed."""
    import shutil

    root = cache_root(settings)
    freed = cache_size_bytes(settings)
    for child in root.iterdir():
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError:
            continue
    return freed


def prune_cache(settings: Settings | None = None) -> int:
    """Drop oldest video folders until the cache fits ``cache_max_mb``.

    Without this the cache grows forever -- every video an agent ever
    touched stays on disk. Whole per-video folders are evicted (not
    individual files) so a partially-pruned video can't leave a video
    file without its frames. Returns bytes freed; a None limit disables
    pruning entirely.
    """
    import shutil

    settings = settings or load_settings()
    limit_mb = settings.cache_max_mb
    if not limit_mb:
        return 0
    limit = limit_mb * 1024 * 1024
    total = cache_size_bytes(settings)
    if total <= limit:
        return 0

    root = cache_root(settings)
    folders = []
    for child in root.iterdir():
        try:
            if child.is_dir():
                size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                folders.append((child.stat().st_mtime, size, child))
        except OSError:
            continue
    folders.sort(key=lambda item: item[0])  # oldest first

    freed = 0
    for _mtime, size, folder in folders:
        if total - freed <= limit:
            break
        try:
            shutil.rmtree(folder)
            freed += size
        except OSError:
            continue
    return freed


def cached_media(video_id: str, suffixes: tuple[str, ...]) -> Path | None:
    """An already-downloaded working copy for this video, if any."""
    folder = cache_dir_for(video_id)
    for entry in sorted(folder.iterdir()) if folder.exists() else []:
        if entry.is_file() and entry.suffix.lower() in suffixes and entry.stat().st_size > 0:
            return entry
    return None


def ensure_local_media(
    url: str,
    *,
    video_id: str,
    mode: str,
    height: int | None,
    audio_format: str = "m4a",
    js_runtime_path: str | None = None,
    ffmpeg_location: str | None = None,
    on_progress: ProgressCallback | None = None,
    settings: Settings | None = None,
) -> Path:
    """Download into the agent cache unless a usable copy is already
    there. Returns the media path."""
    settings = settings or load_settings()
    video_suffixes = (".mp4", ".mkv", ".webm")
    audio_suffixes = (".m4a", ".mp3", ".wav", ".opus", ".webm")
    suffixes = audio_suffixes if mode == MODE_AUDIO_ONLY else video_suffixes

    existing = cached_media(video_id, suffixes)
    if existing:
        return existing

    result = download(
        DownloadOptions(
            url=url,
            mode=mode,
            height=height,
            audio_format=audio_format,
            include_subtitles=False,
            embed_thumbnail=False,
            output_dir=cache_dir_for(video_id, settings),
            ffmpeg_location=ffmpeg_location,
            js_runtime_path=js_runtime_path,
            force_overwrite=False,
        ),
        on_progress=on_progress,
        settings=settings,
    )
    # Prune AFTER writing, not before: evicting to make room for a file
    # whose real size isn't known yet would be guesswork, and the newest
    # folder (this one) is last to be evicted anyway.
    prune_cache(settings)
    if result.path and Path(result.path).exists():
        return Path(result.path)
    fallback = cached_media(video_id, suffixes)
    if fallback:
        return fallback
    raise EngineError("Download finished but no media file was found on disk.")
