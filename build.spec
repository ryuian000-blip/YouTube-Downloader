# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller onedir/windowed build -- cross-platform (Windows + macOS).
# PyInstaller always builds for whatever OS it's actually running on, so
# there's no "build the Mac version from Windows" option here: run this
# with `pyinstaller build.spec` on each target OS separately (or via the
# macOS/Windows jobs in .github/workflows/build.yml, which do exactly
# that in CI so nobody needs to own both a Windows PC and a Mac).
#
#   pyinstaller build.spec
#
# This is deliberately onedir, not onefile: a onefile build packs the
# whole app (including the ~100MB-each ffmpeg/ffprobe/deno binaries) into
# a single self-extracting exe that has to re-decompress its entire
# contents to a fresh temp folder on *every* launch -- measured at
# 3.7-4.5s just to get a window on screen, before the app does any real
# work, and worse under antivirus scanning of the freshly-extracted
# binaries. Onedir ships those same files already unpacked in a folder
# next to the exe, so there's nothing to extract at startup -- measured
# at ~1.1s for the same app. The output is a folder instead of a single
# portable exe, but since this already gets zipped for distribution (see
# the GitHub Actions workflow), that's a wash for whoever's downloading
# it: unzip, then run the exe inside either way.
#
# Expects ffmpeg, ffprobe, and deno to already be sitting next to this
# file -- ffmpeg.exe/ffprobe.exe/deno.exe on Windows, ffmpeg/ffprobe/deno
# (no extension) on macOS, matching whatever this is currently building
# on (see README.md "Building the app"). Missing binaries are silently
# skipped here rather than failing the build, so development builds work
# before those binaries are copied in -- the app itself shows a
# non-blocking warning at runtime if any of them didn't make it in.

import os
import sys

basedir = os.path.dirname(os.path.abspath(SPEC))
is_windows = sys.platform.startswith("win")
is_macos = sys.platform == "darwin"
exe_suffix = ".exe" if is_windows else ""


def _binary(filename: str):
    path = os.path.join(basedir, filename)
    return [(path, ".")] if os.path.isfile(path) else []


binaries = []
for name in ("ffmpeg", "ffprobe", "deno"):
    binaries += _binary(name + exe_suffix)

datas = [(os.path.join(basedir, "assets"), "assets")]

# .ico on Windows, .icns on macOS -- PyInstaller's EXE(icon=...) wants
# whichever format the target OS actually uses; a .png here would just
# be silently ignored rather than converted.
if is_windows:
    icon_name = "icon.ico"
elif is_macos:
    icon_name = "icon.icns"
else:
    icon_name = "icon.png"
icon_path = os.path.join(basedir, "assets", icon_name)
icon = icon_path if os.path.isfile(icon_path) else None

# NOTE: user-facing docs (README, the two "How To Open" guides,
# CONNECT-TO-CLAUDE.md) are deliberately NOT listed as datas here. In a
# onedir build PyInstaller puts datas inside _internal/, where nobody
# looks -- a guide the user never sees is no guide at all. The build
# scripts (build.bat / build.command) and the CI workflow copy them to
# the TOP level of the output folder instead, next to the executables.

a = Analysis(
    ["main.py"],
    pathex=[basedir],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The GUI never transcribes, serves MCP, or runs tests -- but
    # ytdl_engine.transcript imports faster_whisper inside a function, and
    # PyInstaller's analysis follows function-level imports, which would
    # drag ctranslate2 and its ~100MB+ of native libs into a build that
    # can't use them. These are agent-surface dependencies (ytdl_cli.py /
    # ytdl_mcp.py), installed in the dev venv and deliberately left out of
    # the shipped app.
    excludes=["faster_whisper", "ctranslate2", "mcp", "pytest", "onnxruntime"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YouTube Downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

# ---------------------------------------------------------------------------
# Second executable: the agent/CLI surface, shipped in the same folder.
#
# This is what makes the Claude Code integration usable by someone who
# only downloaded the app -- they register THIS exe with `claude mcp add`
# and never need Python, a checkout, or a virtualenv.
#
# It has to be a separate binary rather than an argv mode of the GUI:
# the GUI is windowed (console=False), and a windowed process on Windows
# has no usable stdin/stdout, which is precisely what an MCP stdio server
# needs to talk over. console=True here for the same reason.
#
# Its own Analysis, because it needs the MCP SDK (excluded from the GUI)
# and does not need PySide6. COLLECT below merges both into one folder;
# shared dependencies land at identical destinations and are deduped.
# ---------------------------------------------------------------------------
a_agent = Analysis(
    ["agent_entry.py"],
    pathex=[basedir],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Reached only via runtime dispatch in agent_entry/ytdl_mcp, so
        # static analysis alone doesn't always pull the whole MCP stack in.
        "mcp",
        "mcp.server.mcpserver",
        "ytdl_cli",
        "ytdl_mcp",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # No Qt in the console tool, and faster-whisper stays a dev-venv
    # extra (see requirements.txt) rather than ~100MB+ of bundled native
    # libs -- transcripts fall back to it only for caption-less videos.
    excludes=["PySide6", "shiboken6", "faster_whisper", "ctranslate2", "pytest"],
    noarchive=False,
)
pyz_agent = PYZ(a_agent.pure)

exe_agent = EXE(
    pyz_agent,
    a_agent.scripts,
    [],
    exclude_binaries=True,
    name="ytdl-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

# exclude_binaries=True above + COLLECT here is what makes this onedir:
# the binaries/data land unpacked next to the exe at build time instead
# of being packed into it and re-extracted at every startup.
coll = COLLECT(
    exe,
    exe_agent,
    a.binaries,
    a.zipfiles,
    a.datas,
    a_agent.binaries,
    a_agent.zipfiles,
    a_agent.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="YouTube Downloader",
)

# Windows gets the onedir folder (the COLLECT above), same as always.
# macOS additionally wraps that into a real .app bundle -- without this,
# a PyInstaller build on macOS is just a folder of Unix executables, not
# something Finder treats as an app (no Dock icon, no double-click
# launch, no proper name in the menu bar). BUNDLE() wraps COLLECT's
# already-unpacked onedir contents into Contents/MacOS + Contents/
# Resources, rather than wrapping the onefile EXE directly -- same
# extraction-avoidance benefit as the Windows side.
if is_macos:
    app = BUNDLE(
        coll,
        name="YouTube Downloader.app",
        icon=icon,
        bundle_identifier="com.ytdownloader.app",
        info_plist={
            "CFBundleName": "YouTube Downloader",
            "CFBundleDisplayName": "YouTube Downloader",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            # This app doesn't have an Apple Developer signing identity,
            # so it isn't signed or notarized (see README's "Building the
            # app" section for what that means for people you share the
            # .app with -- a one-time right-click > Open).
            "NSHumanReadableCopyright": "",
        },
    )
