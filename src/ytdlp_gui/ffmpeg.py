"""Locate ffmpeg — required for merging the separate best-video + best-audio streams."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_ffmpeg(configured: str = "") -> Optional[str]:
    """Return a path for yt-dlp's ``ffmpeg_location`` (file or dir), or None."""
    exe = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    if configured:
        p = Path(configured)
        if p.is_file():
            return str(p)
        if (p / exe).is_file():
            return str(p / exe)
    base = _app_dir()
    for cand in (base / "bin" / exe, base / exe, base.parent / "bin" / exe):
        if cand.is_file():
            return str(cand)
    return shutil.which("ffmpeg")
