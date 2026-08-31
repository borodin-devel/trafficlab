from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trafficlab-dashboard")
    parser.add_argument("run_directory", nargs="?", type=Path)
    return parser


def create_window(initial_path: Path | None = None) -> QMainWindow:
    window = QMainWindow()
    window.setWindowTitle("TrafficLab Dashboard")
    window.resize(1200, 760)
    window.setCentralWidget(QWidget())
    window.setProperty("initial_run_directory", initial_path)
    return window


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = create_window(arguments.run_directory)
    window.show()
    return application.exec()
