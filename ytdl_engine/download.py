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
from typing import Callable


from .config import Settings, load_settings
from .core import (
    CANCELLED_MESSAGE,
    CancelledError,
    EngineError,
    PermanentError,
    ProgressCallback,
    base_opts,
    ensure_single_video_url,
    format_timestamp,
    resolve_runtime_paths,
    run_with_retry,
    strip_ansi,
)
from .info import MODE_AUDIO_ONLY, MODE_VIDEO, MODE_VIDEO_ONLY

DOWNLOAD_COMPLETE_MESSAGE = "Download complete."
ALREADY_DOWNLOADED_MESSAGE = "Already downloaded -- no new file was saved."

LIVE_STREAM_MESSAGE = (
    "That's a live stream, which has no end to download to. Wait until "
    "it finishes and YouTube publishes the recording, then download that."
)


def check_limits(
    info: dict,
    settings: Settings | None = None,
    mode: str | None = None,
    height: int | None = None,
) -> None:
    """Enforce the optional duration/size guardrails before downloading.

    Both are off unless the user turns them on (see config.Settings), so
    this is a no-op for most people. Raises EngineError naming the limit
    and how to change it -- a refusal the user can't act on is worse than
    no refusal.

    Also refuses a live stream outright, regardless of the limits. A live
    stream reports no duration and no filesize, so both guardrails are
    structurally unable to fire on the one thing they would most want to
    stop: the download simply never terminates.

    mode/height let the size check measure the format THIS app would
    actually fetch (estimate_download_size) rather than
    ``filesize_approx``, which describes yt-dlp's own default pick. On a
    4K video those differ by hundreds of MB, so a 1080p download used to
    be refused against a number that had nothing to do with it. Omitting
    them falls back to the top-level estimate.
    """
    settings = settings or load_settings()

    if info.get("is_live"):
        raise PermanentError(LIVE_STREAM_MESSAGE)

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
        size = None
        if mode is not None:
            from .info import estimate_download_size

            size = estimate_download_size(info, mode, height)
        if not size:
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
    # Which caption languages to fetch when include_subtitles is set.
    # None = the user's transcript_language setting, filled in by
    # download(); an explicit tuple overrides it for one call.
    subtitle_languages: tuple[str, ...] | None = None


@dataclass
class DownloadResult:
    message: str
    real_download: bool
    path: Path | None
    info: dict


def format_string(mode: str, height: int | None) -> str:
    """yt-dlp format selector for a mode/quality pair.

    Unknown modes raise rather than falling through. This used to end in
    a bare ``return "bestaudio/best"``, so any unrecognised mode string
    quietly selected an audio-only stream -- while the mp3 postprocessor
    and the predicted filename below are both gated on an exact match
    with MODE_AUDIO_ONLY. The result was an audio file with no
    conversion, reported to the caller as an .mp4 that did not exist.
    Config now rejects a bad default_mode too (config._ALLOWED_VALUES);
    this is the backstop for anything that reaches here another way.
    """
    height_filter = f"[height<={height}]" if height else ""
    if mode == MODE_VIDEO:
        return f"bv*{height_filter}+ba/b{height_filter}"
    if mode == MODE_VIDEO_ONLY:
        return f"bv*{height_filter}"
    if mode == MODE_AUDIO_ONLY:
        return "bestaudio/best"
    raise PermanentError(
        f"Unknown download mode {mode!r}. Expected one of: "
        f"{MODE_VIDEO}, {MODE_VIDEO_ONLY}, {MODE_AUDIO_ONLY}."
    )


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


def _build_progress_hook(
    on_progress: ProgressCallback | None,
    should_cancel: Callable[[], bool] | None = None,
):
    def hook(d: dict) -> None:
        # Checked first, and before the on_progress early-out: a caller
        # may want cancellation without wanting progress callbacks.
        #
        # A progress hook is the only place a cancel can be noticed.
        # yt-dlp has no cancel API and the download runs as one blocking
        # call, so there is nothing to poll from outside -- but the hook
        # fires every few hundred milliseconds with the download's own
        # thread on the stack. DownloadCancelled is yt-dlp's own
        # exception for exactly this: it propagates out of the downloader
        # instead of being caught and turned into a retryable
        # DownloadError the way a generic exception would be.
        if should_cancel is not None and should_cancel():
            from yt_dlp.utils import DownloadCancelled

            raise DownloadCancelled(CANCELLED_MESSAGE)
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


def _reject_live_filter(info_dict: dict, *, incomplete: bool = False) -> None:
    """yt-dlp match_filter that refuses live streams.

    Hooked in here rather than checked in download() because yt-dlp calls
    it with the info dict it has just fetched -- so a live stream is
    caught with no extra network round trip, on the one pass the download
    was going to make anyway. Raising (rather than returning a skip
    reason, match_filter's normal signal) is deliberate: a skip makes
    yt-dlp return an info dict with no requested_downloads, which
    download() cannot distinguish from a successful run and would report
    as "Download complete." with no file.
    """
    if info_dict.get("is_live"):
        raise PermanentError(LIVE_STREAM_MESSAGE)
    return None


def build_ydl_opts(
    o: DownloadOptions,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    opts = base_opts(
        o.js_runtime_path,
        format=format_string(o.mode, o.height),
        outtmpl=str(Path(o.output_dir) / "%(title)s.%(ext)s"),
        progress_hooks=[_build_progress_hook(on_progress, should_cancel)],
        match_filter=_reject_live_filter,
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
        # writeautomaticsub as well, not instead: only a small minority of
        # videos carry human-authored subtitles, so asking for
        # writesubtitles alone made the Subtitles checkbox a silent no-op
        # on most of YouTube -- no file, no warning, no error. Requesting
        # both means "human subtitles if they exist, auto-captions
        # otherwise", which is what a user ticking the box means.
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = list(o.subtitle_languages or ("en",))
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
    should_cancel: Callable[[], bool] | None = None,
) -> DownloadResult:
    """Download one video. Raises EngineError (already user-presentable)
    after all retry attempts are exhausted.

    enforce_limits=False is for callers that already showed the user what
    they were about to fetch and got a yes -- the GUI, where the size is
    on screen next to the button. Agent surfaces leave it on.

    should_cancel is polled from the progress hook; when it returns True
    the download stops and CancelledError is raised. See
    _build_progress_hook for why a callable rather than a flag object.
    """
    settings = settings or load_settings()
    o = options
    # Free, and before anything else: a playlist or channel URL would
    # otherwise expand into every video behind it (see
    # ensure_single_video_url).
    ensure_single_video_url(o.url)
    if o.subtitle_languages is None:
        o.subtitle_languages = (settings.transcript_language or "en",)
    o.js_runtime_path, o.ffmpeg_location = resolve_runtime_paths(
        o.js_runtime_path, o.ffmpeg_location
    )
    Path(o.output_dir).mkdir(parents=True, exist_ok=True)
    ydl_opts = build_ydl_opts(o, on_progress, should_cancel)

    if enforce_limits and (settings.max_duration_minutes or settings.max_filesize_mb):
        # Metadata-only pass first: refusing *after* pulling 2GB would
        # defeat the entire point of a size limit. Live streams are
        # refused on the download pass instead, by the match_filter in
        # build_ydl_opts, so that costs nothing when no limit is set.
        from .info import extract_info

        check_limits(
            extract_info(o.url, o.js_runtime_path, retry=False),
            settings,
            mode=o.mode,
            height=o.height,
        )

    def _retry_notice(next_attempt: int, max_attempts: int) -> None:
        message = f"Retrying… (attempt {next_attempt} of {max_attempts})"
        if on_retry:
            on_retry(0.0, message)
        elif on_progress:
            on_progress(0.0, message)

    def _once() -> dict:
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled

        if should_cancel is not None and should_cancel():
            # Cancelled between attempts, or before the first one: never
            # start a download the caller has already called off.
            raise CancelledError(CANCELLED_MESSAGE)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # extract_info(download=True), not the simpler download() --
                # download() only returns a retcode, but extract_info hands
                # back the processed info dict, the only place yt-dlp records
                # whether it *actually* wrote a file (requested_downloads /
                # __real_download below). Without that, a silently skipped
                # "already downloaded" file and a real one both look like
                # success.
                return ydl.extract_info(o.url, download=True) or {}
        except DownloadCancelled as exc:
            # Translated at the boundary so no caller outside this module
            # has to know yt-dlp's exception types, and so run_with_retry
            # sees a CancelledError and stops rather than retrying.
            raise CancelledError(CANCELLED_MESSAGE) from exc

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

# Appended to whatever cache_dir says, always. See cache_root.
CACHE_DIR_NAME = "ytdl-agent-cache"
# Written inside the cache root the first time it is created. prune/clear
# refuse to touch a directory that doesn't have it.
CACHE_MARKER_NAME = ".ytdl-agent-cache"
_CACHE_MARKER_TEXT = (
    "Working copies of videos fetched for transcripts and frame\n"
    "extraction. Safe to delete. This app prunes and clears ONLY\n"
    "directories containing this marker file.\n"
)


def cache_root(settings: Settings | None = None) -> Path:
    """Scratch space for agent-driven work.

    Defaults to the system temp dir, not the user's Downloads folder:
    these are working copies an agent pulled in order to read a video,
    not files the user asked to keep. Override with the ``cache_dir``
    setting if temp is small or on a slow disk. The GUI's own downloads
    are unaffected either way.

    CACHE_DIR_NAME is appended to ``cache_dir`` unconditionally, and that
    is a safety property, not tidiness. prune_cache() deletes whole
    subdirectories of this root and clear_cache() empties it, so when
    this function returned ``cache_dir`` verbatim, the setting was a
    recursive-delete primitive aimed by a single string: pointing it at a
    documents folder and setting a small cache_max_mb turned the next
    transcript call into "rmtree every subfolder in Documents, oldest
    first". Appending a segment means the worst a bad value can do is
    create and manage a scratch folder in an odd place.
    """
    settings = settings or load_settings()
    base = (
        Path(settings.cache_dir).expanduser()
        if settings.cache_dir
        else Path(tempfile.gettempdir())
    )
    root = base / CACHE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    marker = root / CACHE_MARKER_NAME
    if not marker.exists():
        try:
            marker.write_text(_CACHE_MARKER_TEXT, encoding="utf-8")
        except OSError:
            pass  # a cache that can't be marked still works; it just won't prune
    return root


def _is_managed_cache(root: Path) -> bool:
    """Whether this directory is one we created and may delete inside.

    Belt-and-braces behind cache_root's appended segment: a caller that
    somehow hands over an arbitrary path still cannot get a deletion out
    of prune_cache or clear_cache.
    """
    return root.name == CACHE_DIR_NAME and (root / CACHE_MARKER_NAME).exists()


def cache_dir_for(video_id: str, settings: Settings | None = None) -> Path:
    safe = "".join(c for c in (video_id or "video") if c.isalnum() or c in "-_")[:64]
    path = cache_root(settings) / (safe or "video")
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_variant_dir(
    video_id: str,
    mode: str,
    height: int | None,
    settings: Settings | None = None,
) -> Path:
    """Folder for one video at one specific mode/quality.

    The quality is part of the path because cached_media() used to match
    on file suffix alone: extract frames at 480p and then ask for 1080p
    frames of the same video, and the 480p copy was returned as a hit --
    silently, with a path that looked right. Keying the folder means a
    different quality is a different cache entry, and prune_cache still
    evicts the whole per-video folder (every variant together) so a
    partial eviction can't leave frames pointing at a deleted video.
    """
    variant = f"{mode}-{height if height else 'best'}"
    safe = "".join(c for c in variant if c.isalnum() or c in "-_")[:32]
    path = cache_dir_for(video_id, settings) / (safe or "default")
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
    if not _is_managed_cache(root):
        return 0
    freed = cache_size_bytes(settings)
    for child in root.iterdir():
        if child.name == CACHE_MARKER_NAME:
            continue
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError:
            continue
    return freed


def prune_cache(settings: Settings | None = None, keep: Path | None = None) -> int:
    """Drop oldest video folders until the cache fits ``cache_max_mb``.

    Without this the cache grows forever -- every video an agent ever
    touched stays on disk. Whole per-video folders are evicted (not
    individual files) so a partially-pruned video can't leave a video
    file without its frames. Returns bytes freed; a None limit disables
    pruning entirely.

    ``keep`` is never evicted. Without it, a single video larger than
    cache_max_mb was the only eviction candidate on its own first run, so
    the folder that had just been filled got deleted immediately and the
    caller was told "Download finished but no media file was found on
    disk" -- a full download spent and discarded, with an error naming
    the wrong cause. Staying over the limit is the better failure: the
    limit is there to stop unbounded growth, not to make the current
    call impossible.
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
    if not _is_managed_cache(root):
        return 0
    keep_resolved = None
    if keep is not None:
        try:
            keep_resolved = Path(keep).resolve()
        except OSError:
            keep_resolved = None

    folders = []
    for child in root.iterdir():
        try:
            if not child.is_dir():
                continue
            if keep_resolved is not None and child.resolve() == keep_resolved:
                continue
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


def cached_media(
    video_id: str,
    suffixes: tuple[str, ...],
    folder: Path | None = None,
) -> Path | None:
    """An already-downloaded working copy for this video, if any.

    ``folder`` narrows the search to one mode/quality variant -- see
    cache_variant_dir for why matching on suffix across the whole
    per-video folder returned the wrong quality. Omitting it searches the
    per-video folder itself, which is where pre-variant caches and
    directly-written files live.
    """
    target = folder if folder is not None else cache_dir_for(video_id)
    for entry in sorted(target.iterdir()) if target.exists() else []:
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
    there. Returns the media path.

    "Usable" means same video AND same mode/quality -- a 480p copy is not
    a hit for a 1080p request. See cache_variant_dir.
    """
    settings = settings or load_settings()
    video_suffixes = (".mp4", ".mkv", ".webm")
    audio_suffixes = (".m4a", ".mp3", ".wav", ".opus", ".webm")
    suffixes = audio_suffixes if mode == MODE_AUDIO_ONLY else video_suffixes
    variant_dir = cache_variant_dir(video_id, mode, height, settings)

    existing = cached_media(video_id, suffixes, folder=variant_dir)
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
            output_dir=variant_dir,
            ffmpeg_location=ffmpeg_location,
            js_runtime_path=js_runtime_path,
            force_overwrite=False,
        ),
        on_progress=on_progress,
        settings=settings,
    )
    # Prune AFTER writing, not before: evicting to make room for a file
    # whose real size isn't known yet would be guesswork. keep= protects
    # the folder we have just filled -- it is the whole point of the call.
    prune_cache(settings, keep=cache_dir_for(video_id, settings))
    if result.path and Path(result.path).exists():
        return Path(result.path)
    fallback = cached_media(video_id, suffixes, folder=variant_dir)
    if fallback:
        return fallback
    raise EngineError("Download finished but no media file was found on disk.")
