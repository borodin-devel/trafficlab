from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from trafficlab_dashboard.app import create_window


def test_dashboard_shell_accepts_run_path_and_shows_one_window(qtbot: QtBot, tmp_path: Path) -> None:
    """The desktop shell must publish its title and central widget for the first launch."""
    window = create_window(tmp_path)
    qtbot.addWidget(window)
    window.show()

    assert window.property("initial_run_directory") == tmp_path
    assert window.windowTitle() == "TrafficLab Dashboard"
    assert window.centralWidget() is not None
