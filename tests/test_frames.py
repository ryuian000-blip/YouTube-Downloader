"""Frame extraction: the ffmpeg arguments, the timestamp maths, and the
promise not to delete files it did not write.

The only pre-existing coverage aborted inside a fake _run_ffmpeg and
sniffed two strings out of the argument list, so none of the behaviour
below -- the flag/value pairs, showinfo parsing, the cap, the staging
directory -- was actually pinned. The fake here writes real JPEG-ish
files and returns synthetic showinfo output, so the whole path runs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ytdl_engine.core import EngineError
from ytdl_engine.frames import extract_frames_from_file


def fake_ffmpeg(frame_count: int, pts: list[float] | None = None):
    """Stands in for _run_ffmpeg: writes the files ffmpeg would write and
    returns the stderr showinfo lines ffmpeg would print."""
    captured: dict = {}

    def run(args: list[str]) -> str:
        captured["args"] = args
        pattern = Path(args[-1])
        for index in range(1, frame_count + 1):
            target = pattern.with_name(pattern.name.replace("%04d", f"{index:04d}"))
            target.write_bytes(b"\xff\xd8\xff\xd9")  # minimal JPEG marker pair
        stamps = pts if pts is not None else [float(i) for i in range(frame_count)]
        return "".join(f"n:{i} pts_time:{t}\n" for i, t in enumerate(stamps))

    run.captured = captured
    return run


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not really a video")
    return path


def extract(video, out, **kwargs):
    runner = fake_ffmpeg(kwargs.pop("_frames", 3), kwargs.pop("_pts", None))
    with patch("ytdl_engine.frames._run_ffmpeg", runner):
        with patch("ytdl_engine.frames._ffmpeg_binary", return_value="ffmpeg"):
            result = extract_frames_from_file(video, out, **kwargs)
    return result, runner.captured["args"]


# ---------------------------------------------------------------------------
# ffmpeg argument construction
# ---------------------------------------------------------------------------

def test_seek_goes_before_the_input(video, tmp_path):
    """-ss after -i decodes and discards everything up to the seek point;
    before -i it seeks directly. On a long video that is the difference
    between instant and a minute."""
    _, args = extract(video, tmp_path / "out", start=600, end=610)
    assert args.index("-ss") < args.index("-i"), "-ss must precede -i"
    assert args[args.index("-ss") + 1] == "600.000"
    assert args[args.index("-t") + 1] == "10.000"


def test_interval_mode_uses_an_fps_filter(video, tmp_path):
    _, args = extract(video, tmp_path / "out", interval=5)
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("fps=1/5.0000"), vf
    assert "select=" not in vf


def test_scene_mode_uses_a_select_filter(video, tmp_path):
    _, args = extract(video, tmp_path / "out", scene_threshold=0.4)
    vf = args[args.index("-vf") + 1]
    assert "select='gt(scene,0.4)'" in vf
    assert "fps=" not in vf


def test_the_frame_cap_is_the_value_of_frames_v_not_just_present(video, tmp_path):
    """The old assertion was `"8" in args`, which passes if 8 appears as
    any argument at all -- including as the -q:v value."""
    _, args = extract(video, tmp_path / "out", max_frames=8, interval=1)
    assert args[args.index("-frames:v") + 1] == "9", "asks for cap+1 to detect overflow"


@pytest.mark.parametrize(
    "given,expected",
    [(0.0, "0.05"), (-5, "0.05"), (float("nan"), "0.05"), (2.0, "0.95"), (0.4, "0.4")],
)
def test_scene_threshold_is_clamped(video, tmp_path, given, expected):
    """Keeps the filter string well-formed whatever arrives, and is why a
    filter-injection through this value is not possible."""
    _, args = extract(video, tmp_path / "out", scene_threshold=given)
    assert f"gt(scene,{expected})" in args[args.index("-vf") + 1]


@pytest.mark.parametrize("given,expected", [(10, 160), (99999, 1920), (640, 640)])
def test_width_is_clamped(video, tmp_path, given, expected):
    _, args = extract(video, tmp_path / "out", width=given, interval=1)
    assert f"scale={expected}:-2" in args[args.index("-vf") + 1]


def test_a_short_explicit_range_tightens_the_interval(video, tmp_path):
    """A 20-second window at the 10s default would return two frames."""
    _, args = extract(video, tmp_path / "out", start=0, end=20)
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("fps=1/"), vf
    spacing = float(vf.split("fps=1/")[1].split(",")[0])
    assert spacing < 10.0, f"expected finer than the default, got {spacing}"


# ---------------------------------------------------------------------------
# Timestamps and the cap
# ---------------------------------------------------------------------------

def test_showinfo_timestamps_have_the_seek_point_added_back(video, tmp_path):
    """showinfo pts are relative to -ss, so a frame 3s into a seek to
    10:00 is at 10:03 -- not at 0:03, which is what the filenames and the
    returned timestamps would otherwise claim."""
    result, _ = extract(video, tmp_path / "out", start=600, end=610, _frames=3,
                        _pts=[0.0, 2.0, 4.0])
    assert [f.timestamp for f in result.frames] == [600.0, 602.0, 604.0]
    assert "frame_001_10-00.jpg" == result.frames[0].path.name


def test_arithmetic_spacing_when_showinfo_output_is_clipped(video, tmp_path):
    """ffmpeg's stderr can be truncated; frames must still carry sensible
    times rather than all reading 0:00."""
    result, _ = extract(video, tmp_path / "out", interval=5, _frames=4, _pts=[])
    assert [f.timestamp for f in result.frames] == [0.0, 5.0, 10.0, 15.0]


def test_exceeding_the_cap_truncates_reports_it_and_deletes_the_extras(video, tmp_path):
    out = tmp_path / "out"
    result, _ = extract(video, out, max_frames=3, interval=1, _frames=5)
    assert len(result.frames) == 3
    assert result.truncated is True
    assert result.note and "cap" in result.note
    assert not list(out.glob("raw_*.jpg")), "intermediates must not survive"


def test_landing_exactly_on_the_cap_is_not_reported_as_truncated(video, tmp_path):
    result, _ = extract(video, tmp_path / "out", max_frames=3, interval=1, _frames=3)
    assert len(result.frames) == 3
    assert result.truncated is False
    assert result.note is None


def test_no_frames_is_an_explained_error_not_an_empty_result(video, tmp_path):
    with pytest.raises(EngineError, match="no frames"):
        extract(video, tmp_path / "out", interval=1, _frames=0)


def test_a_missing_video_file_says_so(tmp_path):
    with pytest.raises(EngineError, match="No such video file"):
        extract_frames_from_file(tmp_path / "nope.mp4", tmp_path / "out")


# ---------------------------------------------------------------------------
# Not destroying the caller's files
# ---------------------------------------------------------------------------

def test_it_does_not_delete_pre_existing_files_in_a_callers_directory(video, tmp_path):
    """--out could be a photo folder. This used to unlink every
    frame_*.jpg and raw_*.jpg in the destination on sight."""
    out = tmp_path / "my_photos"
    out.mkdir()
    keep = out / "frame_holiday.jpg"
    keep.write_bytes(b"precious")
    also_keep = out / "raw_scan.jpg"
    also_keep.write_bytes(b"also precious")

    extract(video, out, interval=1, _frames=2)

    assert keep.read_bytes() == b"precious", "deleted a file it never wrote"
    assert also_keep.read_bytes() == b"also precious"


def test_intermediates_never_appear_in_the_callers_directory(video, tmp_path):
    out = tmp_path / "out"
    result, _ = extract(video, out, interval=1, _frames=3)
    assert not list(out.glob("raw_*.jpg"))
    assert not [p for p in out.iterdir() if p.is_dir()], "staging dir left behind"
    assert all(f.path.parent == out.resolve() for f in result.frames)


def test_the_staging_directory_is_cleaned_up_after_a_failure(video, tmp_path):
    out = tmp_path / "out"

    def exploding(args):
        raise EngineError("ffmpeg fell over")

    with patch("ytdl_engine.frames._run_ffmpeg", exploding):
        with patch("ytdl_engine.frames._ffmpeg_binary", return_value="ffmpeg"):
            with pytest.raises(EngineError):
                extract_frames_from_file(video, out, interval=1)

    assert not [p for p in out.iterdir() if p.is_dir()], "staging survived a failure"


def test_a_rerun_replaces_only_the_names_it_produces(video, tmp_path):
    out = tmp_path / "out"
    extract(video, out, interval=1, _frames=2, _pts=[0.0, 1.0])
    stranger = out / "frame_999_9-99.jpg"
    stranger.write_bytes(b"from somewhere else")

    result, _ = extract(video, out, interval=1, _frames=2, _pts=[0.0, 1.0])

    assert len(result.frames) == 2
    assert stranger.exists(), "a caller's directory is not ours to tidy"
