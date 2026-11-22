"""Quality presets mapped to yt-dlp format selection / sorting."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Prefer resolution, then fps, HDR, better codecs, then bitrate. Logged-in-only formats
# (e.g. YouTube Premium "616" enhanced-bitrate 1080p) win via higher bitrate.
MAX_SORT = ["res", "fps", "hdr:12", "vcodec", "vbr", "tbr", "acodec", "abr", "asr"]


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    format: str
    sort: list = field(default_factory=list)
    audio_only: bool = False


PRESETS: list[Preset] = [
    Preset("best", "Best available (max quality)", "bv*+ba/b", MAX_SORT),
    Preset("2160", "Best ≤ 2160p (4K)", "bv*[height<=2160]+ba/b[height<=2160]/b", MAX_SORT),
    Preset("1440", "Best ≤ 1440p", "bv*[height<=1440]+ba/b[height<=1440]/b", MAX_SORT),
    Preset("1080", "Best ≤ 1080p", "bv*[height<=1080]+ba/b[height<=1080]/b", MAX_SORT),
    Preset("720", "Best ≤ 720p", "bv*[height<=720]+ba/b[height<=720]/b", MAX_SORT),
    Preset("480", "Best ≤ 480p", "bv*[height<=480]+ba/b[height<=480]/b", MAX_SORT),
    Preset("audio", "Audio only (best, original codec)", "ba/b", ["abr", "asr"], audio_only=True),
    Preset("mp3", "Audio only → MP3", "ba/b", ["abr", "asr"], audio_only=True),
]

PRESET_BY_KEY = {p.key: p for p in PRESETS}


def get_preset(key: str) -> Preset:
    return PRESET_BY_KEY.get(key, PRESETS[0])


def custom_format(video_id: Optional[str], audio_id: Optional[str]) -> str:
    """Build a format spec from explicit format IDs picked in the format table."""
    if video_id and audio_id:
        return f"{video_id}+{audio_id}"
    return video_id or audio_id or "bv*+ba/b"


def describe_format(f: dict) -> dict:
    """Flatten a yt-dlp format dict into display columns."""
    vcodec = f.get("vcodec") or "none"
    acodec = f.get("acodec") or "none"
    if vcodec != "none" and acodec != "none":
        kind = "video+audio"
    elif vcodec != "none":
        kind = "video"
    elif acodec != "none":
        kind = "audio"
    else:
        kind = "other"
    h = f.get("height")
    res = f"{f.get('width') or '?'}x{h}" if h else (f.get("resolution") or "")
    size = f.get("filesize") or f.get("filesize_approx")
    return {
        "id": str(f.get("format_id", "")),
        "kind": kind,
        "ext": f.get("ext") or "",
        "res": res if kind != "audio" else "audio",
        "height": h or 0,
        "fps": f.get("fps") or "",
        "vcodec": "" if vcodec == "none" else vcodec.split(".")[0],
        "acodec": "" if acodec == "none" else acodec.split(".")[0],
        "tbr": round(f.get("tbr") or 0),
        "size": human_size(size) if size else "",
        "note": f.get("format_note") or "",
        "dynamic_range": f.get("dynamic_range") or "",
    }


def human_size(n: Optional[float]) -> str:
    if not n:
        return ""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PiB"
