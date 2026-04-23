from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.config import load_settings
from core.logging import configure_logging
from ui.main_window import MainWindow


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    settings = load_settings()
    configure_logging(settings)

    app = QApplication(sys.argv)
    app.setApplicationName(settings.app.name)
    window = MainWindow(settings)
    window.resize(1480, 900)
    window.show()
    QTimer.singleShot(1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
