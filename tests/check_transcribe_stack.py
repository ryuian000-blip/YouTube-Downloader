"""Can faster-whisper actually decode audio with the versions installed?

Run by CI on the "full" build legs, before anything is packaged. The full
variant exists only to transcribe caption-less videos, and the stack it
needs is resolved fresh on every CI build:

    faster-whisper 1.2.1 -> av>=11      (unbounded!)
    faster_whisper/audio.py:46          av.open(..., metadata_errors="ignore")

PyAV 19.0 removed `metadata_errors`, so a fresh install takes 19.x and
every transcription dies with "open() got an unexpected keyword argument
'metadata_errors'". v1.0.1 shipped a full build that could not transcribe
at all for exactly this reason, and nothing in the build caught it --
`import faster_whisper` succeeds fine, and the test suite mocks Whisper
rather than decoding real audio.

So decode a second of generated silence, which costs nothing and needs no
network and no Whisper model, and fail the build if the audio path is
broken. Deliberately NOT a pytest test: the point is to check the
versions CI resolved, not the dev venv's, and to run before packaging.

    python tests/check_transcribe_stack.py
"""

from __future__ import annotations

import struct
import sys
import tempfile
import wave
from pathlib import Path

SAMPLE_RATE = 16000  # what Whisper wants, so this is the realistic path


def write_silence(path: Path, seconds: int = 1) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack("<h", 0) * (SAMPLE_RATE * seconds))


def main() -> int:
    import av

    from faster_whisper.audio import decode_audio

    print(f"av            {av.__version__}")
    try:
        import ctranslate2

        print(f"ctranslate2   {ctranslate2.__version__}")
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        print(f"ctranslate2   unavailable: {exc}")

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "silence.wav"
        write_silence(wav)
        samples = decode_audio(str(wav), sampling_rate=SAMPLE_RATE)

    count = len(samples)
    print(f"decoded       {count} samples from 1s of silence")
    if count < SAMPLE_RATE // 2:
        print(
            f"FAIL: expected about {SAMPLE_RATE} samples, got {count} -- "
            "the audio path decoded almost nothing."
        )
        return 1

    print("TRANSCRIPTION STACK OK -- faster-whisper can decode audio")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - this is the point of the check
        print(f"FAIL: the transcription stack is broken: {type(exc).__name__}: {exc}")
        print(
            "\nIf this mentions a keyword argument, a dependency has moved "
            "under faster-whisper -- see the `av` pin in requirements.txt."
        )
        sys.exit(1)
