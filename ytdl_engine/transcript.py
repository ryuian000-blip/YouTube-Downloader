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
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp

from .core import EngineError, base_opts, format_timestamp, resolve_runtime_paths
from .download import ensure_local_media
from .info import MODE_AUDIO_ONLY, extract_info

# Preference order. json3 is YouTube's structured caption format (explicit
# start + duration per event); the others are fallbacks for tracks that
# don't offer it.
_CAPTION_FORMATS = ("json3", "srv3", "srv1", "vtt")

DEFAULT_WHISPER_MODEL = "small"


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

def transcribe_audio_file(
    audio_path: Path,
    model_size: str = DEFAULT_WHISPER_MODEL,
    language: str | None = None,
    on_status: "callable | None" = None,
) -> tuple[list[TranscriptSegment], str | None]:
    """Local transcription via faster-whisper.

    CPU + int8 by default so this works on any machine without CUDA. The
    model downloads itself on first use (a few hundred MB for "small"),
    which is why callers get an on_status heads-up rather than a silent
    multi-minute stall.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on env
        raise EngineError(
            "faster-whisper isn't installed. Run: pip install faster-whisper"
        ) from exc

    if on_status:
        on_status(
            f"Loading Whisper model '{model_size}' "
            "(first run downloads it, this can take a few minutes)…"
        )
    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
    except Exception as exc:  # noqa: BLE001
        raise EngineError(f"Couldn't load the Whisper model: {exc}") from exc

    if on_status:
        on_status("Transcribing audio locally…")
    try:
        raw_segments, info = model.transcribe(
            str(audio_path), language=language, vad_filter=True
        )
        segments = [
            TranscriptSegment(
                start=float(s.start or 0.0),
                end=float(s.end or 0.0),
                text=(s.text or "").strip(),
            )
            for s in raw_segments
        ]
    except Exception as exc:  # noqa: BLE001
        raise EngineError(f"Transcription failed: {exc}") from exc

    detected = getattr(info, "language", None)
    return [s for s in segments if s.text], detected


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_transcript(
    url: str,
    lang: str = "en",
    force_whisper: bool = False,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    js_runtime_path: str | None = None,
    ffmpeg_location: str | None = None,
    on_status: "callable | None" = None,
) -> Transcript:
    """Timestamped transcript for a YouTube URL.

    Captions when available (instant), local Whisper otherwise. Raises
    EngineError with an actionable message if neither path can produce
    anything.
    """
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
    )
    segments, detected = transcribe_audio_file(
        audio_path,
        model_size=whisper_model,
        language=None if lang == "auto" else lang,
        on_status=on_status,
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
        note=f"Transcribed locally with Whisper '{whisper_model}'.",
    )
