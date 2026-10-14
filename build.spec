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

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

basedir = os.path.dirname(os.path.abspath(SPEC))
is_windows = sys.platform.startswith("win")
is_macos = sys.platform == "darwin"
exe_suffix = ".exe" if is_windows else ""

# ---------------------------------------------------------------------------
# Build variant -- "standard" (default) or "full".
#
#   pyinstaller build.spec                          -> standard
#   YTDL_BUILD_VARIANT=full pyinstaller build.spec  -> full
#
# The ONLY difference is whether local transcription is bundled. Both
# variants contain the same app, the same two executables, and the same
# ffmpeg/ffprobe/deno; "full" additionally carries faster-whisper and
# CTranslate2 so a caption-less video can be transcribed on the machine.
#
# Why this is a variant and not just always-on: transcription is the only
# feature in here with a native-code dependency -- a ~59MB CTranslate2
# DLL, ONNX Runtime for the voice-activity filter, numpy and tokenizers.
# Measured on Windows at v1.0.1: 494MB -> 683MB unpacked, 205MB -> 281MB
# zipped, so +76MB (+37%) on the download. The large majority of YouTube
# videos have captions and need none of it, so making every downloader
# pay that to serve the minority who hit a caption-less video is the
# wrong default. Two assets, clearly labelled, is the trade.
#
# What is deliberately NOT bundled: CUDA. CTranslate2 resolves
# cublas64_12.dll at runtime and those libraries are ~560MB on their own,
# which would roughly triple the download to serve the few machines that
# have an NVIDIA GPU *and* no CUDA Toolkit already. Without them the GPU
# load raises and transcript.py's existing CPU fallback takes over, so a
# Release user transcribes on CPU unless they have the Toolkit installed.
# See README.md's note under the transcription section.
#
# Why not two spec files: everything else about the two builds has to stay
# identical, and a copy would drift. One spec, one switch.
# ---------------------------------------------------------------------------
variant = os.environ.get("YTDL_BUILD_VARIANT", "standard").strip().lower()
if variant not in ("standard", "full"):
    raise SystemExit(
        f"YTDL_BUILD_VARIANT must be 'standard' or 'full', got {variant!r}"
    )
is_full = variant == "full"
print(f"build.spec: building the {variant.upper()} variant")

# faster-whisper imports ctranslate2 (the inference engine) and, because
# the app transcribes with vad_filter=True, onnxruntime plus the bundled
# Silero VAD model. Excluded from the standard build, required by the full
# one -- one list so the two targets below can never disagree about it.
TRANSCRIBE_MODULES = ["faster_whisper", "ctranslate2", "onnxruntime"]

# Neither faster-whisper nor CTranslate2 ships a PyInstaller hook, and
# both have pieces static analysis cannot see:
#   - faster_whisper/assets/silero_vad_v6.onnx is opened by path at
#     runtime (utils.get_assets_path()), not imported.
#   - ctranslate2's DLLs sit next to its .pyd and are loaded by the OS
#     loader / lazily by ctranslate2.dll itself; cudnn64_9.dll in
#     particular is only pulled in when a CUDA device is actually used,
#     so dependency scanning the .pyd alone misses it.
# collect_* resolve both from the installed package, so this follows
# whatever version requirements.txt happens to pin.
transcribe_datas = []
transcribe_binaries = []
transcribe_hiddenimports = []
if is_full:
    transcribe_datas += collect_data_files("faster_whisper")
    transcribe_binaries += collect_dynamic_libs("ctranslate2")
    transcribe_hiddenimports += [
        "faster_whisper",
        "ctranslate2",
        # Reached through faster_whisper.vad, which the transcribe path
        # only touches at call time.
        "onnxruntime",
    ]


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
    binaries=binaries + transcribe_binaries,
    datas=datas + transcribe_datas,
    hiddenimports=transcribe_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The GUI never serves MCP or runs tests. Transcription is the
    # variant switch: ytdl_engine.transcript imports faster_whisper inside
    # a function and PyInstaller's analysis follows function-level
    # imports, so in a STANDARD build these have to be named here or
    # ctranslate2's native libs come along anyway.
    #
    # In a FULL build they stay in -- note this is currently belt and
    # braces for the GUI specifically, because no app/ file calls
    # get_transcript at all yet (CODE_AUDIT.md M9): transcription is
    # reached through ytdl-agent, the other executable. Kept in step with
    # the agent target anyway, so that wiring the GUI up to it later is a
    # one-file change and not a build mystery.
    excludes=["mcp", "pytest"] + ([] if is_full else TRANSCRIBE_MODULES),
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
    binaries=transcribe_binaries,
    datas=transcribe_datas,
    hiddenimports=[
        # Reached only via runtime dispatch in agent_entry/ytdl_mcp, so
        # static analysis alone doesn't always pull the whole MCP stack in.
        "mcp",
        "mcp.server.mcpserver",
        "ytdl_cli",
        "ytdl_mcp",
    ]
    + transcribe_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # No Qt in the console tool either way. Transcription follows the
    # variant, same as the GUI above: in a standard build faster-whisper
    # stays a dev-venv extra (see requirements.txt) and get_transcript
    # serves only captioned videos; in a full build the agent surface
    # transcribes caption-less ones too.
    excludes=["PySide6", "shiboken6", "pytest"]
    + ([] if is_full else TRANSCRIBE_MODULES),
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
            "CFBundleShortVersionString": "1.0.1",
            "NSHighResolutionCapable": True,
            # This app doesn't have an Apple Developer signing identity,
            # so it isn't signed or notarized (see README's "Building the
            # app" section for what that means for people you share the
            # .app with -- a one-time right-click > Open).
            "NSHumanReadableCopyright": "",
        },
    )
