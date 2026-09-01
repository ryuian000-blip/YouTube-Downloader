# YouTube Downloader

A single-window desktop app that wraps `yt-dlp` so pasting a YouTube link
and downloading video or audio doesn't require touching a command line.
Built on Python + PySide6 per `REBUILD-PYSIDE6.md` (the brief this was
built from -- worth reading for the full rationale behind every choice
below).

## Running it during development

**Windows:** double-click `run.bat`. First run creates a `.venv`, installs
`requirements.txt` into it, and launches the app; later runs skip straight
to launching. It's safe to double-click any time -- it never touches
anything outside this folder.

**macOS:** double-click `run.command` (same behavior as `run.bat`). If
Finder offers to open it in a text editor instead of running it, right
-click it and choose Open, or run `./run.command` from Terminal once. macOS
may also show an "unidentified developer" warning the first time -- see
the Gatekeeper note below.

**Manually (any OS):**

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

The app runs fine without `ffmpeg`/`ffprobe`/`deno` present -- it shows a
non-blocking warning instead of crashing -- but real downloads need all
three. For local testing, drop them directly next to `main.py`
(`run.bat`/`build.bat`/`run.command`/`build.command` all expect them in
this same top-level folder): `ffmpeg.exe`/`ffprobe.exe`/`deno.exe` on
Windows, or `ffmpeg`/`ffprobe`/`deno` (no extension) on macOS.

## Building the app

**Windows:** double-click `build.bat`. It sets up the same `.venv` as
`run.bat` (reusing it if `run.bat` already made one), then runs PyInstaller.
Warns but doesn't stop if `ffmpeg.exe`/`ffprobe.exe`/`deno.exe` aren't
present yet. Produces `dist/YouTube Downloader/YouTube Downloader.exe`
alongside an `_internal` folder it depends on -- an onedir build rather
than a single portable exe, on purpose (see `build.spec`): it starts in
about a quarter of the time, since nothing needs to self-extract to a
temp folder on every launch. Keep the exe and `_internal` together.

**macOS:** double-click `build.command` (same behavior as `build.bat`).
Produces `dist/YouTube Downloader.app`, a real double-clickable app bundle
(via PyInstaller's `BUNDLE()`, see `build.spec`).

**Manually:**

1. Place `ffmpeg`, `ffprobe`, and `deno` in this folder (same level as
   `build.spec`) -- with `.exe` extensions on Windows, without on macOS.
   Get them from:
   - ffmpeg/ffprobe on Windows: https://www.gyan.dev/ffmpeg/builds/ (the
     `ffmpeg-release-essentials` build; both `.exe` files are in the
     extracted `bin/` folder)
   - ffmpeg/ffprobe on macOS: https://ffmpeg.martin-riedl.de/ (the arm64
     or amd64 build matching your Mac, under "macos") -- **not**
     `brew install ffmpeg`: Homebrew's build is dynamically linked against
     ~25 of its own dylibs, so a copy of just the executable only runs on
     the machine that installed it. This site's static builds have no
     such dependencies and are the ones this app has actually been
     tested against.
   - deno: https://github.com/denoland/deno/releases/latest (grab the
     `deno-x86_64-pc-windows-msvc.zip` build on Windows, or the
     `deno-aarch64-apple-darwin.zip` build on Apple Silicon Macs /
     `deno-x86_64-apple-darwin.zip` on Intel Macs)
2. `pip install -r requirements.txt`
3. `pyinstaller build.spec`
4. The finished app is at `dist/YouTube Downloader/YouTube Downloader.exe`
   (Windows, keep it together with the `_internal` folder next to it) or
   `dist/YouTube Downloader.app` (macOS).

The included GitHub Actions workflow (`.github/workflows/build.yml`) does
all of the above fresh in CI on both a Windows and a macOS runner and
uploads each as a build artifact, so nobody needs to commit ~100MB of
binaries to the repo.

### macOS Gatekeeper warning

This app isn't signed with an Apple Developer certificate or notarized (that
requires a paid Apple Developer account), only ad-hoc signed in CI
(`codesign --force --deep --sign -`, see `build.yml`) so it at least
satisfies Apple Silicon's requirement that executables carry *some*
signature. So the first time a friend opens the built `.app`, macOS will
refuse to launch it with an "Apple could not verify..." message (or on
older macOS, "unidentified developer"). `How To Open YouTube Downloader.html`,
included alongside the app in the CI zip (see `build.yml`'s "Zip the app
bundle" step), walks a friend through the fix step by step -- opening
Terminal, exactly what to paste, all of it. Worth mentioning to friends
before you send the zip over so it doesn't look broken; point them at
that file first if they hit the block.

Deliberately a plain HTML document, not a script: a `.command` helper
would hit the *exact same* Gatekeeper block as the app itself (it's still
an executable macOS has to "verify" too), which defeats the point.
A document just opens in the browser -- no verification involved, so it
can never be blocked. It even offers a copy button for the command in
case Safari's clipboard permissions cooperate, and falls back to plain
select-and-copy instructions if not.

**The fix itself, without that file:**

1. Try to open the app once (double-click is fine) -- it'll get blocked
   with the "Not Opened" dialog. This step is required; the bypass button
   below doesn't appear until after a blocked attempt.
2. Open **System Settings > Privacy & Security**, scroll down -- there's a
   line saying `"YouTube Downloader" was blocked` with an **Open Anyway**
   button next to it.
3. Click it, confirm in the dialog that follows (password/Touch ID may be
   asked for), then open the app again as normal.

After either fix, the app opens normally forever, including via
double-click. Worth mentioning to friends before you send it over so it
doesn't look broken.

(Older macOS versions -- pre-Sequoia -- instead let you bypass this by
right-clicking the app and choosing **Open**, then confirming **Open**
again in the dialog that appears, without needing System Settings at all.
Current macOS shows the same blocked dialog either way and only the
System Settings button actually bypasses it.)

If a friend instead sees **"is damaged and can't be opened, you should move
it to Trash"** (no Open option at all, and it's not offered in System
Settings either), that means Gatekeeper couldn't validate the signature
at all -- either they're on a build from before the ad-hoc codesign step
was added, or the zip transfer mangled it. One Terminal command clears it:

```bash
xattr -cr ~/Downloads/"YouTube Downloader.app"
```

(adjust the path if they moved it elsewhere first) -- then open it and
use the System Settings steps above if it's still blocked (rather than
damaged) after that.

## What's actually implemented

- URL entry, `Fetch Info` (runs off the GUI thread, `noplaylist=True` so a
  link carrying a `list=` param -- e.g. YouTube's auto "Radio" mix -- can't
  hang the app trying to resolve an entire dynamic playlist). A fetch
  fetches the video's thumbnail too (also off the GUI thread) and shows it
  next to the title, so it's obvious at a glance whether the right video
  got matched.
- Video (with sound) / Video only / Audio only modes, quality dropdown
  (populated from the fetched formats, "Best available" pre-selected),
  MP3/M4A/WAV audio format dropdown, subtitle + thumbnail-embed checkboxes.
- Destination folder with the native OS picker.
- Threaded download with a live progress bar, built from yt-dlp progress
  hooks, entirely off the GUI thread. If the file was already downloaded
  before, yt-dlp would otherwise silently skip it while still reporting
  "success" -- the app instead predicts the output filename up front
  (offline, via yt-dlp's own filename templating -- see
  `workers.predict_output_path`), warns before starting if it already
  exists, and only overwrites if you confirm.
- Splash screen (real animated opacity fade via `QPropertyAnimation`, not a
  static image) and a hero-card "dock to top + fade in the rest" reveal
  after a successful fetch.
- Binary detection that checks next to the running executable (handling
  `sys._MEIPASS` when frozen) and degrades to a warning, never a crash.
- Dark, sage-green-accented visual design -- every color is a named
  constant in `app/theme.py`, nothing inline. There's no light theme or
  theme picker; dark is the only mode the app has.
- Every button, radio, and checkbox is custom-painted with real
  `QPropertyAnimation`-driven hover/press/check transitions
  (`app/widgets.py`), rather than relying on Qt's native QStyle + QSS --
  the native styling for some of these sub-controls (e.g. a checked
  radio's indicator) turned out not to be reliably honored across
  platforms.

## What still needs a human before this ships

These are the brief's own "verify before shipping" items, unchanged:

- [ ] Confirm PySide6's LGPLv3 dynamic-linking assumption actually holds
      for the exact PyInstaller onedir build produced here.
- [ ] Confirm the current name of yt-dlp's JS-challenge-solver dependency
      (see the comment in `requirements.txt`) against yt-dlp's own docs at
      build time -- it's been renamed before.
- [ ] Run the app against a handful of real YouTube URLs (including at
      least one with a `list=` param) on an actual Windows machine and an
      actual Mac -- everything here has been exercised headlessly (Xvfb)
      in a Linux sandbox during development, which validates the UI,
      threading, and logic paths, but not real network/yt-dlp/ffmpeg
      behavior end to end, and not Windows/macOS-specific DPI,
      native-dialog, or Gatekeeper behavior. `build.spec`'s macOS
      (`BUNDLE()`) branch in particular has only been validated by code
      review and by exercising the same code path on Linux (which takes
      the non-macOS branch) -- it hasn't been run on real macOS yet.
- [ ] `assets/icon.ico` / `assets/logo.png` are a simple generated
      placeholder (sage circle, cream download glyph) -- swap in real
      branding if this app has any.
- [ ] The already-downloaded warning's filename prediction
      (`predict_output_path`) is exercised headlessly against synthetic
      info dicts, but only real yt-dlp runs on a real machine can confirm
      it matches actual output filenames for every format/postprocessor
      combination.

## Giving Claude Code eyes and ears for YouTube

The same download machinery is exposed headlessly, so an AI assistant
that can't watch videos can still **read** one (timestamped transcript)
and **see** any moment of it (frames as images it can actually look at).

What that unlocks, in practice:

> **You:** Find that talk where Boris explains Claude Code's philosophy,
> summarize the part about permissions, and show me what's on screen when
> he demos it.
>
> **Claude:** *searches YouTube → checks duration and captions → reads the
> transcript around "permissions" → extracts four frames at 12:40–13:10 →
> looks at them* → summary, plus a description of the actual slide.

### Setup

```bash
pip install -r requirements.txt
python ytdl_cli.py doctor          # confirms ffmpeg, deno, yt-dlp, yt-dlp-ejs
```

Register the MCP server once (user scope, so it works from any project):

```bash
claude mcp add youtube-downloader --scope user -- "<repo>/.venv/Scripts/python.exe" "<repo>/ytdl_mcp.py"
```

On macOS/Linux use `<repo>/.venv/bin/python`. Claude then has
`search_youtube`, `get_video_info`, `get_transcript`, `extract_frames`,
`download_video`, and `check_setup`. The repo also ships
`.claude/skills/youtube-video/SKILL.md`, which teaches the workflow (and
the CLI fallback) to any Claude Code session opened here.

### CLI

Works standalone, and doubles as the debugging surface for the MCP
server. JSON on stdout, progress on stderr, non-zero exit on failure.

```bash
python ytdl_cli.py search "claude code tutorial" --limit 5
python ytdl_cli.py info    "https://youtu.be/VIDEO_ID"
python ytdl_cli.py transcript "https://youtu.be/VIDEO_ID" --format text --start 4:00 --end 6:30
python ytdl_cli.py frames  "https://youtu.be/VIDEO_ID" --start 4:10 --end 4:30 --max 5
python ytdl_cli.py frames  "https://youtu.be/VIDEO_ID" --scene-threshold 0.3   # slide decks
python ytdl_cli.py download "https://youtu.be/VIDEO_ID" --quality 1080 --dir ~/Downloads
```

Transcripts use YouTube's own captions when they exist (instant) and fall
back to local Whisper via `faster-whisper` when they don't — no API key,
no cloud, no per-video cost. Frames are sampled, not exhaustive: a
20-minute video is ~36,000 frames, so the tools default to one per 10s,
support scene-change detection, and cap output rather than silently
dumping thousands of files.

Videos fetched this way land in a temp cache keyed by video ID, so
transcript-then-frames on the same video downloads it once.

## Project layout

```
run.bat                       double-click to run from source on Windows (sets up .venv itself)
run.command                   double-click to run from source on macOS (sets up .venv itself)
build.bat                     double-click to build the .exe on Windows (sets up .venv itself)
build.command                 double-click to build the .app on macOS (sets up .venv itself)
How To Open YouTube Downloader.html
                               ships alongside the built .app in CI's macOS zip; walks a friend
                               through the one-time Gatekeeper unblock step by step
main.py                       entry point: splash -> main window
app/theme.py                  dark color tokens, QSS builder (single source of truth for style)
app/theme_manager.py          thin wrapper around the (fixed, dark-only) color tokens
app/widgets.py                custom-painted animated buttons, radios, checkboxes, progress bar
app/main_window.py            the one window: all cards, layout, reveal animation
app/splash.py                 animated splash screen
app/binaries.py               re-export of ytdl_engine.binaries (kept as the GUI's import surface)
app/workers.py                QThread wrappers over ytdl_engine: fetch metadata, run the download
ytdl_engine/                  headless engine -- no Qt. All the YouTube-fragile logic lives here,
                              shared by the GUI, the CLI, and the MCP server, so a YouTube change
                              is fixed once:
  core.py                       shared yt-dlp options, retry loop, ANSI stripping, timestamps
  binaries.py                   ffmpeg/ffprobe/deno detection (frozen vs. dev)
  info.py                       extraction, search, size/quality helpers
  download.py                   format strings, postprocessors, agent-facing media cache
  transcript.py                 YouTube captions, with local Whisper as fallback
  frames.py                     ffmpeg frame sampling (interval / scene-change / time range)
ytdl_cli.py                   headless CLI over the engine (JSON out, no prompts)
ytdl_mcp.py                   stdio MCP server: lets Claude Code drive all of the above
.claude/skills/youtube-video/ skill teaching Claude the search -> transcript -> frames workflow
tests/                        pytest suite (headless, no network) + opt-in network smoke scripts
assets/                       icon.ico, icon.icns, icon.png, logo_on_dark.png
build.spec                    PyInstaller spec -- windowed .exe on Windows, .app bundle on macOS
.github/workflows/build.yml   CI: fetches binaries fresh, builds Windows + macOS, uploads both
```

## Tests

```bash
python -m pytest              # headless GUI + engine tests, no network
python tests/smoke_mcp.py --network    # drives the MCP server over real stdio
```
