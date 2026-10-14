"""The agent working cache: where it lives, what it may delete, and
whether a cached copy is actually the one that was asked for.

This file exists because four separate defects lived in these 150 lines
with no tests at all: a cache_dir that aimed shutil.rmtree at any
directory, a lookup that matched on file suffix so a 480p copy answered a
1080p request, a prune that evicted the folder the caller was about to
read, and a clear that would empty a folder it had never written.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ytdl_engine.config import Settings, UserOnlySettingError, update_settings
from ytdl_engine.download import (
    CACHE_DIR_NAME,
    CACHE_MARKER_NAME,
    cache_dir_for,
    cache_root,
    cache_size_bytes,
    cache_variant_dir,
    cached_media,
    clear_cache,
    ensure_local_media,
    prune_cache,
)


@pytest.fixture
def cache_settings(tmp_path) -> Settings:
    return Settings(cache_dir=str(tmp_path / "scratch"), cache_max_mb=1)


def fill(folder: Path, megabytes: float, name: str = "video.mp4") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_bytes(b"\0" * int(megabytes * 1024 * 1024))
    return target


# ---------------------------------------------------------------------------
# Where the cache is allowed to be
# ---------------------------------------------------------------------------

def test_cache_dir_always_gets_its_own_subdirectory(tmp_path):
    """The setting names a PARENT, never the directory we delete inside.

    cache_root used to return cache_dir verbatim, which made one string
    a recursive-delete primitive: point it at Documents, set a small
    cache_max_mb, and the next transcript call pruned Documents.
    """
    settings = Settings(cache_dir=str(tmp_path / "somewhere"))
    root = cache_root(settings)
    assert root == tmp_path / "somewhere" / CACHE_DIR_NAME
    assert root.name == CACHE_DIR_NAME


def test_the_default_cache_lives_under_the_system_temp_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    root = cache_root(Settings(cache_dir=None))
    assert root.name == CACHE_DIR_NAME


def test_a_fresh_cache_root_is_marked_as_ours(cache_settings):
    root = cache_root(cache_settings)
    assert (root / CACHE_MARKER_NAME).exists()
    assert "delete" in (root / CACHE_MARKER_NAME).read_text().lower()


def test_prune_refuses_a_directory_it_did_not_create(tmp_path):
    """Belt-and-braces behind the appended segment: even handed a path
    directly, prune must not delete inside something unmarked."""
    foreign = tmp_path / "Documents"
    victim = foreign / "tax-returns"
    fill(victim, 3, name="2024.pdf")
    settings = Settings(cache_dir=None, cache_max_mb=1)

    with patch("ytdl_engine.download.cache_root", return_value=foreign):
        freed = prune_cache(settings)

    assert freed == 0
    assert victim.exists(), "pruned a directory with no marker file"


def test_clear_refuses_a_directory_it_did_not_create(tmp_path):
    foreign = tmp_path / "Pictures"
    keep = fill(foreign / "holiday", 0.01, name="beach.jpg")

    with patch("ytdl_engine.download.cache_root", return_value=foreign):
        freed = clear_cache(Settings())

    assert freed == 0
    assert keep.exists(), "cleared a directory with no marker file"


def test_an_assistant_cannot_repoint_the_cache(tmp_path):
    """cache_dir is the one path setting that feeds a delete, so it is
    user-only -- enforced in config.update_settings, which every surface
    goes through."""
    with pytest.raises(UserOnlySettingError):
        update_settings({"cache_dir": str(tmp_path)})
    # The user's own surfaces still can.
    update_settings({"cache_dir": str(tmp_path)}, allow_user_only=True)


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------

def test_prune_evicts_oldest_first_until_it_fits(cache_settings):
    root = cache_root(cache_settings)
    old, middle, new = (root / "aaa", root / "bbb", root / "ccc")
    for folder in (old, middle, new):
        fill(folder, 0.5)
    os.utime(old, (1_000, 1_000))
    os.utime(middle, (2_000, 2_000))
    os.utime(new, (3_000, 3_000))

    prune_cache(cache_settings)

    assert not old.exists(), "oldest should go first"
    assert new.exists(), "newest should survive"


def test_prune_stops_as_soon_as_it_is_under_the_limit(cache_settings):
    root = cache_root(cache_settings)
    folders = []
    for index in range(4):
        folder = root / f"v{index}"
        fill(folder, 0.4)
        os.utime(folder, (1_000 + index, 1_000 + index))
        folders.append(folder)

    prune_cache(cache_settings)

    survivors = [f for f in folders if f.exists()]
    assert survivors, "must not empty the whole cache to satisfy the limit"
    assert folders[-1] in survivors


def test_prune_never_evicts_the_folder_the_caller_is_using(cache_settings):
    """A video bigger than the whole limit used to be deleted the instant
    it finished downloading -- it was the only eviction candidate -- and
    the caller got "no media file was found on disk" after paying for the
    entire download."""
    root = cache_root(cache_settings)
    in_use = root / "the-one-we-just-fetched"
    fill(in_use, 3)  # on its own, three times the 1MB limit

    freed = prune_cache(cache_settings, keep=in_use)

    assert in_use.exists(), "evicted the folder it was told to keep"
    assert freed == 0


def test_no_limit_disables_pruning_entirely(tmp_path):
    settings = Settings(cache_dir=str(tmp_path / "scratch"), cache_max_mb=None)
    folder = cache_root(settings) / "big"
    fill(folder, 2)
    assert prune_cache(settings) == 0
    assert folder.exists()


def test_clear_empties_the_cache_but_keeps_the_marker(cache_settings):
    root = cache_root(cache_settings)
    fill(root / "one", 0.2)
    fill(root / "two", 0.2)

    freed = clear_cache(cache_settings)

    assert freed > 0
    assert list(root.iterdir()) == [root / CACHE_MARKER_NAME]


def test_cache_size_counts_nested_files(cache_settings):
    fill(cache_root(cache_settings) / "v" / "frames", 0.25, name="frame_001.jpg")
    assert cache_size_bytes(cache_settings) >= 0.25 * 1024 * 1024


# ---------------------------------------------------------------------------
# Is the cached copy the one that was asked for?
# ---------------------------------------------------------------------------

def test_a_video_id_cannot_escape_the_cache_root(cache_settings):
    folder = cache_dir_for("../../etc/passwd", cache_settings)
    assert folder.parent == cache_root(cache_settings)
    assert ".." not in folder.name


def test_quality_is_part_of_the_cache_key(cache_settings):
    low = cache_variant_dir("abc", "video_only", 480, cache_settings)
    high = cache_variant_dir("abc", "video_only", 1080, cache_settings)
    assert low != high
    # Still inside ONE per-video folder, so prune evicts them together and
    # cannot leave frames pointing at a deleted video.
    assert low.parent == high.parent == cache_dir_for("abc", cache_settings)


def test_a_480p_copy_is_not_a_hit_for_a_1080p_request(cache_settings):
    """The lookup matched on file suffix alone, so asking for 1080p after
    a 480p extraction silently returned the 480p file with a path that
    looked perfectly correct."""
    low_dir = cache_variant_dir("abc", "video_only", 480, cache_settings)
    fill(low_dir, 0.01, name="clip.mp4")

    high_dir = cache_variant_dir("abc", "video_only", 1080, cache_settings)
    assert cached_media("abc", (".mp4",), folder=high_dir) is None
    assert cached_media("abc", (".mp4",), folder=low_dir) is not None


def test_zero_byte_and_wrong_suffix_files_are_not_hits(cache_settings):
    folder = cache_variant_dir("abc", "video_only", 720, cache_settings)
    (folder / "empty.mp4").write_bytes(b"")
    (folder / "notes.txt").write_text("hello")
    assert cached_media("abc", (".mp4",), folder=folder) is None


def test_ensure_local_media_reuses_a_matching_copy_without_downloading(cache_settings):
    folder = cache_variant_dir("xyz", "video_only", 480, cache_settings)
    existing = fill(folder, 0.01, name="clip.mp4")

    with patch("ytdl_engine.download.download") as download:
        result = ensure_local_media(
            "https://youtu.be/xyz",
            video_id="xyz",
            mode="video_only",
            height=480,
            settings=cache_settings,
        )

    assert result == existing
    assert download.call_count == 0, "a cache hit must not re-download"


def test_ensure_local_media_downloads_into_the_variant_folder(cache_settings):
    """Guards the wiring: the download has to land where the lookup looks."""
    captured = {}

    def fake_download(options, **kwargs):
        captured["output_dir"] = options.output_dir
        written = fill(Path(options.output_dir), 0.01, name="clip.mp4")

        class Result:
            path = written
        return Result()

    with patch("ytdl_engine.download.download", fake_download):
        result = ensure_local_media(
            "https://youtu.be/xyz",
            video_id="xyz",
            mode="video_only",
            height=1080,
            settings=cache_settings,
        )

    assert captured["output_dir"] == cache_variant_dir(
        "xyz", "video_only", 1080, cache_settings
    )
    assert result.exists()
