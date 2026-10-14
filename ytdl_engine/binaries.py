"""Locates the bundled binaries (ffmpeg, ffprobe, the JS runtime).

This is the single implementation -- ``app/binaries.py`` re-exports from
here so the GUI, the CLI, and the MCP server all resolve binaries
identically, including the PyInstaller-frozen case.

Handles both the frozen case (PyInstaller extracts/collects to a dir
exposed as ``sys._MEIPASS``) and the unfrozen case (binaries expected at
the repo root during development). Never raises on missing binaries --
callers decide how to surface that (the GUI shows a non-blocking warning;
the CLI/MCP report it in their JSON error payloads).
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Names PyInstaller will bundle. On Windows these carry .exe; on macOS/Linux
# they don't.
_IS_WINDOWS = sys.platform.startswith("win")
_EXE_SUFFIX = ".exe" if _IS_WINDOWS else ""

FFMPEG_NAME = f"ffmpeg{_EXE_SUFFIX}"
FFPROBE_NAME = f"ffprobe{_EXE_SUFFIX}"
# yt-dlp's default/recommended JS runtime. Required even for metadata-only
# extraction: YouTube's nsig/JS challenge has to be solved just to list
# formats. Pairs with the yt-dlp-ejs package (see requirements.txt), which
# carries the solver scripts this engine runs.
DENO_NAME = f"deno{_EXE_SUFFIX}"


def app_root() -> Path:
    """Directory to look for bundled binaries in.

    - Frozen (PyInstaller): ``sys._MEIPASS``, the collected-data dir.
    - Unfrozen (dev): the repo root (one level up from this package),
      matching where a developer drops the binaries.
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
        """Whether a download can actually be expected to succeed.

        All three are required, and the JS runtime is not optional: without
        deno, yt-dlp cannot solve YouTube's nsig challenge and downloads
        fail with a 403 that looks like a network problem. This used to
        return ``ffmpeg and ffprobe`` only, which made the MCP
        ``check_setup`` tool report ready=true on machines where every
        single download 403'd -- the CLI's ``doctor`` disagreed with it
        because that one checks ``missing`` instead. One definition now.
        """
        return bool(self.ffmpeg and self.ffprobe and self.js_runtime)


def _find(name: str, root: Path) -> Path | None:
    local = root / name
    if local.exists():
        return local
    # path=PATH explicitly, NOT shutil.which(name): on Windows, which()
    # prepends the current working directory when path is None (CPython's
    # _win_path_needs_curdir branch), so a planted ffmpeg.exe in whatever
    # folder the CLI or MCP server happened to be launched from would win
    # over the real one on PATH. Passing path skips that insertion.
    on_path = shutil.which(name, path=os.environ.get("PATH"))
    return Path(on_path) if on_path else None


def detect() -> BinaryStatus:
    root = app_root()
    return BinaryStatus(
        ffmpeg=_find(FFMPEG_NAME, root),
        ffprobe=_find(FFPROBE_NAME, root),
        js_runtime=_find(DENO_NAME, root),
    )
