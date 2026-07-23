"""The single main window. Everything the user interacts with lives here --
no secondary QDialogs beyond the folder picker (native) and the
already-downloaded confirmation (QMessageBox -- not pixel-native on
Windows, but it inherits the app's QSS like any other QWidget, and a
custom-built yes/no modal for one confirmation isn't worth the extra
surface area).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app import binaries, theme
from app.splash import SplashOverlay
from app.theme_manager import ThemeManager
from app.widgets import (
    AnimatedButton,
    AnimatedCheckBox,
    AnimatedProgressBar,
    AnimatedRadioButton,
)
from app.workers import (
    MODE_AUDIO_ONLY,
    MODE_VIDEO,
    MODE_VIDEO_ONLY,
    DownloadOptions,
    DownloadWorker,
    FetchWorker,
    VideoInfo,
    predict_output_path,
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


def _fit_pixmap(pixmap: QPixmap, size: QSize) -> QPixmap:
    """Scale to fill ``size`` completely (may overshoot one dimension)
    then center-crop the overshoot away, instead of letterboxing -- a
    thumbnail crop reads as normal; a thumbnail with bars around it reads
    as broken."""
    scaled = pixmap.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - size.width()) // 2)
    y = max(0, (scaled.height() - size.height()) // 2)
    return scaled.copy(x, y, size.width(), size.height())


def _rounded_pixmap(pixmap: QPixmap, radius: int) -> QPixmap:
    """Clip to rounded corners so the thumbnail matches the rest of the
    app's rounded-corner language instead of sitting in a hard-edged box."""
    result = QPixmap(pixmap.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(pixmap.rect()), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result


class MainWindow(QMainWindow):
    # 16:9, the standard YouTube thumbnail aspect ratio -- large enough to
    # actually recognize the video at a glance, small enough to stay a
    # secondary element next to the title rather than dominating the card.
    THUMBNAIL_SIZE = QSize(120, 68)

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
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        self._outer_layout = outer = QVBoxLayout(central)
        outer.setContentsMargins(
            theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG
        )
        outer.setSpacing(theme.SPACE_LG)

        # Spacer that gets animated to zero on first successful fetch,
        # which is what produces the "docks to the top" motion. Given an
        # explicit stretch factor (matching the trailing addStretch below)
        # so the two split any pre-fetch leftover height evenly, the way
        # two plain Expanding widgets used to.
        self._top_spacer = QWidget()
        self._top_spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        outer.addWidget(self._top_spacer, 1)

        outer.addWidget(self._build_hero_card())
        outer.addWidget(self._build_options_card())
        outer.addWidget(self._build_destination_card())
        outer.addLayout(self._build_progress_section())

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

        # Hidden until a successful fetch.
        self._options_card.setVisible(False)
        self._destination_card.setVisible(False)
        self._progress_bar.setVisible(False)
        self._progress_status_label.setVisible(False)
        self._download_btn.setVisible(False)

    def _build_hero_card(self) -> QFrame:
        card = _card()
        self._hero_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        layout.setSpacing(theme.SPACE_SM)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_SM)
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("Paste a YouTube link…")
        self._url_edit.setMinimumHeight(36)
        row.addWidget(self._url_edit, stretch=1)

        self._fetch_btn = AnimatedButton("Fetch Info", primary=True)
        self._fetch_btn.setObjectName("fetchButton")
        self._fetch_btn.setBorderRadius(18)
        self._fetch_btn.setFixedHeight(36)
        self._animated_widgets.append(self._fetch_btn)
        row.addWidget(self._fetch_btn)
        layout.addLayout(row)

        # Thumbnail + title side by side, not title alone: the whole point
        # is letting someone glance at this and instantly recognize *that
        # specific video*, the way a title alone (especially a long or
        # generic one) doesn't reliably do.
        video_row = QHBoxLayout()
        video_row.setSpacing(theme.SPACE_SM)

        self._thumbnail_label = QLabel()
        self._thumbnail_label.setObjectName("thumbnailLabel")
        self._thumbnail_label.setFixedSize(self.THUMBNAIL_SIZE)
        self._thumbnail_label.setAlignment(Qt.AlignCenter)
        self._thumbnail_label.setVisible(False)
        video_row.addWidget(self._thumbnail_label)

        self._video_title_label = QLabel("")
        self._video_title_label.setProperty("role", "videoTitle")
        self._video_title_label.setWordWrap(True)
        self._video_title_label.setVisible(False)
        video_row.addWidget(self._video_title_label, stretch=1)

        layout.addLayout(video_row)

        self._status_label = QLabel("")
        self._status_label.setProperty("role", "status")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        return card

    def _build_options_card(self) -> QFrame:
        card = _card()
        self._options_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        layout.setSpacing(theme.SPACE_MD)

        mode_label = QLabel("MODE")
        mode_label.setProperty("role", "sectionLabel")
        layout.addWidget(mode_label)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(theme.SPACE_MD)
        self._mode_group = QButtonGroup(self)
        self._radio_video = AnimatedRadioButton("Video (with sound)")
        self._radio_video_only = AnimatedRadioButton("Video only (no sound)")
        self._radio_audio_only = AnimatedRadioButton("Audio only")
        self._radio_video.setChecked(True)
        for i, rb in enumerate(
            (self._radio_video, self._radio_video_only, self._radio_audio_only)
        ):
            self._mode_group.addButton(rb, i)
            self._animated_widgets.append(rb)
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        # A grid rather than a nested HBox-of-VBoxes: with two stacked
        # (label + combo) columns side by side, the nested-VBoxes-in-an-
        # HBox version under-reported its own height to the outer layout
        # (the combo boxes' allocated rects overran into the next row).
        # QGridLayout sizes each row from the tallest cell in it, which
        # sidesteps that entirely.
        quality_grid = QGridLayout()
        quality_grid.setHorizontalSpacing(theme.SPACE_MD)
        quality_grid.setVerticalSpacing(theme.SPACE_XS)
        quality_grid.setColumnStretch(0, 1)
        quality_grid.setColumnStretch(1, 1)

        quality_label = QLabel("VIDEO QUALITY")
        quality_label.setProperty("role", "sectionLabel")
        quality_grid.addWidget(quality_label, 0, 0)
        self._quality_combo = QComboBox()
        self._quality_combo.setMinimumHeight(36)
        quality_grid.addWidget(self._quality_combo, 1, 0)

        audio_label = QLabel("AUDIO FORMAT")
        audio_label.setProperty("role", "sectionLabel")
        quality_grid.addWidget(audio_label, 0, 1)
        self._audio_format_combo = QComboBox()
        self._audio_format_combo.setMinimumHeight(36)
        self._audio_format_combo.addItems(["MP3", "M4A", "WAV"])
        quality_grid.addWidget(self._audio_format_combo, 1, 1)

        layout.addLayout(quality_grid)

        extras_label = QLabel("EXTRAS")
        extras_label.setProperty("role", "sectionLabel")
        layout.addWidget(extras_label)

        extras_row = QHBoxLayout()
        extras_row.setSpacing(theme.SPACE_MD)
        self._subtitles_checkbox = AnimatedCheckBox("Include subtitles (if available)")
        self._thumbnail_checkbox = AnimatedCheckBox("Embed thumbnail as cover art")
        self._animated_widgets.append(self._subtitles_checkbox)
        self._animated_widgets.append(self._thumbnail_checkbox)
        extras_row.addWidget(self._subtitles_checkbox)
        extras_row.addWidget(self._thumbnail_checkbox)
        extras_row.addStretch(1)
        layout.addLayout(extras_row)

        return card

    def _build_destination_card(self) -> QFrame:
        card = _card()
        self._destination_card = card
        layout = QHBoxLayout(card)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        layout.setSpacing(theme.SPACE_MD)

        col = QVBoxLayout()
        col.setSpacing(theme.SPACE_XS)
        label = QLabel("SAVE TO")
        label.setProperty("role", "sectionLabel")
        col.addWidget(label)
        self._dest_path_label = QLabel(str(self._output_dir))
        self._dest_path_label.setProperty("role", "path")
        col.addWidget(self._dest_path_label)
        layout.addLayout(col, stretch=1)

        self._change_dest_btn = AnimatedButton("Change…")
        self._change_dest_btn.setFixedHeight(36)
        self._animated_widgets.append(self._change_dest_btn)
        layout.addWidget(self._change_dest_btn, alignment=Qt.AlignVCenter)

        return card

    def _build_progress_section(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(theme.SPACE_SM)

        self._progress_bar = AnimatedProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._animated_widgets.append(self._progress_bar)
        layout.addWidget(self._progress_bar)

        self._progress_status_label = QLabel("")
        self._progress_status_label.setProperty("role", "status")
        self._progress_status_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self._progress_status_label)

        self._download_btn = AnimatedButton("Download", primary=True)
        self._download_btn.setObjectName("downloadButton")
        self._download_btn.setBorderRadius(22)
        self._download_btn.setFixedHeight(44)
        self._animated_widgets.append(self._download_btn)
        layout.addWidget(self._download_btn)

        return layout

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self._fetch_btn.clicked.connect(self._on_fetch_clicked)
        self._url_edit.returnPressed.connect(self._on_fetch_clicked)
        self._change_dest_btn.clicked.connect(self._on_change_destination)
        self._download_btn.clicked.connect(self._on_download_clicked)
        self._mode_group.buttonToggled.connect(lambda *_: self._on_mode_changed())

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

    def _current_mode(self) -> str:
        if self._radio_video_only.isChecked():
            return MODE_VIDEO_ONLY
        if self._radio_audio_only.isChecked():
            return MODE_AUDIO_ONLY
        return MODE_VIDEO

    def _on_mode_changed(self) -> None:
        is_audio_only = self._current_mode() == MODE_AUDIO_ONLY
        self._quality_combo.setEnabled(not is_audio_only)
        self._audio_format_combo.setEnabled(is_audio_only)

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
        self._fetch_btn.setText("Fetching…")
        self._show_hero_status("Fetching video info…", "status")
        self._video_title_label.setVisible(False)
        self._thumbnail_label.setVisible(False)

        self._fetch_worker = FetchWorker(url, self)
        self._fetch_worker.succeeded.connect(self._on_fetch_succeeded)
        self._fetch_worker.failed.connect(self._on_fetch_failed)
        self._fetch_worker.finished.connect(self._reset_fetch_button)
        self._fetch_worker.start()

    def _reset_fetch_button(self) -> None:
        self._fetch_in_progress = False
        self._fetch_btn.setEnabled(True)
        self._fetch_btn.setText("Fetch Info")

    def _show_hero_status(self, text: str, role: str) -> None:
        self._status_label.setText(text)
        _set_role(self._status_label, role)
        self._status_label.setVisible(bool(text))

    def _on_fetch_failed(self, message: str) -> None:
        self._thumbnail_label.clear()
        self._thumbnail_label.setVisible(False)
        self._show_hero_status(message, "statusError")

    def _on_fetch_succeeded(self, info: VideoInfo) -> None:
        self._video_info = info
        self._video_title_label.setText(info.title)
        self._video_title_label.setVisible(True)
        self._show_hero_status("", "status")

        if info.thumbnail is not None and not info.thumbnail.isNull():
            pixmap = QPixmap.fromImage(info.thumbnail)
            pixmap = _fit_pixmap(pixmap, self.THUMBNAIL_SIZE)
            pixmap = _rounded_pixmap(pixmap, theme.RADIUS_CONTROL)
            self._thumbnail_label.setPixmap(pixmap)
            self._thumbnail_label.setVisible(True)
        else:
            # Never fatal -- a slow/unreachable thumbnail host shouldn't
            # block using the app, it just falls back to title-only, the
            # same as before this feature existed.
            self._thumbnail_label.clear()
            self._thumbnail_label.setVisible(False)

        self._quality_combo.clear()
        self._quality_combo.addItem("Best available", None)
        for h in info.heights:
            self._quality_combo.addItem(f"{h}p", h)
        self._quality_combo.setCurrentIndex(0)

        self._reveal_rest_of_ui()

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
        if self._options_card.isVisible():
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
        for widget in (
            self._options_card,
            self._destination_card,
            self._progress_bar,
            self._progress_status_label,
            self._download_btn,
        ):
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
        needed = self.sizeHint()
        screen = self.screen()
        cap_h = int(screen.availableGeometry().height() * 0.9) if screen else 900
        cap_w = int(screen.availableGeometry().width() * 0.9) if screen else 1200
        target_height = min(needed.height(), cap_h)
        target_width = min(needed.width(), cap_w)
        new_width = max(self.width(), target_width)
        new_height = max(self.height(), target_height)
        if new_width != self.width() or new_height != self.height():
            self.resize(new_width, new_height)

        for widget in (
            self._options_card,
            self._destination_card,
            self._progress_bar,
            self._progress_status_label,
            self._download_btn,
        ):
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
            self._dest_path_label.setText(str(self._output_dir))

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

        self._download_in_progress = True
        self._download_btn.setEnabled(False)
        self._download_btn.setText("Downloading…")
        self._progress_bar.setValue(0)
        _set_role(self._progress_status_label, "status")
        self._progress_status_label.setText("Starting…")

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

    def _on_download_progress(self, pct: float, text: str) -> None:
        self._progress_bar.setValue(int(pct))
        self._progress_status_label.setText(text)

    def _on_download_succeeded(self, message: str) -> None:
        self._progress_bar.setValue(100)
        self._progress_status_label.setText(message)
        # This message is also the DownloadWorker-side fallback for the
        # same "already downloaded" case _already_downloaded_path() checks
        # before starting -- if that offline prediction missed something
        # and yt-dlp skipped the file anyway, it shouldn't read as a
        # fresh, successful save (green) the way an actual download does.
        role = "statusWarning" if message.startswith("Already downloaded") else "statusSuccess"
        _set_role(self._progress_status_label, role)

    def _on_download_failed(self, message: str) -> None:
        self._progress_status_label.setText(message)
        _set_role(self._progress_status_label, "statusError")
