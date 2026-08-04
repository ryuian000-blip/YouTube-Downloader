"""The download history page: a searchable list of previously downloaded
videos, each showing its thumbnail, title, a clickable link back to the
video, a redownload button, and a remove button. Lives as the second page
of MainWindow's QStackedWidget (see main_window.py) -- a dropdown or
popover doesn't have room for a scrollable, searchable, thumbnail-bearing
list without feeling cramped in this app's compact window, and a separate
OS window would fight the "takes you back to the normal window" flow
redownloading is meant to have.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import theme
from app.history import HistoryEntry, HistoryStore
from app.imaging import fit_pixmap, rounded_pixmap
from app.theme import ColorTokens
from app.widgets import AnimatedButton


def _relative_date(iso_timestamp: str) -> str:
    """A short, human "when" for a row -- "Today", "3 days ago", falling
    back to a plain date once "N days ago" stops being a useful unit."""
    try:
        when = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return ""
    days = (datetime.now(timezone.utc) - when).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 14:
        return f"{days} days ago"
    return when.strftime("%b %d, %Y")


class HistoryRow(QFrame):
    THUMBNAIL_SIZE = QSize(88, 50)

    redownload_clicked = Signal(str)  # url
    remove_clicked = Signal(str)      # video_id

    def __init__(
        self, entry: HistoryEntry, store: HistoryStore, colors: ColorTokens, parent=None
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", "true")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_SM, theme.SPACE_SM, theme.SPACE_SM
        )
        layout.setSpacing(theme.SPACE_SM)

        thumb_label = QLabel()
        thumb_label.setObjectName("thumbnailLabel")
        thumb_label.setFixedSize(self.THUMBNAIL_SIZE)
        thumb_label.setAlignment(Qt.AlignCenter)
        image = store.load_thumbnail(entry.video_id) if entry.has_thumbnail else None
        if image is not None:
            pixmap = QPixmap.fromImage(image)
            pixmap = fit_pixmap(pixmap, self.THUMBNAIL_SIZE)
            pixmap = rounded_pixmap(pixmap, theme.RADIUS_CONTROL)
            thumb_label.setPixmap(pixmap)
        layout.addWidget(thumb_label)

        col = QVBoxLayout()
        col.setSpacing(2)

        title_label = QLabel(entry.title)
        title_label.setProperty("role", "videoTitle")
        title_label.setWordWrap(True)
        col.addWidget(title_label)

        # Rich-text QLabel hyperlink, not a custom click handler -- Qt
        # already opens it in the OS default browser via
        # setOpenExternalLinks, so there's no reason to hand-roll
        # QDesktopServices.openUrl wiring for something this standard.
        link_label = QLabel(f'<a href="{entry.url}">{entry.url}</a>')
        link_label.setProperty("role", "path")
        link_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link_label.setOpenExternalLinks(True)
        link_label.setCursor(Qt.PointingHandCursor)
        col.addWidget(link_label)

        date_label = QLabel(_relative_date(entry.downloaded_at))
        date_label.setProperty("role", "status")
        col.addWidget(date_label)

        layout.addLayout(col, stretch=1)

        redownload_btn = AnimatedButton("↻")  # ↻
        redownload_btn.setFixedSize(32, 32)
        redownload_btn.setBorderRadius(16)
        redownload_btn.setToolTip("Redownload")
        redownload_btn.apply_theme(colors)
        redownload_btn.clicked.connect(lambda: self.redownload_clicked.emit(entry.url))
        layout.addWidget(redownload_btn, alignment=Qt.AlignVCenter)

        remove_btn = AnimatedButton("✕")  # ✕
        remove_btn.setFixedSize(32, 32)
        remove_btn.setBorderRadius(16)
        remove_btn.setToolTip("Remove from history")
        remove_btn.apply_theme(colors)
        remove_btn.clicked.connect(lambda: self.remove_clicked.emit(entry.video_id))
        layout.addWidget(remove_btn, alignment=Qt.AlignVCenter)


class HistoryPage(QWidget):
    back_requested = Signal()
    redownload_requested = Signal(str)  # url

    def __init__(self, store: HistoryStore, colors: ColorTokens, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._colors = colors
        self._rows: list[HistoryRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG
        )
        outer.setSpacing(theme.SPACE_MD)

        header = QHBoxLayout()
        header.setSpacing(theme.SPACE_MD)
        back_btn = AnimatedButton("← Back")  # ← Back
        back_btn.setFixedHeight(32)
        back_btn.setBorderRadius(16)
        back_btn.apply_theme(colors)
        back_btn.clicked.connect(self.back_requested)
        header.addWidget(back_btn)

        title_label = QLabel("Download History")
        title_label.setProperty("role", "title")
        header.addWidget(title_label, stretch=1)
        outer.addLayout(header)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search by title…")
        self._search_edit.setMinimumHeight(36)
        self._search_edit.textChanged.connect(self._rebuild)
        outer.addWidget(self._search_edit)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(theme.SPACE_SM)

        # The empty-state message lives *inside* the scroll area's content
        # (before the trailing stretch), not as a sibling widget toggled
        # visible/hidden alongside it -- toggling _scroll itself off left
        # no visible item holding this layout's stretch=1, and Qt's
        # QVBoxLayout falls back to inflating the last remaining item (this
        # label) to fill the leftover space instead of leaving it compact,
        # which is what produced the huge empty gap around the text. With
        # the label always inside the one permanently-stretched scroll
        # area instead, there's nothing to fall back onto.
        self._empty_label = QLabel("No downloads yet.")
        self._empty_label.setProperty("role", "status")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._list_layout.addWidget(self._empty_label)

        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_container)
        outer.addWidget(self._scroll, stretch=1)

    def reload(self) -> None:
        """Call before showing this page -- picks up anything downloaded
        or removed since it was last shown, and resets any leftover
        search text from a previous visit."""
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        self._rebuild()

    def _rebuild(self) -> None:
        for row in self._rows:
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        query = self._search_edit.text().strip().lower()
        entries = self._store.entries
        if query:
            entries = [e for e in entries if query in e.title.lower()]
            self._empty_label.setText("No matching downloads.")
        else:
            self._empty_label.setText("No downloads yet.")

        for entry in entries:
            row = HistoryRow(entry, self._store, self._colors)
            row.redownload_clicked.connect(self.redownload_requested)
            row.remove_clicked.connect(self._on_remove)
            # Insert right after the empty label (index 1), ahead of the
            # trailing stretch -- the label itself stays in the layout at
            # index 0 throughout, just hidden, rather than being
            # added/removed, so there's no risk of insertWidget's index
            # math drifting relative to it across repeated rebuilds.
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._rows.append(row)

        self._empty_label.setVisible(not entries)

    def _on_remove(self, video_id: str) -> None:
        self._store.remove(video_id)
        self._rebuild()
