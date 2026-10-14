"""What the app says when this build can't transcribe locally.

The release ships two assets: a standard one without the transcription
engine and a "full" one with it (see build.spec's variant block). So the
answer to "transcription isn't here" depends on where the code is running:
in a checkout it's a pip install, and in a shipped build pip does not
exist -- the download contains no Python at all. Telling a Release user to
run pip for a feature the README advertises is a dead end, which is
exactly what v1.0.0 did.

These tests pin both halves of that, because the frozen branch is the one
no developer ever sees by accident.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ytdl_engine.transcript import whisper_remedy, whisper_unavailable_message


# ---------------------------------------------------------------------------
# In a checkout: pip is the answer.
# ---------------------------------------------------------------------------

def test_a_checkout_is_told_to_pip_install():
    # sys.frozen is absent in a dev checkout, which is how the tests run.
    assert "pip install faster-whisper" in whisper_remedy()


def test_a_checkout_error_names_the_missing_package():
    message = whisper_unavailable_message()
    assert "faster-whisper isn't installed" in message
    assert "pip install faster-whisper" in message


# ---------------------------------------------------------------------------
# In a shipped build: the other download is the answer, never pip.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("platform", "asset"),
    [
        ("win32", "YouTube-Downloader-windows-full.zip"),
        ("darwin", "YouTube-Downloader-macos-full.zip"),
    ],
)
def test_a_frozen_build_names_its_platform_asset(platform, asset):
    with patch("ytdl_engine.transcript.sys") as fake_sys:
        fake_sys.frozen = True
        fake_sys.platform = platform
        remedy = whisper_remedy()
    assert asset in remedy
    # Asset filenames use hyphens, never spaces: GitHub rewrites spaces to
    # dots in release asset names, so a name with spaces stops matching
    # the download instructions. Guard the name we print, too.
    assert " " not in asset


def test_a_frozen_build_never_mentions_pip():
    """The whole point: a Release download has no Python and no pip."""
    for platform in ("win32", "darwin", "linux"):
        with patch("ytdl_engine.transcript.sys") as fake_sys:
            fake_sys.frozen = True
            fake_sys.platform = platform
            assert "pip" not in whisper_unavailable_message()


def test_a_frozen_build_explains_that_captioned_videos_still_work():
    """Otherwise this reads as "the app is broken" rather than "this one
    video needs the other download"."""
    with patch("ytdl_engine.transcript.sys") as fake_sys:
        fake_sys.frozen = True
        fake_sys.platform = "win32"
        message = whisper_unavailable_message()
    assert "captions work fine" in message
    assert "Releases page" in message
