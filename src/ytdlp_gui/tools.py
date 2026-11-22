"""Download and locate helper programs: ffmpeg and a JavaScript runtime (Deno).

ffmpeg merges the separate best-video and best-audio streams; a JS runtime lets yt-dlp solve
YouTube's challenges. Both are fetched from their official GitHub releases into the app's own
data folder, verified by SHA-256, so users don't have to install anything or touch PATH.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from platformdirs import user_data_dir

from . import APP_NAME

EXE = ".exe" if sys.platform.startswith("win") else ""
JS_RUNTIMES = [("deno", ["deno"]), ("node", ["node"]), ("quickjs", ["qjs", "quickjs"]), ("bun", ["bun"])]


class ToolError(Exception):
    pass


class Cancelled(ToolError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    key: str                 # "deno" | "ffmpeg"
    label: str
    url: str
    checksum_url: str
    archive_name: str        # file name as listed in the checksum file
    binaries: tuple          # executables to extract (without .exe)
    approx_mb: int


def platform_id() -> Optional[str]:
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform.startswith("win"):
        return None if arm else "win-x64"
    if sys.platform.startswith("linux"):
        return "linux-arm64" if arm else "linux-x64"
    return None


def specs(plat: Optional[str] = None) -> dict[str, ToolSpec]:
    plat = plat or platform_id()
    if not plat:
        return {}
    deno_triple = {"win-x64": "x86_64-pc-windows-msvc", "linux-x64": "x86_64-unknown-linux-gnu",
                   "linux-arm64": "aarch64-unknown-linux-gnu"}[plat]
    deno_zip = f"deno-{deno_triple}.zip"
    deno_base = "https://github.com/denoland/deno/releases/latest/download/"
    ff_name = {"win-x64": "ffmpeg-master-latest-win64-gpl.zip",
               "linux-x64": "ffmpeg-master-latest-linux64-gpl.tar.xz",
               "linux-arm64": "ffmpeg-master-latest-linuxarm64-gpl.tar.xz"}[plat]
    ff_base = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
    return {
        "deno": ToolSpec("deno", "Deno (JavaScript runtime for YouTube)", deno_base + deno_zip,
                         deno_base + deno_zip + ".sha256sum", deno_zip, ("deno",), 40),
        "ffmpeg": ToolSpec("ffmpeg", "ffmpeg (merges video + audio)", ff_base + ff_name,
                           ff_base + "checksums.sha256", ff_name, ("ffmpeg", "ffprobe"),
                           190 if plat == "win-x64" else 150),
    }


def tools_dir() -> Path:
    override = os.environ.get("YTDLP_GUI_TOOLS")
    return Path(override) if override else Path(user_data_dir(APP_NAME, appauthor=False)) / "bin"


def managed_path(name: str) -> Optional[str]:
    p = tools_dir() / (name + EXE)
    return str(p) if p.is_file() else None


def _versions_file() -> Path:
    return tools_dir() / "versions.json"


def installed_versions() -> dict:
    try:
        return json.loads(_versions_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_js_runtime() -> Optional[tuple[str, str]]:
    """(runtime name, path) — the app-managed Deno first, then any runtime on PATH."""
    p = managed_path("deno")
    if p:
        return "deno", p
    for name, exes in JS_RUNTIMES:
        for exe in exes:
            found = shutil.which(exe)
            if found:
                return name, found
    return None


def missing_tools(ffmpeg_configured: str = "") -> list[str]:
    from .ffmpeg import find_ffmpeg
    out = []
    if not find_ffmpeg(ffmpeg_configured):
        out.append("ffmpeg")
    if not find_js_runtime():
        out.append("deno")
    return out


def status(ffmpeg_configured: str = "") -> dict[str, dict]:
    from .ffmpeg import find_ffmpeg
    vers = installed_versions()
    ff = find_ffmpeg(ffmpeg_configured)
    js = find_js_runtime()
    return {
        "ffmpeg": {"path": ff, "managed": bool(ff) and ff == managed_path("ffmpeg"),
                   "installed": vers.get("ffmpeg", {}).get("installed")},
        "deno": {"path": js[1] if js else None, "runtime": js[0] if js else None,
                 "managed": bool(js) and js[1] == managed_path("deno"),
                 "installed": vers.get("deno", {}).get("installed")},
    }


# ---------------------------------------------------------------- install
ProgressCb = Callable[[float], None]


def _download(url: str, dest: Path, progress: ProgressCb, cancel: threading.Event) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel.is_set():
                raise Cancelled("Cancelled")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if total:
                progress(min(99.0, done * 100.0 / total))
    return h.hexdigest()


def _expected_sha(spec: ToolSpec) -> str:
    req = urllib.request.Request(spec.checksum_url, headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == spec.archive_name:
            return parts[0].lower()
        if len(parts) == 1 and len(parts[0]) == 64:   # bare hash file
            return parts[0].lower()
    raise ToolError(f"No checksum for {spec.archive_name} in {spec.checksum_url}")


def _extract(archive: Path, spec: ToolSpec, out_dir: Path) -> list[Path]:
    """Extract only the wanted executables, by base name; never trust archive paths."""
    wanted = {b + EXE for b in spec.binaries}
    got: list[Path] = []
    if spec.archive_name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                if base in wanted and not info.is_dir():
                    target = out_dir / base
                    with z.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    got.append(target)
    else:
        with tarfile.open(archive, "r:*") as t:
            for m in t.getmembers():
                base = m.name.rsplit("/", 1)[-1]
                if base in wanted and m.isfile():
                    target = out_dir / base
                    src = t.extractfile(m)
                    with src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    got.append(target)
    missing = wanted - {p.name for p in got}
    if missing:
        raise ToolError(f"Archive did not contain {', '.join(sorted(missing))}")
    return got


def install(key: str, progress: ProgressCb = lambda p: None,
            cancel: Optional[threading.Event] = None, spec: Optional[ToolSpec] = None) -> str:
    """Download, verify and install a tool. Returns the installed executable path."""
    cancel = cancel or threading.Event()
    spec = spec or specs().get(key)
    if not spec:
        raise ToolError("Automatic download isn't available for this platform. Install it manually.")
    dest_dir = tools_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest_dir, prefix=".dl-") as tmp:
        tmp = Path(tmp)
        expected = _expected_sha(spec)
        archive = tmp / spec.archive_name
        actual = _download(spec.url, archive, progress, cancel)
        if actual != expected:
            raise ToolError(f"Checksum mismatch for {spec.archive_name} — download discarded.")
        extracted = tmp / "x"
        extracted.mkdir()
        files = _extract(archive, spec, extracted)
        for f in files:
            if not EXE:
                f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            os.replace(f, dest_dir / f.name)
    vers = installed_versions()
    vers[key] = {"installed": time.strftime("%Y-%m-%d"), "source": spec.url, "sha256": expected}
    _versions_file().write_text(json.dumps(vers, indent=2), encoding="utf-8")
    progress(100.0)
    return str(dest_dir / (spec.binaries[0] + EXE))
