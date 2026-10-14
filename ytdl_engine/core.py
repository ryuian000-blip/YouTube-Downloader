"""Shared yt-dlp plumbing: option building, the retry loop, ANSI stripping.

Everything YouTube-fragile lives here so there is exactly one place to fix
when YouTube shifts its anti-bot posture again. The GUI workers, the CLI,
and the MCP server all route through these helpers.

No Qt imports anywhere in this package -- that is the whole point of the
split. The GUI wraps these functions in QThreads; nothing here knows or
cares that a GUI exists.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from .binaries import detect

# Deliberately empty: no player_client pin. This app used to force
# ["android_vr", "android"] because, as of July 2026, yt-dlp's default
# clients 403'd without a PO token while android_vr handed back the full
# un-gated quality ladder. YouTube then flipped that on its head: its
# SABR-only streaming rollout (yt-dlp issue #12482) broke the android
# clients' plain https formats -- downloads would start, reach 30%+, and
# die with a mid-stream 403 that no retry could fix -- while the
# *maintained defaults* in current yt-dlp work, provided the JS challenge
# solver is available (deno + the yt-dlp-ejs script package). Verified
# against a real previously-failing video: pinned clients 403 at ~37%,
# defaults complete at 1080p. Lesson encoded here: client pins rot as
# YouTube's posture shifts -- let yt-dlp's own actively-maintained
# selection rule, and keep yt-dlp itself current (nightly channel, see
# requirements.txt and the build scripts).
EXTRACTOR_ARGS: dict = {}

# Matches ANSI CSI sequences like "\x1b[0;32m" / "\x1b[0m". Belt-and-braces
# alongside the "no_color" option below: that stops yt-dlp generating
# colored _speed_str/_eta_str in the first place (its ANSI auto-detection
# is unreliable for a frozen, windowed app with no real console), but
# stripping defensively means a stray escape code -- from a
# differently-behaved yt-dlp version, say -- shows up as nothing rather
# than as literal garbled control characters in a Qt label or JSON output.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# yt-dlp's own retries/fragment_retries (defaults: 10 each) retry
# individual HTTP requests against format URLs *already resolved* by a
# given extract_info() call -- they don't help when the resolved URLs
# themselves are the problem, e.g. a signed googlevideo URL that 403s from
# a transient anti-bot flag or expires before the merge/postprocess step
# finishes. A fresh extract_info() call resolves brand-new URLs, which is
# what manually clicking Download again does -- run_with_retry automates
# exactly that.
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2

# yt_dlp is imported lazily, inside the functions that use it, not at
# module level. Importing it costs ~1-2s in a frozen build (it pulls in
# cookies, every downloader backend, asyncio, aes...), and the GUI drags
# this package in at startup via app/workers.py -- so an eager import
# taxed every launch for something not needed until the user actually
# fetches a URL. Measured: 3.4s -> 1.2s to a visible window.


def ytdlp_version() -> str:
    """The yt-dlp build in use. A function, not a constant, so merely
    importing this package doesn't drag yt_dlp in -- see the note above.
    """
    import yt_dlp

    return getattr(getattr(yt_dlp, "version", None), "__version__", "unknown")


ProgressCallback = Callable[[float, str], None]


class EngineError(RuntimeError):
    """A user-facing failure. The message is already trimmed to one line
    and safe to show directly in a GUI label, a CLI stderr line, or an MCP
    tool result -- callers should never need to format a traceback."""


class CancelledError(EngineError):
    """The caller asked for the operation to stop.

    Not a failure, and deliberately not a PermanentError subclass of
    anything retryable: run_with_retry must never re-attempt something
    the user just cancelled. Callers should treat this as "stopped", not
    "broken" -- the GUI shows "Download cancelled.", not a red error.
    """


CANCELLED_MESSAGE = "Download cancelled."


class PermanentError(EngineError):
    """A failure that retrying cannot possibly fix.

    run_with_retry re-raises these immediately instead of burning three
    attempts and four seconds of sleep on a certainty. A 403 on a signed
    URL is worth retrying (fresh URLs may work); "this is a live stream"
    and "that's a playlist URL" are not -- they will be just as true on
    attempt three, and the delay only makes the real reason arrive late.
    """


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def tidy_error(exc: BaseException, fallback: str = "Operation failed.") -> str:
    """First line of an exception, trimmed -- yt-dlp errors are often
    multi-line with a stack-ish tail that means nothing to a user."""
    text = str(exc).strip()
    if not text:
        return fallback
    message = text.splitlines()[0].strip()
    # yt-dlp prefixes most of its own failures with a redundant "ERROR: ".
    if message.upper().startswith("ERROR: "):
        message = message[7:]
    if len(message) > 300:
        message = message[:297] + "..."
    return message or fallback


# Paths and path prefixes that identify a URL as pointing at MANY videos
# rather than one. Checked before any network call -- see
# ensure_single_video_url.
_LISTING_PATHS = frozenset({"/playlist", "/results"})
_LISTING_PREFIXES = ("/channel/", "/c/", "/user/", "/@", "/feed/")


def ensure_single_video_url(url: str) -> None:
    """Refuse a playlist, channel, or search-results URL up front.

    ``noplaylist`` does NOT cover this. That option means "if this URL is
    a video *and* a playlist, take just the video" -- so it handles a
    /watch?v=ID&list=... link, which is what it was added for. A bare
    /playlist?list=... or /@handle/videos URL is not a video at all, so
    the option does not apply and yt-dlp expands the whole thing: a
    200-video channel would download 200 files, while _final_path() read
    requested_downloads[0] and reported a single success with one path.
    The caller had no way to tell what had happened.

    A URL-shape check rather than a metadata probe, because it has to be
    free -- this runs before every download, and paying a round trip to
    discover something the URL already says would tax the common case to
    guard the rare one. Anything not recognised here is passed through
    for yt-dlp to accept or reject on its own.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return  # malformed; let yt-dlp produce the error message
    path = (parsed.path or "/").rstrip("/").lower() or "/"

    if path in _LISTING_PATHS or path.startswith(_LISTING_PREFIXES):
        what = "playlist" if path == "/playlist" else "channel or search"
        raise PermanentError(
            f"That's a {what} URL, not a single video. This app downloads "
            "one video at a time -- open the video you want and use its "
            "own link (the one with /watch?v= or youtu.be/ in it)."
        )
    # youtube.com/?list=... with no video id: a playlist by another name.
    if path == "/" and "list=" in (parsed.query or "").lower():
        raise PermanentError(
            "That link points at a playlist rather than one video. Use a "
            "single video's own link (/watch?v=... or youtu.be/...)."
        )


def base_opts(js_runtime_path: str | None = None, **extra: Any) -> dict:
    """The option floor every yt-dlp call in this project shares.

    js_runtime_path: path to deno. Without it, extract_info() fails on most
    real YouTube videos with "The page needs to be reloaded" -- yt-dlp
    needs a JS runtime to solve YouTube's nsig challenge even just to list
    formats, not only to download. Pass None to let yt-dlp find its own
    (works when deno is on PATH).
    """
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        # NOT covered by quiet=True: yt-dlp still renders its live
        # progress bar, and it writes it to STDOUT. That broke the CLI's
        # "JSON on stdout, nothing else" contract outright -- piping to
        # jq failed, because ~30 progress lines arrived before the JSON.
        # Every surface here reports progress through its own hook
        # (progress_hooks), so yt-dlp's own display is redundant anyway.
        "noprogress": True,
        # A video ID carrying list= (e.g. YouTube's auto "Radio" mixes)
        # would otherwise make yt-dlp try to resolve a dynamically
        # generated playlist, which can hang for a long time.
        "noplaylist": True,
        "no_color": True,
        "extractor_args": EXTRACTOR_ARGS,
    }
    if js_runtime_path:
        opts["js_runtimes"] = {"deno": {"path": str(js_runtime_path)}}
    opts.update(extra)
    return opts


def resolve_runtime_paths(
    js_runtime_path: str | None = None,
    ffmpeg_location: str | None = None,
) -> tuple[str | None, str | None]:
    """Fill in deno/ffmpeg from the bundled-binary search when the caller
    didn't pass explicit paths. The GUI always passes its own (it has
    already run detect() for its warning banner); the CLI and MCP server
    rely on this so they Just Work from a checkout."""
    if js_runtime_path and ffmpeg_location:
        return js_runtime_path, ffmpeg_location
    status = detect()
    if not js_runtime_path and status.js_runtime:
        js_runtime_path = str(status.js_runtime)
    if not ffmpeg_location and status.ffmpeg_folder:
        ffmpeg_location = status.ffmpeg_folder
    return js_runtime_path, ffmpeg_location


def run_with_retry(
    action: Callable[[], Any],
    on_retry: Callable[[int, int], None] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    delay_seconds: float = RETRY_DELAY_SECONDS,
    fallback_message: str = "Operation failed.",
) -> Any:
    """Run `action`, re-running it from scratch on failure.

    Deliberately re-runs the WHOLE action (a fresh extract_info, fresh
    signed URLs) rather than retrying a request -- see MAX_ATTEMPTS above
    for why that distinction is the entire point.

    on_retry(next_attempt, max_attempts) fires between attempts so callers
    can surface "Retrying..." without this module knowing about progress
    bars or JSON.
    """
    last_message = fallback_message
    for attempt in range(1, max_attempts + 1):
        try:
            return action()
        except (CancelledError, PermanentError):
            # Deliberately not retried, and deliberately not re-wrapped:
            # the message already says what is permanently wrong -- or,
            # for a cancellation, that the user asked it to stop, which
            # three more attempts would actively defy.
            raise
        except Exception as exc:  # noqa: BLE001 -- deliberately broad
            last_message = tidy_error(exc, fallback_message)
            if attempt == max_attempts:
                raise EngineError(last_message) from exc
            if on_retry:
                on_retry(attempt + 1, max_attempts)
            time.sleep(delay_seconds)
    raise EngineError(last_message)


def format_timestamp(seconds: float) -> str:
    """H:MM:SS for humans and transcript output."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_timestamp(value: str | float | int | None) -> float | None:
    """Accepts 90, "90", "1:30", "01:02:03", "1m30s" -> seconds.

    Exists because every caller of the frame/transcript tools is either a
    human typing a timestamp they read off a video player or an LLM
    copying one out of a transcript -- both write "4:10", not 250.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) > 3:
            raise EngineError(f"Couldn't read the timestamp {value!r}.")
        try:
            numbers = [float(p) for p in parts]
        except ValueError as exc:
            raise EngineError(f"Couldn't read the timestamp {value!r}.") from exc
        total = 0.0
        for number in numbers:
            total = total * 60 + number
        return total
    match = re.fullmatch(
        r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s?)?",
        text,
        re.IGNORECASE,
    )
    if match and any(match.groups()):
        hours, minutes, secs = (float(g) if g else 0.0 for g in match.groups())
        return hours * 3600 + minutes * 60 + secs
    raise EngineError(f"Couldn't read the timestamp {value!r}.")
