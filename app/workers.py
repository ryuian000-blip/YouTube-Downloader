"""Background QThread workers.

Never touch a Qt widget from these -- everything crosses back to the GUI
thread via signal/slot (Qt queues the delivery automatically because the
receiving QObject, MainWindow, lives on the main thread). This is the
direct equivalent of the original Tkinter app's ``self.after(0, ...)``
pattern, per the rebuild brief.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
import yt_dlp
from yt_dlp.networking.common import Request as YdlRequest


# Deliberately empty: no player_client pin. This app used to force
# ["android_vr", "android"] because, as of July 2026, yt-dlp's default
# clients 403'd without a PO token while android_vr handed back the full
# un-gated quality ladder. YouTube then flipped that on its head: its
# SABR-only streaming rollout (yt-dlp issue #12482) broke the android
# clients' plain https formats -- downloads would start, reach 30%+, and
# die with a mid-stream 403 that no retry could fix -- while the
# *maintained defaults* in current yt-dlp (visionos etc., picked by
# nightly) work, provided the JS challenge solver is available (deno +
# the yt-dlp-ejs script package, see requirements.txt). Verified against
# a real previously-failing video: pinned clients 403 at ~37%, defaults
# complete at 1080p. Lesson encoded here: client pins rot as YouTube's
# posture shifts -- let yt-dlp's own actively-maintained selection rule,
# and keep yt-dlp itself current (nightly channel, see build scripts).
_EXTRACTOR_ARGS: dict = {}


# ---------------------------------------------------------------------------
# Fetch (metadata only)
# ---------------------------------------------------------------------------

@dataclass
class VideoInfo:
    title: str
    heights: list[int]  # descending, deduped
    # QImage, not QPixmap: QPixmap depends on the platform's paint engine
    # and isn't safe to construct off the GUI thread, but this dataclass
    # is built entirely on FetchWorker's background thread and handed
    # across via a Qt signal. QImage has no such restriction -- it's just
    # pixel data -- so the conversion to QPixmap happens later, in
    # MainWindow, once this has crossed back to the GUI thread.
    thumbnail: QImage | None = field(repr=False, default=None)
    raw: dict = field(repr=False, default_factory=dict)


def _fetch_thumbnail(ydl: yt_dlp.YoutubeDL, url: str | None) -> QImage | None:
    if not url:
        return None
    try:
        # Goes through yt-dlp's own request director (ydl.urlopen), not a
        # bare urllib.request.urlopen -- a plain urllib call uses Python's
        # default SSL context, which on a PyInstaller-frozen macOS build has
        # no access to the system trust store and fails every HTTPS request
        # with "certificate verify failed: unable to get local issuer
        # certificate". yt-dlp's own networking stack already handles this
        # correctly (that's why the info/title fetch above works fine on
        # the same machine), so reusing it here fixes the thumbnail too.
        request = YdlRequest(url, headers={"User-Agent": "Mozilla/5.0"})
        with ydl.urlopen(request) as response:
            data = response.read()
    except Exception:
        # Never fatal -- a video with unreachable thumbnail artwork should
        # still be fetchable and downloadable, just without a preview.
        return None
    image = QImage.fromData(data)
    return image if not image.isNull() else None


class FetchWorker(QThread):
    succeeded = Signal(object)   # VideoInfo
    failed = Signal(str)

    def __init__(self, url: str, js_runtime_path: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._js_runtime_path = js_runtime_path

    def run(self) -> None:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,  # a video ID + list= (e.g. auto "Radio" mixes)
                                  # would otherwise make yt-dlp try to resolve
                                  # a dynamically-generated playlist and hang.
            "no_color": True,  # see the matching option in DownloadWorker.run --
                                # nothing here currently surfaces styled text to
                                # the UI, but yt-dlp's ANSI-detection is
                                # env/terminal-dependent (see that comment), so
                                # this is cheap insurance against ever needing
                                # the same fix twice.
            "extractor_args": _EXTRACTOR_ARGS,
        }
        if self._js_runtime_path:
            # Without this, extract_info() fails on most real YouTube videos
            # with "The page needs to be reloaded" -- yt-dlp needs a JS
            # runtime to solve YouTube's nsig/JS challenge even just to list
            # formats, not only to download. DownloadWorker already wires
            # this through; fetch needs the same runtime for the same reason.
            opts["js_runtimes"] = {"deno": {"path": self._js_runtime_path}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self._url, download=False)
                if not info:
                    self.failed.emit("Couldn't read that link. Double-check the URL and try again.")
                    return
                thumbnail = _fetch_thumbnail(ydl, info.get("thumbnail"))
        except Exception:
            self.failed.emit("Couldn't read that link. Double-check the URL and try again.")
            return

        heights = sorted(
            {
                fmt.get("height")
                for fmt in info.get("formats", [])
                if fmt.get("height") and fmt.get("vcodec") not in (None, "none")
            },
            reverse=True,
        )
        title = info.get("title") or "Untitled video"
        self.succeeded.emit(
            VideoInfo(title=title, heights=heights, thumbnail=thumbnail, raw=info)
        )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

MODE_VIDEO = "video"
MODE_VIDEO_ONLY = "video_only"
MODE_AUDIO_ONLY = "audio_only"


# ---------------------------------------------------------------------------
# Info-chip helpers (duration / resolution / size shown next to the title)
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
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


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
    from bitrate x duration (which is all that's available for some
    adaptive formats)."""
    for key in ("filesize", "filesize_approx"):
        value = fmt.get(key)
        if value:
            return float(value)
    tbr, duration = fmt.get("tbr"), fmt.get("duration")
    if tbr and duration:
        return float(tbr) * 125.0 * float(duration)  # kbit/s -> bytes/s
    return 0.0


def estimate_download_size(raw_info: dict, mode: str, height: int | None) -> float | None:
    """Approximate byte size of what *this app* would actually fetch for the
    given mode/quality.

    Deliberately not ``raw_info["filesize_approx"]``: that describes
    yt-dlp's own default format pick, which is not the format string this
    app builds (see DownloadWorker._format_string) -- on a 4K video the two
    can differ by well over 100MB, so showing the top-level number next to
    a 1080p selection would be plainly wrong. Returns None rather than a
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
    "Best available", which resolves to the tallest format on offer."""
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


def predict_output_path(raw_info: dict, output_dir: Path, mode: str, audio_format: str) -> Path:
    """Best-effort prediction of where a download would land, using
    yt-dlp's own filename templating against the info dict FetchWorker
    already retrieved -- no network call, so it's cheap enough to call
    before the user even confirms a download, purely to check "does this
    already exist."

    yt-dlp itself already refuses to redownload (and re-report success on)
    an existing file -- see DownloadWorker.run()'s use of
    ``requested_downloads`` / ``__real_download`` for the authoritative,
    after-the-fact version of this same check. This one exists so the app
    can warn *before* starting a download that will turn out to do
    nothing, rather than only explaining it afterward.

    Not guaranteed exact (yt-dlp has a couple of narrow special cases,
    e.g. forcing an .mkv container when embedding a thumbnail into a
    webm), but right for the two extensions this app actually produces:
    "mp4" for video/video-only (merge_output_format is always mp4 here),
    and whatever the user picked for audio-only.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        base = ydl.prepare_filename(raw_info)
    final_ext = audio_format if mode == MODE_AUDIO_ONLY else "mp4"
    return Path(base).with_suffix(f".{final_ext}")


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
    # True once the user has confirmed "yes, download it again" past the
    # already-downloaded warning -- without this, yt-dlp silently skips
    # writing a file that already exists at the destination and *still*
    # reports success, which is exactly the confusing behavior this
    # feature exists to head off.
    force_overwrite: bool = False


# Matches ANSI CSI sequences like "\x1b[0;32m" / "\x1b[0m". Belt-and-suspenders
# alongside the "no_color" ydl_opt below: that stops yt-dlp from generating
# colored _speed_str/_eta_str in the first place (its ANSI auto-detection is
# unreliable for a frozen, windowed app with no real console), but stripping
# defensively here means a stray escape code -- from a differently-behaved
# yt-dlp version, say -- shows up as nothing rather than as the literal
# garbled control characters Qt renders when it doesn't recognize them.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


# NOTE: a `_is_drm_protected()` probe briefly lived here (checking the
# "tv" client's player response after retries were exhausted). Removed on
# purpose, not lost: the "This video is DRM protected" error it keyed on
# turned out to be SABR-rollout noise -- YouTube serves *some* clients a
# DRM-only manifest for videos that other clients stream (and download)
# plainly, so the probe flagged videos as "copy-protected" that current
# yt-dlp downloads fine. Don't re-add a client-specific DRM heuristic.


class DownloadWorker(QThread):
    progress = Signal(float, str)   # percent 0-100, status text
    succeeded = Signal(str)         # final message
    failed = Signal(str)

    # yt-dlp's own retries/fragment_retries (defaults: 10 each) retry
    # individual HTTP requests against the format URLs already resolved by
    # this extract_info() call -- they don't help when the resolved URLs
    # themselves are the problem, e.g. a signed googlevideo URL that 403s
    # from a transient anti-bot flag or expires before the merge/postprocess
    # step finishes. A fresh extract_info() call resolves brand-new URLs,
    # which is what manually clicking Download again already does -- this
    # automates exactly that instead of making the user notice and retry.
    _MAX_ATTEMPTS = 3
    _RETRY_DELAY_SECONDS = 2

    def __init__(self, options: DownloadOptions, parent=None) -> None:
        super().__init__(parent)
        self._opts = options

    def _format_string(self) -> str:
        o = self._opts
        height_filter = f"[height<={o.height}]" if o.height else ""
        if o.mode == MODE_VIDEO:
            return f"bv*{height_filter}+ba/b{height_filter}"
        if o.mode == MODE_VIDEO_ONLY:
            return f"bv*{height_filter}"
        return "bestaudio/best"

    def _progress_hook(self, d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                pct = max(0.0, min(100.0, downloaded / total * 100))
            else:
                pct = 0.0
            speed = _strip_ansi(d.get("_speed_str", "")).strip()
            eta = _strip_ansi(d.get("_eta_str", "")).strip()
            label = f"Downloading… {pct:.0f}%"
            if speed:
                label += f"  ({speed}"
                label += f", ETA {eta})" if eta else ")"
            self.progress.emit(pct, label)
        elif status == "finished":
            self.progress.emit(99.0, "Processing…")

    def run(self) -> None:
        o = self._opts
        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": self._format_string(),
            "outtmpl": str(o.output_dir / "%(title)s.%(ext)s"),
            "progress_hooks": [self._progress_hook],
            # yt-dlp colorizes _speed_str/_eta_str for terminal display
            # (see its ProgressStyles/_format_progress) based on
            # auto-detecting whether the output stream supports ANSI --
            # a heuristic that isn't reliable for a frozen, windowed GUI
            # app with no real console attached. Without this, those
            # embedded escape codes showed up as literal garbled
            # characters in _progress_status_label, since Qt doesn't
            # interpret ANSI sequences the way a terminal would.
            "no_color": True,
            "extractor_args": _EXTRACTOR_ARGS,
        }

        if o.ffmpeg_location:
            ydl_opts["ffmpeg_location"] = o.ffmpeg_location
        if o.js_runtime_path:
            ydl_opts["js_runtimes"] = {"deno": {"path": o.js_runtime_path}}
        if o.force_overwrite:
            # Without this, yt-dlp finds the existing file, skips writing
            # it, and still reports success -- see the module docstring
            # and predict_output_path() for the two-part fix (warn first,
            # actually overwrite if the user says to go ahead anyway).
            ydl_opts["overwrites"] = True

        postprocessors = []

        if o.mode in (MODE_VIDEO, MODE_VIDEO_ONLY):
            ydl_opts["merge_output_format"] = "mp4"

        if o.mode == MODE_AUDIO_ONLY:
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": o.audio_format,
                }
            )

        if o.embed_thumbnail:
            ydl_opts["writethumbnail"] = True
            postprocessors.append({"key": "EmbedThumbnail"})

        if o.include_subtitles:
            ydl_opts["writesubtitles"] = True
            ydl_opts["subtitleslangs"] = ["en"]
            if o.mode != MODE_AUDIO_ONLY:
                postprocessors.append({"key": "FFmpegEmbedSubtitle"})

        ydl_opts["postprocessors"] = postprocessors

        result = None
        last_message = "Download failed."
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # extract_info(download=True), not the simpler download()
                    # -- download() only returns a retcode, but extract_info
                    # hands back the processed info dict, which is the only
                    # place yt-dlp records whether it *actually* wrote a file
                    # (see requested_downloads / __real_download below). That
                    # distinction is the whole point: without it, a silently
                    # skipped "already downloaded" file and a real one both
                    # just look like success.
                    result = ydl.extract_info(o.url, download=True)
                break
            except Exception as exc:
                message = str(exc).strip().splitlines()[0] if str(exc) else "Download failed."
                if len(message) > 160:
                    message = message[:157] + "..."
                last_message = message
                if attempt == self._MAX_ATTEMPTS:
                    self.failed.emit(last_message)
                    return
                self.progress.emit(
                    0.0, f"Retrying… (attempt {attempt + 1} of {self._MAX_ATTEMPTS})"
                )
                time.sleep(self._RETRY_DELAY_SECONDS)

        requested = (result or {}).get("requested_downloads") or []
        if requested:
            real_download = any(d.get("__real_download") for d in requested)
        else:
            # No requested_downloads entries at all is itself unusual for
            # a successful single-video run; assume a real download
            # happened rather than surface a confusing "already
            # downloaded" message that may not be true.
            real_download = bool((result or {}).get("__real_download", True))

        if real_download or o.force_overwrite:
            self.succeeded.emit("Download complete.")
        else:
            # predict_output_path() in main_window should have already
            # caught this before the worker even started -- this is the
            # fallback for whatever that offline prediction couldn't
            # foresee, so the user still gets an accurate message instead
            # of a false "complete." for a file that didn't change.
            self.succeeded.emit("Already downloaded -- no new file was saved.")
