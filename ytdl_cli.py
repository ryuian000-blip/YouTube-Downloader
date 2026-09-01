#!/usr/bin/env python3
"""Headless CLI over ytdl_engine -- search, download, transcribe, and
extract frames from YouTube videos.

Built for two audiences at once: a human debugging the engine, and an
agent (Claude Code) driving it through Bash. Hence the contract:

  * machine-readable JSON on stdout, nothing else
  * human progress/status on stderr, so piping stdout to jq stays clean
  * non-zero exit on failure, with the reason as JSON on stdout
  * no interactive prompts, ever

Examples
--------
    python ytdl_cli.py search "claude code tutorial" --limit 5
    python ytdl_cli.py info https://youtu.be/VwGrXe2ricE
    python ytdl_cli.py transcript https://youtu.be/VwGrXe2ricE --format text
    python ytdl_cli.py frames https://youtu.be/VwGrXe2ricE --start 4:00 --end 4:30
    python ytdl_cli.py download https://youtu.be/VwGrXe2ricE --quality 1080
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `python ytdl_cli.py` work from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
from ytdl_engine.config import (  # noqa: E402
    describe_sources,
    known_fields,
    load_settings,
    settings_path,
    update_settings,
)
from ytdl_engine.download import cache_root, cache_size_bytes, clear_cache  # noqa: E402
from ytdl_engine.download import download as run_download  # noqa: E402


def _force_utf8_stdio() -> None:
    """Write UTF-8 regardless of the console's codepage.

    Windows consoles default to a legacy codepage (cp1252 here), and
    Python encodes stdout with it. Any video whose title or description
    contains a character outside that codepage -- em dashes, emoji,
    non-Latin scripts, i.e. an enormous share of YouTube -- raised
    UnicodeEncodeError mid-write and took the whole command down. The
    JSON contract can't depend on the user's regional settings.

    errors="replace" so a stray unencodable byte degrades one character
    rather than failing the command.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Not a reconfigurable text stream (redirected, or a frozen
            # windowed process with no real stdio) -- nothing to do.
            pass


def emit(payload: dict, exit_code: int = 0) -> None:
    """The single stdout writer. `ensure_ascii=False` so transcripts keep
    their real characters instead of \\uXXXX noise."""
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()
    raise SystemExit(exit_code)


def status(message: str) -> None:
    """Progress goes to stderr so stdout stays pure JSON."""
    print(message, file=sys.stderr, flush=True)


def fail(message: str, hint: str | None = None) -> None:
    payload = {"ok": False, "error": message}
    if hint:
        payload["hint"] = hint
    emit(payload, exit_code=1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(args) -> None:
    results = search_youtube(args.query, limit=args.limit)
    emit({"ok": True, "query": args.query, "count": len(results), "results": results})


def cmd_info(args) -> None:
    emit({"ok": True, **get_video_info(args.url)})


def cmd_download(args) -> None:
    settings = load_settings()
    mode_map = {
        "video": MODE_VIDEO,
        "video-only": MODE_VIDEO_ONLY,
        "audio": MODE_AUDIO_ONLY,
    }
    # Unset flags fall through to the user's settings rather than to
    # argparse defaults, so `config set` genuinely governs behaviour.
    mode = mode_map[args.mode] if args.mode else settings.default_mode
    if mode not in mode_map.values():
        mode = MODE_VIDEO
    quality = args.quality if args.quality is not None else settings.max_height
    audio_format = args.audio_format or settings.audio_format
    output_dir = (
        Path(args.dir).expanduser().resolve()
        if args.dir
        else settings.resolved_download_dir()
    )
    result = run_download(
        DownloadOptions(
            url=args.url,
            mode=mode,
            height=quality,
            audio_format=audio_format,
            include_subtitles=args.subtitles,
            embed_thumbnail=False,
            output_dir=output_dir,
            ffmpeg_location=None,
            js_runtime_path=None,
            force_overwrite=args.overwrite,
        ),
        on_progress=lambda pct, text: status(text),
        settings=settings,
    )
    emit(
        {
            "ok": True,
            "message": result.message,
            "real_download": result.real_download,
            "path": str(result.path) if result.path else None,
            "output_dir": str(output_dir),
            "quality_cap": quality,
        }
    )


def cmd_transcript(args) -> None:
    transcript = get_transcript(
        args.url,
        lang=args.lang,
        force_whisper=args.force_whisper,
        whisper_model=args.model,
        on_status=status,
    )
    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    transcript = transcript.slice(start, end)

    if args.format == "text":
        payload = {
            "ok": True,
            "method": transcript.method,
            "language": transcript.language,
            "title": transcript.title,
            "text": transcript.as_text(),
        }
    elif args.format == "srt":
        payload = {
            "ok": True,
            "method": transcript.method,
            "language": transcript.language,
            "title": transcript.title,
            "srt": transcript.as_srt(),
        }
    else:
        payload = {"ok": True, **transcript.as_dict()}
    emit(payload)


def cmd_frames(args) -> None:
    frame_set = extract_frames(
        args.target,
        interval=args.interval,
        scene_threshold=args.scene_threshold,
        start=parse_timestamp(args.start),
        end=parse_timestamp(args.end),
        max_frames=args.max,
        width=args.width,
        height=args.quality,
        output_dir=Path(args.out).expanduser().resolve() if args.out else None,
        on_status=status,
    )
    emit({"ok": True, **frame_set.as_dict()})


def cmd_config(args) -> None:
    """Show or change the settings every surface reads."""
    if args.action == "path":
        emit({"ok": True, "path": str(settings_path()), "exists": settings_path().exists()})

    if args.action == "reset":
        target = settings_path()
        if target.exists():
            target.unlink()
        emit({"ok": True, "message": "Settings reset to defaults.", "path": str(target)})

    if args.action == "set":
        if not args.assignments:
            fail("Nothing to set. Use: config set max_height=720 download_dir=D:/Videos")
        changes: dict = {}
        for item in args.assignments:
            if "=" not in item:
                fail(f"Expected key=value, got {item!r}.")
            key, _, value = item.partition("=")
            changes[key.strip()] = value.strip()
        try:
            updated = update_settings(changes)
        except KeyError as exc:
            # exc.args[0], not str(exc): KeyError stringifies with the
            # message wrapped in quotes, which reads as a typo in output.
            fail(exc.args[0] if exc.args else "Unknown setting.")
        except (TypeError, ValueError) as exc:
            fail(f"Invalid value: {exc}")
        emit(
            {
                "ok": True,
                "message": f"Updated {', '.join(sorted(changes))}.",
                "path": str(settings_path()),
                "settings": updated.as_dict(),
            }
        )

    # show (default)
    settings = load_settings()
    emit(
        {
            "ok": True,
            "path": str(settings_path()),
            "exists": settings_path().exists(),
            "settings": settings.as_dict(),
            # Which layer each value came from, so it's obvious when an
            # env var is quietly overriding the file.
            "sources": describe_sources(),
            "effective_download_dir": str(settings.resolved_download_dir()),
        }
    )


def cmd_cache(args) -> None:
    """Inspect or empty the working cache of videos fetched for
    transcripts/frames."""
    settings = load_settings()
    root = cache_root(settings)
    if args.action == "clear":
        freed = clear_cache(settings)
        emit(
            {
                "ok": True,
                "message": f"Cleared {freed / (1024 * 1024):.1f} MB.",
                "path": str(root),
            }
        )
    size = cache_size_bytes(settings)
    videos = [p.name for p in root.iterdir() if p.is_dir()] if root.exists() else []
    emit(
        {
            "ok": True,
            "path": str(root),
            "size_mb": round(size / (1024 * 1024), 1),
            "limit_mb": settings.cache_max_mb,
            "cached_videos": len(videos),
        }
    )


def _install_hint(name: str) -> str:
    """Exactly what to run to get a missing binary, on THIS platform.

    A diagnostic that says "ffmpeg not found" and stops just relocates
    the problem to a search engine.
    """
    if sys.platform.startswith("win"):
        hints = {
            "ffmpeg": "winget install Gyan.FFmpeg   (or https://www.gyan.dev/ffmpeg/builds/)",
            "ffprobe": "winget install Gyan.FFmpeg   (ships with ffmpeg)",
            "deno": "winget install DenoLand.Deno   (or https://deno.com/)",
        }
    elif sys.platform == "darwin":
        hints = {
            "ffmpeg": "brew install ffmpeg   (or https://ffmpeg.martin-riedl.de/ for a portable build)",
            "ffprobe": "brew install ffmpeg   (ships with ffmpeg)",
            "deno": "brew install deno   (or https://deno.com/)",
        }
    else:
        hints = {
            "ffmpeg": "sudo apt install ffmpeg   (or your distro's package manager)",
            "ffprobe": "sudo apt install ffmpeg   (ships with ffmpeg)",
            "deno": "curl -fsSL https://deno.land/install.sh | sh",
        }
    return hints.get(name, f"Install {name} and put it on your PATH.")


def _environment_report() -> dict:
    """Shared by `doctor` and `setup`."""
    binaries = detect()
    problems: list[dict] = []

    for name, path in (
        ("ffmpeg", binaries.ffmpeg),
        ("ffprobe", binaries.ffprobe),
        ("deno", binaries.js_runtime),
    ):
        if not path:
            problems.append(
                {
                    "what": name,
                    "why": (
                        "Needed to merge video and audio."
                        if name != "deno"
                        else "Needed to solve YouTube's JS challenge; without it "
                        "downloads fail with HTTP 403."
                    ),
                    "fix": _install_hint(name),
                }
            )

    try:
        import yt_dlp_ejs  # noqa: F401

        ejs = "installed"
    except ImportError:
        ejs = "MISSING"
        problems.append(
            {
                "what": "yt-dlp-ejs",
                "why": "YouTube's JS challenges can't be solved; downloads 403.",
                "fix": "pip install yt-dlp-ejs",
            }
        )

    try:
        import faster_whisper  # noqa: F401

        whisper = "installed"
    except ImportError:
        whisper = "not installed"

    return {
        "yt_dlp_version": ytdlp_version(),
        "python": sys.version.split()[0],
        "ffmpeg": str(binaries.ffmpeg) if binaries.ffmpeg else None,
        "ffprobe": str(binaries.ffprobe) if binaries.ffprobe else None,
        "js_runtime": str(binaries.js_runtime) if binaries.js_runtime else None,
        "yt_dlp_ejs": ejs,
        "faster_whisper": whisper,
        "problems": problems,
        "ready": not problems,
    }


def cmd_setup(args) -> None:
    """One-shot first-run setup: check the environment, show the defaults
    that matter, and record that the user has seen them."""
    report = _environment_report()
    settings = load_settings()

    if args.confirm:
        update_settings({"defaults_confirmed": True})
        emit(
            {
                "ok": True,
                "message": "Setup complete — defaults confirmed.",
                "download_dir": str(settings.resolved_download_dir()),
                "max_height": settings.max_height,
                "ready": report["ready"],
                "problems": report["problems"],
            }
        )

    emit(
        {
            "ok": report["ready"],
            "environment": report,
            "defaults": {
                "download_folder": str(settings.resolved_download_dir()),
                "quality": (
                    f"up to {settings.max_height}p"
                    if settings.max_height
                    else "best available"
                ),
                "transcripts": (
                    "YouTube captions when available; local Whisper otherwise"
                    if settings.allow_whisper
                    else "YouTube captions only"
                ),
                "already_confirmed": settings.defaults_confirmed,
            },
            "next_steps": [
                "Change anything you don't like: "
                "config set download_dir=... max_height=...",
                "Accept these defaults: setup --confirm",
                "Connect to Claude Code: "
                'claude mcp add youtube-downloader --scope user -- "<this program>" mcp',
            ],
        },
        exit_code=0 if report["ready"] else 1,
    )


def cmd_doctor(args) -> None:
    """Environment check -- the first thing to run when something breaks.

    Every problem it reports carries both why it matters and the exact
    command to fix it on this platform.
    """
    report = _environment_report()
    emit({"ok": report["ready"], **report}, exit_code=0 if report["ready"] else 1)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ytdl_cli.py",
        description="Search, download, transcribe and extract frames from YouTube videos.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="Search YouTube and return matching videos.")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=5, help="Max results (1-25, default 5).")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("info", help="Metadata for one video (duration, chapters, captions).")
    p.add_argument("url")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("download", help="Download a video or its audio.")
    p.add_argument("url")
    p.add_argument("--quality", type=int, default=None,
                   help="Max height, e.g. 1080. Default: the max_height setting.")
    p.add_argument("--mode", choices=["video", "video-only", "audio"], default=None,
                   help="Default: the default_mode setting.")
    p.add_argument("--audio-format", default=None, choices=["mp3", "m4a", "wav"],
                   help="Default: the audio_format setting.")
    p.add_argument("--dir", default=None,
                   help="Output directory. Default: the download_dir setting.")
    p.add_argument("--subtitles", action="store_true", help="Also fetch English subtitles.")
    p.add_argument("--overwrite", action="store_true",
                   help="Redownload even if the file already exists.")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser(
        "transcript",
        help="Timestamped transcript (YouTube captions when available, else Whisper).",
    )
    p.add_argument("url")
    p.add_argument("--lang", default="en", help="Caption language (default en; 'auto' for Whisper autodetect).")
    p.add_argument("--format", choices=["json", "text", "srt"], default="json")
    p.add_argument("--start", default=None, help="Only segments from this time (e.g. 4:10).")
    p.add_argument("--end", default=None, help="Only segments up to this time (e.g. 4:30).")
    p.add_argument("--force-whisper", action="store_true",
                   help="Ignore YouTube captions and transcribe locally.")
    p.add_argument("--model", default=DEFAULT_WHISPER_MODEL,
                   help=f"Whisper model size (default {DEFAULT_WHISPER_MODEL}).")
    p.set_defaults(func=cmd_transcript)

    p = sub.add_parser("frames", help="Extract frames as JPEGs from a URL or local file.")
    p.add_argument("target", help="YouTube URL or path to a local video file.")
    p.add_argument("--interval", type=float, default=None,
                   help="Seconds between frames (default 10, or finer for short ranges).")
    p.add_argument("--scene-threshold", type=float, default=None,
                   help="Use scene-change detection instead, e.g. 0.3.")
    p.add_argument("--start", default=None, help="Start time, e.g. 4:10.")
    p.add_argument("--end", default=None, help="End time, e.g. 4:30.")
    p.add_argument("--max", type=int, default=DEFAULT_MAX_FRAMES,
                   help=f"Max frames (default {DEFAULT_MAX_FRAMES}).")
    p.add_argument("--width", type=int, default=800, help="Frame width in px (default 800).")
    p.add_argument("--quality", type=int, default=480,
                   help="Video height to download for extraction (default 480).")
    p.add_argument("--out", default=None, help="Output directory (default: a temp cache dir).")
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser("doctor", help="Check binaries and dependencies are all present.")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "setup",
        help="First-run setup: check everything and confirm your defaults.",
    )
    p.add_argument("--confirm", action="store_true",
                   help="Accept the defaults shown (stops the first-download prompt).")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser(
        "config",
        help="Show or change settings (quality cap, download folder, limits...).",
        description=(
            "Settings resolve as: explicit flag > environment variable "
            "(YTDL_<NAME>) > settings file > built-in default.\n\n"
            "Settings: " + ", ".join(known_fields())
        ),
    )
    p.add_argument("action", nargs="?", default="show",
                   choices=["show", "set", "path", "reset"])
    p.add_argument("assignments", nargs="*",
                   help="For 'set': key=value pairs, e.g. max_height=720. "
                        "Use 'none' to clear a limit.")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser(
        "cache", help="Show or clear the working cache of fetched videos."
    )
    p.add_argument("action", nargs="?", default="show", choices=["show", "clear"])
    p.set_defaults(func=cmd_cache)

    return parser


def main(argv: list[str] | None = None) -> None:
    _force_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except EngineError as exc:
        fail(str(exc))
    except KeyboardInterrupt:
        fail("Cancelled.")
    except Exception as exc:  # noqa: BLE001 -- a CLI must never print a traceback at an agent
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
