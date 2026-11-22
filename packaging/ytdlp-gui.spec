# PyInstaller spec — build with:  pyinstaller packaging/ytdlp-gui.spec
# Produces dist/ytdlp-gui/ (onedir). Put ffmpeg(.exe) in dist/ytdlp-gui/bin/ to bundle it.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("yt_dlp", "yt_dlp_ejs"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass
hiddenimports += ["secretstorage", "Cryptodome", "websockets", "brotli", "certifi"]

a = Analysis(
    ["launcher.py"],
    pathex=["../src"],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml",
              "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ytdlp-gui",
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ytdlp-gui", upx=False)
