from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

from tests.trafficlab_dashboard.support.dashboard_fixtures import copy_checked_dashboard_run
from trafficlab_dashboard.app import create_window
from trafficlab_dashboard.window import DashboardWindow


def test_create_window_returns_dashboard_window_with_title_and_initial_directory(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = create_window(tmp_path)
    qtbot.addWidget(window)

    assert type(window) is DashboardWindow
    assert window.initial_run_directory == tmp_path
    assert window.windowTitle() == "TrafficLab Dashboard"
    assert window.centralWidget() is not None


def test_window_show_with_cli_path_schedules_run_load(qtbot: QtBot, tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    window = create_window(run_directory)
    qtbot.addWidget(window)

    window.show()
    qtbot.waitUntil(lambda: window.state.run is not None and window.canvas.current_aspect == "throughput")

    assert window.state.run is not None
    assert window.state.run.directory == run_directory
    assert window.aspect_combo.currentData() == "throughput"


def test_window_show_without_cli_path_opens_directory_chooser_and_cancel_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    del tmp_path
    calls: list[tuple[str, str]] = []

    def choose_directory(*args: object, **kwargs: object) -> str:
        del kwargs
        calls.append((str(args[1]), str(args[2])))
        return ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose_directory)
    window = create_window(None)
    qtbot.addWidget(window)

    window.show()
    qtbot.waitUntil(lambda: len(calls) == 1)

    assert calls == [("Open TrafficLab Run", str(Path.cwd()))]
    assert window.state.run is None
    assert window.statusBar().currentMessage() == "No run loaded"
