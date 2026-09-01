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
from ytdl_engine.download import download as run_download  # noqa: E402


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
    mode = {
        "video": MODE_VIDEO,
        "video-only": MODE_VIDEO_ONLY,
        "audio": MODE_AUDIO_ONLY,
    }[args.mode]
    output_dir = Path(args.dir).expanduser().resolve() if args.dir else Path.cwd()
    result = run_download(
        DownloadOptions(
            url=args.url,
            mode=mode,
            height=args.quality,
            audio_format=args.audio_format,
            include_subtitles=args.subtitles,
            embed_thumbnail=False,
            output_dir=output_dir,
            ffmpeg_location=None,
            js_runtime_path=None,
            force_overwrite=args.overwrite,
        ),
        on_progress=lambda pct, text: status(text),
    )
    emit(
        {
            "ok": True,
            "message": result.message,
            "real_download": result.real_download,
            "path": str(result.path) if result.path else None,
            "output_dir": str(output_dir),
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


def cmd_doctor(args) -> None:
    """Environment check -- the first thing to run when something breaks."""
    binaries = detect()
    payload = {
        "ok": binaries.is_download_ready,
        "yt_dlp_version": ytdlp_version(),
        "python": sys.version.split()[0],
        "ffmpeg": str(binaries.ffmpeg) if binaries.ffmpeg else None,
        "ffprobe": str(binaries.ffprobe) if binaries.ffprobe else None,
        "js_runtime": str(binaries.js_runtime) if binaries.js_runtime else None,
        "missing": binaries.missing,
    }
    try:
        import yt_dlp_ejs  # noqa: F401

        payload["yt_dlp_ejs"] = "installed"
    except ImportError:
        payload["yt_dlp_ejs"] = "MISSING"
        payload["ok"] = False
        payload["hint"] = (
            "yt-dlp-ejs is missing: YouTube's JS challenges can't be solved and "
            "downloads will 403. Run: pip install yt-dlp-ejs"
        )
    try:
        import faster_whisper  # noqa: F401

        payload["faster_whisper"] = "installed"
    except ImportError:
        # Not fatal: only needed for videos that have no captions.
        payload["faster_whisper"] = "not installed (needed only for caption-less videos)"
    emit(payload, exit_code=0 if payload["ok"] else 1)


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
                   help="Max height, e.g. 1080. Omit for best available.")
    p.add_argument("--mode", choices=["video", "video-only", "audio"], default="video")
    p.add_argument("--audio-format", default="mp3", choices=["mp3", "m4a", "wav"])
    p.add_argument("--dir", default=None, help="Output directory (default: cwd).")
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

    return parser


def main(argv: list[str] | None = None) -> None:
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
