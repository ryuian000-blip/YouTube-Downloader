"""Frame extraction: turning a video into images an LLM can actually look at.

"Frame by frame" taken literally would be ~36,000 images for a 20-minute
video, which helps nobody -- so this module samples: a fixed interval by
default, scene-change detection when the caller wants "the moments that
changed", and a tight interval over an explicit time range when the caller
already knows where to look (typically from a transcript timestamp).

Every mode is capped, and the cap is reported rather than silently
applied, so a caller never believes it saw everything when it didn't.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .binaries import detect
from .core import EngineError, format_timestamp

DEFAULT_INTERVAL_SECONDS = 10.0
DEFAULT_SCENE_THRESHOLD = 0.3
DEFAULT_MAX_FRAMES = 50
DEFAULT_WIDTH = 800
# 480p keeps a "just let me see what's on screen" pass fast; callers that
# need to read fine print pass a higher quality explicitly.
DEFAULT_FRAME_HEIGHT = 480

_SHOWINFO_PTS_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")
# Windows: keep ffmpeg/ffprobe from flashing a console window when the
# caller is a windowed GUI process.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Frame:
    path: Path
    timestamp: float

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "timestamp": round(self.timestamp, 2),
            "timestamp_hms": format_timestamp(self.timestamp),
        }


@dataclass
class FrameSet:
    frames: list[Frame]
    output_dir: Path
    mode: str
    truncated: bool = False
    note: str | None = None
    source: Path | None = None

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "output_dir": str(self.output_dir),
            "source": str(self.source) if self.source else None,
            "frame_count": len(self.frames),
            "truncated": self.truncated,
            "note": self.note,
            "frames": [f.as_dict() for f in self.frames],
        }


def _ffmpeg_binary() -> Path:
    status = detect()
    if not status.ffmpeg:
        raise EngineError(
            "ffmpeg wasn't found. Put ffmpeg next to the project (see README) "
            "or install it on PATH."
        )
    return status.ffmpeg


def probe_duration(path: Path) -> float | None:
    """Media duration in seconds, or None when ffprobe can't say."""
    status = detect()
    if not status.ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                str(status.ffprobe),
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=_NO_WINDOW,
        )
        payload = json.loads(proc.stdout or "{}")
        duration = (payload.get("format") or {}).get("duration")
        return float(duration) if duration else None
    except Exception:  # noqa: BLE001 -- duration is a nicety, not required
        return None


def _run_ffmpeg(args: list[str], timeout: int = 900) -> str:
    """Run ffmpeg, returning stderr (where it writes showinfo output)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineError("ffmpeg timed out extracting frames.") from exc
    except OSError as exc:
        raise EngineError(f"Couldn't run ffmpeg: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "unknown error"
        raise EngineError(f"ffmpeg failed while extracting frames: {detail}")
    return proc.stderr or ""


def _collect(output_dir: Path, pattern: str = "raw_*.jpg") -> list[Path]:
    return sorted(output_dir.glob(pattern))


def _rename_with_timestamps(raw_files: list[Path], timestamps: list[float]) -> list[Frame]:
    """ffmpeg can only number its outputs; the timestamp has to be applied
    afterwards. Naming files by time is the whole usability point -- an
    agent reading `frame_00-04-15.jpg` knows what it is looking at without
    cross-referencing a JSON index."""
    frames: list[Frame] = []
    for index, raw in enumerate(raw_files):
        stamp = timestamps[index] if index < len(timestamps) else 0.0
        hms = format_timestamp(stamp).replace(":", "-")
        target = raw.with_name(f"frame_{index + 1:03d}_{hms}.jpg")
        if target.exists():
            target.unlink()
        raw.rename(target)
        frames.append(Frame(path=target.resolve(), timestamp=stamp))
    return frames


def extract_frames_from_file(
    video_path: Path,
    output_dir: Path,
    interval: float | None = None,
    scene_threshold: float | None = None,
    start: float | None = None,
    end: float | None = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
    width: int = DEFAULT_WIDTH,
) -> FrameSet:
    """Sample frames out of a local video file.

    Mode is chosen by argument: scene_threshold -> scene detection,
    otherwise a fixed interval (defaulting to one frame per 10s, or a
    tighter spacing when an explicit short range is given).
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise EngineError(f"No such video file: {video_path}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("frame_*.jpg"):
        stale.unlink()
    for stale in output_dir.glob("raw_*.jpg"):
        stale.unlink()

    max_frames = max(1, min(int(max_frames or DEFAULT_MAX_FRAMES), 200))
    width = max(160, min(int(width or DEFAULT_WIDTH), 1920))
    ffmpeg = _ffmpeg_binary()

    start_at = max(0.0, float(start)) if start is not None else 0.0
    duration = None
    if end is not None:
        duration = max(0.1, float(end) - start_at)

    # A tight explicit range implies the caller is zooming in, so default
    # to a finer spacing than the whole-video default rather than handing
    # back two frames for a 20-second window.
    if interval is None and scene_threshold is None:
        if duration is not None and duration <= 120:
            interval = max(0.5, duration / min(max_frames, 12))
        else:
            interval = DEFAULT_INTERVAL_SECONDS

    scale = f"scale={width}:-2"
    args: list[str] = [str(ffmpeg), "-hide_banner", "-loglevel", "info", "-y"]
    if start_at > 0:
        args += ["-ss", f"{start_at:.3f}"]
    args += ["-i", str(video_path)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]

    if scene_threshold is not None:
        mode = "scene"
        threshold = max(0.05, min(float(scene_threshold), 0.95))
        vf = f"select='gt(scene,{threshold})',{scale},showinfo"
        args += ["-vf", vf, "-vsync", "vfr"]
    else:
        mode = "interval"
        vf = f"fps=1/{max(0.1, float(interval)):.4f},{scale},showinfo"
        args += ["-vf", vf, "-vsync", "vfr"]

    # One extra frame so we can tell "exactly at the cap" from "there was
    # more we didn't return".
    args += ["-frames:v", str(max_frames + 1), "-q:v", "3"]
    args += [str(output_dir / "raw_%04d.jpg")]

    stderr = _run_ffmpeg(args)

    raw_files = _collect(output_dir)
    if not raw_files:
        raise EngineError(
            "ffmpeg produced no frames — the range may be past the end of the "
            "video, or the scene threshold too high."
        )

    truncated = len(raw_files) > max_frames
    if truncated:
        for extra in raw_files[max_frames:]:
            extra.unlink()
        raw_files = raw_files[:max_frames]

    # showinfo timestamps are relative to the seek point, so add it back.
    parsed = [float(m) + start_at for m in _SHOWINFO_PTS_RE.findall(stderr)]
    if len(parsed) < len(raw_files):
        # Fall back to arithmetic spacing if showinfo output was clipped.
        step = float(interval) if mode == "interval" and interval else 0.0
        parsed = [start_at + i * step for i in range(len(raw_files))]

    frames = _rename_with_timestamps(raw_files, parsed)

    note = None
    if truncated:
        note = (
            f"Stopped at the {max_frames}-frame cap. Narrow the range with "
            "start/end, raise interval, or raise max_frames for more."
        )
    return FrameSet(
        frames=frames,
        output_dir=output_dir.resolve(),
        mode=mode,
        truncated=truncated,
        note=note,
        source=video_path.resolve(),
    )


def extract_frames(
    target: str,
    interval: float | None = None,
    scene_threshold: float | None = None,
    start: float | None = None,
    end: float | None = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
    width: int = DEFAULT_WIDTH,
    height: int | None = DEFAULT_FRAME_HEIGHT,
    output_dir: Path | None = None,
    js_runtime_path: str | None = None,
    ffmpeg_location: str | None = None,
    on_status: "callable | None" = None,
) -> FrameSet:
    """Frames from either a YouTube URL or a local file path.

    For a URL, the video is downloaded into the shared agent cache
    (download.ensure_local_media) so a later transcript/frames call on the
    same video reuses it instead of re-fetching.
    """
    # Local file: no network at all.
    candidate = Path(target)
    if candidate.exists() and candidate.is_file():
        return extract_frames_from_file(
            candidate,
            output_dir or candidate.parent / "frames",
            interval=interval,
            scene_threshold=scene_threshold,
            start=start,
            end=end,
            max_frames=max_frames,
            width=width,
        )

    # Imported lazily so a local-file extraction never pays for yt-dlp.
    from .core import resolve_runtime_paths
    from .download import MODE_VIDEO_ONLY, cache_dir_for, ensure_local_media
    from .info import extract_info

    js_runtime_path, ffmpeg_location = resolve_runtime_paths(
        js_runtime_path, ffmpeg_location
    )
    if on_status:
        on_status("Reading video info…")
    info = extract_info(target, js_runtime_path)
    video_id = info.get("id") or "video"

    if on_status:
        on_status("Fetching video (cached after the first time)…")
    media = ensure_local_media(
        target,
        video_id=video_id,
        # Video-only: frames need pictures, not sound, and skipping the
        # audio stream makes both the download and the ffmpeg pass cheaper.
        mode=MODE_VIDEO_ONLY,
        height=height,
        js_runtime_path=js_runtime_path,
        ffmpeg_location=ffmpeg_location,
        on_progress=(lambda pct, text: on_status(text)) if on_status else None,
    )
    if on_status:
        on_status("Extracting frames…")
    return extract_frames_from_file(
        media,
        output_dir or cache_dir_for(video_id) / "frames",
        interval=interval,
        scene_threshold=scene_threshold,
        start=start,
        end=end,
        max_frames=max_frames,
        width=width,
    )
