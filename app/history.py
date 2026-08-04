"""Persistent record of downloaded videos -- what the History page reads
from and writes to. Deliberately has no relationship to the actual video
file on disk: this is a list of "you downloaded this before", not a file
tracker, so removing an entry never touches a downloaded file and a
deleted file never touches its entry (see README/feature discussion).

Storage is a plain JSON file plus one small cached thumbnail image per
entry, not a database and not base64-in-JSON: history is a personal-scale
list (dozens to low hundreds of entries), so there's nothing here that
benefits from a query engine, and keeping images out of the JSON keeps it
small and human-readable. Thumbnails are cached at download time rather
than re-fetched from the network whenever the history page opens, both
for speed and so browsing history doesn't depend on connectivity.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QImage


@dataclass
class HistoryEntry:
    video_id: str
    title: str
    url: str
    downloaded_at: str  # ISO 8601 UTC -- also the sort key (newest first)
    has_thumbnail: bool


class HistoryStore:
    def __init__(self) -> None:
        app_data = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        base = Path(app_data) if app_data else Path.home() / ".youtube-downloader"
        self._dir = base
        self._thumb_dir = base / "history_thumbnails"
        self._thumb_dir.mkdir(parents=True, exist_ok=True)
        self._json_path = base / "history.json"
        self._entries: dict[str, HistoryEntry] = self._load()

    def _load(self) -> dict[str, HistoryEntry]:
        if not self._json_path.exists():
            return {}
        try:
            raw = json.loads(self._json_path.read_text(encoding="utf-8"))
        except Exception:
            # A corrupt history file should never block using the app --
            # worst case, history starts over empty.
            return {}
        entries: dict[str, HistoryEntry] = {}
        for item in raw:
            try:
                entry = HistoryEntry(**item)
                entries[entry.video_id] = entry
            except Exception:
                continue
        return entries

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = [asdict(e) for e in self._entries.values()]
        self._json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @property
    def entries(self) -> list[HistoryEntry]:
        """Newest-downloaded-first."""
        return sorted(self._entries.values(), key=lambda e: e.downloaded_at, reverse=True)

    def thumbnail_path(self, video_id: str) -> Path:
        return self._thumb_dir / f"{video_id}.jpg"

    def load_thumbnail(self, video_id: str) -> QImage | None:
        path = self.thumbnail_path(video_id)
        if not path.exists():
            return None
        image = QImage(str(path))
        return image if not image.isNull() else None

    def add_or_update(
        self, video_id: str, title: str, url: str, thumbnail: QImage | None
    ) -> HistoryEntry:
        """Adds a new entry, or -- if this video was already downloaded
        before -- updates its title/url/timestamp in place rather than
        creating a duplicate. The refreshed timestamp is what moves a
        redownloaded video back to the top of the list."""
        thumb_path = self.thumbnail_path(video_id)
        if thumbnail is not None and not thumbnail.isNull():
            has_thumbnail = thumbnail.save(str(thumb_path), "JPG", 85)
        else:
            # No new thumbnail this time (e.g. unreachable host) -- keep
            # whatever was already cached from a previous download rather
            # than losing it.
            has_thumbnail = thumb_path.exists()

        entry = HistoryEntry(
            video_id=video_id,
            title=title,
            url=url,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
            has_thumbnail=has_thumbnail,
        )
        self._entries[video_id] = entry
        self._save()
        return entry

    def remove(self, video_id: str) -> None:
        self._entries.pop(video_id, None)
        thumb_path = self.thumbnail_path(video_id)
        if thumb_path.exists():
            try:
                thumb_path.unlink()
            except OSError:
                pass
        self._save()
