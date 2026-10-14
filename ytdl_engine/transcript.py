"""Transcripts, cheapest source first.

1. YouTube's own captions (manual, then auto-generated) -- instant, free,
   already timestamped, no ML. Most videos have them.
2. Local Whisper (faster-whisper) on downloaded audio -- only when there
   are no captions, or the caller explicitly forces it.

Both paths return the SAME segment shape, so a caller can't accidentally
depend on which one ran. Which one did run is reported in `method` for
when that matters (auto-captions mis-hear proper nouns; Whisper doesn't
know the video's language until it listens).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


from .core import EngineError, base_opts, format_timestamp, resolve_runtime_paths
from .download import ensure_local_media
from .info import MODE_AUDIO_ONLY, extract_info

# Preference order. json3 is YouTube's structured caption format (explicit
# start + duration per event); the others are fallbacks for tracks that
# don't offer it.
_CAPTION_FORMATS = ("json3", "srv3", "srv1", "vtt")

DEFAULT_WHISPER_MODEL = "small"


def whisper_remedy() -> str:
    """How to GET local transcription, phrased for the build you're in.

    "pip install faster-whisper" is the correct answer in a checkout and
    useless in a shipped build: a Release download deliberately contains
    no Python and no pip, so sending someone there for a feature the app
    advertises dead-ends them. The release ships a second, larger asset
    with transcription bundled -- a frozen build names THAT instead.
    """
    if not getattr(sys, "frozen", False):
        return "Run: pip install faster-whisper"

    if sys.platform == "darwin":
        asset = "YouTube-Downloader-macos-full.zip"
    elif sys.platform.startswith("win"):
        asset = "YouTube-Downloader-windows-full.zip"
    else:
        asset = "the '-full' asset"
    return (
        f"Download {asset} from the project's Releases page — it is this "
        "same app with the transcription engine built in."
    )


def whisper_unavailable_message() -> str:
    """The error a caption-less video raises when this build can't transcribe."""
    if not getattr(sys, "frozen", False):
        return "faster-whisper isn't installed. " + whisper_remedy()
    return (
        "This video has no captions, so it has to be transcribed here, and "
        "this is the standard download — it doesn't include the "
        "transcription engine. " + whisper_remedy() + " Videos that do have "
        "captions work fine in this copy."
    )


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "start_hms": format_timestamp(self.start),
            "text": self.text,
        }


@dataclass
class Transcript:
    segments: list[TranscriptSegment] = field(default_factory=list)
    method: str = "captions"       # "captions" | "auto-captions" | "whisper"
    language: str | None = None
    video_id: str | None = None
    title: str | None = None
    duration_seconds: float | None = None
    truncated: bool = False
    note: str | None = None
    # "cuda" | "cpu" for a Whisper transcript; None when captions were used.
    device: str | None = None

    def slice(self, start: float | None, end: float | None) -> "Transcript":
        """Time-window a transcript. Segments are kept when they OVERLAP
        the window, not only when fully inside it -- asking for 4:10-4:30
        should include the sentence already in progress at 4:10."""
        if start is None and end is None:
            return self
        lo = start if start is not None else float("-inf")
        hi = end if end is not None else float("inf")
        kept = [s for s in self.segments if s.end >= lo and s.start <= hi]
        return Transcript(
            segments=kept,
            method=self.method,
            language=self.language,
            video_id=self.video_id,
            title=self.title,
            duration_seconds=self.duration_seconds,
            truncated=self.truncated,
            note=self.note,
            device=self.device,
        )

    def as_text(self) -> str:
        return "\n".join(
            f"[{format_timestamp(s.start)}] {s.text}" for s in self.segments
        )

    def as_plain_text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()

    def as_srt(self) -> str:
        def stamp(seconds: float) -> str:
            millis = int(round(seconds * 1000))
            hours, millis = divmod(millis, 3_600_000)
            minutes, millis = divmod(millis, 60_000)
            secs, millis = divmod(millis, 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        blocks = []
        for index, seg in enumerate(self.segments, start=1):
            blocks.append(
                f"{index}\n{stamp(seg.start)} --> {stamp(seg.end)}\n{seg.text}\n"
            )
        return "\n".join(blocks)

    def as_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "method": self.method,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "segment_count": len(self.segments),
            "truncated": self.truncated,
            "note": self.note,
            "device": self.device,
            "segments": [s.as_dict() for s in self.segments],
        }


# ---------------------------------------------------------------------------
# Caption path
# ---------------------------------------------------------------------------

def _pick_track(tracks: dict, lang: str) -> tuple[str, dict] | None:
    """Find the best caption track for `lang`.

    Matches exact language first, then any regional variant ("en-US" for
    "en"), then -- only if the caller asked for English and the video has
    none -- any track at all, since a translated/original-language track
    is far more useful to a caller than nothing.
    """
    if not isinstance(tracks, dict) or not tracks:
        return None
    candidates = [lang]
    candidates += [k for k in tracks if k.split("-")[0] == lang and k != lang]
    if lang == "en":
        candidates += [k for k in tracks if k not in candidates]
    for key in candidates:
        entries = tracks.get(key)
        if not entries:
            continue
        by_ext = {e.get("ext"): e for e in entries if isinstance(e, dict)}
        for ext in _CAPTION_FORMATS:
            if ext in by_ext and by_ext[ext].get("url"):
                return key, by_ext[ext]
    return None


def _parse_json3(payload: dict) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for event in payload.get("events") or []:
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(seg.get("utf8", "") for seg in segs)
        text = " ".join(text.split())
        if not text:
            continue
        start = float(event.get("tStartMs", 0)) / 1000.0
        duration = float(event.get("dDurationMs", 0)) / 1000.0
        segments.append(TranscriptSegment(start=start, end=start + duration, text=text))
    return segments


def _parse_vtt(text: str) -> list[TranscriptSegment]:
    """Minimal WEBVTT/SRT cue parser -- only used when a track offers no
    json3 variant."""
    def to_seconds(stamp: str) -> float:
        stamp = stamp.strip().replace(",", ".")
        parts = stamp.split(":")
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
        return total

    segments: list[TranscriptSegment] = []
    start = end = None
    buffer: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "-->" in line:
            if start is not None and buffer:
                body = " ".join(" ".join(buffer).split())
                if body:
                    segments.append(TranscriptSegment(start, end or start, body))
            left, _, right = line.partition("-->")
            try:
                start = to_seconds(left)
                end = to_seconds(right.split()[0]) if right.split() else start
            except ValueError:
                start = end = None
            buffer = []
        elif not line:
            if start is not None and buffer:
                body = " ".join(" ".join(buffer).split())
                if body:
                    segments.append(TranscriptSegment(start, end or start, body))
                buffer = []
                start = end = None
        elif start is not None and not line.startswith(("WEBVTT", "Kind:", "Language:")):
            if line.isdigit() and not buffer:
                continue  # SRT cue number
            buffer.append(line)
    if start is not None and buffer:
        body = " ".join(" ".join(buffer).split())
        if body:
            segments.append(TranscriptSegment(start, end or start, body))
    return segments


def _dedupe(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """YouTube's auto-captions roll words forward across cues, so
    consecutive cues repeat text. Drop exact repeats and cues wholly
    contained in the previous one."""
    cleaned: list[TranscriptSegment] = []
    for seg in segments:
        if cleaned and seg.text == cleaned[-1].text:
            cleaned[-1].end = max(cleaned[-1].end, seg.end)
            continue
        cleaned.append(seg)
    return cleaned


def _captions_from_info(
    info: dict, lang: str, js_runtime_path: str | None
) -> tuple[list[TranscriptSegment], str, str] | None:
    """-> (segments, method, language) or None when the video has no
    usable captions."""
    import yt_dlp

    for key, method in (("subtitles", "captions"), ("automatic_captions", "auto-captions")):
        picked = _pick_track(info.get(key) or {}, lang)
        if not picked:
            continue
        track_lang, entry = picked
        try:
            with yt_dlp.YoutubeDL(base_opts(js_runtime_path)) as ydl:
                with ydl.urlopen(entry["url"]) as response:
                    raw = response.read()
        except Exception:  # noqa: BLE001 -- fall through to the next source
            continue
        ext = entry.get("ext")
        try:
            if ext == "json3":
                segments = _parse_json3(json.loads(raw.decode("utf-8", "replace")))
            else:
                segments = _parse_vtt(raw.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            continue
        segments = _dedupe([s for s in segments if s.text])
        if segments:
            return segments, method, track_lang
    return None


# ---------------------------------------------------------------------------
# Whisper path
# ---------------------------------------------------------------------------

def cuda_available() -> bool:
    """Whether CTranslate2 can see a usable CUDA device.

    Only answers "is a GPU visible" -- it does NOT prove inference works,
    because the usual failure (missing cuDNN/cuBLAS DLLs) surfaces at
    model load, not here. That's why the caller still falls back.
    """
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 -- any import/driver problem means "no"
        return False


def resolve_whisper_device(preference: str = "auto") -> tuple[str, str]:
    """-> (device, compute_type) for faster-whisper.

    float16 on GPU and int8 on CPU: those are the fastest types each
    backend actually supports well (int8 is the point of running on CPU
    at all; float16 is the point of running on a GPU).

    "cuda" requested explicitly with no GPU present is an error, not a
    silent downgrade -- someone who asked for the GPU should be told it
    isn't there rather than wonder why it's still slow. "auto" downgrades
    quietly, which is the whole job of "auto".
    """
    preference = (preference or "auto").strip().lower()
    if preference == "cpu":
        return "cpu", "int8"
    if preference == "cuda":
        if not cuda_available():
            raise EngineError(
                "GPU transcription was requested but no CUDA device was found. "
                "Set the transcription device back to Auto or CPU, or check "
                "your NVIDIA driver."
            )
        return "cuda", "float16"
    return ("cuda", "float16") if cuda_available() else ("cpu", "int8")


def transcribe_audio_file(
    audio_path: Path,
    model_size: str = DEFAULT_WHISPER_MODEL,
    language: str | None = None,
    on_status: "callable | None" = None,
    device: str = "auto",
) -> tuple[list[TranscriptSegment], str | None, str]:
    """Local transcription via faster-whisper.

    Returns (segments, detected_language, device_used).

    Runs on the GPU when one is available, which is several times faster
    than CPU -- but every GPU path falls back to CPU rather than failing
    the transcript, because the ways a CUDA setup breaks (missing cuDNN,
    a driver mismatch, or simply not enough VRAM for a bigger model on a
    laptop card) all surface as an exception at load or inference time
    rather than as a clean "no GPU here" answer.

    The model downloads itself on first use (a few hundred MB for
    "small"), which is why callers get an on_status heads-up rather than
    a silent multi-minute stall.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on env
        raise EngineError(whisper_unavailable_message()) from exc

    device, compute_type = resolve_whisper_device(device)

    def _load(dev: str, ctype: str):
        if on_status:
            where = "GPU" if dev == "cuda" else "CPU"
            on_status(
                f"Loading Whisper model '{model_size}' on {where} "
                "(first run downloads it, this can take a few minutes)…"
            )
        return WhisperModel(model_size, device=dev, compute_type=ctype)

    def _fallback_to_cpu(reason: str):
        if on_status:
            on_status(f"GPU transcription unavailable ({reason}); using CPU instead…")
        return _load("cpu", "int8"), "cpu", "int8"

    try:
        model = _load(device, compute_type)
    except Exception as exc:  # noqa: BLE001
        if device != "cuda":
            raise EngineError(f"Couldn't load the Whisper model: {exc}") from exc
        try:
            model, device, compute_type = _fallback_to_cpu(str(exc).splitlines()[0])
        except Exception as cpu_exc:  # noqa: BLE001
            raise EngineError(f"Couldn't load the Whisper model: {cpu_exc}") from cpu_exc

    def _run(m):
        raw_segments, info = m.transcribe(
            str(audio_path), language=language, vad_filter=True
        )
        # Materialised here, not returned lazily: faster-whisper yields
        # segments as it decodes, so a GPU failure mid-stream would
        # otherwise escape this function's fallback and surface to the
        # caller as a half-finished transcript.
        return [
            TranscriptSegment(
                start=float(s.start or 0.0),
                end=float(s.end or 0.0),
                text=(s.text or "").strip(),
            )
            for s in raw_segments
        ], info

    if on_status:
        on_status(f"Transcribing audio locally on {'GPU' if device == 'cuda' else 'CPU'}…")
    try:
        segments, info = _run(model)
    except Exception as exc:  # noqa: BLE001
        if device != "cuda":
            raise EngineError(f"Transcription failed: {exc}") from exc
        # Most likely out of VRAM for this model size. Retry on CPU
        # rather than hand back nothing.
        model, device, compute_type = _fallback_to_cpu(str(exc).splitlines()[0])
        try:
            segments, info = _run(model)
        except Exception as cpu_exc:  # noqa: BLE001
            raise EngineError(f"Transcription failed: {cpu_exc}") from cpu_exc

    detected = getattr(info, "language", None)
    return [s for s in segments if s.text], detected, device


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_transcript(
    url: str,
    lang: str | None = None,
    force_whisper: bool = False,
    whisper_model: str | None = None,
    js_runtime_path: str | None = None,
    ffmpeg_location: str | None = None,
    on_status: "callable | None" = None,
    settings=None,
    whisper_device: str | None = None,
) -> Transcript:
    """Timestamped transcript for a YouTube URL.

    Captions when available (instant), local Whisper otherwise. Raises
    EngineError with an actionable message if neither path can produce
    anything.

    lang/whisper_model default to None so an unset argument falls through
    to the user's configured value rather than to a constant baked into
    this signature.
    """
    from .config import load_settings

    settings = settings or load_settings()
    lang = lang if lang is not None else settings.transcript_language
    whisper_model = whisper_model if whisper_model is not None else settings.whisper_model
    whisper_device = (
        whisper_device if whisper_device is not None else settings.whisper_device
    )
    js_runtime_path, ffmpeg_location = resolve_runtime_paths(
        js_runtime_path, ffmpeg_location
    )
    info = extract_info(url, js_runtime_path)
    video_id = info.get("id")
    title = info.get("title")
    duration = info.get("duration")

    if not force_whisper:
        if on_status:
            on_status("Looking for YouTube captions…")
        found = _captions_from_info(info, lang, js_runtime_path)
        if found:
            segments, method, track_lang = found
            return Transcript(
                segments=segments,
                method=method,
                language=track_lang,
                video_id=video_id,
                title=title,
                duration_seconds=duration,
            )

    if not settings.allow_whisper:
        # Opt-out for anyone who'd rather fail fast than spend minutes on
        # CPU transcription -- says exactly which setting to flip.
        raise EngineError(
            "This video has no captions, and local transcription is turned off "
            "(allow_whisper). Enable it to transcribe audio directly."
        )

    if on_status:
        on_status(
            "No captions available — downloading audio to transcribe locally…"
            if not force_whisper
            else "Downloading audio to transcribe locally…"
        )
    audio_path = ensure_local_media(
        url,
        video_id=video_id or "video",
        mode=MODE_AUDIO_ONLY,
        height=None,
        audio_format="m4a",
        js_runtime_path=js_runtime_path,
        ffmpeg_location=ffmpeg_location,
        on_progress=(lambda pct, text: on_status(text)) if on_status else None,
        settings=settings,
    )
    segments, detected, device_used = transcribe_audio_file(
        audio_path,
        model_size=whisper_model,
        language=None if lang == "auto" else lang,
        on_status=on_status,
        device=whisper_device,
    )
    if not segments:
        raise EngineError(
            "No captions were available and local transcription produced no text "
            "(the video may have no speech)."
        )
    return Transcript(
        segments=segments,
        method="whisper",
        language=detected,
        video_id=video_id,
        title=title,
        duration_seconds=duration,
        device=device_used,
        note=(
            f"Transcribed locally with Whisper '{whisper_model}' on "
            f"{'GPU' if device_used == 'cuda' else 'CPU'}."
        ),
    )
