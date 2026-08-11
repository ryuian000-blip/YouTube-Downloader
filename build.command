#!/bin/bash
# Builds the standalone YouTube Downloader.app with PyInstaller, on macOS.
# Double-click this file in Finder, or run it from a terminal.
#
# Expects ffmpeg, ffprobe, and deno (no file extension, unlike the
# Windows .exe versions) to already be sitting in this same folder --
# build.spec bundles them if present and silently skips any that are
# missing (see README.md).

cd "$(dirname "$0")" || exit 1

pause_and_exit() {
    echo
    read -n 1 -s -r -p "Press any key to close this window..."
    echo
    exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
    echo
    echo "Python wasn't found on this computer."
    echo "Install Python 3.10 or newer from https://www.python.org/downloads/macos/"
    echo "(or via Homebrew: brew install python), then run this file again."
    pause_and_exit
fi

if [ ! -d ".venv" ]; then
    echo "Setting up a virtual environment (first run only)..."
    if ! python3 -m venv .venv; then
        echo
        echo "Failed to create the virtual environment. See the error above."
        pause_and_exit
    fi
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

echo "Checking dependencies (this only installs anything the first time)..."
if ! python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt; then
    echo
    echo "Failed to install dependencies. See the error above."
    pause_and_exit
fi

echo
missing=0
for name in ffmpeg ffprobe deno; do
    if [ ! -f "$name" ]; then
        echo "  - $name not found"
        missing=1
    fi
done
if [ "$missing" -eq 1 ]; then
    echo "Building anyway -- the app will still work, but downloads that"
    echo "need the missing binary/binaries will fail until they're added"
    echo "to this folder and it's rebuilt."
    echo
fi

echo "Building YouTube Downloader.app with PyInstaller..."
echo "(this can take a couple of minutes)"
echo
if ! python3 -m PyInstaller build.spec; then
    echo
    echo "Build failed. See the error above."
    pause_and_exit
fi

echo
echo "Done. The finished app is at:"
echo "  $(pwd)/dist/YouTube Downloader.app"
echo
echo "First launch on a Mac other than this one will need a right-click >"
echo "Open (not a double-click) -- see README.md's macOS section for why."
echo
read -n 1 -s -r -p "Press any key to close this window..."
echo
