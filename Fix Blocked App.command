#!/bin/bash
# Clears the macOS quarantine flag on "YouTube Downloader.app" so it opens
# without Gatekeeper's "Apple could not verify..." block on this Mac.
#
# What this actually does, and why it's needed: this app isn't signed
# with a paid Apple Developer certificate ($99/year), so unlike apps from
# the App Store or big companies, macOS can't verify who built it and
# refuses to open it by default the first time. This clears the flag
# macOS sets on anything downloaded from the internet, which is the same
# thing System Settings > Privacy & Security > "Open Anyway" does for a
# blocked app -- just without digging through System Settings.
#
# Right-click this file and choose Open once (or double-click, if that
# works on your Mac) before opening YouTube Downloader.app for the first
# time. Safe to run more than once.

cd "$(dirname "$0")" || exit 1

pause_and_exit() {
    echo
    read -n 1 -s -r -p "Press any key to close this window..."
    echo
    exit 1
}

APP="YouTube Downloader.app"

if [ ! -d "$APP" ]; then
    echo "Couldn't find \"$APP\" next to this script."
    echo "Make sure this file is in the same folder as the app (don't move"
    echo "just one of the two), then try again."
    pause_and_exit
fi

echo "Clearing the quarantine flag on \"$APP\"..."
if ! xattr -cr "$APP"; then
    echo
    echo "That didn't work -- see the error above."
    pause_and_exit
fi

echo
echo "Done. You can now open \"$APP\" normally (double-click it)."
echo
read -n 1 -s -r -p "Press any key to close this window..."
echo
