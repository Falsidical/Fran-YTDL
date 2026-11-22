import sys

from PySide6.QtWidgets import QApplication

from . import APP_NAME


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("yt-dlp GUI")
    from .ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1:
        w.url.setText(" ".join(sys.argv[1:]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
