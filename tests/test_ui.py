from ytdlp_gui.config import Account, Settings
from ytdlp_gui.ui.accounts_dialog import AccountsDialog
from ytdlp_gui.ui.main_window import MainWindow
from ytdlp_gui.ui.settings_dialog import SettingsDialog


def test_main_window_auto_selects_account(qtbot, tmp_path):
    s = Settings(output_dir=str(tmp_path), accounts=[Account("YT", "firefox", sites=["youtube.com"])])
    w = MainWindow(s)
    qtbot.addWidget(w)
    assert w.account.count() == 2
    w.url.setText("https://www.youtube.com/watch?v=abc")
    assert w.account.currentData() == "YT"
    w.account.setCurrentIndex(0)
    w._update_hint()
    assert "premium" in w.hint.text().lower()


def test_probe_result_populates_table(qtbot, tmp_path):
    w = MainWindow(Settings(output_dir=str(tmp_path)))
    qtbot.addWidget(w)
    w._probe_done({"title": "T", "duration": 61, "formats": [
        {"format_id": "140", "acodec": "mp4a.40.2", "vcodec": "none", "tbr": 129},
        {"format_id": "616", "vcodec": "vp09", "acodec": "none", "height": 1080, "width": 1920, "tbr": 9000,
         "format_note": "Premium"},
        {"format_id": "137", "vcodec": "avc1", "acodec": "none", "height": 1080, "width": 1920, "tbr": 4000},
    ], "webpage_url": "https://youtu.be/x"})
    assert w.formats.rowCount() == 3
    assert w.formats.item(0, 0).text() == "616"  # highest-bitrate video first
    assert "premium" in w.info.text().lower()
    w.formats.selectRow(0)
    assert "616" in w.sel_label.text()


def test_dialogs_open(qtbot):
    d = AccountsDialog([Account("A", "firefox")])
    qtbot.addWidget(d)
    d._add()
    assert len(d.accounts) == 2
    d.name.setText("Renamed")
    d.name.textEdited.emit("Renamed")
    assert d.accounts[1].name == "Renamed"
    sd = SettingsDialog(Settings())
    qtbot.addWidget(sd)
    sd.concurrent.setValue(5)
    sd._accept()
    assert sd.result_settings().max_concurrent == 5


def test_tools_banner(qtbot, tmp_path, monkeypatch):
    from ytdlp_gui import tools
    missing = ["ffmpeg", "deno"]
    monkeypatch.setattr(tools, "missing_tools", lambda *_a: list(missing))
    w = MainWindow(Settings(output_dir=str(tmp_path)))
    qtbot.addWidget(w)
    assert not w.tools_banner.isHidden()
    assert "ffmpeg" in w.tools_label.text() and "Deno" in w.tools_label.text()
    missing.clear()            # simulate a successful install
    w._tools_all_done()
    assert w.tools_banner.isHidden()


def test_tools_banner_dismissed(qtbot, tmp_path, monkeypatch):
    from ytdlp_gui import tools
    monkeypatch.setattr(tools, "missing_tools", lambda *_a: ["deno"])
    w = MainWindow(Settings(output_dir=str(tmp_path)))
    qtbot.addWidget(w)
    w._dismiss_tools()
    assert w.tools_banner.isHidden() and w.settings.tools_prompt_dismissed
    sd = SettingsDialog(w.settings)
    qtbot.addWidget(sd)
    assert set(sd._tool_labels) == {"ffmpeg", "deno"}
