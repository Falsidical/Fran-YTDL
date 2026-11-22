"""Main application window: fetch formats, pick quality/account, manage the download queue."""
from __future__ import annotations

import copy
import itertools
import sys
from dataclasses import dataclass
from typing import Optional

import yt_dlp.version
from PySide6.QtCore import QThreadPool, Qt, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QKeySequence, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .. import __version__, config
from ..config import Account, Settings
from ..engine import JobSpec
from ..ffmpeg import find_ffmpeg
from ..formats import PRESETS, custom_format, describe_format, get_preset
from ..workers import DownloadWorker, ProbeWorker
from .accounts_dialog import AccountsDialog
from .settings_dialog import SettingsDialog

FORMAT_COLS = ["ID", "Type", "Ext", "Resolution", "FPS", "HDR", "Video codec", "Audio codec",
               "Bitrate (kbps)", "Size", "Note"]
QUEUE_COLS = ["Title", "Account", "Quality", "Status", "Progress", "Speed", "ETA"]


@dataclass
class Job:
    id: int
    spec: JobSpec
    title: str
    quality: str
    row: int
    worker: Optional[DownloadWorker] = None
    status: str = "queued"
    bar: Optional[QProgressBar] = None


def _dur(sec) -> str:
    if not sec:
        return ""
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings or config.load()
        self.setWindowTitle("yt-dlp GUI")
        self.resize(1100, 760)
        self.setAcceptDrops(True)

        self.probe_pool = QThreadPool(self)
        self.dl_pool = QThreadPool(self)
        self.dl_pool.setMaxThreadCount(self.settings.max_concurrent)
        self.jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._probe_worker = None
        self._info: Optional[dict] = None
        self._info_account: Optional[str] = None
        self.net = QNetworkAccessManager(self)

        self._build_menu()
        self._build_ui()
        self._refresh_accounts()
        self._check_ffmpeg()

    # ------------------------------------------------------------------ UI
    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("&Accounts…", self)
        a.triggered.connect(self.open_accounts)
        m.addAction(a)
        a = QAction("&Settings…", self)
        a.setShortcut(QKeySequence.Preferences)
        a.triggered.connect(self.open_settings)
        m.addAction(a)
        a = QAction("Open download &folder", self)
        a.triggered.connect(self.open_output_dir)
        m.addAction(a)
        m.addSeparator()
        a = QAction("&Quit", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)
        h = self.menuBar().addMenu("&Help")
        a = QAction("&About", self)
        a.triggered.connect(self._about)
        h.addAction(a)

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)

        # URL row
        self.url = QLineEdit()
        self.url.setPlaceholderText("Paste a video or playlist URL (several URLs separated by spaces are queued together)")
        self.url.returnPressed.connect(self.fetch)
        self.url.textChanged.connect(self._url_changed)
        self.fetch_btn = QPushButton("Fetch formats")
        self.fetch_btn.clicked.connect(self.fetch)
        self.dl_btn = QPushButton("Download")
        self.dl_btn.setDefault(True)
        self.dl_btn.clicked.connect(self.download_preset)
        row = QHBoxLayout()
        row.addWidget(self.url, 1)
        row.addWidget(self.fetch_btn)
        row.addWidget(self.dl_btn)
        root.addLayout(row)

        # Options row
        self.account = QComboBox()
        self.account.setMinimumWidth(200)
        self.account.setToolTip("Download as a signed-in user via browser cookies — unlocks premium, "
                                "members-only and age-restricted formats.")
        manage = QPushButton("Manage…")
        manage.clicked.connect(self.open_accounts)
        self.preset = QComboBox()
        for p in PRESETS:
            self.preset.addItem(p.label, p.key)
        self.preset.setCurrentIndex(max(0, self.preset.findData(self.settings.preset)))
        self.container = QComboBox()
        self.container.addItem("MKV (no re-encode, any codec)", "mkv")
        self.container.addItem("MP4 (best compatibility)", "mp4")
        self.container.addItem("WebM", "webm")
        self.container.setCurrentIndex(max(0, self.container.findData(self.settings.container)))
        self.playlist = QCheckBox("Whole playlist")
        self.playlist.setChecked(True)
        self.playlist.setToolTip("When the URL points at a video inside a playlist, download the entire playlist.")
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Account:"))
        opts.addWidget(self.account)
        opts.addWidget(manage)
        opts.addSpacing(16)
        opts.addWidget(QLabel("Quality:"))
        opts.addWidget(self.preset)
        opts.addWidget(QLabel("Container:"))
        opts.addWidget(self.container)
        opts.addWidget(self.playlist)
        opts.addStretch(1)
        root.addLayout(opts)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")
        root.addWidget(self.hint)

        # Info + formats
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        info_row = QHBoxLayout()
        self.thumb = QLabel()
        self.thumb.setFixedSize(192, 108)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background: palette(alternate-base); border-radius: 4px;")
        self.info = QLabel("Fetch a URL to see its available formats.")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        info_row.addWidget(self.thumb)
        info_row.addWidget(self.info, 1)
        tl.addLayout(info_row)

        self.formats = QTableWidget(0, len(FORMAT_COLS))
        self.formats.setHorizontalHeaderLabels(FORMAT_COLS)
        self.formats.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.formats.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.formats.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.formats.verticalHeader().hide()
        self.formats.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.formats.horizontalHeader().setStretchLastSection(True)
        self.formats.itemSelectionChanged.connect(self._format_selection_changed)
        self.formats.doubleClicked.connect(lambda _i: self.download_selected())
        tl.addWidget(self.formats, 1)
        fr = QHBoxLayout()
        self.sel_label = QLabel("Tip: select one video row and one audio row (Ctrl+click) to merge exact formats.")
        self.dl_sel_btn = QPushButton("Download selected formats")
        self.dl_sel_btn.setEnabled(False)
        self.dl_sel_btn.clicked.connect(self.download_selected)
        fr.addWidget(self.sel_label, 1)
        fr.addWidget(self.dl_sel_btn)
        tl.addLayout(fr)

        # Queue + log
        self.queue = QTableWidget(0, len(QUEUE_COLS))
        self.queue.setHorizontalHeaderLabels(QUEUE_COLS)
        self.queue.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.queue.verticalHeader().hide()
        hh = self.queue.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        self.queue.setColumnWidth(4, 160)
        self.queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._queue_menu)
        qw = QWidget()
        ql = QVBoxLayout(qw)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.addWidget(self.queue)
        qb = QHBoxLayout()
        for text, fn in (("Cancel selected", self.cancel_selected), ("Retry selected", self.retry_selected),
                         ("Clear finished", self.clear_finished), ("Open folder", self.open_output_dir)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            qb.addWidget(b)
        qb.addStretch(1)
        ql.addLayout(qb)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFont(QFont("monospace"))
        tabs = QTabWidget()
        tabs.addTab(qw, "Queue")
        tabs.addTab(self.log, "Log")
        self.tabs = tabs

        split = QSplitter(Qt.Vertical)
        split.addWidget(top)
        split.addWidget(tabs)
        split.setSizes([420, 300])
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"yt-dlp {yt_dlp.version.__version__}")

    # ------------------------------------------------------------ accounts
    def _refresh_accounts(self, keep: Optional[str] = None):
        keep = keep if keep is not None else self.account.currentData()
        self.account.blockSignals(True)
        self.account.clear()
        self.account.addItem("No account (anonymous)", "")
        for a in self.settings.accounts:
            self.account.addItem(f"{a.name}  [{a.browser}]", a.name)
        idx = self.account.findData(keep) if keep else -1
        if idx < 0 and self.settings.default_account:
            idx = self.account.findData(self.settings.default_account)
        self.account.setCurrentIndex(max(0, idx))
        self.account.blockSignals(False)
        self._update_hint()

    def current_account(self) -> Optional[Account]:
        name = self.account.currentData()
        acc = self.settings.account(name) if name else None
        return copy.deepcopy(acc) if acc else None

    def _url_changed(self, text: str):
        urls = text.split()
        if urls:
            acc = self.settings.account_for_url(urls[0])
            if acc:
                self.account.setCurrentIndex(max(0, self.account.findData(acc.name)))
        self._update_hint()

    def _update_hint(self):
        url = self.url.text().lower() if hasattr(self, "url") else ""
        acc = self.account.currentData()
        if not acc and ("youtube.com" in url or "youtu.be" in url):
            if self.settings.accounts:
                self.hint.setText("ℹ Anonymous YouTube downloads can miss premium/members-only formats. "
                                  "Pick an account to download as your signed-in self.")
            else:
                self.hint.setText("ℹ For maximum quality (Premium enhanced bitrate, members-only, age-restricted), "
                                  "add an account under Manage… — it uses your browser's login.")
        else:
            self.hint.setText("")
        self.hint.setVisible(bool(self.hint.text()))

    def open_accounts(self):
        dlg = AccountsDialog(self.settings.accounts, self)
        if dlg.exec():
            self.settings.accounts = dlg.accounts
            if not self.settings.account(self.settings.default_account):
                self.settings.default_account = ""
            config.save(self.settings)
            self._refresh_accounts()
            self._url_changed(self.url.text())

    def open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            new = dlg.result_settings()
            new.preset = self.preset.currentData()
            new.container = self.container.currentData()
            self.settings = new
            self.dl_pool.setMaxThreadCount(new.max_concurrent)
            config.save(self.settings)
            self._refresh_accounts()
            self._check_ffmpeg()

    def _check_ffmpeg(self):
        if not find_ffmpeg(self.settings.ffmpeg_path):
            self.statusBar().showMessage(
                "⚠ ffmpeg not found — the best video and audio streams can't be merged. "
                "Install ffmpeg or set its path in Settings.")

    # ------------------------------------------------------------- probing
    def fetch(self):
        urls = self.url.text().split()
        if not urls or self._probe_worker:
            return
        acc = self.current_account()
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching…")
        self.info.setText(f"Fetching information{' as ' + acc.name if acc else ''}…")
        self.formats.setRowCount(0)
        self.thumb.clear()
        w = ProbeWorker(copy.deepcopy(self.settings), urls[0], acc)
        w.setAutoDelete(False)
        w.signals.done.connect(self._probe_done)
        w.signals.failed.connect(self._probe_failed)
        w.signals.log.connect(lambda lvl, m: self._log(lvl, m))
        self._probe_worker = w
        self._info_account = acc.name if acc else None
        self.probe_pool.start(w)

    def _probe_end(self):
        self._probe_worker = None
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch formats")

    def _probe_failed(self, msg: str):
        self._probe_end()
        self._info = None
        self.info.setText(f"<b>Could not fetch:</b><br>{_esc(msg)}".replace("\n", "<br>"))
        self._log("error", msg)

    def _probe_done(self, info: dict):
        self._probe_end()
        self._info = info
        if info.get("_type") == "playlist":
            entries = info.get("entries") or []
            self.info.setText(f"<b>Playlist:</b> {_esc(info.get('title') or '')}<br>"
                              f"{len(entries)} entries · {_esc(info.get('uploader') or info.get('channel') or '')}<br>"
                              "Use <i>Download</i> to fetch all entries with the selected quality preset.")
            self.playlist.setChecked(True)
            thumbs = info.get("thumbnails") or []
            self._load_thumb(info.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None))
            return
        fmts = [describe_format(f) for f in info.get("formats") or []]
        order = {"video+audio": 1, "video": 0, "audio": 2, "other": 3}
        fmts.sort(key=lambda f: (order[f["kind"]], -f["height"], -(f["tbr"] or 0)))
        premium = [f for f in fmts if "premium" in f["note"].lower()]
        best_h = max((f["height"] for f in fmts), default=0)
        self.formats.setRowCount(len(fmts))
        bold = QFont()
        bold.setBold(True)
        for r, f in enumerate(fmts):
            vals = [f["id"], f["kind"], f["ext"], f["res"], str(f["fps"]), f["dynamic_range"],
                    f["vcodec"], f["acodec"], str(f["tbr"] or ""), f["size"], f["note"]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, f["kind"])
                if "premium" in f["note"].lower():
                    it.setFont(bold)
                    it.setForeground(QBrush(QColor("#c58a00")))
                self.formats.setItem(r, c, it)
        acct = f" · as <b>{_esc(self._info_account)}</b>" if self._info_account else " · anonymous"
        extra = (f"<br><span style='color:#c58a00'>★ {len(premium)} premium format(s) available</span>"
                 if premium else "")
        self.info.setText(
            f"<b>{_esc(info.get('title') or '')}</b><br>"
            f"{_esc(info.get('uploader') or info.get('channel') or '')} · {_dur(info.get('duration'))}"
            f" · {len(fmts)} formats · up to {best_h}p{acct}{extra}")
        self._load_thumb(info.get("thumbnail"))

    def _load_thumb(self, url: Optional[str]):
        if not url:
            return
        reply = self.net.get(QNetworkRequest(QUrl(url)))

        def done():
            if reply.error() == QNetworkReply.NoError:
                pm = QPixmap()
                if pm.loadFromData(bytes(reply.readAll())):
                    self.thumb.setPixmap(pm.scaled(self.thumb.size(), Qt.KeepAspectRatio,
                                                   Qt.SmoothTransformation))
            reply.deleteLater()
        reply.finished.connect(done)

    def _selected_format_ids(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        rows = sorted({i.row() for i in self.formats.selectedIndexes()})
        video = audio = combined = None
        for r in rows:
            it = self.formats.item(r, 0)
            kind = it.data(Qt.UserRole)
            if kind == "video" and not video:
                video = it.text()
            elif kind == "audio" and not audio:
                audio = it.text()
            elif kind == "video+audio" and not combined:
                combined = it.text()
        return video, audio, combined

    def _format_selection_changed(self):
        v, a, c = self._selected_format_ids()
        if v or a or c:
            if v and not a:
                spec, note = f"{v}+ba", " (+ best audio)"
            else:
                spec, note = (custom_format(v, a) if (v or a) else c), ""
            self.sel_label.setText(f"Selected: {spec}{note}")
            self.dl_sel_btn.setEnabled(True)
        else:
            self.sel_label.setText("Tip: select one video row and one audio row (Ctrl+click) to merge exact formats.")
            self.dl_sel_btn.setEnabled(False)

    # ---------------------------------------------------------- downloading
    def download_preset(self):
        urls = self.url.text().split()
        if not urls:
            self.url.setFocus()
            return
        preset = get_preset(self.preset.currentData())
        for u in urls:
            title = u
            if self._info and len(urls) == 1 and (self._info.get("webpage_url") == u or self._info.get("original_url") == u):
                title = self._info.get("title") or u
            self._enqueue(JobSpec(url=u, preset=preset.key, container=self.container.currentData(),
                                  playlist=self.playlist.isChecked(), account=self.current_account()),
                          title, preset.label)
        self._remember_choices()

    def download_selected(self):
        if not self._info:
            return
        v, a, c = self._selected_format_ids()
        if v and not a:
            spec = f"{v}+ba/{v}"
        elif v or a:
            spec = custom_format(v, a)
        elif c:
            spec = c
        else:
            return
        url = self._info.get("webpage_url") or self._info.get("original_url") or self.url.text().split()[0]
        acc = self.settings.account(self._info_account) if self._info_account else None
        self._enqueue(JobSpec(url=url, format_override=spec, container=self.container.currentData(),
                              playlist=False, account=copy.deepcopy(acc)),
                      self._info.get("title") or url, f"formats {spec}")

    def _remember_choices(self):
        self.settings.preset = self.preset.currentData()
        self.settings.container = self.container.currentData()
        config.save(self.settings)

    def _enqueue(self, spec: JobSpec, title: str, quality: str):
        jid = next(self._ids)
        row = self.queue.rowCount()
        self.queue.insertRow(row)
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(True)
        bar.setFormat("0%")
        vals = [title, spec.account.name if spec.account else "—", quality, "queued", "", "", ""]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            it.setData(Qt.UserRole, jid)
            self.queue.setItem(row, c, it)
        self.queue.setCellWidget(row, 4, bar)
        job = Job(jid, spec, title, quality, row, bar=bar)
        self.jobs[jid] = job
        self._start(job)
        self.tabs.setCurrentIndex(0)

    def _start(self, job: Job):
        w = DownloadWorker(job.id, copy.deepcopy(self.settings), job.spec)
        w.setAutoDelete(False)
        w.signals.progress.connect(self._on_progress)
        w.signals.log.connect(lambda jid, lvl, m: self._log(lvl, m, jid))
        w.signals.finished.connect(self._on_finished)
        job.worker = w
        job.status = "queued"
        self._set_cell(job, 3, "queued")
        self.dl_pool.start(w)

    def _row_of(self, job: Job) -> int:
        for r in range(self.queue.rowCount()):
            it = self.queue.item(r, 0)
            if it and it.data(Qt.UserRole) == job.id:
                return r
        return -1

    def _set_cell(self, job: Job, col: int, text: str):
        r = self._row_of(job)
        if r >= 0:
            self.queue.item(r, col).setText(text)

    def _on_progress(self, jid: int, p):
        job = self.jobs.get(jid)
        if not job or job.status in ("cancelled", "done", "error"):
            return
        job.status = p.status or job.status
        if p.title:
            label = f"[{p.item}] {p.title}" if p.item else p.title
            if label != job.title:
                job.title = label
                self._set_cell(job, 0, label)
        self._set_cell(job, 3, job.status)
        self._set_cell(job, 5, p.speed)
        self._set_cell(job, 6, p.eta)
        job.bar.setValue(int(p.percent * 10))
        job.bar.setFormat(f"{p.percent:.1f}%")

    def _on_finished(self, jid: int, status: str, msg: str):
        job = self.jobs.get(jid)
        if not job:
            return
        job.status = status
        job.worker = None
        self._set_cell(job, 3, status if not msg else f"{status}: {msg.splitlines()[0]}")
        self._set_cell(job, 5, "")
        self._set_cell(job, 6, "")
        r = self._row_of(job)
        if status == "done":
            job.bar.setValue(1000)
            job.bar.setFormat("100%")
        if r >= 0 and msg:
            self.queue.item(r, 3).setToolTip(msg)
        if msg:
            self._log("error", msg, jid)
        active = sum(1 for j in self.jobs.values() if j.worker)
        self.statusBar().showMessage(f"{job.title}: {status}" + (f" · {active} active" if active else ""))

    def _selected_jobs(self) -> list[Job]:
        ids = {self.queue.item(i.row(), 0).data(Qt.UserRole) for i in self.queue.selectedIndexes()}
        return [self.jobs[i] for i in ids if i in self.jobs]

    def cancel_selected(self):
        for job in self._selected_jobs():
            if job.worker:
                job.worker.cancel()
                if self.dl_pool.tryTake(job.worker):
                    self._on_finished(job.id, "cancelled", "")
                else:
                    self._set_cell(job, 3, "cancelling…")

    def retry_selected(self):
        for job in self._selected_jobs():
            if not job.worker and job.status in ("error", "cancelled"):
                job.bar.setValue(0)
                job.bar.setFormat("0%")
                self._set_cell(job, 3, "queued")
                self.queue.item(self._row_of(job), 3).setToolTip("")
                self._start(job)

    def clear_finished(self):
        for r in reversed(range(self.queue.rowCount())):
            jid = self.queue.item(r, 0).data(Qt.UserRole)
            job = self.jobs.get(jid)
            if job and not job.worker:
                self.queue.removeRow(r)
                del self.jobs[jid]

    def _queue_menu(self, pos):
        m = QMenu(self)
        m.addAction("Cancel", self.cancel_selected)
        m.addAction("Retry", self.retry_selected)
        m.addAction("Copy URL", lambda: QApplication.clipboard().setText(
            "\n".join(j.spec.url for j in self._selected_jobs())))
        m.addSeparator()
        m.addAction("Open download folder", self.open_output_dir)
        m.exec(self.queue.viewport().mapToGlobal(pos))

    def open_output_dir(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.settings.output_dir))

    # ---------------------------------------------------------------- misc
    def _log(self, level: str, msg: str, jid: Optional[int] = None):
        prefix = f"#{jid} " if jid else ""
        tag = {"warning": "WARN ", "error": "ERROR "}.get(level, "")
        self.log.appendPlainText(f"{prefix}{tag}{msg}")

    def _about(self):
        QMessageBox.about(self, "About yt-dlp GUI",
                          f"<b>yt-dlp GUI</b> {__version__}<br>yt-dlp {yt_dlp.version.__version__}<br>"
                          f"Python {sys.version.split()[0]}<br>ffmpeg: {find_ffmpeg(self.settings.ffmpeg_path) or 'not found'}"
                          "<br><br>Only download content you have the right to download.")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        text = " ".join(u.toString() for u in md.urls()) if md.hasUrls() else md.text()
        self.url.setText(text.strip())
        self.fetch()

    def closeEvent(self, e):
        active = [j for j in self.jobs.values() if j.worker]
        if active:
            r = QMessageBox.question(self, "Quit", f"{len(active)} download(s) still running. Cancel them and quit?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            for j in active:
                j.worker.cancel()
                self.dl_pool.tryTake(j.worker)
            self.dl_pool.waitForDone(5000)
        self._remember_choices()
        e.accept()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
