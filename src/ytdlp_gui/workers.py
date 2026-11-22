"""Qt workers that run yt-dlp off the GUI thread and report back via signals."""
from __future__ import annotations

import traceback
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Signal
from yt_dlp.utils import DownloadCancelled

from . import engine
from .accounts import AccountCheck, check_account, explain_cookie_error
from .config import Account, Settings


class ProbeSignals(QObject):
    done = Signal(dict)
    failed = Signal(str)
    log = Signal(str, str)


class ProbeWorker(QRunnable):
    def __init__(self, settings: Settings, url: str, account: Optional[Account]):
        super().__init__()
        self.settings, self.url, self.account = settings, url, account
        self.signals = ProbeSignals()

    def run(self):
        try:
            info = engine.probe(self.settings, self.url, self.account, self.signals.log.emit)
            self.signals.done.emit(info)
        except Exception as e:
            msg = str(e)
            if self.account and "cookie" in msg.lower():
                msg = explain_cookie_error(msg, self.account.browser)
            self.signals.failed.emit(msg)


class DownloadSignals(QObject):
    progress = Signal(int, object)     # job id, engine.Progress
    log = Signal(int, str, str)        # job id, level, message
    finished = Signal(int, str, str)   # job id, final status, message


class DownloadWorker(QRunnable):
    def __init__(self, job_id: int, settings: Settings, spec: engine.JobSpec):
        super().__init__()
        self.job_id = job_id
        self.signals = DownloadSignals()
        self.downloader = engine.Downloader(
            settings, spec,
            on_progress=lambda p: self.signals.progress.emit(job_id, p),
            on_log=lambda lvl, m: self.signals.log.emit(job_id, lvl, m),
        )

    def cancel(self):
        self.downloader.cancel()

    def run(self):
        if self.downloader.cancelled:
            self.signals.finished.emit(self.job_id, "cancelled", "")
            return
        try:
            rc = self.downloader.run()
            if self.downloader.cancelled:
                self.signals.finished.emit(self.job_id, "cancelled", "")
            elif rc == 0:
                self.signals.finished.emit(self.job_id, "done", "")
            else:
                self.signals.finished.emit(self.job_id, "error", "Some items failed — see log.")
        except DownloadCancelled:
            self.signals.finished.emit(self.job_id, "cancelled", "")
        except Exception as e:
            msg = str(e)
            acc = self.downloader.job.account
            if acc and "cookie" in msg.lower():
                msg = explain_cookie_error(msg, acc.browser)
            self.signals.log.emit(self.job_id, "error", traceback.format_exc(limit=3))
            self.signals.finished.emit(self.job_id, "error", msg)


class CheckSignals(QObject):
    done = Signal(object)  # AccountCheck


class AccountCheckWorker(QRunnable):
    def __init__(self, account: Account):
        super().__init__()
        self.account = account
        self.signals = CheckSignals()

    def run(self):
        try:
            res = check_account(self.account)
        except Exception as e:
            res = AccountCheck(False, error=str(e))
        self.signals.done.emit(res)
