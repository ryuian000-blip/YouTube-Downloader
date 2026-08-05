"""The download history page: a searchable, day-grouped list of previously
downloaded videos, each showing its thumbnail, title (itself the link back
to the video), duration/size/exact-date, a redownload button, and a remove
button. Lives as the second page of MainWindow's QStackedWidget (see
main_window.py) -- a dropdown or popover doesn't have room for a
scrollable, searchable, thumbnail-bearing list without feeling cramped in
this app's compact window, and a separate OS window would fight the "takes
you back to the normal window" flow redownloading is meant to have.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import icons, theme
from app.history import HistoryEntry, HistoryStore
from app.imaging import fit_pixmap, rounded_pixmap
from app.theme import ColorTokens
from app.widgets import IconButton
from app.workers import format_filesize


def _bucket_label(when_utc: datetime) -> str:
    """The day-group a timestamp falls into, in the same spirit as file
    explorers' "Today / Yesterday / Earlier this week / ..." grouping --
    not byte-for-byte identical bucket boundaries, just the same idea:
    coarse and relative near the top, calendar-exact once it's old enough
    that "N days ago" stops being a useful unit."""
    when_local = when_utc.astimezone()
    today = datetime.now().astimezone().date()
    d = when_local.date()
    delta_days = (today - d).days

    if delta_days <= 0:
        return "Today"
    if delta_days == 1:
        return "Yesterday"

    week_start = today - timedelta(days=today.weekday())
    if d >= week_start:
        return "Earlier this week"
    last_week_start = week_start - timedelta(days=7)
    if d >= last_week_start:
        return "Last week"

    month_start = today.replace(day=1)
    if d >= month_start:
        return "Earlier this month"
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    if d >= last_month_start:
        return "Last month"

    year_start = today.replace(month=1, day=1)
    if d >= year_start:
        return "Earlier this year"
    return str(d.year)


def _exact_date(iso_timestamp: str) -> str:
    """The precise download date, in local time. Deliberately date-only,
    no time-of-day: the group header above each row already carries the
    coarse "how long ago", so this chip's job is just to answer "which
    day, exactly" without repeating it."""
    try:
        when = datetime.fromisoformat(iso_timestamp).astimezone()
    except ValueError:
        return ""
    return when.strftime("%b %d, %Y")


class _GroupHeader(QWidget):
    """A collapsible day-group divider: chevron, label, trailing rule.
    Clicking anywhere on the row toggles the group -- there is no separate
    hit target, since the whole header is the affordance."""

    toggled = Signal(bool)  # new expanded state

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label.upper()
        self._colors: ColorTokens | None = None
        self._expanded = True
        self._angle = 0.0  # 0 = chevron pointing down (expanded), -90 = right (collapsed)
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"chevronAngle", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def apply_theme(self, c: ColorTokens) -> None:
        self._colors = c
        self.update()

    def _get_angle(self) -> float:
        return self._angle

    def _set_angle(self, value: float) -> None:
        self._angle = value
        self.update()

    chevronAngle = Property(float, _get_angle, _set_angle)

    def isExpanded(self) -> bool:  # noqa: N802 (Qt naming)
        return self._expanded

    def setExpanded(self, expanded: bool, animate: bool = True) -> None:  # noqa: N802
        if expanded == self._expanded:
            return
        self._expanded = expanded
        target = 0.0 if expanded else -90.0
        self._anim.stop()
        if animate:
            self._anim.setStartValue(self._angle)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._set_angle(target)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.setExpanded(not self._expanded)
            self.toggled.emit(self._expanded)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        c = self._colors
        if c is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())

        chevron_size = 16.0
        chevron_rect = QRectF(
            rect.left(), rect.center().y() - chevron_size / 2, chevron_size, chevron_size
        )
        p.save()
        p.translate(chevron_rect.center())
        p.rotate(self._angle)
        p.translate(-chevron_rect.center())
        icons.draw_chevron_down(p, chevron_rect, QColor(c.TEXT_MUTED))
        p.restore()

        font = self.font()
        font.setBold(True)
        font.setPixelSize(theme.SIZE_SECTION_LABEL)
        p.setFont(font)
        p.setPen(QColor(c.TEXT_MUTED))
        label_x = chevron_rect.right() + theme.SPACE_SM
        fm = QFontMetrics(font)
        p.drawText(
            QRectF(label_x, rect.top(), fm.horizontalAdvance(self._label) + 4, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._label,
        )

        rule_x = label_x + fm.horizontalAdvance(self._label) + theme.SPACE_SM
        if rule_x < rect.right():
            p.setPen(QPen(QColor(c.BORDER), 1))
            y = rect.center().y()
            p.drawLine(QPointF(rule_x, y), QPointF(rect.right(), y))
        p.end()


class _ChipLabel(QLabel):
    """A row-info chip -- duration/size/date. Overrides minimumSizeHint()
    down to a small floor so three of these sharing one row (with no
    individual stretch factor) can compress under real width pressure,
    while deliberately keeping QLabel's default Preferred size policy so
    it still renders at its natural width whenever there's room.

    An earlier version of this reused ElidedLabel for the same purpose,
    but ElidedLabel's Qt.SizePolicy.Ignored policy turned out to collapse
    same-row Ignored siblings with no stretch factor to literally zero
    width instead of the intended small-but-visible floor -- confirmed by
    direct geometry inspection, not a guess. Preferred + only overriding
    the minimum is the combination that actually behaves as "shrink
    toward a floor, never below it, never to nothing".
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        base = super().minimumSizeHint()
        return QSize(min(40, base.width()), base.height())


class HistoryRow(QFrame):
    THUMBNAIL_SIZE = QSize(116, 65)  # 16:9, matching the poster layout's ratio

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
            # All four corners, unlike the poster band's top-only rounding
            # -- this tile stands alone, it isn't the top half of a card
            # with something else rounding the bottom half underneath it.
            pixmap = rounded_pixmap(pixmap, theme.RADIUS_HISTORY_THUMB)
            thumb_label.setPixmap(pixmap)
        layout.addWidget(thumb_label)

        col = QVBoxLayout()
        col.setSpacing(4)

        # The title itself is the link now (rich-text <a>, opened via Qt's
        # own setOpenExternalLinks -- no hand-rolled QDesktopServices
        # wiring needed for something this standard). Escaped explicitly:
        # this string is built with an f-string into HTML, and a title
        # containing "&" or "<" would otherwise corrupt the markup.
        # Color is pinned to TEXT_PRIMARY inline, overriding the app-wide
        # "QLabel a" QSS rule (theme.py) on purpose -- a whole title in
        # accent green would read as a link first and a title second; the
        # underline alone is enough to signal "this opens the video".
        safe_title = html.escape(entry.title)
        safe_url = html.escape(entry.url, quote=True)
        title_label = QLabel(
            f'<a href="{safe_url}" style="color:{colors.TEXT_PRIMARY}; '
            f'text-decoration:underline;">{safe_title}</a>'
        )
        title_label.setProperty("role", "videoTitle")
        title_label.setWordWrap(True)
        title_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        title_label.setOpenExternalLinks(True)
        title_label.setCursor(Qt.PointingHandCursor)
        title_label.setToolTip(entry.url)
        col.addWidget(title_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(5)
        for text in filter(
            None,
            [entry.duration, format_filesize(entry.size_bytes), _exact_date(entry.downloaded_at)],
        ):
            chip = _ChipLabel(text)
            chip.setProperty("role", "chip")
            meta_row.addWidget(chip)
        meta_row.addStretch(1)
        col.addLayout(meta_row)

        layout.addLayout(col, stretch=1)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        redownload_btn = IconButton("refresh", diameter=30)
        redownload_btn.setToolTip("Redownload")
        redownload_btn.apply_theme(colors)
        redownload_btn.clicked.connect(lambda: self.redownload_clicked.emit(entry.url))
        actions.addWidget(redownload_btn)

        remove_btn = IconButton("close", danger=True, diameter=30)
        remove_btn.setToolTip("Remove from history")
        remove_btn.apply_theme(colors)
        remove_btn.clicked.connect(lambda: self.remove_clicked.emit(entry.video_id))
        actions.addWidget(remove_btn)

        layout.addLayout(actions)


class HistoryPage(QWidget):
    back_requested = Signal()
    redownload_requested = Signal(str)  # url

    def __init__(self, store: HistoryStore, colors: ColorTokens, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._colors = colors
        # (bucket_label, row) for every currently-built row, so a group
        # header's toggle can find and hide/show exactly its own rows.
        self._row_entries: list[tuple[str, HistoryRow]] = []
        self._headers: list[_GroupHeader] = []
        self._collapsed: set[str] = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG
        )
        outer.setSpacing(theme.SPACE_MD)

        top_row = QHBoxLayout()
        top_row.setSpacing(theme.SPACE_SM)
        back_btn = IconButton("back", diameter=38)
        back_btn.setToolTip("Back")
        back_btn.apply_theme(colors)
        back_btn.clicked.connect(self.back_requested)
        top_row.addWidget(back_btn)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search by title…")
        self._search_edit.setMinimumHeight(38)
        self._search_edit.textChanged.connect(self._rebuild)
        top_row.addWidget(self._search_edit, stretch=1)
        outer.addLayout(top_row)

        heading_row = QHBoxLayout()
        title_label = QLabel("History")
        title_label.setProperty("role", "title")
        heading_row.addWidget(title_label)
        heading_row.addStretch(1)
        self._count_label = QLabel("")
        self._count_label.setProperty("role", "status")
        heading_row.addWidget(self._count_label)
        outer.addLayout(heading_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        # Matches the download page's scroll area (MainWindow._page_scroll):
        # this app elides/compresses its way to fitting rather than ever
        # scrolling sideways.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        # which is what produced a huge empty gap around the text before.
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
        search text/collapsed groups from a previous visit."""
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        self._collapsed.clear()
        self._rebuild()

    def _clear_list(self) -> None:
        # removeWidget() only detaches a widget from the layout's own
        # bookkeeping -- per Qt's own docs, "it is the caller's
        # responsibility to give the widget a reasonable geometry" after
        # that call. It does NOT hide the widget, so without an explicit
        # hide() here, a row removed this way stays visible at its old
        # position -- still painted, just no longer positioned by anything
        # -- until deleteLater()'s deferred deletion actually runs on some
        # future event-loop pass. Typing quickly into search (a _rebuild()
        # per keystroke) could very visibly stack old and new rows on top
        # of each other in that gap; hide() makes removal immediate and
        # deterministic instead of dependent on GC-ish timing.
        for _bucket, row in self._row_entries:
            self._list_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._row_entries.clear()
        for header in self._headers:
            self._list_layout.removeWidget(header)
            header.hide()
            header.deleteLater()
        self._headers.clear()

    def _make_row(self, entry: HistoryEntry) -> HistoryRow:
        row = HistoryRow(entry, self._store, self._colors)
        row.redownload_clicked.connect(self.redownload_requested)
        row.remove_clicked.connect(self._on_remove)
        return row

    def _insert(self, widget: QWidget) -> None:
        # Always immediately before the trailing stretch, so the layout
        # keeps exactly one stretch item and it stays last no matter how
        # many headers/rows get inserted around it.
        self._list_layout.insertWidget(self._list_layout.count() - 1, widget)

    def _rebuild(self) -> None:
        self._clear_list()

        query = self._search_edit.text().strip().lower()
        entries = self._store.entries
        if query:
            entries = [e for e in entries if query in e.title.lower()]
            self._empty_label.setText("No matching downloads.")
        else:
            self._empty_label.setText("No downloads yet.")

        if query:
            # Flat, ungrouped list while searching -- matching how a file
            # explorer's own search collapses date grouping into a plain
            # result list. This also sidesteps a real problem a grouped
            # search view would have: _rebuild() runs on every keystroke,
            # so any collapse state assigned to search-time groups would
            # be discarded and rebuilt fresh on the very next keystroke.
            for entry in entries:
                row = self._make_row(entry)
                self._insert(row)
                self._row_entries.append(("", row))
        else:
            current_bucket = None
            for entry in entries:
                bucket = _bucket_label(datetime.fromisoformat(entry.downloaded_at))
                if bucket != current_bucket:
                    header = _GroupHeader(bucket)
                    header.apply_theme(self._colors)
                    header.setExpanded(bucket not in self._collapsed, animate=False)
                    header.toggled.connect(
                        lambda expanded, b=bucket: self._on_group_toggled(b, expanded)
                    )
                    self._insert(header)
                    self._headers.append(header)
                    current_bucket = bucket

                row = self._make_row(entry)
                row.setVisible(bucket not in self._collapsed)
                self._insert(row)
                self._row_entries.append((bucket, row))

        self._empty_label.setVisible(not entries)
        count = len(entries)
        self._count_label.setText(f"{count} video" if count == 1 else f"{count} videos")

    def _on_group_toggled(self, bucket: str, expanded: bool) -> None:
        if expanded:
            self._collapsed.discard(bucket)
        else:
            self._collapsed.add(bucket)
        for row_bucket, row in self._row_entries:
            if row_bucket == bucket:
                row.setVisible(expanded)

    def _on_remove(self, video_id: str) -> None:
        self._store.remove(video_id)
        self._rebuild()
