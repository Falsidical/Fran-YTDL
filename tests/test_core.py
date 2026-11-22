import json
import sqlite3

from ytdlp_gui import accounts, config, engine
from ytdlp_gui.config import Account, Settings
from ytdlp_gui.formats import custom_format, describe_format, get_preset


def test_config_round_trip(tmp_path):
    s = Settings(output_dir=str(tmp_path), accounts=[Account("Me", "firefox", sites=["youtube.com"])])
    config.save(s)
    s2 = config.load()
    assert s2.output_dir == str(tmp_path)
    assert s2.accounts[0].name == "Me" and s2.accounts[0].sites == ["youtube.com"]


def test_config_ignores_unknown_keys(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"bogus": 1, "max_concurrent": 4, "accounts": [{"name": "x", "browser": "chrome", "junk": 2}]}))
    s = config.load(p)
    assert s.max_concurrent == 4 and s.accounts[0].browser == "chrome"


def test_corrupt_config_falls_back(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not json")
    assert isinstance(config.load(p), Settings)


def test_account_matching():
    a = Account("yt", "firefox", sites=["youtube.com", "*.vimeo.com"])
    assert a.matches("https://www.youtube.com/watch?v=x")
    assert a.matches("https://youtube.com/watch?v=x")
    assert a.matches("https://player.vimeo.com/video/1")
    assert not a.matches("https://notyoutube.com/x")
    s = Settings(accounts=[a, Account("other", "chrome")], default_account="other")
    assert s.account_for_url("https://m.youtube.com/x").name == "yt"
    assert s.account_for_url("https://example.com").name == "other"


def test_cookie_tuple(monkeypatch):
    a = Account("x", "chrome", profile="/p/Profile 1", keyring="KWALLET6", container=None)
    monkeypatch.setattr(accounts, "_platform", lambda: "linux")
    assert accounts.to_cookiesfrombrowser(a) == ("chrome", "/p/Profile 1", "KWALLET6", None)
    monkeypatch.setattr(accounts, "_platform", lambda: "win")
    assert accounts.to_cookiesfrombrowser(a) == ("chrome", "/p/Profile 1", None, None)


def test_build_opts_best_with_account(tmp_path):
    s = Settings(output_dir=str(tmp_path), embed_subs=True, sponsorblock_remove=True)
    acc = Account("me", "firefox", profile=r"C:\Users\me\AppData\Roaming\Mozilla\Firefox\Profiles\a.default")
    o = engine.build_opts(s, engine.JobSpec("https://youtu.be/x", account=acc))
    assert o["format"] == "bv*+ba/b"
    assert o["format_sort"][0] == "res"
    assert o["cookiesfrombrowser"][1].startswith("C:\\")  # Windows path survives intact
    assert o["merge_output_format"] == "mkv"
    assert o["paths"]["home"] == str(tmp_path)
    keys = [p["key"] for p in o["postprocessors"]]
    assert "FFmpegEmbedSubtitle" in keys and "SponsorBlock" in keys and "EmbedThumbnail" in keys


def test_build_opts_audio_and_override(tmp_path):
    s = Settings(output_dir=str(tmp_path))
    o = engine.build_opts(s, engine.JobSpec("u", preset="mp3"))
    ea = next(p for p in o["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert ea["preferredcodec"] == "mp3" and ea["preferredquality"] == "0"
    assert "cookiesfrombrowser" not in o or o["cookiesfrombrowser"] is None
    o = engine.build_opts(s, engine.JobSpec("u", format_override="137+140", playlist=False))
    assert o["format"] == "137+140" and o["noplaylist"] is True


def test_presets_and_formats():
    assert get_preset("nope").key == "best"
    assert "height<=1080" in get_preset("1080").format
    assert custom_format("137", "140") == "137+140"
    assert custom_format(None, "140") == "140"
    d = describe_format({"format_id": "616", "vcodec": "vp09.00.40.08", "acodec": "none", "height": 1080,
                         "width": 1920, "tbr": 9000.4, "format_note": "Premium", "filesize": 2048})
    assert d["kind"] == "video" and d["vcodec"] == "vp09" and d["size"] == "2.0 KiB" and d["tbr"] == 9000


def test_firefox_profile_detection_and_check(tmp_path, monkeypatch):
    root = tmp_path / "ff"
    prof = root / "abc.default-release"
    prof.mkdir(parents=True)
    (root / "profiles.ini").write_text("[Profile0]\nName=default-release\nIsRelative=1\nPath=abc.default-release\n")
    db = sqlite3.connect(prof / "cookies.sqlite")
    db.execute("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT DEFAULT '', name TEXT,"
               " value TEXT, host TEXT, path TEXT, expiry INTEGER, lastAccessed INTEGER, creationTime INTEGER,"
               " isSecure INTEGER, isHttpOnly INTEGER, inBrowserElement INTEGER DEFAULT 0, sameSite INTEGER DEFAULT 0,"
               " schemeMap INTEGER DEFAULT 0)")
    for name, host in [("SAPISID", ".youtube.com"), ("PREF", ".youtube.com"), ("x", ".example.org")]:
        db.execute("INSERT INTO moz_cookies (name, value, host, path, expiry, lastAccessed, creationTime, isSecure, isHttpOnly)"
                   " VALUES (?, 'v', ?, '/', 4102444800, 0, 0, 1, 0)", (name, host))
    db.commit()
    db.close()

    found = accounts.firefox_profiles([root])
    assert len(found) == 1 and found[0].name == "default-release"

    res = accounts.check_account(Account("me", "firefox", profile=found[0].path, sites=["youtube.com"]))
    assert res.ok, res.error
    assert res.total == 3
    assert res.per_site["youtube.com"] == (2, True)
    assert "logged in" in res.summary()


def test_chromium_profile_detection(tmp_path):
    root = tmp_path / "chrome"
    (root / "Default" / "Network").mkdir(parents=True)
    (root / "Default" / "Network" / "Cookies").write_bytes(b"")
    (root / "Profile 2").mkdir()
    (root / "Profile 2" / "Cookies").write_bytes(b"")
    (root / "Local State").write_text(json.dumps({"profile": {"info_cache": {"Default": {"name": "Personal"},
                                                                             "Profile 2": {"name": "Work"}}}}))
    found = accounts.chromium_profiles("chrome", [root])
    assert [p.name for p in found] == ["Personal", "Work"]


def test_cookie_error_hints():
    msg = accounts.explain_cookie_error("Could not copy Chrome cookie database", "chrome")
    assert "Close Google Chrome" in msg
