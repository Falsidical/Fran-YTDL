"""Application settings."""
from __future__ import annotations

import copy
import sys

import yt_dlp.version
from PySide6.QtCore import QProcess, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import Settings
from .. import tools
from ..ffmpeg import find_ffmpeg
from ..workers import ToolInstallWorker


def _with_browse(edit: QLineEdit, pick) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(edit, 1)
    b = QPushButton("Browse…")
    b.clicked.connect(pick)
    lay.addWidget(b)
    return w


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(640, 0)
        self.s = copy.deepcopy(settings)

        self.out_dir = QLineEdit(self.s.output_dir)
        self.template = QLineEdit(self.s.filename_template)
        self.template.setToolTip("yt-dlp output template, e.g. %(uploader)s/%(title)s.%(ext)s")
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 8)
        self.concurrent.setValue(self.s.max_concurrent)
        self.rate = QLineEdit(self.s.rate_limit)
        self.rate.setPlaceholderText("unlimited  (e.g. 5M, 500K)")
        self.ffmpeg = QLineEdit(self.s.ffmpeg_path)
        found = find_ffmpeg(self.s.ffmpeg_path)
        self.ffmpeg.setPlaceholderText(f"auto: {found}" if found else "not found — required to merge best video+audio")
        self.default_account = QComboBox()
        self.default_account.addItem("(none — only auto-select by site)", "")
        for a in self.s.accounts:
            self.default_account.addItem(a.name, a.name)
        self.default_account.setCurrentIndex(max(0, self.default_account.findData(self.s.default_account)))

        general = QFormLayout()
        general.addRow("Download folder", _with_browse(self.out_dir, self._pick_dir))
        general.addRow("Filename template", self.template)
        general.addRow("Parallel downloads", self.concurrent)
        general.addRow("Rate limit", self.rate)
        general.addRow("ffmpeg", _with_browse(self.ffmpeg, self._pick_ffmpeg))
        general.addRow("Default account", self.default_account)

        self.meta = QCheckBox("Embed metadata")
        self.meta.setChecked(self.s.embed_metadata)
        self.chapters = QCheckBox("Embed chapters")
        self.chapters.setChecked(self.s.embed_chapters)
        self.thumb = QCheckBox("Embed thumbnail")
        self.thumb.setChecked(self.s.embed_thumbnail)
        self.subs = QCheckBox("Embed subtitles, languages:")
        self.subs.setChecked(self.s.embed_subs)
        self.sub_langs = QLineEdit(self.s.sub_langs)
        self.sponsor = QCheckBox("Remove sponsor segments (SponsorBlock)")
        self.sponsor.setChecked(self.s.sponsorblock_remove)
        sub_row = QHBoxLayout()
        sub_row.addWidget(self.subs)
        sub_row.addWidget(self.sub_langs, 1)
        pp = QGroupBox("Post-processing")
        ppl = QVBoxLayout(pp)
        for w in (self.meta, self.chapters, self.thumb):
            ppl.addWidget(w)
        ppl.addLayout(sub_row)
        ppl.addWidget(self.sponsor)

        tg = QGroupBox("Helper tools (needed for best quality)")
        tl = QFormLayout(tg)
        self._tool_labels, self._tool_buttons = {}, {}
        self._tool_worker = None
        for key, title in (("ffmpeg", "ffmpeg"), ("deno", "JS runtime")):
            lbl = QLabel()
            lbl.setWordWrap(True)
            btn = QPushButton()
            btn.clicked.connect(lambda _=False, k=key: self._install_tool(k))
            row = QHBoxLayout()
            row.addWidget(lbl, 1)
            row.addWidget(btn)
            tl.addRow(title, row)
            self._tool_labels[key], self._tool_buttons[key] = lbl, btn
        open_btn = QPushButton("Open tools folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(tools.tools_dir()))))
        tl.addRow("", open_btn)
        self._refresh_tools()

        upd = QGroupBox("yt-dlp")
        ul = QHBoxLayout(upd)
        self.ver_label = QLabel(f"Version {yt_dlp.version.__version__}")
        ul.addWidget(self.ver_label, 1)
        self.update_btn = QPushButton("Update yt-dlp")
        self.update_btn.clicked.connect(self._update)
        if getattr(sys, "frozen", False):
            self.update_btn.setEnabled(False)
            self.update_btn.setToolTip("Bundled build: download a newer release of this app to update yt-dlp.")
        ul.addWidget(self.update_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(general)
        lay.addWidget(pp)
        lay.addWidget(tg)
        lay.addWidget(upd)
        lay.addWidget(buttons)

    def _refresh_tools(self):
        st = tools.status(self.ffmpeg.text().strip())
        can_dl = bool(tools.specs())
        for key, info in st.items():
            lbl, btn = self._tool_labels[key], self._tool_buttons[key]
            if info["path"]:
                extra = f" (managed, installed {info['installed']})" if info["managed"] else ""
                rt = f"{info['runtime']}: " if key == "deno" else ""
                lbl.setText(f"✔ {rt}{info['path']}{extra}")
            else:
                lbl.setText("✘ not found")
            btn.setText("Update" if info["managed"] else "Download")
            btn.setEnabled(can_dl and not self._tool_worker)
            btn.setVisible(can_dl)

    def _install_tool(self, key: str):
        w = ToolInstallWorker([key])
        w.setAutoDelete(False)
        w.signals.progress.connect(lambda k, p: self._tool_labels[k].setText(f"Downloading… {p:.0f}%"))
        w.signals.done.connect(self._tool_done)
        self._tool_worker = w
        for b in self._tool_buttons.values():
            b.setEnabled(False)
        QThreadPool.globalInstance().start(w)

    def _tool_done(self, key: str, ok: bool, msg: str):
        self._tool_worker = None
        if not ok:
            QMessageBox.warning(self, "Download failed", f"Couldn't install {key}:\n{msg}")
        self._refresh_tools()

    def reject(self):
        if self._tool_worker:
            self._tool_worker.cancel()
        super().reject()

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Download folder", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def _pick_ffmpeg(self):
        f, _ = QFileDialog.getOpenFileName(self, "Locate ffmpeg")
        if f:
            self.ffmpeg.setText(f)

    def _update(self):
        self.update_btn.setEnabled(False)
        self.ver_label.setText("Updating…")
        self._proc = QProcess(self)
        self._proc.finished.connect(self._update_done)
        self._proc.start(sys.executable, ["-m", "pip", "install", "-U", "yt-dlp[default]"])

    def _update_done(self, code, _status):
        self.update_btn.setEnabled(True)
        out = bytes(self._proc.readAllStandardError()).decode(errors="replace")[-800:]
        if code == 0:
            self.ver_label.setText("Updated — restart the app to use the new version.")
        else:
            self.ver_label.setText(f"Version {yt_dlp.version.__version__}")
            QMessageBox.warning(self, "Update failed", out or "pip exited with an error.")

    def _accept(self):
        if self._tool_worker:
            self._tool_worker.cancel()
        s = self.s
        s.output_dir = self.out_dir.text().strip() or s.output_dir
        s.filename_template = self.template.text().strip() or "%(title)s [%(id)s].%(ext)s"
        s.max_concurrent = self.concurrent.value()
        s.rate_limit = self.rate.text().strip()
        s.ffmpeg_path = self.ffmpeg.text().strip()
        s.default_account = self.default_account.currentData() or ""
        s.embed_metadata = self.meta.isChecked()
        s.embed_chapters = self.chapters.isChecked()
        s.embed_thumbnail = self.thumb.isChecked()
        s.embed_subs = self.subs.isChecked()
        s.sub_langs = self.sub_langs.text().strip() or "en.*"
        s.sponsorblock_remove = self.sponsor.isChecked()
        self.accept()

    def result_settings(self) -> Settings:
        return self.s
