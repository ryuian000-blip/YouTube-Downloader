# Let Claude watch YouTube videos

This app ships a second program next to it, `ytdl-agent`, that connects
it to Claude Code. Once connected, Claude can search YouTube, read a
video's transcript, and *look at* frames from any moment in it.

You don't need Python or anything else installed — it's all in this
folder.

## 1. Check everything works

Open a terminal **in this folder** and run:

**Windows**

```
"ytdl-agent.exe" setup
```

**macOS** (inside the app bundle)

```
"YouTube Downloader.app/Contents/MacOS/ytdl-agent" setup
```

It reports what it found, the folder downloads will go to, and the
quality it'll use. If something's missing, it tells you the exact command
to fix it.

Happy with the defaults? Accept them:

```
ytdl-agent setup --confirm
```

Want different ones, three ways — all the same settings:

- **In the app**: click the gear icon, top right.
- **Here**: `ytdl-agent config set download_dir="D:/Videos" max_height=720`
- **Just ask Claude**: "always download at 720p", "save videos to D:/Videos"

```
ytdl-agent config show
```

## 2. Connect it to Claude Code

One command, using the **full path** to `ytdl-agent` in this folder:

**Windows**

```
claude mcp add youtube-downloader --scope user -- "C:\path\to\YouTube Downloader\ytdl-agent.exe" mcp
```

**macOS**

```
claude mcp add youtube-downloader --scope user -- "/Applications/YouTube Downloader.app/Contents/MacOS/ytdl-agent" mcp
```

`--scope user` means it works in every project, not just the one you're
in.

Check it connected:

```
claude mcp list
```

If it says failed, run `ytdl-agent setup` again — a wrong path is the
usual cause, and the quotes matter if your path has spaces.

## 3. Use it

Just ask Claude, in plain language:

> Summarize this video for me: https://youtu.be/...

> Find that talk about database indexes and tell me what the diagram at
> 12:30 shows.

> What's the code on screen at 4:10 in this video?

The first time Claude downloads a video, it will tell you where the file
will go and at what quality, and wait for your OK. It asks once, not
every time.

## Good to know

- **Transcripts are usually instant** — most videos already have
  captions. Videos without them get transcribed on your own machine,
  which takes a few minutes the first time (it downloads a speech model,
  a few hundred MB, once).
- **Frames are samples, not every frame.** A 20-minute video is about
  36,000 frames. Claude takes one every few seconds, or just the moments
  you asked about.
- **Working copies are cached** in a temp folder so asking a second
  question about the same video is fast. It cleans up after itself once
  it passes 5 GB. `ytdl-agent cache show` to look, `cache clear` to empty.
- **Downloads you asked Claude for go to the same folder** the app itself
  uses. Change it in either place and both follow.

## If something stops working

```
ytdl-agent doctor
```

YouTube changes things regularly and can break video downloading until
the underlying tool is updated. `doctor` will say so, and exactly what to
run.
