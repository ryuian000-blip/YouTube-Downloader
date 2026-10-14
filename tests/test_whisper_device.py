"""GPU transcription: device selection and the fallbacks that keep it safe.

The fallbacks matter more than the happy path. A CUDA setup breaks in
ways that only surface as an exception at model load or mid-inference
(missing cuDNN, driver mismatch, or not enough VRAM for a bigger model on
a laptop card), so every one of those has to end in a CPU transcript
rather than no transcript.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ytdl_engine import EngineError
from ytdl_engine.config import load_settings, update_settings
from ytdl_engine.transcript import (
    Transcript,
    TranscriptSegment,
    resolve_whisper_device,
    transcribe_audio_file,
)


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------

def test_auto_uses_the_gpu_when_one_is_present():
    with patch("ytdl_engine.transcript.cuda_available", return_value=True):
        assert resolve_whisper_device("auto") == ("cuda", "float16")


def test_auto_falls_back_to_cpu_with_no_gpu():
    with patch("ytdl_engine.transcript.cuda_available", return_value=False):
        assert resolve_whisper_device("auto") == ("cpu", "int8")


def test_cpu_is_honoured_even_with_a_gpu_present():
    with patch("ytdl_engine.transcript.cuda_available", return_value=True):
        assert resolve_whisper_device("cpu") == ("cpu", "int8")


def test_explicitly_requesting_cuda_without_a_gpu_is_an_error():
    """Not a silent downgrade: someone who asked for the GPU should be
    told it isn't there rather than wonder why it's still slow."""
    with patch("ytdl_engine.transcript.cuda_available", return_value=False):
        with pytest.raises(EngineError) as excinfo:
            resolve_whisper_device("cuda")
    assert "no CUDA device" in str(excinfo.value)


@pytest.mark.parametrize("value", ["AUTO", " auto ", ""])
def test_device_preference_parsing_is_forgiving(value):
    with patch("ytdl_engine.transcript.cuda_available", return_value=False):
        assert resolve_whisper_device(value) == ("cpu", "int8")


def test_cuda_available_is_false_when_ctranslate2_is_unusable():
    """Any import or driver problem means "no GPU", never a crash."""
    from ytdl_engine import transcript as mod

    with patch.dict("sys.modules", {"ctranslate2": None}):
        assert mod.cuda_available() is False


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------

def fake_segment(text="hello", start=0.0, end=1.0):
    seg = MagicMock()
    seg.start, seg.end, seg.text = start, end, text
    return seg


def fake_info(language="en"):
    info = MagicMock()
    info.language = language
    return info


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "audio.m4a"
    path.write_bytes(b"not really audio")
    return path


def test_gpu_model_load_failure_falls_back_to_cpu(audio):
    """Missing cuDNN/cuBLAS shows up here, not in cuda_available()."""
    attempts = []

    def model_factory(size, device=None, compute_type=None):
        attempts.append(device)
        if device == "cuda":
            raise RuntimeError("Library cudnn_ops64_9.dll is not found")
        model = MagicMock()
        model.transcribe.return_value = ([fake_segment()], fake_info())
        return model

    notes = []
    with patch("faster_whisper.WhisperModel", side_effect=model_factory), \
         patch("ytdl_engine.transcript.cuda_available", return_value=True):
        segments, lang, device = transcribe_audio_file(
            audio, device="auto", on_status=notes.append
        )

    assert attempts == ["cuda", "cpu"], "should try the GPU, then fall back"
    assert device == "cpu"
    assert segments and segments[0].text == "hello"
    assert any("CPU" in n for n in notes), "the user should be told it fell back"


def test_gpu_inference_failure_falls_back_to_cpu(audio):
    """Out of VRAM typically fails here, after the model loaded fine."""
    devices = []

    def model_factory(size, device=None, compute_type=None):
        devices.append(device)
        model = MagicMock()
        if device == "cuda":
            model.transcribe.side_effect = RuntimeError("CUDA out of memory")
        else:
            model.transcribe.return_value = ([fake_segment("recovered")], fake_info())
        return model

    with patch("faster_whisper.WhisperModel", side_effect=model_factory), \
         patch("ytdl_engine.transcript.cuda_available", return_value=True):
        segments, lang, device = transcribe_audio_file(audio, device="auto")

    assert devices == ["cuda", "cpu"]
    assert device == "cpu"
    assert segments[0].text == "recovered"


def test_cpu_failure_is_reported_not_swallowed(audio):
    def model_factory(size, device=None, compute_type=None):
        raise RuntimeError("disk full")

    with patch("faster_whisper.WhisperModel", side_effect=model_factory), \
         patch("ytdl_engine.transcript.cuda_available", return_value=False):
        with pytest.raises(EngineError) as excinfo:
            transcribe_audio_file(audio, device="auto")
    assert "disk full" in str(excinfo.value)


def test_successful_gpu_run_reports_cuda(audio):
    def model_factory(size, device=None, compute_type=None):
        model = MagicMock()
        model.transcribe.return_value = ([fake_segment()], fake_info())
        return model

    with patch("faster_whisper.WhisperModel", side_effect=model_factory), \
         patch("ytdl_engine.transcript.cuda_available", return_value=True):
        _segments, _lang, device = transcribe_audio_file(audio, device="auto")
    assert device == "cuda"


def test_segments_are_materialised_before_returning(audio):
    """faster-whisper yields segments lazily as it decodes, so a GPU
    failure part-way through would escape the fallback and reach the
    caller as a half-finished transcript if they weren't consumed here."""
    def model_factory(size, device=None, compute_type=None):
        model = MagicMock()
        if device == "cuda":
            def exploding_generator():
                yield fake_segment("partial")
                raise RuntimeError("CUDA error: an illegal memory access")
            model.transcribe.return_value = (exploding_generator(), fake_info())
        else:
            model.transcribe.return_value = ([fake_segment("full")], fake_info())
        return model

    with patch("faster_whisper.WhisperModel", side_effect=model_factory), \
         patch("ytdl_engine.transcript.cuda_available", return_value=True):
        segments, _lang, device = transcribe_audio_file(audio, device="auto")

    assert device == "cpu", "a mid-stream GPU failure must still fall back"
    assert [s.text for s in segments] == ["full"], "no partial GPU output should leak"


# ---------------------------------------------------------------------------
# Reporting and settings
# ---------------------------------------------------------------------------

def test_transcript_carries_the_device_it_ran_on():
    transcript = Transcript(
        segments=[TranscriptSegment(0, 1, "x")], method="whisper", device="cuda"
    )
    assert transcript.as_dict()["device"] == "cuda"
    assert transcript.slice(0, 1).device == "cuda", "slicing must not lose it"


def test_caption_transcripts_have_no_device():
    transcript = Transcript(segments=[TranscriptSegment(0, 1, "x")], method="captions")
    assert transcript.as_dict()["device"] is None


def test_whisper_device_defaults_to_auto():
    assert load_settings().whisper_device == "auto"


def test_whisper_device_is_configurable():
    update_settings({"whisper_device": "cuda"})
    assert load_settings().whisper_device == "cuda"
