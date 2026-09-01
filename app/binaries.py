"""Bundled-binary discovery for the GUI.

The implementation moved to ``ytdl_engine.binaries`` so the GUI, the CLI,
and the MCP server resolve ffmpeg/ffprobe/deno identically (including the
PyInstaller-frozen ``sys._MEIPASS`` case). This module stays as the GUI's
import surface -- ``from app.binaries import detect`` still works exactly
as before.
"""

from __future__ import annotations

from ytdl_engine.binaries import (
    DENO_NAME,
    FFMPEG_NAME,
    FFPROBE_NAME,
    BinaryStatus,
    app_root,
    detect,
)

__all__ = [
    "BinaryStatus",
    "DENO_NAME",
    "FFMPEG_NAME",
    "FFPROBE_NAME",
    "app_root",
    "detect",
]
