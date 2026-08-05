#!/bin/bash
# Runs YouTube Downloader from source (development mode) on macOS.
# Double-click this file in Finder, or run it from a terminal.
#
# Mirrors run.bat's behavior exactly (see that file's comments) --
# creates a .venv on first run, installs requirements.txt into it, warns
# (but doesn't block) if ffmpeg/ffprobe/deno aren't sitting next to this
# file yet, then launches the app.

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

if [ ! -f "ffmpeg" ]; then
    echo
    echo "Note: ffmpeg not found next to this file -- the app will still"
    echo "open, but downloads that need it will fail until it's added."
    echo
fi

echo
echo "Starting YouTube Downloader..."
python3 main.py
