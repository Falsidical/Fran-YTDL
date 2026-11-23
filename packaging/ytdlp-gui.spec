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

# Drop Qt plugins the app doesn't use, along with the large libraries they pull in:
# the virtual keyboard (QtQuick/QML), the PDF image format (QtPdf), and the GTK3 platform theme
# (host GTK/ICU/glycin libs, which also hurt portability across distros).
_DROP = ("libqtvirtualkeyboardplugin", "libqpdf", "libqgtk3", "libQt6Quick", "libQt6Qml",
         "libQt6Pdf", "libQt6VirtualKeyboard", "libgtk-3", "libgdk-3", "libglycin")


def _keep(dest):
    dest = dest.replace("\\", "/")
    name = dest.rsplit("/", 1)[-1]
    if any(name.startswith(p) for p in _DROP):
        return False
    # Host ICU at the top level only came in through GTK; Qt keeps its own copy in PySide6/Qt/lib.
    return not ("/" not in dest and name.startswith("libicu"))


a.binaries = [b for b in a.binaries if _keep(b[0])]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ytdlp-gui",
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ytdlp-gui", upx=False)
