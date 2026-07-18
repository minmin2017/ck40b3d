"""Entry point: python -m ck40b_sim"""
import sys
from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def main():
    # Force Western (Arabic) digits everywhere — on Thai-locale Windows the
    # default QLocale renders spin-box and line-number digits in Thai script,
    # which is hard to read against G-code.
    QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
    app = QApplication(sys.argv)
    apply_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
