"""Engine tests -- no Qt, no network.

Anything that would hit YouTube is mocked at the yt_dlp.YoutubeDL
boundary; the real-network checks live in tests/smoke_network.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ytdl_engine import (
    DownloadOptions,
    EngineError,
    MODE_AUDIO_ONLY,
    MODE_VIDEO,
    MODE_VIDEO_ONLY,
    available_heights,
    format_string,
    format_timestamp,
    parse_timestamp,
    strip_ansi,
)
# The submodule, not the re-exported `download` FUNCTION of the same name
# that `from ytdl_engine import download` would bind.
import ytdl_engine.download as engine_download
from ytdl_engine.transcript import (
    Transcript,
    TranscriptSegment,
    _dedupe,
    _parse_json3,
    _parse_vtt,
    _pick_track,
)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0:00"), (9, "0:09"), (61, "1:01"), (600, "10:00"), (3661, "1:01:01")],
)
def test_format_timestamp(seconds, expected):
    assert format_timestamp(seconds) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (90, 90.0),
        ("90", 90.0),
        ("1:30", 90.0),
        ("01:02:03", 3723.0),
        ("2m30s", 150.0),
        ("1h", 3600.0),
        (None, None),
        ("", None),
    ],
)
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == expected


def test_parse_timestamp_rejects_nonsense():
    with pytest.raises(EngineError):
        parse_timestamp("half past four")


def test_strip_ansi_removes_yt_dlp_colour_codes():
    assert strip_ansi("\x1b[0;32m6.25MiB/s\x1b[0m") == "6.25MiB/s"


@pytest.mark.parametrize(
    "mode,height,expected",
    [
        (MODE_VIDEO, 1080, "bv*[height<=1080]+ba/b[height<=1080]"),
        (MODE_VIDEO, None, "bv*+ba/b"),
        (MODE_VIDEO_ONLY, 720, "bv*[height<=720]"),
        (MODE_AUDIO_ONLY, 1080, "bestaudio/best"),
    ],
)
def test_format_string(mode, height, expected):
    assert format_string(mode, height) == expected


def test_base_opts_silences_yt_dlps_own_progress_bar():
    """quiet=True does NOT stop yt-dlp drawing its progress bar, and it
    draws it to stdout -- which broke the CLI's "JSON on stdout, nothing
    else" contract (jq choked on ~30 progress lines before the JSON).
    Every surface reports progress via its own hook, so yt-dlp's display
    is redundant as well as harmful."""
    from ytdl_engine.core import base_opts

    opts = base_opts()
    assert opts["noprogress"] is True
    assert opts["quiet"] is True
    assert opts["no_color"] is True


def test_available_heights_ignores_audio_only_formats():
    info = {
        "formats": [
            {"height": 1080, "vcodec": "avc1"},
            {"height": 720, "vcodec": "avc1"},
            {"height": 720, "vcodec": "avc1"},   # duplicate
            {"height": None, "vcodec": "none", "acodec": "mp4a"},
        ]
    }
    assert available_heights(info) == [1080, 720]


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------

def make_options(tmp_path, **overrides):
    base = dict(
        url="https://youtu.be/fake",
        mode=MODE_VIDEO,
        height=None,
        audio_format="mp3",
        include_subtitles=False,
        embed_thumbnail=False,
        output_dir=tmp_path,
        ffmpeg_location=None,
        js_runtime_path=None,
        force_overwrite=False,
    )
    base.update(overrides)
    return DownloadOptions(**base)


def mock_ydl(side_effect):
    instance = MagicMock()
    instance.__enter__.return_value.extract_info.side_effect = side_effect
    return instance


def test_download_retries_transient_failures_then_succeeds(tmp_path):
    """A mid-download 403 usually means the signed URL went stale, and a
    fresh extract_info resolves a new one -- which is exactly what
    clicking Download again did by hand."""
    calls = {"n": 0}

    def flaky(url, download=True):
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("HTTP Error 403: Forbidden")
        return {"requested_downloads": [{"__real_download": True, "filepath": "out.mp4"}]}

    messages = []
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(flaky)), \
         patch("ytdl_engine.core.time.sleep") as sleep:
        result = engine_download.download(
            make_options(tmp_path), on_progress=lambda pct, text: messages.append(text)
        )

    assert calls["n"] == 3
    assert result.message == engine_download.DOWNLOAD_COMPLETE_MESSAGE
    assert sum("Retrying" in m for m in messages) == 2
    assert sleep.call_count == 2


def test_download_gives_up_after_max_attempts_reporting_the_last_error(tmp_path):
    calls = {"n": 0}

    def always_fails(url, download=True):
        calls["n"] += 1
        raise Exception(f"HTTP Error 403: Forbidden (attempt {calls['n']})")

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(always_fails)), \
         patch("ytdl_engine.core.time.sleep"):
        with pytest.raises(EngineError) as excinfo:
            engine_download.download(make_options(tmp_path))

    assert calls["n"] == 3
    assert "attempt 3" in str(excinfo.value)


def test_first_attempt_success_adds_no_retry_delay(tmp_path):
    def immediate(url, download=True):
        return {"requested_downloads": [{"__real_download": True}]}

    messages = []
    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(immediate)), \
         patch("ytdl_engine.core.time.sleep") as sleep:
        engine_download.download(
            make_options(tmp_path), on_progress=lambda pct, text: messages.append(text)
        )

    assert sleep.call_count == 0
    assert not any("Retrying" in m for m in messages)


def test_skipped_file_reports_already_downloaded(tmp_path):
    def skipped(url, download=True):
        return {"requested_downloads": [{"__real_download": False}]}

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(skipped)):
        result = engine_download.download(make_options(tmp_path))

    assert result.message == engine_download.ALREADY_DOWNLOADED_MESSAGE
    assert result.real_download is False


def test_force_overwrite_reports_a_real_download(tmp_path):
    def skipped(url, download=True):
        return {"requested_downloads": [{"__real_download": False}]}

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(skipped)):
        result = engine_download.download(make_options(tmp_path, force_overwrite=True))

    assert result.message == engine_download.DOWNLOAD_COMPLETE_MESSAGE


def test_audio_mode_adds_the_extract_audio_postprocessor(tmp_path):
    opts = engine_download.build_ydl_opts(
        make_options(tmp_path, mode=MODE_AUDIO_ONLY, audio_format="mp3")
    )
    keys = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegExtractAudio" in keys
    assert "merge_output_format" not in opts


def test_video_mode_merges_to_mp4(tmp_path):
    opts = engine_download.build_ydl_opts(make_options(tmp_path, mode=MODE_VIDEO))
    assert opts["merge_output_format"] == "mp4"


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def test_parse_json3_captions():
    payload = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Hello "}, {"utf8": "world"}]},
            {"tStartMs": 2000, "dDurationMs": 1500, "segs": [{"utf8": "second line"}]},
            {"tStartMs": 4000, "dDurationMs": 500, "segs": [{"utf8": "\n"}]},  # blank
        ]
    }
    segments = _parse_json3(payload)
    assert [s.text for s in segments] == ["Hello world", "second line"]
    assert segments[0].start == 0.0
    assert segments[0].end == 2.0


def test_parse_vtt_captions():
    vtt = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\nFirst cue\n\n"
        "00:00:03.500 --> 00:00:05.000\nSecond cue\n"
    )
    segments = _parse_vtt(vtt)
    assert [s.text for s in segments] == ["First cue", "Second cue"]
    assert segments[0].start == 1.0
    assert segments[1].end == 5.0


def test_dedupe_collapses_repeated_rolling_captions():
    segments = [
        TranscriptSegment(0, 1, "hello"),
        TranscriptSegment(1, 2, "hello"),
        TranscriptSegment(2, 3, "world"),
    ]
    out = _dedupe(segments)
    assert [s.text for s in out] == ["hello", "world"]
    assert out[0].end == 2, "merged repeats should extend the first cue's end"


def test_pick_track_prefers_exact_language_then_variants():
    tracks = {
        "en-US": [{"ext": "json3", "url": "u1"}],
        "fr": [{"ext": "json3", "url": "u2"}],
    }
    lang, entry = _pick_track(tracks, "en")
    assert lang == "en-US" and entry["url"] == "u1"


def test_pick_track_falls_back_to_any_language_for_english_requests():
    tracks = {"de": [{"ext": "json3", "url": "u3"}]}
    picked = _pick_track(tracks, "en")
    assert picked is not None and picked[0] == "de"


def test_pick_track_returns_none_when_nothing_usable():
    assert _pick_track({}, "en") is None
    assert _pick_track({"en": [{"ext": "exotic", "url": "u"}]}, "en") is None


def sample_transcript():
    return Transcript(
        segments=[
            TranscriptSegment(0, 5, "intro"),
            TranscriptSegment(5, 10, "middle"),
            TranscriptSegment(10, 15, "end"),
        ],
        method="captions",
        language="en",
        video_id="abc",
        title="Sample",
        duration_seconds=15,
    )


def test_transcript_slice_keeps_overlapping_segments():
    """Asking for 6-7s should include the sentence already in progress."""
    sliced = sample_transcript().slice(6, 7)
    assert [s.text for s in sliced.segments] == ["middle"]


def test_transcript_slice_includes_partial_overlaps_at_both_ends():
    sliced = sample_transcript().slice(4, 11)
    assert [s.text for s in sliced.segments] == ["intro", "middle", "end"]


def test_transcript_slice_with_no_bounds_is_a_noop():
    transcript = sample_transcript()
    assert transcript.slice(None, None) is transcript


def test_transcript_renders_text_srt_and_dict():
    transcript = sample_transcript()
    assert "[0:00] intro" in transcript.as_text()
    srt = transcript.as_srt()
    assert "00:00:00,000 --> 00:00:05,000" in srt
    assert srt.startswith("1\n")
    payload = transcript.as_dict()
    assert payload["segment_count"] == 3
    assert payload["segments"][0]["start_hms"] == "0:00"
    # Must stay JSON-serializable: it crosses a CLI/MCP boundary.
    json.dumps(payload)


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

def test_extract_frames_rejects_a_missing_file(tmp_path):
    from ytdl_engine.frames import extract_frames_from_file

    with pytest.raises(EngineError):
        extract_frames_from_file(tmp_path / "nope.mp4", tmp_path / "out")


def test_frameset_is_json_serializable(tmp_path):
    from ytdl_engine.frames import Frame, FrameSet

    frame_set = FrameSet(
        frames=[Frame(path=tmp_path / "frame_001_0-10.jpg", timestamp=10.0)],
        output_dir=tmp_path,
        mode="interval",
    )
    payload = frame_set.as_dict()
    json.dumps(payload)
    assert payload["frames"][0]["timestamp_hms"] == "0:10"


# ---------------------------------------------------------------------------
# Binary discovery stays shared with the GUI
# ---------------------------------------------------------------------------

def test_app_binaries_is_the_same_implementation_as_the_engine():
    from app import binaries as app_binaries
    from ytdl_engine import binaries as engine_binaries

    assert app_binaries.detect is engine_binaries.detect
    assert app_binaries.app_root() == engine_binaries.app_root()


# ---------------------------------------------------------------------------
# The GUI's Qt wrappers actually drive the engine
# ---------------------------------------------------------------------------

def test_download_worker_runs_the_engine_and_emits_its_message(qapp, tmp_path):
    """Guards a bug this refactor introduced and shipped un-caught for a
    moment: `from ytdl_engine import download` binds the re-exported
    FUNCTION, so `engine_download.download(...)` raised AttributeError the
    first time a real download ran. Nothing exercised DownloadWorker.run()
    end to end, so every other test still passed. This one does."""
    from app.workers import DownloadWorker

    def immediate(url, download=True):
        return {"requested_downloads": [{"__real_download": True, "filepath": "out.mp4"}]}

    emitted = {}
    worker = DownloadWorker(make_options(tmp_path))
    worker.succeeded.connect(lambda msg: emitted.setdefault("succeeded", msg))
    worker.failed.connect(lambda msg: emitted.setdefault("failed", msg))

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(immediate)):
        worker.run()  # run() directly: no thread needed, and no event loop to pump

    assert emitted.get("succeeded") == engine_download.DOWNLOAD_COMPLETE_MESSAGE
    assert "failed" not in emitted


def test_download_worker_reports_engine_errors_as_plain_messages(qapp, tmp_path):
    from app.workers import DownloadWorker

    def always_fails(url, download=True):
        raise Exception("HTTP Error 403: Forbidden")

    emitted = {}
    worker = DownloadWorker(make_options(tmp_path))
    worker.succeeded.connect(lambda msg: emitted.setdefault("succeeded", msg))
    worker.failed.connect(lambda msg: emitted.setdefault("failed", msg))

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(always_fails)), \
         patch("ytdl_engine.core.time.sleep"):
        worker.run()

    assert "403" in emitted.get("failed", "")
    assert "Traceback" not in emitted.get("failed", "")


def test_fetch_worker_emits_video_info_without_retrying(qapp):
    """A typo'd URL should report back immediately -- fetch deliberately
    does not retry, unlike download."""
    from app.workers import FetchWorker

    calls = {"n": 0}

    def failing(url, download=False):
        calls["n"] += 1
        raise Exception("Video unavailable")

    emitted = {}
    worker = FetchWorker("https://youtu.be/bad")
    worker.succeeded.connect(lambda info: emitted.setdefault("info", info))
    worker.failed.connect(lambda msg: emitted.setdefault("failed", msg))

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(failing)), \
         patch("ytdl_engine.core.time.sleep") as sleep:
        worker.run()

    assert calls["n"] == 1, "fetch should try exactly once"
    assert sleep.call_count == 0
    assert emitted.get("failed")


def test_fetch_worker_builds_video_info_from_engine_output(qapp):
    from app.workers import FetchWorker

    info_payload = {
        "id": "abc",
        "title": "A Video",
        "thumbnail": None,
        "formats": [
            {"height": 1080, "vcodec": "avc1"},
            {"height": 720, "vcodec": "avc1"},
        ],
    }

    def ok(url, download=False):
        return info_payload

    emitted = {}
    worker = FetchWorker("https://youtu.be/abc")
    worker.succeeded.connect(lambda info: emitted.setdefault("info", info))
    worker.failed.connect(lambda msg: emitted.setdefault("failed", msg))

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl(ok)):
        worker.run()

    result = emitted.get("info")
    assert result is not None, emitted.get("failed")
    assert result.title == "A Video"
    assert result.heights == [1080, 720]
    assert result.raw is info_payload
