"""Browser-cookie accounts: detect browsers/profiles and verify logins.

yt-dlp can read a browser's cookie store directly (``cookiesfrombrowser``), which is
the most reliable way to download as a logged-in user — that's what unlocks
members-only, age-restricted, and premium (higher-bitrate) formats.
"""
from __future__ import annotations

import configparser
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Account

# Display names for the browsers yt-dlp supports on Windows/Linux.
BROWSERS = {
    "chrome": "Google Chrome",
    "firefox": "Mozilla Firefox",
    "edge": "Microsoft Edge",
    "brave": "Brave",
    "chromium": "Chromium",
    "vivaldi": "Vivaldi",
    "opera": "Opera",
    "whale": "Naver Whale",
}

LINUX_KEYRINGS = ["", "GNOMEKEYRING", "KWALLET", "KWALLET5", "KWALLET6", "BASICTEXT"]

# Cookie names that indicate an authenticated session, per site.
LOGIN_COOKIES = {
    "youtube.com": {"SAPISID", "__Secure-3PAPISID", "LOGIN_INFO", "__Secure-1PSID"},
    "google.com": {"SAPISID", "__Secure-3PAPISID", "SID"},
    "vimeo.com": {"vimeo"},
    "twitch.tv": {"auth-token"},
    "x.com": {"auth_token"},
    "twitter.com": {"auth_token"},
    "instagram.com": {"sessionid"},
    "facebook.com": {"c_user", "xs"},
    "tiktok.com": {"sessionid"},
    "patreon.com": {"session_id"},
    "nicovideo.jp": {"user_session"},
    "bilibili.com": {"SESSDATA"},
    "crunchyroll.com": {"etp_rt"},
    "reddit.com": {"reddit_session", "token_v2"},
}


@dataclass
class BrowserProfile:
    browser: str
    name: str          # human readable
    path: str          # absolute path, passed to yt-dlp as the profile

    def label(self) -> str:
        return f"{self.name}  ({self.path})"


def _platform() -> str:
    if sys.platform.startswith("win"):
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def _chromium_roots(browser: str) -> list[Path]:
    home = Path.home()
    plat = _platform()
    if plat == "win":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return [{
            "chrome": local / "Google" / "Chrome" / "User Data",
            "chromium": local / "Chromium" / "User Data",
            "edge": local / "Microsoft" / "Edge" / "User Data",
            "brave": local / "BraveSoftware" / "Brave-Browser" / "User Data",
            "vivaldi": local / "Vivaldi" / "User Data",
            "opera": roaming / "Opera Software" / "Opera Stable",
            "whale": local / "Naver" / "Naver Whale" / "User Data",
        }[browser]]
    if plat == "mac":
        sup = home / "Library" / "Application Support"
        return [{
            "chrome": sup / "Google" / "Chrome",
            "chromium": sup / "Chromium",
            "edge": sup / "Microsoft Edge",
            "brave": sup / "BraveSoftware" / "Brave-Browser",
            "vivaldi": sup / "Vivaldi",
            "opera": sup / "com.operasoftware.Opera",
            "whale": sup / "Naver" / "Whale",
        }[browser]]
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    rel = {
        "chrome": "google-chrome",
        "chromium": "chromium",
        "edge": "microsoft-edge",
        "brave": "BraveSoftware/Brave-Browser",
        "vivaldi": "vivaldi",
        "opera": "opera",
        "whale": "naver-whale",
    }[browser]
    roots = [cfg / rel]
    # Flatpak / snap installs keep their own config trees.
    flatpak_ids = {
        "chrome": "com.google.Chrome", "chromium": "org.chromium.Chromium",
        "edge": "com.microsoft.Edge", "brave": "com.brave.Browser",
        "vivaldi": "com.vivaldi.Vivaldi", "opera": "com.opera.Opera",
    }
    if browser in flatpak_ids:
        roots.append(home / ".var" / "app" / flatpak_ids[browser] / "config" / rel)
    if browser == "chromium":
        roots.append(home / "snap" / "chromium" / "common" / "chromium")
    return roots


def _firefox_roots() -> list[Path]:
    home = Path.home()
    plat = _platform()
    if plat == "win":
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return [roaming / "Mozilla" / "Firefox"]
    if plat == "mac":
        return [home / "Library" / "Application Support" / "Firefox"]
    return [
        home / ".mozilla" / "firefox",
        home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        home / ".librewolf",
    ]


def chromium_profiles(browser: str, roots: Optional[list[Path]] = None) -> list[BrowserProfile]:
    out: list[BrowserProfile] = []
    for root in roots if roots is not None else _chromium_roots(browser):
        if not root.is_dir():
            continue
        names: dict[str, str] = {}
        try:
            state = json.loads((root / "Local State").read_text(encoding="utf-8"))
            for key, info in state.get("profile", {}).get("info_cache", {}).items():
                names[key] = info.get("name") or key
        except (OSError, ValueError):
            pass
        candidates = set(names) | {p.name for p in root.iterdir()
                                   if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))}
        for key in sorted(candidates, key=lambda k: (k != "Default", k)):
            pdir = root / key
            if (pdir / "Cookies").exists() or (pdir / "Network" / "Cookies").exists():
                out.append(BrowserProfile(browser, names.get(key, key), str(pdir)))
        # Opera keeps cookies directly in the root.
        if not out and ((root / "Cookies").exists() or (root / "Network" / "Cookies").exists()):
            out.append(BrowserProfile(browser, "Default", str(root)))
    return out


def firefox_profiles(roots: Optional[list[Path]] = None) -> list[BrowserProfile]:
    out: list[BrowserProfile] = []
    for root in roots if roots is not None else _firefox_roots():
        ini = root / "profiles.ini"
        if not ini.is_file():
            continue
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(ini, encoding="utf-8")
        except configparser.Error:
            continue
        for sec in cp.sections():
            if not sec.startswith("Profile"):
                continue
            path = cp.get(sec, "Path", fallback=None)
            if not path:
                continue
            is_rel = cp.get(sec, "IsRelative", fallback="1") == "1"
            full = (root / path) if is_rel else Path(path)
            if (full / "cookies.sqlite").exists():
                out.append(BrowserProfile("firefox", cp.get(sec, "Name", fallback=full.name), str(full)))
    return out


def list_profiles(browser: str) -> list[BrowserProfile]:
    if browser == "firefox":
        return firefox_profiles()
    if browser in BROWSERS:
        return chromium_profiles(browser)
    return []


def detect_browsers() -> dict[str, list[BrowserProfile]]:
    """Browsers that appear to be installed, with their cookie-bearing profiles."""
    found = {}
    for b in BROWSERS:
        profiles = list_profiles(b)
        if profiles:
            found[b] = profiles
    return found


def to_cookiesfrombrowser(acc: Account) -> tuple:
    """yt-dlp's ``cookiesfrombrowser`` tuple: (browser, profile, keyring, container)."""
    return (
        acc.browser,
        acc.profile or None,
        (acc.keyring or None) if _platform() == "linux" else None,
        acc.container or None,
    )


@dataclass
class AccountCheck:
    ok: bool
    total: int = 0
    per_site: dict = None        # site -> (cookie count, logged_in bool)
    error: str = ""

    def summary(self) -> str:
        if not self.ok:
            return self.error
        lines = [f"Read {self.total} cookies."]
        for site, (count, logged_in) in sorted((self.per_site or {}).items()):
            mark = "logged in ✔" if logged_in else "no login cookie"
            lines.append(f"  {site}: {count} cookies — {mark}")
        if not self.per_site:
            lines.append("  No cookies for the account's sites (or for known sites).")
        return "\n".join(lines)


class _QuietLogger:
    def __init__(self):
        self.messages: list[str] = []

    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg, only_once=False): self.messages.append(str(msg))
    def error(self, msg): self.messages.append(str(msg))


def check_account(acc: Account) -> AccountCheck:
    """Load cookies the same way yt-dlp will during a download, and report login state."""
    from yt_dlp.cookies import YDLLogger, extract_cookies_from_browser  # noqa: F401

    browser, profile, keyring, container = to_cookiesfrombrowser(acc)
    log = _QuietLogger()
    try:
        jar = extract_cookies_from_browser(browser, profile, log, keyring=keyring, container=container)
    except Exception as e:  # yt-dlp raises plain errors for locked/encrypted DBs
        return AccountCheck(False, error=explain_cookie_error(str(e), browser))

    sites = [s.lstrip("*.").lower() for s in acc.sites if s.strip()] or list(LOGIN_COOKIES)
    per_site = {}
    total = 0
    for c in jar:
        total += 1
        dom = c.domain.lstrip(".").lower()
        for site in sites:
            if dom == site or dom.endswith("." + site):
                count, logged = per_site.get(site, (0, False))
                logged = logged or c.name in LOGIN_COOKIES.get(site, set())
                per_site[site] = (count + 1, logged)
    return AccountCheck(True, total=total, per_site=per_site)


def explain_cookie_error(msg: str, browser: str) -> str:
    """Turn common cookie-extraction failures into actionable advice."""
    low = msg.lower()
    hints = []
    if "could not copy" in low or "database is locked" in low or "permission denied" in low:
        hints.append(f"Close {BROWSERS.get(browser, browser)} completely (including background/tray "
                     "processes) and try again — the cookie database is locked while it runs.")
    if "dpapi" in low or "app-bound" in low or "failed to decrypt" in low:
        hints.append("Recent Chromium-based browsers on Windows use app-bound cookie encryption, "
                     "which yt-dlp cannot always read. Firefox is the most reliable choice: sign in "
                     "there and point this account at Firefox.")
    if "keyring" in low or "secretstorage" in low or "kwallet" in low:
        hints.append("On Linux, try choosing the keyring explicitly (GNOMEKEYRING for GNOME, "
                     "KWALLET6/KWALLET5 for KDE, BASICTEXT if the browser was started with "
                     "--password-store=basic).")
    if "could not find" in low:
        hints.append("The browser profile wasn't found. Pick the profile from the list, or make sure "
                     "the browser has been opened at least once.")
    return msg + ("\n\n" + "\n".join("• " + h for h in hints) if hints else "")
