"""Persistent settings and account profiles (JSON in the user config dir)."""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from platformdirs import user_config_dir, user_downloads_dir

from . import APP_NAME


@dataclass
class Account:
    """A browser-cookie 'account': yt-dlp logs in by reusing a browser session."""

    name: str
    browser: str                      # chrome, firefox, edge, brave, ...
    profile: Optional[str] = None     # profile name or path; None = default profile
    keyring: Optional[str] = None     # Linux only: GNOMEKEYRING / KWALLET / BASICTEXT
    container: Optional[str] = None   # Firefox multi-account container
    sites: list[str] = field(default_factory=list)  # e.g. ["youtube.com", "*.patreon.com"]

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        for pat in self.sites:
            pat = pat.strip().lower()
            if not pat:
                continue
            if fnmatch.fnmatch(host, pat) or host == pat or host.endswith("." + pat):
                return True
        return False


@dataclass
class Settings:
    output_dir: str = field(default_factory=lambda: _default_download_dir())
    filename_template: str = "%(title)s [%(id)s].%(ext)s"
    max_concurrent: int = 2
    ffmpeg_path: str = ""
    preset: str = "best"
    container: str = "mkv"
    embed_subs: bool = False
    sub_langs: str = "en.*"
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    embed_chapters: bool = True
    sponsorblock_remove: bool = False
    rate_limit: str = ""              # e.g. "5M"
    default_account: str = ""         # account name or ""
    tools_prompt_dismissed: bool = False
    accounts: list[Account] = field(default_factory=list)

    # -- helpers ---------------------------------------------------------
    def account(self, name: str) -> Optional[Account]:
        return next((a for a in self.accounts if a.name == name), None)

    def account_for_url(self, url: str) -> Optional[Account]:
        for a in self.accounts:
            if a.matches(url):
                return a
        return self.account(self.default_account) if self.default_account else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in data.items() if k in known}
        accs = [Account(**{k: v for k, v in a.items() if k in Account.__dataclass_fields__})
                for a in data.pop("accounts", [])]
        s = cls(**data)
        s.accounts = accs
        return s


def _default_download_dir() -> str:
    try:
        return user_downloads_dir()
    except Exception:
        return str(Path.home() / "Downloads")


def config_path() -> Path:
    override = os.environ.get("YTDLP_GUI_CONFIG")
    if override:
        return Path(override)
    return Path(user_config_dir(APP_NAME, appauthor=False)) / "settings.json"


def load(path: Optional[Path] = None) -> Settings:
    path = path or config_path()
    try:
        return Settings.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return Settings()


def save(settings: Settings, path: Optional[Path] = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)
