# Rebuild brief: YouTube Downloader, on Python + PySide6

Paste this entire document as the first message to a fresh coding session. It is
self-contained — the session doesn't need any other context.

## What you're building

A single-window Windows (and macOS) desktop app that wraps `yt-dlp` so a
non-technical person can paste a YouTube link and download video or audio,
without touching a command line. It's handed directly to end users as one
standalone executable — no installer, no Python required on their machine, no
auto-update, no telemetry.

This is a rebuild of an existing, working Tkinter/CustomTkinter app, done in a
different stack because the original hit real, time-consuming bugs that trace
back to limitations of that specific toolkit (details below, under "Why this
stack"). The goal is the same app, same look, same behavior — built on a
foundation that doesn't have those specific problems.

## Tech stack (do not substitute without a documented reason)

- **Language: Python.** Not because Python is generically nice, but because
  `yt-dlp` is a *Python library*, not just a CLI tool — `import yt_dlp;
  yt_dlp.YoutubeDL(...)` gives you structured data (format lists, titles) and
  native progress-hook callbacks. Any other language would reduce this to
  shelling out to a `yt-dlp.exe` and parsing its stdout, which is strictly
  worse: fragile, higher latency, no clean progress callback. The app's own
  code is pure orchestration glue (call yt-dlp, call ffmpeg, update a progress
  bar) — there is no performance-sensitive work happening in-process, so a
  systems language buys nothing here.
- **GUI: PySide6** (Qt for Python). Not PyQt6 — PySide6 is LGPLv3, meaning
  it's free to use in a closed-source app you give away for free with no
  fee; PyQt6 is GPLv3 or a paid commercial license, which would force either
  open-sourcing this app or paying Riverbank. **Build-time check:** LGPLv3
  compliance generally assumes Qt is linked dynamically/relinkably, not
  statically compiled in. This should already hold under PyInstaller's
  default onefile mode (it extracts shared libraries to a temp dir at
  runtime rather than statically compiling them), but confirm this for
  whatever exact packaging mode ends up used before shipping.
- **Packaging: PyInstaller**, `--onefile --windowed`, same as the original.
  It remains the dominant, most proven choice for this exact situation —
  bundling odd binaries alongside a GUI framework, with the most mature
  hook ecosystem and community support. **Documented fallback, not a
  default switch:** Nuitka (a real Python-to-C compiler, not just a
  bundler) is a credible alternative if PyInstaller's bundle size or cold-
  start extraction time becomes an actual, measured problem — benchmark it
  against the real app if that happens, rather than switching preemptively
  on the assumption it'll be better.
- **YouTube extraction: yt-dlp, unchanged.** Still the best-maintained,
  most-current tool for this — actively releasing (security patches,
  ongoing YouTube-specific extractor fixes) as of mid-2026, governed by a
  multi-maintainer team (not a single point of failure), and historically
  ships fixes within hours of a YouTube-side break. No credible
  better-maintained alternative exists.
- **JS runtime: bundle Deno, and stay on it.** yt-dlp now requires an
  external JavaScript runtime to solve YouTube's playback-challenge (this
  isn't optional — without it, metadata fetch still works but the actual
  download fails with HTTP 403). yt-dlp supports several runtimes, in its
  own recommended order: **Deno, Node.js, QuickJS, Bun** — and only Deno is
  enabled by default. That ordering isn't arbitrary: Deno runs the
  challenge-solving JS inside its V8 sandbox with no filesystem or network
  access by default, a real security property the others don't give you for
  free. **Do not swap to QuickJS by default** to save bundle size — it
  carries a specific, documented caveat (QuickJS can't execute scripts from
  stdin, so yt-dlp writes them to temp files instead, a theoretical
  time-of-check-to-time-of-use risk) and has historically had weaker
  performance, though recent optimizations have narrowed that gap. Only
  reconsider this tradeoff if bundle size turns out to be a real, measured
  problem for actual users — not a preemptive optimization.
- **Image handling: Pillow, unchanged.** Used for the splash-screen logo
  fade. PyInstaller has a built-in `--splash` flag that avoids needing
  Pillow at all *if* you supply an already-correctly-sized static PNG — but
  it doesn't fit this app: it's explicitly incompatible with macOS (runs in
  a background thread, which Tcl/Tk disallows there), and it only supports
  a static image, not a real cross-fade animation. Since this app needs
  both cross-platform support and a genuine animated fade, Pillow remains
  the right, well-trodden tool here.
- **ffmpeg / ffprobe:** unchanged — bundled binaries, used for merging
  video+audio, extracting audio formats, embedding subtitles/thumbnails.

## Why this stack (context for pitfalls to avoid)

The original Tkinter/CustomTkinter build hit two significant, time-consuming
bugs, both root-caused to that toolkit specifically:

1. **CustomTkinter's dropdown (`CTkOptionMenu`) is backed by a real native OS
   menu (`tkinter.Menu`) on Windows.** Tk has no hook to recolor or round a
   native menu's chrome, so it permanently clashed with the app's dark rounded
   theme no matter what color kwargs were passed. Fixing this required
   hand-building a full custom dropdown widget from scratch (popup
   positioning, keyboard nav, click-outside-to-close, the works) — a lot of
   code and several rounds of bugs to solve a problem that shouldn't need
   solving.
2. **DPI double-scaling.** `CTkToplevel.geometry()` silently re-applies
   Windows DPI scaling to whatever geometry string it's given. Since real
   on-screen pixel measurements (`winfo_rootx()` etc.) were already scaled,
   handing those to `CTkToplevel.geometry()` scaled them a second time —
   on any display not at exactly 100% Windows scaling, this inflated a popup
   window's size and threw off its position.

Qt doesn't have either problem: `QComboBox` isn't a native OS control — it's
drawn entirely by Qt's own style engine (QSS), so a themed dropdown works
out of the box with no hand-built replacement needed. Qt has also had
a real high-DPI scaling model since Qt 5.6/5.14, mature by Qt 6, so the
double-scaling class of bug mostly doesn't occur there. **Use Qt's native
`QComboBox`, styled via QSS, for both dropdowns in this app** — do not
reimplement the original's hand-built inline-expanding dropdown. That
workaround existed specifically to route around Tkinter's bugs; rebuilding
it under a toolkit that doesn't have those bugs would just be unnecessary
complexity solving an already-solved problem.

## Functional requirements

**Link input & fetch:**
- A text field for pasting a YouTube URL, plus a "Fetch Info" button.
- On fetch: call `yt_dlp.YoutubeDL(...).extract_info(url, download=False)`
  with `skip_download=True` and **`noplaylist=True`** — without
  `noplaylist`, a URL carrying both a video ID and a `list=` param (e.g.
  YouTube's auto-generated "Radio" mixes) makes yt-dlp try to resolve the
  whole playlist, which for a Radio mix is dynamically generated and can hang
  indefinitely, instead of just the one video this app handles.
- Must run off the GUI thread (see "Threading" below). On success: show the
  video title, populate the quality dropdown with the available heights
  (descending, plus a "Best available" option at the top, selected by
  default), and reveal the rest of the UI (see "Reveal animation" below). On
  failure: show a plain "couldn't read that link" message, not a raw
  exception.

**Download options** (revealed after a successful fetch):
- Three mutually-exclusive modes via radio buttons: **Video (with sound)**,
  **Video only (no sound)**, **Audio only**.
- **Video quality dropdown**: populated from the fetched heights (e.g.
  "1080p", "720p", ...), plus "Best available". Disabled when mode is
  "Audio only".
- **Audio format dropdown**: MP3 / M4A / WAV. Disabled unless mode is "Audio
  only".
- **Extras**: two checkboxes — "Include subtitles (if available)" and "Embed
  thumbnail as cover art".
- **Save destination**: shows the current output folder (default: the user's
  Downloads folder), with a "Change..." button that opens the **native OS
  folder picker** (this is a deliberate exception — see "UI conventions"
  below).

**Download:**
- Build a yt-dlp format string from mode + selected quality:
  - Video: `bv*[height<=N]+ba/b[height<=N]` (or without the height filter if
    "Best available")
  - Video only: `bv*[height<=N]`
  - Audio only: `bestaudio/best`
- Video modes: `merge_output_format = "mp4"`.
- Audio mode: `FFmpegExtractAudio` postprocessor with the selected codec.
- If thumbnail checkbox checked: `writethumbnail=True` + `EmbedThumbnail`
  postprocessor.
- If subtitles checkbox checked: `writesubtitles=True`,
  `subtitleslangs=["en"]`, and (for non-audio modes) an
  `FFmpegEmbedSubtitle` postprocessor.
- Pass `ffmpeg_location` (the detected ffmpeg folder) and the JS runtime path
  (`js_runtimes` opt) if found.
- Progress hooks update a progress bar and a percentage label live; on
  completion show a success message with the accent color, on failure show a
  truncated error message.
- Must run off the GUI thread.

**Binary detection:** ffmpeg/ffprobe/the JS runtime binary are expected next
to the executable (handle PyInstaller's temp extraction path — `sys._MEIPASS`
— when frozen, vs. the script's own directory when run unfrozen). If missing,
show a non-blocking warning in the status area — never crash on startup just
because a bundled binary isn't present yet (useful during development before
those binaries are copied in).

**Splash screen:** on launch, show the app icon/logo fading in over the
background, hold briefly, then fade out to reveal the main window — same
effect as the original (frame-by-frame alpha compositing via Pillow, or use
Qt's `QPropertyAnimation` on opacity if that gets a cleaner result — your
call, but it must be a real animated fade, not a static splash).

**Threading (mandatory, not optional):** Never touch a Qt widget from a
background thread. Run `extract_info`/`download` calls on a `QThread` (or
`QThreadPool` + `QRunnable`), and marshal every UI update back to the main
thread via Qt's signal/slot mechanism. This is the direct equivalent of the
original app's `self.after(0, ...)` pattern for cross-thread UI updates —
getting this wrong is the single most common way an app like this ends up
crashing or corrupting its own UI state.

## Visual design (match exactly)

Styled to read as an official Anthropic product — the same warm, light,
restrained visual language as claude.ai — rather than a generic dark-mode
utility. One deliberate departure from Anthropic's actual brand: their
signature accent is a terracotta orange; this app uses a light sage green
instead, everywhere orange would otherwise appear.

**Color tokens** (light warm theme, define as named constants, don't inline
hex values):

```
BG            #faf9f5   window/page background, warm cream
SURFACE       #ffffff   card background
SURFACE_ALT   #f0efe7   input/dropdown idle background, inside cards
BORDER        #e5e3d8   hairline borders and dividers
TEXT_PRIMARY  #141413   primary text, near-black warm
TEXT_MUTED    #6b6a63   secondary text, section labels, status copy
ACCENT        #8fa876   light sage green (stands in for Anthropic's orange)
ACCENT_HOVER  #7c9463   darker sage, hover/pressed state
ACCENT_TINT   #eaf0e3   pale sage wash — selected radio/checkbox fill, focus ring
ON_ACCENT     #141413   text/icon color on top of ACCENT or ACCENT_HOVER
ERROR         #a8462f   muted warm red
WARNING       #8a6a2e   muted warm amber
SUCCESS       (= ACCENT)
```

ON_ACCENT is dark, not white — ACCENT is light enough that white text on it
fails contrast (roughly 2.6:1); TEXT_PRIMARY on ACCENT lands around 7:1, and
still holds around 5.5:1 on ACCENT_HOVER, so button labels stay legible in
both states without darkening the accent into something no longer "light."
Check any further token tweaks against WCAG contrast before finalizing —
ERROR and WARNING above are already tuned so their text reads at ~4.5:1+ on
BG rather than the more decorative, lower-contrast values a first pass might
reach for.

One accent color, warm neutrals in the same cream/black family (no cool
grays), semantic colors (ERROR/WARNING) used only where meaning actually
requires them — not as decoration elsewhere. Since cards are near-white on a
cream page rather than a lighter panel on a dark one, give them a soft drop
shadow (e.g. `0 1px 3px rgba(20,20,19,0.08)`) in addition to the 1px BORDER,
so they read as gently lifted rather than flat.

**Spacing:** a 4px baseline grid — every gap/padding is one of `4, 8, 16, 24`
(tightest grouping / related items in a row / default gap inside a card /
gap between major sections and the window's outer margin), never an
arbitrary number.

**Corners:** cards use an 18px radius; form controls (entry, dropdowns,
buttons) use 8px, except the primary "Download" button which is fully
pill-shaped (radius = half its height) and the "Fetch Info" button which is
similarly pill-shaped.

**Typography:** Anthropic's actual product pairs a rounded humanist sans
(Styrene) with a serif (Tiempos) for headings — both commercially licensed,
not available as system fonts, so don't bundle them; get the same warmth
from scale and weight instead of literal font matching. Use "Segoe UI" on
Windows (system default on macOS). Title 21px bold, section labels 12px
bold (uppercase, muted color), body text 13px, buttons 13px bold (secondary
buttons 13px regular), status text 12px. If a closer visual match matters
more than packaging simplicity, a bundled geometric/humanist sans such as
Public Sans or Inter for the title only is a reasonable stand-in for
Styrene — treat this as optional polish, not a requirement.

**Layout, top to bottom:**
1. App title, "YouTube Downloader".
2. A card containing: URL entry + "Fetch Info" button (side by side), the
   fetched video title below it, and a status line below that.
3. (Revealed after a successful fetch) An options card: mode radio buttons,
   video quality dropdown, audio format dropdown, extras checkboxes.
4. A destination card: current save folder + "Change..." button.
5. Progress bar, status label, "Download" button (full-width, pill-shaped,
   accent-colored).

**Window behavior:** before any fetch, the title+link card is vertically
centered in the window. On a successful fetch, it animates ("docks") up to
the top, and the rest of the UI fades in below it. Window starts sized to
fit its content up to a sensible cap, is resizable, has a reasonable minimum
size, and is centered on screen at launch.

## UI conventions

- **This app is a single window.** Nothing should be built as a separate
  `QDialog`/secondary top-level window for anything that's conceptually part
  of the main flow. The one deliberate exception: **native OS dialogs**
  (the folder picker behind "Change...", any unavoidable OS-level alert) —
  those are standard system integration points, not custom app UI, and
  reimplementing a folder picker inline would be worse UX than the native
  one. Everything else stays inside the one window.
- Prefer direct, immediate responses over animated/eased ones for anything
  input-driven (scrolling, if you end up needing any). An earlier attempt at
  an eased "chase" scroll in the Tkinter build was explicitly reverted for
  feeling laggy — each input tick trailing behind a moving target reads as
  unresponsive, not smooth. If a scroll area turns out to be necessary at
  all, use Qt's default `QScrollArea` behavior rather than reimplementing
  wheel-scroll math by hand.
- If you do build any custom animation (the splash fade, the hero-dock
  transition), use Qt's `QPropertyAnimation`/`QVariantAnimation` rather than
  a hand-rolled timer loop — Qt's animation framework is vsync-friendly by
  design, which sidesteps a whole class of stutter bugs that come from
  scheduling animation ticks tighter than the OS's timer can reliably honor.
- Any dropdown's currently-selected item must be unambiguous at a glance
  (Qt's `QComboBox` already handles this correctly out of the box — don't
  override it into something worse).

## Packaging

- `PyInstaller --onefile --windowed`, named `YouTube Downloader`, icon set
  from the app's `.ico`.
- Bundle `ffmpeg.exe`, `ffprobe.exe`, the JS runtime binary, plus the logo
  and icon assets, as PyInstaller `--add-binary`/`--add-data`.
- `requirements.txt`: `PySide6`, `yt-dlp`, `pillow`, `pyinstaller` (plus
  whatever yt-dlp's JS-runtime-solver package is called at implementation
  time — check current yt-dlp docs, this has been renamed/restructured
  before).
- Set up a GitHub Actions Windows-runner workflow that fetches ffmpeg (e.g.
  via Chocolatey) and the JS runtime binary fresh in CI, builds, and uploads
  the `.exe` as an artifact — don't commit ~100MB+ binaries to git.
- `.gitignore`: exclude `build/`, `dist/`, `__pycache__/`, the bundled
  binaries themselves (fetched fresh by CI, kept locally per a short README
  for whoever builds this by hand).

## Build-time verification checklist

A few things this brief calls out as "check at build time" rather than
settled facts — don't skip these:

- [ ] Confirm PySide6's LGPLv3 dynamic-linking requirement actually holds
      under whatever exact PyInstaller mode gets used (should be fine with
      default onefile, but verify rather than assume).
- [ ] Confirm the current name/import path of yt-dlp's JS-challenge-solver
      dependency — this has been renamed/restructured before, check yt-dlp's
      own current docs rather than trusting this brief's package name.
- [ ] Only evaluate swapping Deno → QuickJS if bundle size is an actual,
      measured complaint — and if you do, document the TOCTOU caveat in a
      code comment near wherever the runtime path gets configured.
- [ ] Only evaluate Nuitka if PyInstaller's build/startup time is an actual,
      measured problem — benchmark against this real app, don't switch on
      spec alone.

## The honest tradeoff of this whole stack

Going from Tkinter to Qt trades Tkinter's near-zero footprint (it ships with
Python itself) for Qt's much larger one — PySide6 alone typically adds tens
of megabytes to a PyInstaller onefile build, stacked on top of the ffmpeg,
ffprobe, and Deno binaries already being bundled. For a "thin GUI wrapper"
utility handed directly to non-technical users as a single .exe, that's a
meaningfully heavier download and slower cold-start extraction. The
trade is a UI that's actually themeable without fighting the toolkit, and
DPI handling that doesn't need manual patching — worth it for this app given
how much time the original build lost to exactly those two problems, but
it's a real cost, not a free upgrade. If bundle size turns out to matter
more than expected once this is in someone's hands, that's the tension to
revisit first.

## What "done" looks like

A working app that launches, fetches real video info from a pasted YouTube
URL, lists real available qualities, downloads and correctly post-processes
video or audio to the chosen folder with a live progress bar, looks like the
color/spacing/typography spec above, and builds into a single standalone
`.exe` via one PyInstaller command. Confirm it actually runs before calling
it done — don't just confirm it type-checks or imports cleanly.
