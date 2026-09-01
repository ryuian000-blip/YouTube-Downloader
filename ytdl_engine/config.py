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
    # Set false to make caption-less videos fail fast instead of spending
    # minutes on local transcription.
    allow_whisper: bool = True

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


def _coerce(name: str, raw: Any) -> Any:
    """Turn a JSON value or an env string into the field's real type.

    Env vars arrive as strings, and "none"/"" has to mean None so a user
    can clear a cap from the shell as easily as from the file.
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
        return text
    if "bool" in declared:
        return bool(raw)
    if "int" in declared and isinstance(raw, (int, float)):
        return int(raw)
    if "float" in declared and isinstance(raw, (int, float)):
        return float(raw)
    return raw


def known_fields() -> list[str]:
    return [f.name for f in fields(Settings)]


def load_settings(path: Path | None = None) -> Settings:
    """Built-in defaults <- settings file <- environment.

    Never raises: a corrupt or unreadable settings file falls back to the
    defaults rather than taking the whole app down over a stray comma.
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
                        except (TypeError, ValueError):
                            pass  # keep the default for that one field
    except (OSError, json.JSONDecodeError):
        pass

    for name in known_fields():
        raw = os.environ.get(f"{_ENV_PREFIX}{name.upper()}")
        if raw is not None:
            try:
                setattr(settings, name, _coerce(name, raw))
            except (TypeError, ValueError):
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


def update_settings(changes: dict[str, Any], path: Path | None = None) -> Settings:
    """Apply changes on top of what's on disk (NOT on top of the
    env-merged view), then save. Unknown keys raise, so a typo'd setting
    name is reported instead of silently doing nothing."""
    target = path or settings_path()
    on_disk = Settings()
    try:
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in _FIELD_TYPES:
                        setattr(on_disk, key, _coerce(key, value))
    except (OSError, json.JSONDecodeError):
        pass

    unknown = [k for k in changes if k not in _FIELD_TYPES]
    if unknown:
        raise KeyError(
            f"Unknown setting(s): {', '.join(sorted(unknown))}. "
            f"Valid settings: {', '.join(known_fields())}"
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
    except (OSError, json.JSONDecodeError):
        pass
    for name in known_fields():
        if os.environ.get(f"{_ENV_PREFIX}{name.upper()}") is not None:
            sources[name] = f"env {_ENV_PREFIX}{name.upper()}"
    return sources
