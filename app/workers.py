"""Background QThread workers.

Never touch a Qt widget from these -- everything crosses back to the GUI
thread via signal/slot (Qt queues the delivery automatically because the
receiving QObject, MainWindow, lives on the main thread). This is the
direct equivalent of the original Tkinter app's ``self.after(0, ...)``
pattern, per the rebuild brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
import yt_dlp
from yt_dlp.networking.common import Request as YdlRequest


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

    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,  # a video ID + list= (e.g. auto "Radio" mixes)
                                  # would otherwise make yt-dlp try to resolve
                                  # a dynamically-generated playlist and hang.
        }
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


class DownloadWorker(QThread):
    progress = Signal(float, str)   # percent 0-100, status text
    succeeded = Signal(str)         # final message
    failed = Signal(str)

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
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
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
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] if str(exc) else "Download failed."
            if len(message) > 160:
                message = message[:157] + "..."
            self.failed.emit(message)
            return

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
