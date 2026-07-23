# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller onefile/windowed build -- cross-platform (Windows + macOS).
# PyInstaller always builds for whatever OS it's actually running on, so
# there's no "build the Mac version from Windows" option here: run this
# with `pyinstaller build.spec` on each target OS separately (or via the
# macOS/Windows jobs in .github/workflows/build.yml, which do exactly
# that in CI so nobody needs to own both a Windows PC and a Mac).
#
#   pyinstaller build.spec
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="YouTube Downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

# Windows gets a bare windowed .exe (the EXE above), same as always. macOS
# additionally wraps that into a real .app bundle -- without this, a
# onefile EXE built on macOS is just a Unix executable, not something
# Finder treats as an app (no Dock icon, no double-click launch, no
# proper name in the menu bar).
if is_macos:
    app = BUNDLE(
        exe,
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
