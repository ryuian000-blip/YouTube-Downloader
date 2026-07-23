"""Locates bundled binaries (ffmpeg, ffprobe, the JS runtime) next to the
running executable.

Handles both the frozen case (PyInstaller onefile extracts to a temp dir
exposed as ``sys._MEIPASS``) and the unfrozen case (binaries expected next
to this script's own directory during development, before they've been
copied in). Never raises on missing binaries -- callers decide how to
surface that (a non-blocking warning in the status area, per the brief).
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Names PyInstaller will bundle. On Windows these carry .exe; on macOS/Linux
# (development / testing) they don't.
_IS_WINDOWS = sys.platform.startswith("win")
_EXE_SUFFIX = ".exe" if _IS_WINDOWS else ""

FFMPEG_NAME = f"ffmpeg{_EXE_SUFFIX}"
FFPROBE_NAME = f"ffprobe{_EXE_SUFFIX}"
# yt-dlp's default/recommended JS runtime, per the rebuild brief.
DENO_NAME = f"deno{_EXE_SUFFIX}"


def app_root() -> Path:
    """Directory to look for bundled binaries in.

    - Frozen (PyInstaller onefile): ``sys._MEIPASS``, the temp extraction dir.
    - Unfrozen (dev): the directory this file's package lives in, one level
      up (the project root), matching where a developer would drop binaries
      before they're wired into the PyInstaller build.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class BinaryStatus:
    ffmpeg: Path | None
    ffprobe: Path | None
    js_runtime: Path | None

    @property
    def ffmpeg_folder(self) -> str | None:
        """Folder to hand yt-dlp as ``ffmpeg_location`` (it wants a dir)."""
        return str(self.ffmpeg.parent) if self.ffmpeg else None

    @property
    def missing(self) -> list[str]:
        names = []
        if not self.ffmpeg:
            names.append("ffmpeg")
        if not self.ffprobe:
            names.append("ffprobe")
        if not self.js_runtime:
            names.append("JS runtime (deno)")
        return names

    @property
    def is_download_ready(self) -> bool:
        """ffmpeg/ffprobe are required for merging; the JS runtime is
        required for the download step to not 403. All three matter, but
        this flag specifically covers whether downloads can be attempted."""
        return bool(self.ffmpeg and self.ffprobe)


def _find(name: str, root: Path) -> Path | None:
    local = root / name
    if local.exists():
        return local
    on_path = shutil.which(name)
    return Path(on_path) if on_path else None


def detect() -> BinaryStatus:
    root = app_root()
    return BinaryStatus(
        ffmpeg=_find(FFMPEG_NAME, root),
        ffprobe=_find(FFPROBE_NAME, root),
        js_runtime=_find(DENO_NAME, root),
    )
