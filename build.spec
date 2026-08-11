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

a = Analysis(
    ["main.py"],
    pathex=[basedir],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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

# exclude_binaries=True above + COLLECT here is what makes this onedir:
# the binaries/data land unpacked next to the exe at build time instead
# of being packed into it and re-extracted at every startup.
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
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
