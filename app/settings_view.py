"""The settings page: the third page of MainWindow's QStackedWidget.

A page rather than a modal dialog, matching the History page and the
app's no-secondary-windows rule -- the same reason History isn't a popup.

These are the SHARED settings (ytdl_engine.config), so anything changed
here also governs what Claude Code does through the MCP server, and vice
versa. That's the point: a user shouldn't need a terminal to control what
an assistant does on their machine.

Changes save as they're made -- no Save button to forget. Text/number
fields commit on editingFinished (so a half-typed number is never
written); everything else commits on change.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import theme
from app.theme_manager import ColorTokens
from app.widgets import (
    AnimatedButton,
    AnimatedCheckBox,
    AnimatedComboBox,
    DestinationButton,
    ElidedLabel,
    IconButton,
    set_role,
)
from ytdl_engine.config import Settings, load_settings, settings_path, update_settings
from ytdl_engine.download import cache_root, cache_size_bytes, clear_cache

# (label, value) -- None means "no cap", which the engine treats as best
# available. Shown as words, never as a blank or "None".
QUALITY_CHOICES = [
    ("Best available", None),
    ("2160p", 2160),
    ("1440p", 1440),
    ("1080p", 1080),
    ("720p", 720),
    ("480p", 480),
    ("360p", 360),
]

OFF_CHOICES_MINUTES = [
    ("No limit", None), ("15 minutes", 15), ("30 minutes", 30),
    ("1 hour", 60), ("2 hours", 120), ("4 hours", 240),
]

OFF_CHOICES_MB = [
    ("No limit", None), ("100 MB", 100), ("500 MB", 500),
    ("1 GB", 1024), ("2 GB", 2048), ("5 GB", 5120),
]

# Deliberately terse. A combo box sizes itself to its longest item, so
# an explanatory sentence inside one silently sets the minimum width of
# the whole page -- these two lists were forcing 400px+ each. The
# explanation belongs in the hint text above the control, where it can
# wrap.
WHISPER_CHOICES = [
    ("Tiny", "tiny"),
    ("Base", "base"),
    ("Small", "small"),
    ("Medium", "medium"),
]

CACHE_CHOICES = [
    ("Never clean up", None), ("1 GB", 1000), ("2 GB", 2000),
    ("5 GB", 5000), ("10 GB", 10000),
]

FRAME_QUALITY_CHOICES = [("360p", 360), ("480p", 480), ("720p", 720), ("1080p", 1080)]


def _section(title: str) -> QLabel:
    label = QLabel(title)
    set_role(label, "sectionHeader")
    # Wrappable so a heading can never set the page's minimum width.
    label.setWordWrap(True)
    return label


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    set_role(label, "status")
    label.setWordWrap(True)
    return label


class _Row(QWidget):
    """One labelled control: caption on the left, control on the right."""

    def __init__(self, label: str, control: QWidget, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE_SM)
        caption = QLabel(label)
        # Word-wrapped so the caption can SHRINK. A plain QLabel reports
        # its full text width as its minimum, and with the scroll area's
        # horizontal bar off (this app elides rather than scrolls
        # sideways) that hard floor clipped the controls off the right
        # edge instead of letting the row narrow -- measured at 83px of
        # overflow before this. Wrapping drops the minimum to the longest
        # single word; these captions only ever wrap in a very narrow
        # window.
        caption.setWordWrap(True)
        row.addWidget(caption, stretch=1)
        row.addWidget(control, stretch=0)


class SettingsPage(QWidget):
    back_requested = Signal()
    # Emitted when the shared download folder changes here, so the main
    # page's destination button can follow without either page reaching
    # into the other.
    download_dir_changed = Signal(str)

    def __init__(self, colors: ColorTokens, parent=None) -> None:
        super().__init__(parent)
        self._colors = colors
        self._loading = False   # suppresses saves while populating controls
        self._combos: list[AnimatedComboBox] = []

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
        title = QLabel("Settings")
        set_role(title, "title")
        top_row.addWidget(title)
        top_row.addStretch(1)
        outer.addLayout(top_row)

        # Scrolled, like the History page: the window sizes itself around
        # the download page, and a tall settings list must not be able to
        # force the whole window taller.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(theme.SPACE_MD)

        self._build_downloads_card()
        self._build_ai_card()
        self._build_transcripts_card()
        self._build_storage_card()
        self._build_footer()

        self._body.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        self.reload()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _card(self) -> QVBoxLayout:
        frame = QFrame()
        frame.setProperty("card", "true")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        layout.setSpacing(theme.SPACE_SM)
        self._body.addWidget(frame)
        return layout

    def _combo(self, choices: list[tuple[str, object]], on_change) -> AnimatedComboBox:
        combo = AnimatedComboBox()
        combo.apply_theme(self._colors)
        for label, value in choices:
            combo.addItem(label, value)
        combo.setMinimumWidth(190)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo, f=on_change: self._on_combo(c, f)
        )
        self._combos.append(combo)
        return combo

    def _on_combo(self, combo: AnimatedComboBox, field: str) -> None:
        if self._loading:
            return
        self._save({field: combo.currentData()})

    def _build_downloads_card(self) -> None:
        card = self._card()
        card.addWidget(_section("Downloads"))
        card.addWidget(
            _hint(
                "Where videos are saved — used by this app and by anything "
                "driving it, so there's only ever one download folder."
            )
        )
        self._dest_button = DestinationButton()
        self._dest_button.apply_theme(self._colors)
        self._dest_button.clicked.connect(self._choose_folder)
        card.addWidget(self._dest_button)

    def _build_ai_card(self) -> None:
        card = self._card()
        card.addWidget(_section("When an assistant downloads"))
        card.addWidget(
            _hint(
                "Applies when Claude (or another AI) downloads without being "
                "told a specific quality. Your own downloads on the main "
                "screen always use the dropdown there.\n\n"
                "Frames are still images an assistant reads to see what is "
                "on screen; a higher quality helps it read small text."
            )
        )
        self._quality_combo = self._combo(QUALITY_CHOICES, "max_height")
        card.addWidget(_Row("Maximum quality", self._quality_combo))

        self._duration_combo = self._combo(OFF_CHOICES_MINUTES, "max_duration_minutes")
        card.addWidget(_Row("Skip videos longer than", self._duration_combo))

        self._filesize_combo = self._combo(OFF_CHOICES_MB, "max_filesize_mb")
        card.addWidget(_Row("Skip files bigger than", self._filesize_combo))

        self._frame_quality_combo = self._combo(
            FRAME_QUALITY_CHOICES, "frame_download_height"
        )
        card.addWidget(_Row("Quality for reading frames", self._frame_quality_combo))

    def _build_transcripts_card(self) -> None:
        card = self._card()
        card.addWidget(_section("Transcripts"))
        card.addWidget(
            _hint(
                "YouTube's own captions are used when a video has them — "
                "instant. Without captions the audio is transcribed on this "
                "computer instead, which takes a few minutes. A larger "
                "accuracy setting is slower but transcribes more reliably."
            )
        )
        # The caption lives in the _Row, not in the checkbox: a checkbox
        # reports its full label width as its minimum and cannot wrap, so
        # a sentence inside one set the minimum width of the entire page
        # (measured at 499px). As a labelled row the text wraps like every
        # other setting here, and the rows line up.
        self._whisper_checkbox = AnimatedCheckBox("")
        self._whisper_checkbox.apply_theme(self._colors)
        self._whisper_checkbox.toggled.connect(
            lambda checked: self._save({"allow_whisper": checked})
        )
        card.addWidget(
            _Row("Transcribe when captions are missing", self._whisper_checkbox)
        )

        self._whisper_combo = self._combo(WHISPER_CHOICES, "whisper_model")
        card.addWidget(_Row("Accuracy", self._whisper_combo))

    def _build_storage_card(self) -> None:
        card = self._card()
        card.addWidget(_section("Working files"))
        card.addWidget(
            _hint(
                "Videos fetched only to read or look at are kept temporarily "
                "so repeat questions are fast. The oldest are removed "
                "automatically past this size."
            )
        )
        self._cache_combo = self._combo(CACHE_CHOICES, "cache_max_mb")
        card.addWidget(_Row("Keep at most", self._cache_combo))

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_SM)
        self._cache_label = QLabel("")
        set_role(self._cache_label, "status")
        row.addWidget(self._cache_label)
        row.addStretch(1)
        self._clear_button = AnimatedButton("Clear now")
        self._clear_button.apply_theme(self._colors)
        self._clear_button.setFixedHeight(34)
        self._clear_button.clicked.connect(self._clear_cache)
        row.addWidget(self._clear_button)
        card.addLayout(row)

    def _build_footer(self) -> None:
        row = QHBoxLayout()
        # Elided, not wrapped: a file path is a single unbreakable token,
        # so word wrap can't shrink it and it set the whole page's minimum
        # width (measured: 324px of it). Given an explicit stretch here it
        # avoids ElidedLabel's documented collapse-to-zero case, which only
        # bites when several Ignored-policy siblings share a row with none.
        self._path_label = ElidedLabel("")
        set_role(self._path_label, "status")
        row.addWidget(self._path_label, stretch=1)
        reset = AnimatedButton("Reset to defaults")
        reset.apply_theme(self._colors)
        reset.setFixedHeight(34)
        reset.clicked.connect(self._reset)
        row.addWidget(reset)
        self._body.addLayout(row)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _select(self, combo: AnimatedComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def reload(self) -> None:
        """Re-read settings from disk. Called on every visit, so a change
        made through the CLI or by an assistant shows up here."""
        settings = load_settings()
        self._loading = True
        try:
            self._dest_button.setPath(str(settings.resolved_download_dir()))
            self._select(self._quality_combo, settings.max_height)
            self._select(self._duration_combo, settings.max_duration_minutes)
            self._select(self._filesize_combo, settings.max_filesize_mb)
            self._select(self._frame_quality_combo, settings.frame_download_height)
            self._whisper_checkbox.setChecked(settings.allow_whisper)
            self._select(self._whisper_combo, settings.whisper_model)
            self._select(self._cache_combo, settings.cache_max_mb)
        finally:
            self._loading = False
        self._whisper_combo.setEnabled(settings.allow_whisper)
        self._path_label.setText(f"Saved in {settings_path()}")
        self._refresh_cache_label()

    def _refresh_cache_label(self) -> None:
        try:
            size_mb = cache_size_bytes() / (1024 * 1024)
        except OSError:
            size_mb = 0.0
        self._cache_label.setText(f"Currently using {size_mb:.0f} MB")

    def _save(self, changes: dict) -> None:
        if self._loading:
            return
        try:
            update_settings(changes)
        except Exception:  # noqa: BLE001 -- a read-only disk shouldn't
            return         # take the window down; the control just won't stick.
        if "allow_whisper" in changes:
            self._whisper_combo.setEnabled(bool(changes["allow_whisper"]))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _choose_folder(self) -> None:
        current = load_settings().resolved_download_dir()
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a download folder", str(current)
        )
        if chosen:
            self._save({"download_dir": chosen})
            self._dest_button.setPath(chosen)
            self.download_dir_changed.emit(chosen)

    def _clear_cache(self) -> None:
        try:
            clear_cache()
        except OSError:
            pass
        self._refresh_cache_label()

    def _reset(self) -> None:
        target = settings_path()
        try:
            if target.exists():
                target.unlink()
        except OSError:
            return
        self.reload()
        self.download_dir_changed.emit(str(load_settings().resolved_download_dir()))

    def apply_theme(self, colors: ColorTokens) -> None:
        self._colors = colors
        for widget in self.findChildren(QWidget):
            if hasattr(widget, "apply_theme"):
                widget.apply_theme(colors)
