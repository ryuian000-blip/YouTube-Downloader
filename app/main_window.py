"""The single main window. Everything the user interacts with lives here --
no secondary QDialogs beyond the folder picker (native) and the
already-downloaded confirmation (QMessageBox -- not pixel-native on
Windows, but it inherits the app's QSS like any other QWidget, and a
custom-built yes/no modal for one confirmation isn't worth the extra
surface area).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app import binaries, theme
from app.history import HistoryStore
from app.history_view import HistoryPage
from app.splash import SplashOverlay
from app.theme_manager import ThemeManager
from app.widgets import (
    AnimatedButton,
    AnimatedCheckBox,
    AnimatedComboBox,
    AnimatedProgressBar,
    AnimatedSegmentedControl,
    DestinationButton,
    IconButton,
    PosterThumbnail,
)
from app.workers import (
    MODE_AUDIO_ONLY,
    MODE_VIDEO,
    MODE_VIDEO_ONLY,
    DownloadOptions,
    DownloadWorker,
    FetchWorker,
    VideoInfo,
    estimate_download_size,
    format_duration,
    format_filesize,
    predict_output_path,
    selected_height,
)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def _set_role(widget: QWidget, value: str, prop: str = "role") -> None:
    """Set a QSS dynamic property and force a style repolish, since Qt
    doesn't repaint property-selector styles automatically after the
    property changes at runtime."""
    widget.setProperty(prop, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _card() -> QFrame:
    frame = QFrame()
    frame.setProperty("card", "true")
    return frame


class MainWindow(QMainWindow):
    # The thumbnail is no longer a fixed-size inline element: it is a
    # full-bleed 16:9 band whose pixel size is derived from the card's
    # current width every time that width changes (_rescale_poster_thumb).

    def __init__(self, theme_manager: ThemeManager) -> None:
        super().__init__()
        self._theme_manager = theme_manager
        self.setWindowTitle("YouTube Downloader")
        icon_path = ASSETS_DIR / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.setMinimumSize(480, 420)
        self.resize(560, 640)

        self._binary_status = binaries.detect()
        self._output_dir = Path.home() / "Downloads"
        self._video_info: VideoInfo | None = None
        self._last_download_options: DownloadOptions | None = None
        self._fetch_worker: FetchWorker | None = None
        self._download_worker: DownloadWorker | None = None
        # Explicit flags rather than checking _fetch_worker/_download_worker
        # for None or QThread.isRunning(): a worker's finished signal (and
        # therefore the moment its Python reference gets cleared) is only
        # ever delivered once the Qt event loop actually runs, but the
        # underlying OS thread can finish -- and isRunning() can already
        # read False -- slightly before that delivery happens. A flag set
        # synchronously the instant a worker starts, and cleared
        # synchronously in the same handler that clears the reference,
        # has no such gap to race against.
        self._fetch_in_progress = False
        self._download_in_progress = False
        self._reveal_animations: list[QPropertyAnimation] = []
        self._splash_overlay: SplashOverlay | None = None
        # Every AnimatedButton / AnimatedRadioButton / AnimatedCheckBox
        # instance -- these paint themselves entirely (see app/widgets.py),
        # so they need their colors pushed to them explicitly, rather than
        # picking colors up from QSS.
        self._animated_widgets: list = []

        self._build_ui()
        self._wire_signals()
        self._apply_binary_warning()
        self._on_mode_changed()
        self._apply_control_theme()

    # ------------------------------------------------------------------
    # Startup splash (in-window overlay, not a separate top-level window --
    # see app/splash.py for why)
    # ------------------------------------------------------------------

    def play_intro(self) -> None:
        """Call once, after this window is already showing (see main.py),
        so the OS title bar and the real content underneath are both in
        place before the logo fade starts."""
        if self._splash_overlay is not None:
            return
        overlay = SplashOverlay(ASSETS_DIR, self._theme_manager, self.centralWidget())
        overlay.setGeometry(self.centralWidget().rect())
        overlay.finished.connect(self._on_intro_finished)
        self._splash_overlay = overlay
        overlay.play()

    def _on_intro_finished(self) -> None:
        self._splash_overlay = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._splash_overlay is not None:
            self._splash_overlay.setGeometry(self.centralWidget().rect())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # QStackedWidget, not a dropdown/popover or a second OS window: the
        # history page needs real vertical space for a scrollable,
        # searchable, thumbnail-bearing list, which a dropdown can't give
        # it in a window this compact -- and a separate window would fight
        # the "redownload takes you back to the normal window" flow, which
        # is naturally just a page swap within a single window.
        self._stack = QStackedWidget()
        self._stack.setObjectName("centralWidget")
        self.setCentralWidget(self._stack)

        # The poster layout needs roughly 850px of height once everything is
        # revealed, which is taller than the usable area on a 768px-tall
        # laptop screen. A scroll area means that case degrades to a
        # scrollbar instead of Qt compressing cards below their minimum size
        # and clipping their contents.
        self._page_scroll = QScrollArea()
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setFrameShape(QFrame.NoFrame)
        self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._page_content = QWidget()
        self._outer_layout = outer = QVBoxLayout(self._page_content)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG
        )
        outer.setSpacing(theme.SPACE_MD)

        # Spacer that gets animated to zero on first successful fetch,
        # which is what produces the "docks to the top" motion. It sits
        # ABOVE the URL row on purpose: that is what leaves the URL field
        # sitting in the middle of an otherwise empty window before the
        # first fetch, then lifts it to the top as the rest appears.
        # Given an explicit stretch factor (matching the trailing
        # addStretch below) so the two split any pre-fetch leftover height
        # evenly, the way two plain Expanding widgets used to.
        self._top_spacer = QWidget()
        self._top_spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        outer.addWidget(self._top_spacer, 1)

        outer.addLayout(self._build_url_row())
        outer.addWidget(self._status_label)
        outer.addWidget(self._build_media_card())
        outer.addWidget(self._build_controls_card())
        outer.addLayout(self._build_destination_row())

        # A trailing stretch, not a second collapsible spacer widget: it
        # needs no docking animation (nothing sits below it to dock
        # toward), and being the layout's very last item means it can
        # never cause the "double gap" problem retiring _top_spacer
        # avoids (see _retire_spacer) -- there's nothing after it for a
        # second spacing gap to stack against. Leaving it permanently in
        # place also matters functionally, not just cosmetically: once
        # nothing was left to absorb the window's leftover height after
        # _top_spacer retired, Qt didn't just leave that height unused --
        # it grew the next Preferred-policy widget it could (the progress
        # status label) to fill it, which read as a mysteriously oversized
        # gap between the progress bar and the Download button rather
        # than where it belongs, trailing after the button.
        outer.addStretch(1)

        self._page_scroll.setWidget(self._page_content)

        # Hidden until a successful fetch.
        for widget in self._revealable():
            widget.setVisible(False)

        self._history = HistoryStore()
        self._history_page = HistoryPage(self._history, self._theme_manager.colors(), self)
        self._history_page.back_requested.connect(lambda: self._stack.setCurrentIndex(0))
        self._history_page.redownload_requested.connect(self._on_history_redownload)

        self._stack.addWidget(self._page_scroll)   # index 0
        self._stack.addWidget(self._history_page)  # index 1
        self._stack.setCurrentIndex(0)

    def _revealable(self) -> list[QWidget]:
        """Everything that stays hidden until the first successful fetch.

        The progress bar and its status label are not listed: both now live
        inside the media card, so they're revealed and faded in as part of
        it. Listing them again here would attach a second
        QGraphicsOpacityEffect to widgets already inside a fading parent.
        """
        return [
            self._media_card,
            self._controls_card,
            self._dest_widget,
        ]

    # -- top row: paste a link -------------------------------------------

    def _build_url_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_SM)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("Paste a YouTube link…")
        self._url_edit.setMinimumHeight(38)
        row.addWidget(self._url_edit, stretch=1)

        # Icon buttons rather than text: at this width a "Fetch Info" pill
        # plus a "History" pill together take about a third of the row away
        # from the field they sit next to. The fetch button carries the
        # loading state that used to be the button's own "Fetching…" label
        # (see IconButton.setBusy).
        self._fetch_btn = IconButton("arrow", primary=True, diameter=38)
        self._fetch_btn.setToolTip("Fetch video info")
        self._animated_widgets.append(self._fetch_btn)
        row.addWidget(self._fetch_btn)

        self._history_btn = IconButton("clock", diameter=38)
        self._history_btn.setToolTip("Download history")
        self._animated_widgets.append(self._history_btn)
        row.addWidget(self._history_btn)

        self._status_label = QLabel("")
        self._status_label.setProperty("role", "status")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        return row

    # -- media card: thumbnail band + title block, one rounded object ------

    def _build_media_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("mediaCard")
        self._media_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Full-bleed 16:9 band that keeps its own aspect ratio and re-renders
        # itself whenever its width changes (see PosterThumbnail).
        self._thumbnail_label = PosterThumbnail(theme.RADIUS_CARD)
        self._thumbnail_label.setObjectName("posterThumb")
        layout.addWidget(self._thumbnail_label)

        # Directly beneath the artwork and spanning the card edge-to-edge,
        # so download progress reads as a scrubber belonging to this video
        # rather than a detached bar somewhere further down the window.
        # Squared off and slimmed for that reason (see setFlush) -- and it
        # sits mid-card, not at an edge, so it can be full-bleed without
        # fighting the card's rounded corners.
        self._progress_bar = AnimatedProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFlush(True)
        self._animated_widgets.append(self._progress_bar)
        layout.addWidget(self._progress_bar)

        meta = QWidget()
        meta.setObjectName("posterMeta")
        self._poster_meta = meta
        meta_layout = QVBoxLayout(meta)
        meta_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        meta_layout.setSpacing(theme.SPACE_SM)

        self._video_title_label = QLabel("")
        self._video_title_label.setProperty("role", "posterTitle")
        self._video_title_label.setWordWrap(True)
        meta_layout.addWidget(self._video_title_label)

        self._chip_row = QHBoxLayout()
        self._chip_row.setSpacing(theme.SPACE_XS + 2)
        self._chip_row.addStretch(1)
        meta_layout.addLayout(self._chip_row)

        # Download status lives in the card too, right under the bar it
        # describes. Hidden while empty so it doesn't reserve a blank line
        # in the card before a download has ever run.
        self._progress_status_label = QLabel("")
        self._progress_status_label.setProperty("role", "status")
        self._progress_status_label.setWordWrap(True)
        self._progress_status_label.setVisible(False)
        meta_layout.addWidget(self._progress_status_label)

        layout.addWidget(meta)
        return card

    # -- controls ---------------------------------------------------------

    def _build_controls_card(self) -> QFrame:
        card = _card()
        self._controls_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        layout.setSpacing(theme.SPACE_MD)

        # One segmented control instead of three radios: the three modes are
        # mutually exclusive and short-labelled, which is exactly what a
        # segmented control is for, and it collapses three stacked rows into
        # one -- the single biggest space saving in this layout.
        self._mode_control = AnimatedSegmentedControl(
            ["Video + sound", "Video only", "Audio only"]
        )
        self._animated_widgets.append(self._mode_control)
        layout.addWidget(self._mode_control)

        quality_row = QHBoxLayout()
        quality_row.setSpacing(theme.SPACE_MD)

        quality_col = QVBoxLayout()
        quality_col.setSpacing(theme.SPACE_XS)
        quality_label = QLabel("QUALITY")
        quality_label.setProperty("role", "sectionLabel")
        quality_col.addWidget(quality_label)
        self._quality_combo = AnimatedComboBox()
        self._animated_widgets.append(self._quality_combo)
        quality_col.addWidget(self._quality_combo)
        quality_row.addLayout(quality_col, stretch=1)

        audio_col = QVBoxLayout()
        audio_col.setSpacing(theme.SPACE_XS)
        audio_label = QLabel("AUDIO FORMAT")
        audio_label.setProperty("role", "sectionLabel")
        audio_col.addWidget(audio_label)
        self._audio_format_combo = AnimatedComboBox()
        self._audio_format_combo.addItems(["MP3", "M4A", "WAV"])
        self._animated_widgets.append(self._audio_format_combo)
        audio_col.addWidget(self._audio_format_combo)
        quality_row.addLayout(audio_col, stretch=1)

        layout.addLayout(quality_row)

        extras_row = QHBoxLayout()
        extras_row.setSpacing(theme.SPACE_LG)
        self._subtitles_checkbox = AnimatedCheckBox("Subtitles")
        self._subtitles_checkbox.setToolTip("Include English subtitles, if the video has them")
        self._thumbnail_checkbox = AnimatedCheckBox("Embed thumbnail")
        self._thumbnail_checkbox.setToolTip("Embed the thumbnail into the file as cover art")
        self._animated_widgets.append(self._subtitles_checkbox)
        self._animated_widgets.append(self._thumbnail_checkbox)
        extras_row.addWidget(self._subtitles_checkbox)
        extras_row.addWidget(self._thumbnail_checkbox)
        extras_row.addStretch(1)
        layout.addLayout(extras_row)

        return card

    # -- destination: one line, not a card --------------------------------

    def _build_destination_row(self) -> QHBoxLayout:
        # Destination and Download share one row: the path is now itself
        # the control that changes it (DestinationButton), which is what
        # freed the slot the old "Change" button occupied for the primary
        # action. Progress moved up into the media card, so this row is the
        # last thing in the window.
        self._dest_widget = QWidget()
        row = QHBoxLayout(self._dest_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE_SM)

        self._dest_button = DestinationButton()
        self._dest_button.setPath(str(self._output_dir))
        self._animated_widgets.append(self._dest_button)
        row.addWidget(self._dest_button, stretch=1)

        # Smaller than the old full-width pill, so it leans harder on the
        # accent fill and generous padding to stay the obvious primary
        # action next to a control of similar height.
        self._download_btn = AnimatedButton("Download", primary=True)
        self._download_btn.setObjectName("downloadButton")
        self._download_btn.setBorderRadius(20)
        self._download_btn.setFixedHeight(40)
        self._download_btn.setMinimumWidth(150)
        self._animated_widgets.append(self._download_btn)
        row.addWidget(self._download_btn)

        wrapper = QHBoxLayout()
        wrapper.addWidget(self._dest_widget)
        return wrapper

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self._fetch_btn.clicked.connect(self._on_fetch_clicked)
        self._url_edit.returnPressed.connect(self._on_fetch_clicked)
        self._dest_button.clicked.connect(self._on_change_destination)
        self._download_btn.clicked.connect(self._on_download_clicked)
        self._mode_control.selectionChanged.connect(lambda *_: self._on_mode_changed())
        self._history_btn.clicked.connect(self._on_show_history)
        # The resolution and size chips describe the *selected* quality, so
        # they have to be recomputed whenever that selection moves.
        self._quality_combo.currentIndexChanged.connect(lambda *_: self._refresh_chips())

    def _apply_control_theme(self) -> None:
        c = self._theme_manager.colors()
        for widget in self._animated_widgets:
            widget.apply_theme(c)

    def _apply_binary_warning(self) -> None:
        missing = self._binary_status.missing
        if not missing:
            return
        self._status_label.setText(
            "Heads up: " + ", ".join(missing) + " not found next to the app. "
            "Some downloads may fail until they're added."
        )
        _set_role(self._status_label, "statusWarning")
        self._status_label.setVisible(True)

    # ------------------------------------------------------------------
    # Mode / quality interplay
    # ------------------------------------------------------------------

    # Segment order must match the labels passed to AnimatedSegmentedControl.
    _MODES = (MODE_VIDEO, MODE_VIDEO_ONLY, MODE_AUDIO_ONLY)

    def _current_mode(self) -> str:
        return self._MODES[self._mode_control.currentIndex()]

    def _on_mode_changed(self) -> None:
        is_audio_only = self._current_mode() == MODE_AUDIO_ONLY
        self._quality_combo.setEnabled(not is_audio_only)
        self._audio_format_combo.setEnabled(is_audio_only)
        # Switching between video and audio changes what would actually be
        # downloaded, so the size chip has to follow.
        self._refresh_chips()

    # ------------------------------------------------------------------
    # Info chips (duration / resolution / size)
    # ------------------------------------------------------------------

    def _clear_chips(self) -> None:
        while self._chip_row.count() > 1:  # keep the trailing stretch
            item = self._chip_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_chips(self) -> None:
        """Rebuild the facts shown under the title. Resolution and size
        describe the *currently selected* quality rather than the video's
        maximum, so they stay truthful as the dropdown changes."""
        self._clear_chips()
        info = self._video_info
        if info is None:
            return

        mode = self._current_mode()
        height = self._quality_combo.currentData()
        values: list[str] = []

        duration = format_duration(info.raw)
        if duration:
            values.append(duration)

        if mode != MODE_AUDIO_ONLY:
            resolved = selected_height(info.raw, height)
            if resolved:
                values.append(f"{resolved}p")
        else:
            values.append(self._audio_format_combo.currentText())

        size = format_filesize(estimate_download_size(info.raw, mode, height))
        if size:
            # "~" because this is assembled from per-format estimates and the
            # muxed result is never exactly the sum of its parts.
            values.append(f"~{size}")

        for index, text in enumerate(values):
            chip = QLabel(text)
            chip.setProperty("role", "chip")
            self._chip_row.insertWidget(index, chip)

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _on_fetch_clicked(self) -> None:
        # Guards against a real reentrancy bug, not a hypothetical one:
        # _fetch_btn.setEnabled(False) below blocks a second *click*, but
        # this is also wired to _url_edit.returnPressed, and disabling
        # the button doesn't disable the line edit -- mashing Enter while
        # a fetch is already running spun up a second concurrent
        # FetchWorker, each racing to set _video_info out from under the
        # other.
        if self._fetch_in_progress:
            return

        url = self._url_edit.text().strip()
        if not url:
            self._show_hero_status("Paste a YouTube link first.", "statusError")
            return

        self._fetch_in_progress = True
        self._fetch_btn.setEnabled(False)
        self._fetch_btn.setBusy(True)
        self._show_hero_status("Fetching video info…", "status")

        self._fetch_worker = FetchWorker(url, self)
        self._fetch_worker.succeeded.connect(self._on_fetch_succeeded)
        self._fetch_worker.failed.connect(self._on_fetch_failed)
        self._fetch_worker.finished.connect(self._reset_fetch_button)
        self._fetch_worker.start()

    def _reset_fetch_button(self) -> None:
        self._fetch_in_progress = False
        self._fetch_btn.setBusy(False)
        self._fetch_btn.setEnabled(True)

    def _show_hero_status(self, text: str, role: str) -> None:
        self._status_label.setText(text)
        _set_role(self._status_label, role)
        self._status_label.setVisible(bool(text))

    def _on_fetch_failed(self, message: str) -> None:
        self._show_hero_status(message, "statusError")

    def _on_fetch_succeeded(self, info: VideoInfo) -> None:
        self._video_info = info
        self._video_title_label.setText(info.title)
        self._show_hero_status("", "status")
        self._rescale_poster_thumb()

        self._quality_combo.blockSignals(True)
        self._quality_combo.clear()
        self._quality_combo.addItem("Best available", None)
        for h in info.heights:
            self._quality_combo.addItem(f"{h}p", h)
        self._quality_combo.setCurrentIndex(0)
        self._quality_combo.blockSignals(False)

        self._refresh_chips()
        self._reveal_rest_of_ui()

    # ------------------------------------------------------------------
    # Poster thumbnail
    # ------------------------------------------------------------------

    def _rescale_poster_thumb(self) -> None:
        """Hand the fetched artwork to the band, which handles its own
        scaling, aspect ratio and corner rounding from there."""
        info = self._video_info
        self._thumbnail_label.setImage(info.thumbnail if info else None)

    # ------------------------------------------------------------------
    # Reveal animation
    # ------------------------------------------------------------------

    def _retire_spacer(self, spacer: QWidget) -> None:
        """Remove a fully-collapsed docking spacer from the layout for
        good -- see the comment in _reveal_rest_of_ui for why leaving it
        in place (even at zero height) still leaves a gap."""
        self._outer_layout.removeWidget(spacer)
        spacer.setVisible(False)
        self._outer_layout.invalidate()
        self._outer_layout.activate()

    def _reveal_rest_of_ui(self) -> None:
        if self._controls_card.isVisible():
            return  # already revealed, e.g. fetching a second link

        # 1) Collapse the top docking spacer from its current height to 0
        #    -- this is what "docks" the title/hero card up toward the
        #    window top. Once fully collapsed, retire it from the layout
        #    entirely (see _retire_spacer): a QVBoxLayout's own uniform
        #    inter-item spacing still reserves a full gap on *both* sides
        #    of a zero-height widget, so leaving it in place stacks two
        #    spacing gaps into one oversized empty band around where it
        #    used to be. The trailing addStretch below the progress
        #    section needs none of this -- see the comment where it's
        #    added in _build_ui.
        current_height = self._top_spacer.height()
        if current_height > 0:
            dock_anim = QPropertyAnimation(self._top_spacer, b"maximumHeight", self)
            dock_anim.setDuration(400)
            dock_anim.setStartValue(current_height)
            dock_anim.setEndValue(0)
            dock_anim.setEasingCurve(QEasingCurve.OutCubic)
            dock_anim.finished.connect(lambda: self._retire_spacer(self._top_spacer))
            dock_anim.start()
            self._reveal_animations.append(dock_anim)
        else:
            self._retire_spacer(self._top_spacer)

        # 2) Reveal the rest of the UI with a fade-in.
        for widget in self._revealable():
            widget.setVisible(True)

        # Force the *central widget's* outer layout (not self.layout(),
        # which on a QMainWindow is the internal QMainWindowLayout and
        # doesn't touch the central widget's own QVBoxLayout) to re-run
        # now that these cards have real content and a real sizeHint --
        # otherwise the newly-shown cards keep whatever cramped geometry
        # they had while hidden, and their children overlap the row below.
        self._outer_layout.invalidate()
        self._outer_layout.activate()

        # The window was sized to fit only the centered hero card. Now
        # that four more sections have real content, the window itself
        # needs to grow, or the layout is forced to compress cards below
        # their own minimum size -- this is what actually caused
        # overlapping rows before, and (via the same mechanism, just on
        # the horizontal axis) is also what let the MODE row's radio
        # labels get clipped: only height was grown here, never width, so
        # on a machine where the real Segoe UI font renders those labels
        # a bit wider than expected, the window stayed too narrow and the
        # layout had no choice but to compress the radios below the width
        # their text needs. Grow both, each capped so it never overruns
        # the screen.
        # sizeHint() is taken from the scrolled *content* widget, not from
        # the window: a QScrollArea reports only the size it would like its
        # viewport to be, so asking the window would size it to the
        # scrollbar rather than to the content and leave everything
        # permanently scrolled.
        needed = self._page_content.sizeHint()
        chrome_h = self.height() - self._page_scroll.viewport().height()
        chrome_w = self.width() - self._page_scroll.viewport().width()
        screen = self.screen()
        cap_h = int(screen.availableGeometry().height() * 0.9) if screen else 900
        cap_w = int(screen.availableGeometry().width() * 0.9) if screen else 1200
        target_height = min(needed.height() + chrome_h, cap_h)
        # Width grows only to what the content genuinely *requires*, not to
        # what it would prefer. Height has no such choice -- nothing here can
        # shorten itself, so the window must fit it or scroll. Width is
        # different now that the segmented control and the save path both
        # elide: chasing the preferred width would let a machine whose font
        # renders the mode labels wider than expected balloon the window far
        # past the size this layout was composed at.
        target_width = min(
            self._page_content.minimumSizeHint().width() + chrome_w, cap_w
        )
        new_width = max(self.width(), target_width)
        new_height = max(self.height(), target_height)
        if new_width != self.width() or new_height != self.height():
            self.resize(new_width, new_height)

        for widget in self._revealable():
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(450)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)

            def _cleanup(widget=widget, anim=anim):
                widget.setGraphicsEffect(None)
                if anim in self._reveal_animations:
                    self._reveal_animations.remove(anim)

            anim.finished.connect(_cleanup)
            anim.start()
            self._reveal_animations.append(anim)

    # ------------------------------------------------------------------
    # Destination
    # ------------------------------------------------------------------

    def _on_change_destination(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a folder", str(self._output_dir)
        )
        if chosen:
            self._output_dir = Path(chosen)
            self._dest_button.setPath(str(self._output_dir))

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _already_downloaded_path(self) -> Path | None:
        """None if nothing needs a confirmation; otherwise the file that
        would get silently skipped (see workers.predict_output_path)."""
        if self._video_info is None:
            return None
        try:
            predicted = predict_output_path(
                self._video_info.raw,
                self._output_dir,
                self._current_mode(),
                self._audio_format_combo.currentText().lower(),
            )
        except Exception:
            # A failed *prediction* should never block an actual download
            # -- worst case, yt-dlp's own after-the-fact check in
            # DownloadWorker still catches a real skip.
            return None
        return predicted if predicted.exists() else None

    def _on_download_clicked(self) -> None:
        if self._video_info is None or self._download_in_progress:
            return

        force_overwrite = False
        existing = self._already_downloaded_path()
        if existing is not None:
            # Built manually rather than via the QMessageBox.question()
            # convenience method, specifically to drop the icon: that
            # icon is a platform-drawn pixmap (a generic blue "?" on
            # Windows) that QSS can't recolor, so next to the rest of
            # this app's on-brand dark/sage styling it just looked like a
            # stray system dialog rather than part of the app. Text alone
            # is enough to ask a short yes/no question.
            box = QMessageBox(self)
            box.setWindowTitle("Already downloaded")
            box.setText(f'You already downloaded "{self._video_info.title}". Download it again?')
            box.setIcon(QMessageBox.NoIcon)
            yes_button = box.addButton("Yes", QMessageBox.YesRole)
            no_button = box.addButton("No", QMessageBox.NoRole)
            # Enter defaults to *not* re-downloading/overwriting -- the
            # safer of the two outcomes if someone just reflexively
            # hits Enter.
            box.setDefaultButton(no_button)
            box.exec()
            if box.clickedButton() is not yes_button:
                return
            force_overwrite = True

        height = self._quality_combo.currentData()
        options = DownloadOptions(
            url=self._url_edit.text().strip(),
            mode=self._current_mode(),
            height=height,
            audio_format=self._audio_format_combo.currentText().lower(),
            include_subtitles=self._subtitles_checkbox.isChecked(),
            embed_thumbnail=self._thumbnail_checkbox.isChecked(),
            output_dir=self._output_dir,
            ffmpeg_location=self._binary_status.ffmpeg_folder,
            js_runtime_path=(
                str(self._binary_status.js_runtime)
                if self._binary_status.js_runtime
                else None
            ),
            force_overwrite=force_overwrite,
        )
        # Recorded here, not re-read from the combo boxes when the download
        # finishes: nothing currently locks quality/mode while a download is
        # in flight, so re-reading them afterward could describe a
        # selection the user changed mid-download rather than what was
        # actually saved.
        self._last_download_options = options

        self._download_in_progress = True
        self._download_btn.setEnabled(False)
        self._download_btn.setText("Downloading…")
        self._progress_bar.setValue(0)
        self._show_progress_status("Starting…", "status")

        self._download_worker = DownloadWorker(options, self)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.succeeded.connect(self._on_download_succeeded)
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.finished.connect(self._reset_download_button)
        self._download_worker.start()

    def _reset_download_button(self) -> None:
        self._download_in_progress = False
        self._download_btn.setEnabled(True)
        self._download_btn.setText("Download")
        # See the matching comment in _reset_fetch_button -- connected
        # ahead of finished->deleteLater below, so this always clears the
        # reference before the underlying QThread is actually deleted.
        self._download_worker = None

    def _show_progress_status(self, text: str, role: str) -> None:
        """The status line sits inside the media card now, so it hides
        itself when empty rather than reserving a blank row in the card."""
        self._progress_status_label.setText(text)
        _set_role(self._progress_status_label, role)
        self._progress_status_label.setVisible(bool(text))

    def _on_download_progress(self, pct: float, text: str) -> None:
        self._progress_bar.setValue(int(pct))
        self._show_progress_status(text, "status")

    def _on_download_succeeded(self, message: str) -> None:
        self._progress_bar.setValue(100)
        # This message is also the DownloadWorker-side fallback for the
        # same "already downloaded" case _already_downloaded_path() checks
        # before starting -- if that offline prediction missed something
        # and yt-dlp skipped the file anyway, it shouldn't read as a
        # fresh, successful save (green) the way an actual download does.
        role = "statusWarning" if message.startswith("Already downloaded") else "statusSuccess"
        self._show_progress_status(message, role)
        self._record_history_entry()

    def _on_download_failed(self, message: str) -> None:
        self._show_progress_status(message, "statusError")

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _record_history_entry(self) -> None:
        if self._video_info is None:
            return
        raw = self._video_info.raw
        video_id = raw.get("id")
        if not video_id:
            return
        # webpage_url, not the raw text the user pasted -- yt-dlp already
        # normalizes it to one canonical form (e.g. youtu.be/<id> and
        # youtube.com/watch?v=<id> both resolve to the same webpage_url),
        # which is what keeps re-downloading a video from a differently
        # formatted link updating the same history entry instead of
        # creating a duplicate.
        url = raw.get("webpage_url") or self._url_edit.text().strip()

        opts = self._last_download_options
        mode = opts.mode if opts else self._current_mode()
        height = opts.height if opts else self._quality_combo.currentData()

        self._history.add_or_update(
            video_id=str(video_id),
            title=self._video_info.title,
            url=url,
            thumbnail=self._video_info.thumbnail,
            duration=format_duration(raw),
            size_bytes=estimate_download_size(raw, mode, height),
        )

    def _on_show_history(self) -> None:
        self._history_page.reload()
        self._stack.setCurrentIndex(1)

    def _on_history_redownload(self, url: str) -> None:
        self._stack.setCurrentIndex(0)
        self._url_edit.setText(url)
        self._on_fetch_clicked()
