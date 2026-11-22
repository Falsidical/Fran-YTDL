"""Thin layer over yt-dlp's Python API.

Options are produced through ``yt_dlp.parse_options`` (the same parser the CLI uses), so
post-processor ordering and defaults always match the installed yt-dlp version.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from .accounts import to_cookiesfrombrowser
from .config import Account, Settings
from .ffmpeg import find_ffmpeg
from .formats import get_preset


@dataclass
class JobSpec:
    url: str
    preset: str = "best"
    format_override: str = ""        # explicit "137+140" from the format table
    container: str = "mkv"
    playlist: bool = True
    account: Optional[Account] = None


def build_argv(settings: Settings, job: JobSpec) -> list[str]:
    preset = get_preset(job.preset)
    argv: list[str] = []
    fmt = job.format_override or preset.format
    argv += ["-f", fmt]
    if preset.sort and not job.format_override:
        argv += ["-S", ",".join(preset.sort)]
    argv += ["-P", settings.output_dir, "-o", settings.filename_template]
    argv += ["--yes-playlist" if job.playlist else "--no-playlist"]

    if preset.audio_only:
        argv += ["-x", "--audio-quality", "0"]
        if preset.key == "mp3":
            argv += ["--audio-format", "mp3"]
    elif job.container:
        argv += ["--merge-output-format", job.container]
        if job.container == "mp4":
            argv += ["--remux-video", "mp4"]

    if settings.embed_metadata:
        argv.append("--embed-metadata")
    if settings.embed_chapters:
        argv.append("--embed-chapters")
    if settings.embed_thumbnail:
        argv += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    if settings.embed_subs and not preset.audio_only:
        argv += ["--embed-subs", "--sub-langs", settings.sub_langs or "en.*"]
    if settings.sponsorblock_remove:
        argv += ["--sponsorblock-remove", "default"]
    if settings.rate_limit.strip():
        argv += ["-r", settings.rate_limit.strip()]
    ff = find_ffmpeg(settings.ffmpeg_path)
    if ff:
        argv += ["--ffmpeg-location", ff]
    return argv


def build_opts(settings: Settings, job: JobSpec) -> dict:
    opts = yt_dlp.parse_options(build_argv(settings, job)).ydl_opts
    if job.account:
        # Set directly (not via CLI string) so Windows paths with ':' survive.
        opts["cookiesfrombrowser"] = to_cookiesfrombrowser(job.account)
    opts["noprogress"] = True
    opts["quiet"] = True
    opts["no_warnings"] = False
    return opts


def probe_opts(settings: Settings, account: Optional[Account]) -> dict:
    opts = yt_dlp.parse_options(["--flat-playlist"]).ydl_opts
    if account:
        opts["cookiesfrombrowser"] = to_cookiesfrombrowser(account)
    ff = find_ffmpeg(settings.ffmpeg_path)
    if ff:
        opts["ffmpeg_location"] = ff
    opts["quiet"] = True
    opts["ignoreerrors"] = False   # surface extraction errors to the UI instead of returning None
    return opts


class _Logger:
    def __init__(self, sink: Callable[[str, str], None]):
        self.sink = sink

    def debug(self, msg):
        # yt-dlp routes info-level messages through debug() with a "[debug] " prefix for real debug.
        if not msg.startswith("[debug] "):
            self.sink("info", msg)

    def info(self, msg):
        self.sink("info", msg)

    def warning(self, msg):
        self.sink("warning", msg)

    def error(self, msg):
        self.sink("error", msg)


def probe(settings: Settings, url: str, account: Optional[Account],
          log: Callable[[str, str], None] = lambda lvl, m: None) -> dict:
    opts = probe_opts(settings, account)
    opts["logger"] = _Logger(log)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise RuntimeError("yt-dlp returned no information for this URL.")
        return ydl.sanitize_info(info)


@dataclass
class Progress:
    status: str = ""             # downloading | finished | postprocessing | error
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    title: str = ""
    filename: str = ""
    item: str = ""               # "3/12" for playlists


@dataclass
class Downloader:
    settings: Settings
    job: JobSpec
    on_progress: Callable[[Progress], None] = lambda p: None
    on_log: Callable[[str, str], None] = lambda lvl, m: None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _hook(self, d: dict):
        if self._cancel.is_set():
            raise DownloadCancelled("Cancelled by user")
        info = d.get("info_dict") or {}
        p = Progress(title=info.get("title") or "", filename=d.get("filename") or "")
        if info.get("playlist_index") and info.get("n_entries"):
            p.item = f"{info['playlist_index']}/{info['n_entries']}"
        st = d.get("status")
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            if total:
                p.percent = min(100.0, done * 100.0 / total)
            elif d.get("fragment_count"):
                p.percent = (d.get("fragment_index") or 0) * 100.0 / d["fragment_count"]
            p.status = "downloading"
            p.speed = _fmt_speed(d.get("speed"))
            p.eta = _fmt_eta(d.get("eta"))
        elif st == "finished":
            p.status, p.percent = "processing", 100.0
        elif st == "error":
            p.status = "error"
        self.on_progress(p)

    def _pp_hook(self, d: dict):
        if self._cancel.is_set():
            raise DownloadCancelled("Cancelled by user")
        if d.get("status") == "started":
            info = d.get("info_dict") or {}
            self.on_progress(Progress(status=f"post-processing ({d.get('postprocessor')})",
                                      percent=100.0, title=info.get("title") or ""))

    def run(self) -> int:
        opts = build_opts(self.settings, self.job)
        opts["logger"] = _Logger(self.on_log)
        opts["progress_hooks"] = [self._hook]
        opts["postprocessor_hooks"] = [self._pp_hook]
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.download([self.job.url])


def _fmt_speed(bps) -> str:
    if not bps:
        return ""
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if bps < 1024:
            return f"{bps:.1f} {unit}"
        bps /= 1024
    return f"{bps:.1f} TiB/s"


def _fmt_eta(sec) -> str:
    if sec is None:
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
