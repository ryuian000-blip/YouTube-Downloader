#!/bin/bash
# One-time setup: clears the macOS quarantine flag on "YouTube Downloader.app"
# so it opens without Gatekeeper's "Apple could not verify..." block.
#
# What this actually does, and why it's needed: this app isn't signed
# with a paid Apple Developer certificate ($99/year), so unlike apps from
# the App Store or big companies, macOS can't verify who built it and
# refuses to open it by default the first time. This clears the flag
# macOS puts on anything downloaded from the internet, which is the same
# thing System Settings > Privacy & Security > "Open Anyway" does for a
# blocked app -- just without digging through System Settings.
#
# Double-click this file (or right-click > Open, if double-click doesn't
# work on your Mac) once, before opening YouTube Downloader.app for the
# first time. Safe to run more than once.

cd "$(dirname "$0")" || exit 1

pause_and_exit() {
    echo
    read -n 1 -s -r -p "Press any key to close this window..."
    echo
    exit 1
}

APP="YouTube Downloader.app"

echo "YouTube Downloader -- one-time setup"
echo "====================================="
echo

if [ ! -d "$APP" ]; then
    echo "Couldn't find \"$APP\" next to this script."
    echo "Make sure this file is in the same folder as the app (don't move"
    echo "just one of the two out of the folder), then try again."
    pause_and_exit
fi

echo "Unlocking \"$APP\" so macOS will let it open..."
if ! xattr -cr "$APP"; then
    echo
    echo "That didn't work -- see the error above."
    pause_and_exit
fi

echo
echo "All set. You can close this window and open \"$APP\" normally now"
echo "(double-click it) -- you won't need to do this again."
echo
read -n 1 -s -r -p "Press any key to close this window..."
echo
