"""User-adjustable settings shared by the GUI, the CLI, and the MCP server.

Every default that used to be hard-coded in a function signature lives
here instead, so "what quality does it pick and where does it put the
file" has one answer the user can change, rather than three scattered
ones they can't.

Resolution order, highest priority first:

  1. an explicit argument at the call site (``--quality 720``, the GUI's
     dropdown, an MCP tool parameter)
  2. an environment variable (``YTDL_MAX_HEIGHT=720``) -- handy for
     one-off runs and CI without editing a file
  3. the settings file (``settings.json``, next to history.json)
  4. the built-in default below

Nothing here is required: with no settings file and no env vars, the
built-in defaults are exactly what the app shipped with.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

SETTINGS_FILENAME = "settings.json"
_ENV_PREFIX = "YTDL_"
APP_NAME = "YouTube Downloader"


def app_data_dir() -> Path:
    """Where this app keeps its data -- the same folder history.json is in.

    Computed without Qt (the engine must stay importable headlessly), but
    deliberately matching QStandardPaths.AppDataLocation, which is what
    app/history.py uses, so the GUI and the agent surfaces agree on one
    folder instead of quietly keeping two.
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME


def settings_path() -> Path:
    override = os.environ.get(f"{_ENV_PREFIX}SETTINGS_FILE")
    if override:
        return Path(override).expanduser()
    return app_data_dir() / SETTINGS_FILENAME


def default_download_dir() -> Path:
    return Path.home() / "Downloads"


@dataclass
class Settings:
    """Every knob, with the value used when nothing overrides it.

    ``None`` consistently means "no limit / decide automatically", so a
    user can switch a cap off as easily as they can set one.
    """

    # --- Where files go ----------------------------------------------
    # Shared by the GUI's destination picker and agent downloads, so the
    # folder chosen in the app is the folder Claude saves to. None = the
    # OS Downloads folder.
    download_dir: str | None = None

    # --- Quality ------------------------------------------------------
    # Ceiling for downloads made WITHOUT an explicit quality (agent
    # surfaces, mostly). None = best available, no cap. Defaults to 1080
    # rather than unlimited because an agent asked to "download this"
    # shouldn't silently pull a multi-gigabyte 4K file; raise or clear it
    # if you do want the maximum. The GUI is unaffected -- its dropdown
    # is always an explicit choice.
    max_height: int | None = 1080
    default_mode: str = "video"          # video | video_only | audio_only
    audio_format: str = "mp3"            # mp3 | m4a | wav

    # --- Safety limits ------------------------------------------------
    # Both off by default: an agent only ever downloads because the user
    # asked, so imposing a silent ceiling would break legitimate use.
    # They exist for anyone who wants a guardrail against a runaway
    # 10-hour livestream or an accidental huge file.
    max_duration_minutes: int | None = None
    max_filesize_mb: int | None = None

    # --- Frames -------------------------------------------------------
    frame_interval_seconds: float = 10.0
    frame_max: int = 50
    frame_width: int = 800
    # Quality of the copy fetched purely to slice frames out of. 480p
    # keeps it fast and is plenty to see what's on screen; raise it when
    # you need to read fine print in a UI or a chart.
    frame_download_height: int | None = 480
    scene_threshold: float = 0.3

    # --- Transcripts --------------------------------------------------
    transcript_language: str = "en"
    whisper_model: str = "small"
    # "auto" | "cuda" | "cpu". Auto uses the GPU when one is present,
    # which is several times faster, and falls back to CPU when it isn't
    # -- so it is safe to leave alone on any machine. "cuda" forces the
    # GPU and errors if there is none, rather than quietly being slow.
    whisper_device: str = "auto"
    # Set false to make caption-less videos fail fast instead of spending
    # minutes on local transcription.
    allow_whisper: bool = True

    # --- First-run confirmation ---------------------------------------
    # False until the user has actually seen where downloads go and at
    # what quality. While false, an agent's first download_video call
    # returns those defaults for confirmation INSTEAD of downloading.
    #
    # A setting rather than a line in the skill on purpose: guidance in a
    # prompt is advice an assistant may skip, especially under a
    # permission mode that auto-approves routine tool calls. This makes
    # the first download structurally impossible to do silently. It asks
    # exactly once -- confirming (or running `setup`) flips it, because a
    # prompt on every download is one people learn to click through.
    defaults_confirmed: bool = False

    # --- Working cache ------------------------------------------------
    # Videos pulled just to transcribe or extract frames from. None = the
    # system temp dir. The cache is pruned oldest-first once it exceeds
    # cache_max_mb (None disables pruning entirely).
    cache_dir: str | None = None
    cache_max_mb: int | None = 5000

    # ------------------------------------------------------------------

    def resolved_download_dir(self) -> Path:
        return (
            Path(self.download_dir).expanduser()
            if self.download_dir
            else default_download_dir()
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_FIELD_TYPES = {f.name: f.type for f in fields(Settings)}

# Fields whose *value* matters, not just its type. Without this a typo'd
# or wrongly-spelled value was accepted and then silently misbehaved
# somewhere far away: `default_mode=audio` (the spelling the CLI's own
# --mode flag uses) is not a valid mode, and it used to reach
# format_string(), fall through to the audio branch, skip the mp3
# postprocessor, and produce an audio-only stream the caller was told
# would be an .mp4. Rejecting it here -- at the one chokepoint every
# surface goes through -- means a bad value is reported where it was
# typed instead of becoming a wrong file an hour later.
#
# whisper_model is constrained for a second reason: faster-whisper
# treats any value containing "/" as a Hugging Face repo id and
# downloads it, so a free-form string let an agent-set value pull
# arbitrary remote weights into a native deserializer. These are the
# size names faster-whisper itself recognises.
_ALLOWED_VALUES: dict[str, tuple[str, ...]] = {
    "default_mode": ("video", "video_only", "audio_only"),
    "audio_format": ("mp3", "m4a", "wav"),
    "whisper_device": ("auto", "cuda", "cpu"),
    "whisper_model": (
        "tiny", "tiny.en", "base", "base.en", "small", "small.en",
        "medium", "medium.en", "large", "large-v1", "large-v2",
        "large-v3", "large-v3-turbo", "turbo",
        "distil-small.en", "distil-medium.en",
        "distil-large-v2", "distil-large-v3",
    ),
}

# Settings only the user may change, never an assistant. Enforced here,
# in update_settings, rather than in the MCP server: the guard used to
# live on one of three callers, so the CLI's `config set` bypassed it
# completely. The default is the safe one, so a surface added later is
# restricted until it deliberately opts out.
#
#   defaults_confirmed -- the first-download consent flag. An assistant
#     flipping this is an assistant granting itself permission.
#   cache_dir -- feeds shutil.rmtree in prune_cache/clear_cache. Even
#     with cache_root() now confined to its own subdirectory, there is no
#     reason an assistant needs to move a scratch folder.
#
# download_dir is deliberately NOT here: pointing downloads somewhere
# else is a documented thing to ask for ("always save to D:/Videos"),
# nothing is ever overwritten (force_overwrite defaults to False), and
# the skill tells assistants to use update_settings for exactly that.
_USER_ONLY_SETTINGS = frozenset({"defaults_confirmed", "cache_dir"})


def _coerce(name: str, raw: Any) -> Any:
    """Turn a JSON value or an env string into the field's real type.

    Env vars arrive as strings, and "none"/"" has to mean None so a user
    can clear a cap from the shell as easily as from the file.

    Raises ValueError for a value outside _ALLOWED_VALUES. Callers that
    must not fail (load_settings) catch it and keep the default.
    """
    if raw is None:
        return None
    declared = str(_FIELD_TYPES.get(name, "str"))
    if isinstance(raw, str):
        text = raw.strip()
        if text.lower() in ("none", "null", ""):
            return None
        if "bool" in declared:
            return text.lower() in ("1", "true", "yes", "on")
        if "int" in declared:
            return int(float(text))
        if "float" in declared:
            return float(text)
        return _check_allowed(name, text)
    if "bool" in declared:
        return bool(raw)
    if "int" in declared and isinstance(raw, (int, float)):
        return int(raw)
    if "float" in declared and isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        return _check_allowed(name, raw)
    return raw


def _check_allowed(name: str, text: str) -> str:
    allowed = _ALLOWED_VALUES.get(name)
    if allowed and text not in allowed:
        raise ValueError(
            f"{name} must be one of: {', '.join(allowed)} (got {text!r})."
        )
    return text


def known_fields() -> list[str]:
    return [f.name for f in fields(Settings)]


def allowed_values(name: str) -> tuple[str, ...]:
    """The accepted values for a constrained setting, else ().

    Public so the CLI help and the MCP tool descriptions can state the
    constraint without restating the list -- a second copy is a copy that
    drifts, and the MCP schema in particular is what an assistant reads
    instead of guessing.
    """
    return _ALLOWED_VALUES.get(name, ())


def load_settings(path: Path | None = None) -> Settings:
    """Built-in defaults <- settings file <- environment.

    Never raises: a corrupt or unreadable settings file falls back to the
    defaults rather than taking the whole app down over a stray comma.

    The except clauses below are wider than they look like they need to
    be, and both widenings are load-bearing. This function runs at import
    time on all three surfaces, so anything that escapes here takes down
    the GUI, the CLI and the MCP server at startup with no way back
    except deleting the file by hand:

      - ValueError at the file read, not json.JSONDecodeError: a file
        containing invalid UTF-8 raises UnicodeDecodeError, which is a
        ValueError but NOT a JSONDecodeError, so it used to escape.
      - ArithmeticError per field: _coerce does int(float(text)), and
        float("1e400") is inf, so int() raises OverflowError -- an
        ArithmeticError, not a ValueError. Reachable from the file or
        from YTDL_MAX_HEIGHT=1e400.
    """
    settings = Settings()
    target = path or settings_path()

    try:
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in _FIELD_TYPES:
                        try:
                            setattr(settings, key, _coerce(key, value))
                        except (TypeError, ValueError, ArithmeticError):
                            pass  # keep the default for that one field
    except (OSError, ValueError):
        pass

    for name in known_fields():
        raw = os.environ.get(f"{_ENV_PREFIX}{name.upper()}")
        if raw is not None:
            try:
                setattr(settings, name, _coerce(name, raw))
            except (TypeError, ValueError, ArithmeticError):
                pass

    return settings


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Write the settings file. Only the file is written -- environment
    overrides stay in the environment, so saving can't silently bake a
    one-off env value into permanent config."""
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.as_dict()
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target


class UserOnlySettingError(PermissionError):
    """An assistant tried to change a setting only the user may change.

    A distinct type so the MCP server can turn it into a tool error that
    tells the assistant what to ask the user to do instead, rather than
    reporting it as a generic failure.
    """


def update_settings(
    changes: dict[str, Any],
    path: Path | None = None,
    *,
    allow_user_only: bool = False,
) -> Settings:
    """Apply changes on top of what's on disk (NOT on top of the
    env-merged view), then save. Unknown keys raise, so a typo'd setting
    name is reported instead of silently doing nothing.

    allow_user_only gates _USER_ONLY_SETTINGS and defaults to False --
    the restrictive value -- so this is safe to call from a new surface
    without remembering to pass anything. The GUI and the CLI pass True
    because both are the user operating the app directly; the MCP server
    never does.
    """
    target = path or settings_path()
    on_disk = Settings()
    try:
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in _FIELD_TYPES:
                        try:
                            setattr(on_disk, key, _coerce(key, value))
                        except (TypeError, ValueError, ArithmeticError):
                            pass  # a bad value already on disk keeps its default
    except (OSError, ValueError):
        pass

    unknown = [k for k in changes if k not in _FIELD_TYPES]
    if unknown:
        raise KeyError(
            f"Unknown setting(s): {', '.join(sorted(unknown))}. "
            f"Valid settings: {', '.join(known_fields())}"
        )
    if not allow_user_only:
        blocked = sorted(_USER_ONLY_SETTINGS & set(changes))
        if blocked:
            raise UserOnlySettingError(
                f"{', '.join(blocked)} can only be changed by the user, "
                "in the app's Settings page or with "
                f"`ytdl-agent config set {blocked[0]}=...`."
            )
    for key, value in changes.items():
        setattr(on_disk, key, _coerce(key, value))
    save_settings(on_disk, target)
    return load_settings(target)


def describe_sources(path: Path | None = None) -> dict[str, str]:
    """Which layer each setting's current value came from -- so `config
    show` can explain *why* a value is what it is, instead of leaving the
    user to guess whether their file or an env var won."""
    target = path or settings_path()
    sources = {name: "default" for name in known_fields()}
    try:
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in data:
                    if key in sources:
                        sources[key] = "settings file"
    except (OSError, ValueError):
        pass
    for name in known_fields():
        if os.environ.get(f"{_ENV_PREFIX}{name.upper()}") is not None:
            sources[name] = f"env {_ENV_PREFIX}{name.upper()}"
    return sources
