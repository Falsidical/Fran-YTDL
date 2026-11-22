"""Manage browser-cookie account profiles."""
from __future__ import annotations

import copy
import sys

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from ..accounts import BROWSERS, LINUX_KEYRINGS, detect_browsers, list_profiles
from ..config import Account
from ..workers import AccountCheckWorker

HELP = (
    "<b>How accounts work:</b> sign in to the site (YouTube, Vimeo, Patreon, …) in a normal "
    "browser, then create an account here pointing at that browser profile. yt-dlp reads the "
    "browser's cookies at download time, so you get the same formats you would as a logged-in "
    "user — members-only videos, age-restricted content and premium high-bitrate streams."
    "<br><br><b>Tip:</b> Firefox is the most reliable. On Windows, Chrome/Edge/Brave must be fully "
    "closed, and newer versions may block cookie access entirely."
)


class AccountsDialog(QDialog):
    def __init__(self, accounts: list[Account], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Accounts")
        self.resize(820, 520)
        self.accounts = copy.deepcopy(accounts)
        self.pool = QThreadPool.globalInstance()
        self._workers = []
        self._detected = detect_browsers()
        self._loading = False

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load_current)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add)
        del_btn = QPushButton("Remove")
        del_btn.clicked.connect(self._remove)
        left = QVBoxLayout()
        left.addWidget(self.list)
        row = QHBoxLayout()
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        left.addLayout(row)

        self.name = QLineEdit()
        self.browser = QComboBox()
        for key, label in BROWSERS.items():
            n = len(self._detected.get(key, []))
            self.browser.addItem(f"{label}" + (f"  — detected ({n} profile{'s' * (n != 1)})" if n else ""), key)
        self.profile = QComboBox()
        self.profile.setEditable(True)
        self.profile.setToolTip("Profile name or full path. Leave as (default profile) to use the browser's default.")
        self.keyring = QComboBox()
        for k in LINUX_KEYRINGS:
            self.keyring.addItem(k or "(auto)", k)
        self.container = QLineEdit()
        self.container.setPlaceholderText("Firefox container name (optional)")
        self.sites = QLineEdit()
        self.sites.setPlaceholderText("youtube.com, vimeo.com  — auto-select this account for these sites")

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Browser", self.browser)
        form.addRow("Profile", self.profile)
        self._keyring_label = QLabel("Keyring (Linux)")
        form.addRow(self._keyring_label, self.keyring)
        self._container_label = QLabel("Container")
        form.addRow(self._container_label, self.container)
        form.addRow("Sites", self.sites)
        if not sys.platform.startswith("linux"):
            self._keyring_label.hide()
            self.keyring.hide()

        self.test_btn = QPushButton("Test account (read cookies)")
        self.test_btn.clicked.connect(self._test)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("Test results appear here.")
        help_lbl = QLabel(HELP)
        help_lbl.setWordWrap(True)
        help_lbl.setTextFormat(Qt.RichText)

        self.editor = QWidget()
        right = QVBoxLayout(self.editor)
        right.addLayout(form)
        right.addWidget(self.test_btn)
        right.addWidget(self.result, 1)
        right.addWidget(help_lbl)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addWidget(self.editor, 2)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.addLayout(body)
        outer.addWidget(buttons)

        self.browser.currentIndexChanged.connect(self._browser_changed)
        for w in (self.name, self.container, self.sites):
            w.textEdited.connect(self._store_current)
        self.profile.currentTextChanged.connect(self._store_current)
        self.keyring.currentIndexChanged.connect(self._store_current)

        for a in self.accounts:
            self.list.addItem(QListWidgetItem(a.name))
        self.editor.setEnabled(bool(self.accounts))
        if self.accounts:
            self.list.setCurrentRow(0)

    # -- list management --------------------------------------------------
    def _current(self):
        i = self.list.currentRow()
        return self.accounts[i] if 0 <= i < len(self.accounts) else None

    def _add(self):
        browser = next(iter(self._detected), "firefox")
        base = f"{BROWSERS[browser]} account"
        name, n = base, 2
        while any(a.name == name for a in self.accounts):
            name, n = f"{base} {n}", n + 1
        sites = ["youtube.com"] if not self.accounts else []
        self.accounts.append(Account(name=name, browser=browser, sites=sites))
        self.list.addItem(QListWidgetItem(name))
        self.list.setCurrentRow(len(self.accounts) - 1)
        self.editor.setEnabled(True)
        self.name.setFocus()
        self.name.selectAll()

    def _remove(self):
        i = self.list.currentRow()
        if i < 0:
            return
        self.accounts.pop(i)
        self.list.takeItem(i)
        self.editor.setEnabled(bool(self.accounts))

    # -- editor ------------------------------------------------------------
    def _fill_profiles(self, browser: str, selected: str | None):
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItem("(default profile)", "")
        for p in self._detected.get(browser) or list_profiles(browser):
            self.profile.addItem(p.label(), p.path)
        if selected:
            idx = self.profile.findData(selected)
            if idx < 0:
                self.profile.addItem(selected, selected)
                idx = self.profile.count() - 1
            self.profile.setCurrentIndex(idx)
        self.profile.blockSignals(False)
        is_ff = browser == "firefox"
        self.container.setVisible(is_ff)
        self._container_label.setVisible(is_ff)

    def _load_current(self, _row=None):
        a = self._current()
        if not a:
            return
        self._loading = True
        self.name.setText(a.name)
        self.browser.setCurrentIndex(max(0, self.browser.findData(a.browser)))
        self._fill_profiles(a.browser, a.profile)
        self.keyring.setCurrentIndex(max(0, self.keyring.findData(a.keyring or "")))
        self.container.setText(a.container or "")
        self.sites.setText(", ".join(a.sites))
        self.result.clear()
        self._loading = False

    def _browser_changed(self):
        if self._loading:
            return
        self._fill_profiles(self.browser.currentData(), None)
        self._store_current()

    def _profile_value(self) -> str:
        text = self.profile.currentText().strip()
        idx = self.profile.currentIndex()
        if idx >= 0 and self.profile.itemText(idx) == text:
            return self.profile.itemData(idx) or ""
        return text  # user typed a custom name/path

    def _store_current(self, *_):
        if self._loading:
            return
        a = self._current()
        if not a:
            return
        a.name = self.name.text().strip() or a.name
        a.browser = self.browser.currentData()
        a.profile = self._profile_value() or None
        a.keyring = self.keyring.currentData() or None
        a.container = self.container.text().strip() or None if a.browser == "firefox" else None
        a.sites = [s.strip() for s in self.sites.text().replace(";", ",").split(",") if s.strip()]
        self.list.currentItem().setText(a.name)

    def _test(self):
        self._store_current()
        a = self._current()
        if not a:
            return
        self.test_btn.setEnabled(False)
        self.result.setPlainText(f"Reading cookies from {BROWSERS.get(a.browser, a.browser)}…")
        w = AccountCheckWorker(copy.deepcopy(a))
        w.setAutoDelete(False)
        w.signals.done.connect(lambda res, w=w: self._test_done(res, w))
        self._workers.append(w)
        self.pool.start(w)

    def _test_done(self, res, worker):
        self._workers.remove(worker)
        self.test_btn.setEnabled(True)
        self.result.setPlainText(res.summary())

    def _accept(self):
        self._store_current()
        names = [a.name for a in self.accounts]
        if len(names) != len(set(names)):
            QMessageBox.warning(self, "Accounts", "Account names must be unique.")
            return
        self.accept()
