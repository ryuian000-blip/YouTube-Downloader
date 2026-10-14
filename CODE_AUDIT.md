# YouTube Downloader — code audit

**Date:** 2026-10-14 · **Commit:** current `main` · **Scope:** `ytdl_engine/`, `app/`, `ytdl_cli.py`, `ytdl_mcp.py`, `agent_entry.py`, `tests/`, `build.spec`

---

> ## ✅ ALL FINDINGS FIXED — 2026-10-14
>
> Every finding below has been resolved. **261 tests pass** (was 149),
> coverage **79%** (was 72%), ruff clean on `E9,F401,F811,F841,ASYNC240`.
>
> | Was | Now | Evidence |
> |---|---|---|
> | `ytdl_cli.py` 26% | **82%** | `tests/test_cli_contract.py` (31 tests) |
> | `ytdl_mcp.py` 56% | **89%** | `tests/test_mcp_tools.py` (25 tests) |
> | `download.py` 54% | **83%** | `tests/test_cache.py` (18 tests) |
> | `frames.py` 45% | **70%** | `tests/test_frames.py` (23 tests) |
> | no cancellation | covered | `tests/test_cancellation.py` (13 tests) |
>
> **Four criticals were proven fixed against live YouTube, not just unit-tested:**
>
> - **C2** — downloaded "Me at the zoo" (auto-captions only, the exact case
>   that silently did nothing) with `--subtitles`: `ffprobe` shows a
>   `mov_text` subtitle stream present, and absent in the control run.
> - **C3** — a genuinely live stream (`is_live: true`, `duration: null`, so
>   the guardrails provably could not fire) is refused immediately with no
>   file written, instead of downloading forever.
> - **C1/C6** — a custom `cache_dir` produced
>   `<dir>/ytdl-agent-cache/.ytdl-agent-cache` (sentinel + marker), and
>   frame extraction at 360p then 720p created two separate variant
>   folders under one per-video folder rather than reusing the first copy.
> - **C4** — a playlist URL is refused in **0.33s with zero network calls**.
>
> Three corrections to the original audit, found while fixing:
>
> 1. **C5's reachability was mischaracterised.** The setting's vocabulary is
>    `audio_only`; `audio` is only the `--mode` flag's spelling. The real
>    defect was the absent validation plus the CLI/MCP divergence.
> 2. **M6's slow-fixture diagnosis was wrong.** Redirecting the cache did
>    not speed up the Settings-page fixtures — they are still ~4.5s, which
>    is Qt window construction, not the 712MB cache walk. The isolation fix
>    was still correct (tests no longer read or can delete the real cache,
>    and no longer write to `%APPDATA%`), just not a performance fix.
> 3. **The lint count was 16, not 20.** The earlier figure included RUF100
>    hits that were artefacts of an ad-hoc selector flagging deliberate
>    `# noqa: BLE001` comments; the project has no ruff config.
>
> Two deliberate deviations from the audit's own recommendations:
>
> - **`download_dir` was NOT made user-only** (unlike `cache_dir`). It is a
>   documented capability the skill tells assistants to use, nothing is
>   ever overwritten, and the destructive primitive was `cache_dir` — now
>   both blocked *and* structurally confined.
> - **`whisper_model` was not made a `Literal`.** Config validates it at
>   the chokepoint, and the MCP description is generated from that same
>   list via `config.allowed_values()`, so there is one source of truth
>   instead of a second copy that drifts.
>
> **H1 needed a judgement call.** A nonce would not help — an assistant
> receives it and can echo it straight back. Instead the gate now requires
> that the defaults were *actually handed back first*, so a first call
> arriving with `confirmed=true` gets the question rather than a download.
> That makes what the gate promises (the user saw the folder and quality)
> structurally true; it still cannot prove they *agreed*, and the code says
> so rather than overclaiming.

---

Six independent audits (engine correctness, Qt layer, architecture, robustness,
test quality, security) plus objective tooling. **Every finding below was
re-verified against the source by hand** — agent claims are not relayed on
trust, and three were corrected in the process (noted inline). Where a finding
is established by reading code rather than by executing it, it says so.

## Verdict

The code is better than most of what ships. Subprocess handling is genuinely
clean (no `shell=True`, no `os.system`, no `eval`/`exec`/`pickle` anywhere; both
`subprocess.run` calls are list-form with timeouts), path traversal via video
title is impossible, settings parsing is JSON with a field allowlist, and the
comments explain *why* rather than *what* — the `EXTRACTOR_ARGS = {}` comment and
the "no client-specific DRM heuristic" warning are the kind of thing that stops
a future regression.

It is **not** flawless. There are seven defects that produce data loss, a hang,
or a silently wrong artifact, and they cluster in one place: **the agent-facing
surface trusts its own callers.** The GUI is well defended because a human is
watching it. The MCP and CLI paths assume the string they were handed is sane,
and that is where every serious finding lives.

| Severity | Count | Theme |
|---|---|---|
| Critical | 7 | Data loss, non-termination, silently wrong output |
| High | 7 | Trust boundary, lifecycle, crash-on-startup, untested surfaces |
| Medium | 9 | Hardening, dead settings, test isolation |
| Low | 20 | Lint (unused imports, duplicate imports, blocking async I/O) |

---

## CRITICAL

### C1 — `cache_dir` is an unvalidated path that feeds `shutil.rmtree`

`cache_root()` ([download.py:287](ytdl_engine/download.py:287)) is
`Path(settings.cache_dir).expanduser()` — not resolved, not confined, not
required to be a directory this app created. `prune_cache()`
([download.py:360](ytdl_engine/download.py:360)) then walks that root and
`shutil.rmtree`s **every subdirectory, oldest first**, until the total fits
`cache_max_mb`. `clear_cache()` ([download.py:321](ytdl_engine/download.py:321))
deletes every child unconditionally.

`cache_dir` is not in `_USER_ONLY_SETTINGS`
([ytdl_mcp.py:457](ytdl_mcp.py:457) — that set contains only
`defaults_confirmed`), so `update_settings_tool` accepts it.

Two MCP calls aim it anywhere: set `cache_dir` to a documents folder and
`cache_max_mb` to 1, then any `get_transcript`/`extract_frames` reaches
`prune_cache` at [download.py:423](ytdl_engine/download.py:423). No confirmation
prompt sits on that path. `rmtree` does not use the recycle bin.

*Verified by reading all four functions and the guard set. I did not execute the
deletion.* It is also reachable by ordinary mistake — a typo in `config set
cache_dir=`, or pointing it at an existing folder over 5 GB, since
`cache_max_mb` defaults to 5000. Aggravating: the Settings page shows "Currently
using N MB" but **never shows the path**
([settings_view.py:384](app/settings_view.py:384)), so after a bad `cache_dir`
the innocuous **"Clear now"** button ([settings_view.py:417](app/settings_view.py:417))
silently means "delete that folder".

**Fix:** always append a fixed segment in `cache_root()` (`… / "ytdl-agent-cache"`),
refuse to prune or clear a root without a marker file the app wrote, and add
`cache_dir` + `download_dir` to `_USER_ONLY_SETTINGS`. Show the path in Settings.

### C2 — The Subtitles checkbox does nothing on most videos

[download.py:174-178](ytdl_engine/download.py:174) sets `writesubtitles` and
`subtitleslangs` but **never `writeautomaticsub`**. `grep -rn writeautomaticsub`
across the repo returns nothing. YouTube exposes human-authored subtitles for a
small minority of videos; everything else has auto-captions only. So for most
videos the checkbox is a silent no-op — no file, no warning, no error. Affects
the GUI checkbox, the CLI `--subtitles` flag, and the MCP `subtitles` parameter
identically.

### C3 — Livestreams never terminate

`is_live` is read exactly once, at [info.py:257](ytdl_engine/info.py:257), to
populate a display field. Nothing in the download path branches on it. A live
stream has no duration and no filesize, so `check_limits`
([download.py:220](ytdl_engine/download.py:220)) cannot fire — both guards it
would use are `None`. The download then runs until the stream ends or the disk
fills. There is no cancel button (see H2), so the GUI user's only exit is to kill
the process. *Established by code reading; not reproduced against a live stream.*

### C4 — A playlist or channel URL expands and reports one success

`noplaylist: True` ([core.py](ytdl_engine/core.py)) tells yt-dlp "if this URL is
a video *and* a playlist, take the video". A bare `/playlist?list=…` or
`/@channel/videos` URL is not a video, so that flag does not apply and yt-dlp
downloads the whole thing. No code guards it: `grep` for `_type == "playlist"` /
`entries` in the download path finds only [info.py:303](ytdl_engine/info.py:303),
which handles search results. `_final_path`
([download.py:183](ytdl_engine/download.py:183)) reads `requested_downloads[0]`,
so a 200-video channel reports a single success and one path.
*Code reading; not reproduced.*

### C5 — `default_mode` is never validated, and the two agent surfaces disagree

**Verified empirically.** `default_mode` is typed `str` with the valid values
only in a comment ([config.py:87](ytdl_engine/config.py:87)). `_coerce` validates
by declared *type*, so any string is accepted:

```
_coerce('default_mode', 'audio')      -> 'audio'       ACCEPTED
_coerce('default_mode', 'garbage')    -> 'garbage'     ACCEPTED
```

`format_string` ([download.py](ytdl_engine/download.py)) ends in a bare
fallthrough — anything not exactly `video` or `video_only` returns
`bestaudio/best` — while the mp3 postprocessor
([download.py:167](ytdl_engine/download.py:167)) and the predicted extension
([download.py:126](ytdl_engine/download.py:126)) are gated on exact equality with
`audio_only`. Measured:

| `mode` | format selector | postprocessors | `predict_output_path` ext |
|---|---|---|---|
| `video` | `bv*[height<=1080]+ba/b[…]` | — | `.mp4` |
| `audio_only` | `bestaudio/best` | `FFmpegExtractAudio` | `.mp3` |
| **`audio`** | **`bestaudio/best`** | **none** | **`.mp4`** |
| **`typo`** | **`bestaudio/best`** | **none** | **`.mp4`** |

So an unrecognized mode downloads an **audio-only stream, performs no
conversion, and predicts a `.mp4` that will never exist** — which also breaks the
"you already downloaded this" prompt, since it checks for the wrong filename.

The two surfaces then diverge on reaching it. The CLI normalizes an unknown
value back to video ([ytdl_cli.py:124-126](ytdl_cli.py:124)); the MCP tool passes
`settings.default_mode` through raw
(`mode_map.get(mode or "", settings.default_mode)`,
[ytdl_mcp.py:399](ytdl_mcp.py:399)). Identical settings, different artifact,
neither warns.

*Correction to the test agent's report:* it described this as `default_mode=audio`
being "the vocabulary the CLI's own `--mode audio` flag uses". The setting's
documented vocabulary is `audio_only`; `audio` is only the flag spelling. The
real defect is the absent validation plus the divergence, not a vocabulary clash
— but the flag/setting spelling mismatch is exactly what makes the wrong value
easy to write.

### C6 — The cache ignores quality, so a 1080p request can return 480p

`cached_media` ([download.py:301](ytdl_engine/download.py:301)) matches on file
**suffix only**:

```python
if entry.is_file() and entry.suffix.lower() in suffixes and entry.stat().st_size > 0:
    return entry
```

`ensure_local_media` returns that hit before considering `height` at all. Extract
frames at 480p, then ask for 1080p frames of the same video, and you silently get
the 480p copy. The agent has no way to detect this; the returned path looks
correct.

### C7 — Pruning can delete the file that was just downloaded

[download.py:423](ytdl_engine/download.py:423) calls `prune_cache(settings)`
after the download and *before* `if result.path and Path(result.path).exists()`.
The comment argues the newest folder is evicted last — true, but the loop
condition is `if total - freed <= limit: break`, so when a **single video exceeds
`cache_max_mb`** it is the only candidate and gets evicted immediately. The
fallback `cached_media` then finds nothing and the call raises *"Download
finished but no media file was found on disk."* A full download is spent, then
thrown away, and the error names the wrong cause.

---

## HIGH

### H1 — The confirmation gate is self-attested, and the guard sits on one of three callers
[ytdl_mcp.py:359](ytdl_mcp.py:359) is `if not settings.defaults_confirmed and not
confirmed:` — `confirmed` is a boolean the *model* sets, so a first call with
`confirmed=true` downloads immediately and [ytdl_mcp.py:410-415](ytdl_mcp.py:410)
then persists `defaults_confirmed=True` forever. Separately the CLI has no guard
at all: `config set` ([ytdl_cli.py:233](ytdl_cli.py:233)) calls
`ytdl_engine.config.update_settings`, which has no `_USER_ONLY_SETTINGS` concept,
so `config set defaults_confirmed=true` flips it; `setup`
([ytdl_cli.py:389](ytdl_cli.py:389)) sets it unconditionally. The guard belongs
in `config.update_settings` (the chokepoint) with an explicit opt-out, not on one
caller.

### H2 — No cancellation, and QThreads are destroyed while running
`grep` finds no `closeEvent`, `aboutToQuit`, `wait()`, or `requestInterruption`
anywhere in `app/`. There is no cancel affordance of any kind. Closing the window
mid-download destroys a running `QThread` — Qt's own "QThread: Destroyed while
thread is still running" path. Compounds C3: a livestream download cannot be
stopped except by killing the process.

### H3 — Remote video titles are rendered as Qt rich text
`setTextFormat` is called **nowhere** in `app/` (verified by grep), so every
QLabel is `Qt::AutoText`, which parses markup when `mightBeRichText()` matches.
The title comes straight from yt-dlp, i.e. from any uploader, and is set at
[main_window.py:626](app/main_window.py:626) and interpolated into a QMessageBox
at [main_window.py:879](app/main_window.py:879). The security agent verified
under PySide6 that a crafted title causes a QLabel to load and render a local
file (`sizeHint` 36×12 → 2048×2000). Impact is UI spoofing inside the app's own
chrome plus local-file rendering; the SMB/NetNTLM escalation was **not**
confirmed and should be treated as unproven. Two-line fix. Note
[history_view.py:254-259](app/history_view.py:254) does this correctly with
`html.escape(..., quote=True)` — the right pattern already exists in the codebase.

### H4 — `load_settings()` is documented "never raises" and does
**Verified empirically.** `_coerce("max_height", "1e400")` raises
**`OverflowError`**, which is an `ArithmeticError` — not caught by either
`except (TypeError, ValueError)` clause. A `settings.json` containing invalid
UTF-8 raises `UnicodeDecodeError`, which is a `ValueError` but not a
`json.JSONDecodeError`, so the `except (OSError, json.JSONDecodeError)` clause
misses it. Either one takes down the GUI, CLI and MCP server at startup with no
recovery short of deleting the file by hand, contradicting the docstring at
[config.py:194](ytdl_engine/config.py:194). Also reachable via
`YTDL_MAX_HEIGHT=1e400`.

### H5 — Settings page cannot restore the GPU/CPU choice
`reload()` ([settings_view.py:344-359](app/settings_view.py:344)) re-selects
quality, duration, filesize, frame quality, the whisper checkbox, model and cache
— but never `_device_combo`, which is only ever `setEnabled`
([settings_view.py:361](app/settings_view.py:361)). With `whisper_device="cpu"`
saved, the page displays **"Automatic"**. This is a regression I introduced with
the GPU feature earlier today, and the existing round-trip test
([test_settings_page.py:96](tests/test_settings_page.py:96)) covers only
`max_height` and `cache_max_mb`, so nothing caught it.

### H6 — `check_setup` reports ready where downloads 403
`is_download_ready`'s docstring says deno is required, but it returns
`bool(ffmpeg and ffprobe)`. The MCP `check_setup_tool` uses that field while the
CLI `doctor` uses `not problems` (which does include deno). So the agent-facing
health check reports `ready: true` on a machine where every download fails with
403 — the exact failure this project already burned a day on.

### H7 — The agent-facing surfaces are the least tested code in the repo
Coverage: **`ytdl_cli.py` 26%**, `frames.py` 45%, `download.py` 54%,
`ytdl_mcp.py` 56% (72% overall). Nothing in `tests/` touches `build_parser`,
`cmd_download`, `cmd_transcript`, `cmd_frames`, `cmd_cache`, or `config
set/reset/path`. **Six of the eight MCP tools have no collected test** —
including `_run` ([ytdl_mcp.py:98](ytdl_mcp.py:98)), the `EngineError → ToolError`
mapping every error message depends on. Frames arg construction has one weak
test whose `assert "8" in args` passes if `8` appears as any argument. Cache
management (`prune_cache`, `clear_cache`, `cached_media`, `ensure_local_media` —
150 lines) has no tests at all, which is why C1, C6 and C7 all survived. Two of
the critical findings above were found by the audit's probes, not by the suite.

---

## MEDIUM

| # | Finding | Location |
|---|---|---|
| M1 | `whisper_model` is a free-form string on the MCP tool (unlike `whisper_device`, correctly a `Literal`); `faster_whisper` treats any value containing `/` as a Hugging Face repo and `snapshot_download`s it into a C++ deserializer | [ytdl_mcp.py:182](ytdl_mcp.py:182), [transcript.py:339](ytdl_engine/transcript.py:339) |
| M2 | `shutil.which(name)` is called without `path=`; **verified** that Python 3.14.6 on win32 does `path.insert(0, os.curdir)` when `path is None`, so CWD is searched ahead of PATH. Mitigated today because the bundled binaries win the `local.exists()` branch, but a dev checkout missing one resolves against wherever Claude Code was launched. One-keyword fix | [binaries.py:82](ytdl_engine/binaries.py:82) |
| M3 | `clear_error()` flips `_in_error` and the state role but never calls `setText`, so `url()` can return the error sentence as if the user typed it | `app/widgets.py` |
| M4 | Frame extraction unconditionally `unlink()`s every pre-existing `frame_*.jpg` / `raw_*.jpg` in the output directory — destructive if pointed at a photo folder | [frames.py:177-180](ytdl_engine/frames.py:177) |
| M5 | `extract_frames_tool(target=…)` writes to `candidate.parent / "frames"`, and `download_video_tool(output_dir=…)` is unconstrained — an agent can write into any directory | [frames.py:300](ytdl_engine/frames.py:300), [ytdl_mcp.py:392](ytdl_mcp.py:392) |
| M6 | Tests write into a real user folder (`%APPDATA%\YT Downloader Tests\`) and read last run's leftovers; `settings_page` fixture walks the developer's real 712 MB `%TEMP%\ytdl-agent-cache`, making those setups the suite's slowest at 4–6.7s | [conftest.py:33](tests/conftest.py:33), [settings_view.py:384](app/settings_view.py:384) |
| M7 | `settings.scene_threshold` is read by nothing — a dead setting the GUI and docs both present as real | — |
| M8 | Five CLI flags (`--lang`, `--model`, `--max`, `--width`, frames `--quality`) have non-None argparse defaults, so they preempt the user's settings; `download`'s flags correctly use `default=None`. Inconsistent within one file | `ytdl_cli.py` |
| M9 | The GUI cannot invoke transcription or frame extraction at all — no `app/` file calls `get_transcript` or `extract_frames` — yet the Settings page configures both. The app asks for preferences it cannot act on | `app/` |

## LOW — lint (20 items, all mechanical)

14 unused imports (F401) · 2 duplicate `yt_dlp` imports
([info.py:150](ytdl_engine/info.py:150), [:284](ytdl_engine/info.py:284), F811) ·
1 unused variable (`widgets.py:1195`, F841) · 3 blocking `Path` operations inside
async MCP handlers ([ytdl_mcp.py:361](ytdl_mcp.py:361),
[:393](ytdl_mcp.py:393), ASYNC240). Vulture is clean at confidence 80.

---

## What is genuinely right

Worth recording so it does not get "simplified" away later:

- **Subprocess layer.** No shell is ever spawned. The `-vf` filter strings cannot
  be injected: every interpolated value is clamped and float-formatted before
  use (`width` → `max(160, min(…, 1920))`, `scene_threshold` →
  `max(0.05, min(…, 0.95))`, `interval` → `f"{…:.4f}"`), and `:.4f` cannot emit
  `,` or `'`. Pathological `nan`/`inf` produce a rejected command, not an
  injection.
- **Path traversal via title is impossible** — yt-dlp's `sanitize_filename`
  replaces separators with lookalike codepoints *before* the join, verified
  against the installed 2026.08.18.
- **`cache_dir_for()`** uses a strict allowlist (`c.isalnum() or c in "-_"`,
  truncated to 64) so a video id cannot escape the cache root. The irony is that
  the id is sanitized carefully while the root it joins to is not validated at
  all (C1).
- **`fetch_thumbnail_bytes`** routing through yt-dlp's request director rather
  than bare `urllib` is the right call for a frozen build with a broken SSL trust
  store, and the comment says why.
- **The retry suite** ([test_engine.py:144-233](tests/test_engine.py:144)) counts
  attempts, asserts the *last* error survives, asserts `sleep` is skipped on
  first-attempt success, and distinguishes skipped-vs-real downloads via
  `__real_download`.
- **`test_whisper_device.py`** is the strongest file in the repo: the exploding
  generator at `:162` proves segments are materialised before the fallback, and
  `:139` proves a CPU failure is not swallowed. That is the correct priority —
  the fallback paths matter more than the happy path.
- **The cp1252 subprocess regression test** ([test_first_run.py:209](tests/test_first_run.py:209))
  runs a real subprocess with a CJK path. That is how you pin a shipped bug.
- **`EXTRACTOR_ARGS = {}`** and the "do not re-add a client-specific DRM
  heuristic" comment are load-bearing institutional memory.

---

## Recommended order of work

1. **C1** — only finding that destroys user data; contained fix (sentinel
   segment + marker file + extend the guard set).
2. **C2, C5, C6** — silent wrong output. C2 is one dict key; C5 is validating one
   field at the chokepoint; C6 is recording the height in the cached filename.
3. **C3, C4, C7** — refuse what cannot be handled: guard `is_live`, reject
   playlist/channel URLs with a message naming why, and never evict the folder
   the current call is about to read.
4. **H3 (2 lines), H4, H5, H6, M2 (1 keyword)** — small, each closes a real hole.
5. **H2** — needs a design decision (cooperative cancellation + `closeEvent`
   joining workers).
6. **H1** — needs a trust-boundary decision, not just a patch.
7. **H7** — the tests that would have caught C1/C5/C6/C7 in the first place.

## Ideas worth adding

Ranked by value over effort:

1. **Cancel button** — currently impossible to stop a download; prerequisite for
   handling livestreams gracefully rather than refusing them.
2. **Transcript + frames in the GUI** — the engine and settings already exist
   (M9). This is the project's most distinctive capability and the desktop app
   cannot reach it.
3. **Playlist support, done properly** — a queue with per-item progress, instead
   of C4's accidental expansion.
4. **Bundle `faster-whisper` in the shipped build** — the released app reports
   "install faster-whisper" for a feature it advertises. Roughly doubles the
   download size; worth asking users.
5. **Resume interrupted downloads** — yt-dlp supports it; a 2 GB download that
   dies at 90% currently starts over.
6. **SponsorBlock / chapter-aware trimming** — one yt-dlp postprocessor away.
7. **Clipboard detection and drag-and-drop** — paste-free start, cheap polish.
8. **Show the cache path in Settings** — small, and it is what makes C1 invisible.

---

## What I checked

- Read in full: `ytdl_engine/{core,download,info,transcript,config,frames,binaries}.py`,
  `ytdl_cli.py`, `ytdl_mcp.py`, `agent_entry.py`, `app/{workers,settings_view,main_window,history_view,history,binaries}.py`,
  the `tests/` suite, `build.spec`.
- **Ran:** `pytest --cov` (149 passing, 72%), `ruff check --select E9,F,B,S,ASYNC,RUF` (20 substantive),
  `vulture` at confidence 80 (clean).
- **Executed to verify specific claims:** the `format_string`/`build_ydl_opts`
  mode matrix (C5), `_coerce` accepting arbitrary `default_mode` (C5),
  `_coerce("max_height","1e400")` → `OverflowError` (H4), `shutil.which`'s
  `curdir` insertion in the installed Python 3.14.6 (M2).
- **Grepped to confirm absence:** `writeautomaticsub` (C2), `is_live` in the
  download path (C3), playlist/`entries` guards (C4), `setTextFormat` in `app/`
  (H3), `closeEvent`/`wait`/`requestInterruption` (H2), cancellation of any kind.
- **Established by code reading, not executed:** C1 (did not run the deletion),
  C3 (no live stream tested), C4 (no playlist URL fetched), C7 (loop logic read,
  not triggered).
- **Corrected from the agent reports:** the `default_mode` vocabulary claim (C5),
  the characterization of C7 as "deletes the just-downloaded file" in general
  when it is specifically "a video larger than the cache limit", and the
  SMB/NetNTLM escalation in H3, which is unproven.
