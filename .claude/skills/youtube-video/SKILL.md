---
name: youtube-video
description: Watch, read, or search YouTube videos - get a timestamped transcript, look at frames from any moment, search for a video by topic, or download one. Use whenever a YouTube URL appears, when the user asks what a video says or shows, when they want a video summarized, quoted, or fact-checked, or when they ask you to find a video about something.
---

# Working with YouTube videos

You can't watch a video directly, but this project gives you two ways to
read and see one: a **transcript** (what was said, timestamped) and
**frames** (still images you can actually look at).

## The flow that works

1. **Find it** — if the user didn't give a URL, `search`.
2. **Check it** — `info` first on anything unfamiliar. Duration tells you
   whether to read the transcript in slices; the caption flags tell you
   whether a transcript is instant or slow.
3. **Read it** — `transcript`. This answers most questions on its own.
4. **Look at it** — `frames`, but only for moments the words can't
   convey: a chart, a UI, code on screen, a diagram, slides. Take the
   timestamp from the transcript, extract a few frames around it, then
   **read the returned image paths** to actually see them.

Do not download the video as a setup step. `transcript` and `frames`
fetch what they need themselves, into a temp cache that later calls
reuse. Only use `download` when the user actually wants the file kept.

## If the MCP server is registered (preferred)

Use the tools directly: `search_youtube`, `get_video_info`,
`get_transcript`, `extract_frames`, `download_video`, `check_setup`.
They're typed and self-describing. Everything below is the fallback.

## CLI fallback

Run from the repo root with its venv. On Windows:

```bash
.venv/Scripts/python.exe ytdl_cli.py <command> ...
```

On macOS/Linux use `.venv/bin/python`. Every command prints JSON to
stdout; progress goes to stderr.

```bash
# Find a video
.venv/Scripts/python.exe ytdl_cli.py search "claude code tutorial" --limit 5

# Duration, chapters, whether captions exist
.venv/Scripts/python.exe ytdl_cli.py info "https://youtu.be/VIDEO_ID"

# Whole transcript, human-readable with timestamps
.venv/Scripts/python.exe ytdl_cli.py transcript "https://youtu.be/VIDEO_ID" --format text

# Just one section of a long video (do this for anything over ~15 min)
.venv/Scripts/python.exe ytdl_cli.py transcript "https://youtu.be/VIDEO_ID" \
    --format text --start 4:00 --end 6:30

# Frames around a moment worth seeing, then READ the paths it prints
.venv/Scripts/python.exe ytdl_cli.py frames "https://youtu.be/VIDEO_ID" \
    --start 4:10 --end 4:30 --max 5

# Slide decks / cut-heavy videos: only frames where the picture changed
.venv/Scripts/python.exe ytdl_cli.py frames "https://youtu.be/VIDEO_ID" \
    --scene-threshold 0.3 --max 20

# Download for the user (this one keeps the file)
.venv/Scripts/python.exe ytdl_cli.py download "https://youtu.be/VIDEO_ID" \
    --quality 1080 --dir ~/Downloads

# Audio only
.venv/Scripts/python.exe ytdl_cli.py download "https://youtu.be/VIDEO_ID" \
    --mode audio --audio-format mp3

# When something breaks, run this first
.venv/Scripts/python.exe ytdl_cli.py doctor
```

Timestamps accept `4:10`, `1:02:03`, `250`, or `2m30s`.

## The first download asks first

The very first time you download a video on a machine, the tool returns
`needs_confirmation` instead of downloading. It hands you the folder and
quality — show the user, in your own words, and wait:

> I'll save this to C:\Users\you\Downloads at up to 1080p. Go ahead, or
> change either first?

When they agree, call again with `confirmed=true`. This happens **once**,
not per download. Don't work around it, and don't confirm on the user's
behalf — the whole point is that a file never lands on their disk in a
shape they didn't choose.

If they want something different, either pass `output_dir` / `quality`
for that one call, or tell them the command to change it for good.

## Defaults are the user's, not yours

Quality, destination folder, frame counts, and any size/duration limits
all come from the user's settings. **Don't assume — check.**

```bash
.venv/Scripts/python.exe ytdl_cli.py config show
```

(Or the `get_settings` MCP tool.) It reports every value and which layer
it came from.

- Omitting an argument uses their configured value. Pass one explicitly
  only to override a *single* call, and say so when you do.
- Downloads cap at `max_height` (1080p unless they changed it), so
  "download this" does not mean "fetch 4K" by default. If they ask for
  maximum quality, pass it explicitly for that call.
- Files go to `download_dir` — the same folder the desktop app uses.
  Report the actual path back; don't say "your Downloads folder" unless
  you've confirmed that's where it went.
- If a download is refused for exceeding a limit, the error names the
  setting. Relay that and let them decide — **don't change their
  settings for them.** Lasting changes are theirs to make:
  `ytdl_cli.py config set <key>=<value>`.

## Judgment calls

**Read frames, don't skim them.** Five frames you actually look at beat
fifty you list. Frames cost far more context than transcript text, so
extract a narrow range rather than sweeping the whole video.

**Slice long transcripts.** A 40-minute talk is a lot of tokens at once.
If the user asked about one topic, use `info` chapters or a first pass to
locate it, then pull that window.

**Quote with timestamps.** `[4:12]` makes a claim checkable, and the user
can jump straight there.

**Auto-captions mishear things.** Proper nouns, product names, and
technical terms are the usual casualties. If a quote looks wrong or
matters a lot, pull frames at that timestamp — the word is often on
screen. `--force-whisper` gets a second opinion but is much slower.

**No captions and no `faster-whisper` installed** → transcript fails with
a message saying so. `pip install faster-whisper` fixes it; first run
also downloads the model, so warn the user it'll take a few minutes.

**Downloads failing with 403** → run `doctor`. Almost always either
`yt-dlp-ejs` missing or a stale yt-dlp; YouTube changes break old builds.
Fix is `pip install -U --pre "yt-dlp[default]" yt-dlp-ejs`.
