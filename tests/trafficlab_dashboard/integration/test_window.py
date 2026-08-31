from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from tests.trafficlab_dashboard.support.dashboard_fixtures import (
    copy_checked_dashboard_run,
    write_complete_dashboard_run,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab_dashboard import window as window_module
from trafficlab_dashboard.run_data import DashboardRun
from trafficlab_dashboard.window import DashboardWindow


def _select_aspect(qtbot: QtBot, window: DashboardWindow, aspect_id: str) -> None:
    index = window.aspect_combo.findData(aspect_id)
    assert index >= 0
    window.aspect_combo.setCurrentIndex(index)
    qtbot.waitUntil(lambda: window.canvas.current_aspect == aspect_id)


def _loaded_window(qtbot: QtBot, run_directory: Path) -> DashboardWindow:
    window = DashboardWindow(initial_run_directory=run_directory)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.state.run is not None and window.canvas.current_aspect == "throughput")
    return window


def _widget_at(window: DashboardWindow, index: int) -> QWidget:
    item = window.controls_layout.itemAt(index)
    assert item is not None
    widget = item.widget()
    assert widget is not None
    return widget


def test_window_control_row_keeps_the_specified_order(qtbot: QtBot, tmp_path: Path) -> None:
    window = _loaded_window(qtbot, copy_checked_dashboard_run(tmp_path))

    names = [
        _widget_at(window, index).objectName()
        for index in range(window.controls_layout.count())
    ]

    assert names == [
        "open_run_button",
        "aspect_combo",
        "reference_button",
        "generated_button",
        "reset_button",
        "export_button",
    ]


def test_trace_buttons_prevent_empty_trace_plot(qtbot: QtBot, tmp_path: Path) -> None:
    window = _loaded_window(qtbot, copy_checked_dashboard_run(tmp_path))

    QTest.mouseClick(window.reference_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(window.generated_button, Qt.MouseButton.LeftButton)

    assert window.reference_button.isChecked() is False
    assert window.generated_button.isChecked() is True
    assert "At least one trace" in window.statusBar().currentMessage()


def test_pair_aspect_disables_trace_buttons_without_forgetting_stored_visibility(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = _loaded_window(qtbot, copy_checked_dashboard_run(tmp_path))
    QTest.mouseClick(window.reference_button, Qt.MouseButton.LeftButton)

    _select_aspect(qtbot, window, "similarity_scores")

    assert window.reference_button.isEnabled() is False
    assert window.generated_button.isEnabled() is False

    _select_aspect(qtbot, window, "throughput")

    assert window.reference_button.isEnabled() is True
    assert window.generated_button.isEnabled() is True
    assert window.reference_button.isChecked() is False
    assert window.generated_button.isChecked() is True


def test_unavailable_registry_entries_stay_visible_but_disabled_with_exact_reason(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    run_directory = write_complete_dashboard_run(tmp_path)
    (run_directory / "similarity.json").unlink()
    window = _loaded_window(qtbot, run_directory)

    similarity_index = window.aspect_combo.findData("similarity_scores")
    assert similarity_index >= 0
    model = cast(QStandardItemModel, window.aspect_combo.model())
    item = model.item(similarity_index)

    assert item.isEnabled() is False
    assert item.toolTip() == "similarity.json is missing"
    assert window.aspect_combo.findData("similarity_scores") == similarity_index


def test_aspect_change_resets_viewport_but_visibility_only_redraw_preserves_it(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = _loaded_window(qtbot, copy_checked_dashboard_run(tmp_path))
    window.canvas.axes.set_xlim(0.2, 0.8)
    window.canvas.axes.set_ylim(0.1, 0.4)

    _select_aspect(qtbot, window, "packet_rate")

    assert window.canvas.axes.get_xlim() != (0.2, 0.8)
    assert window.canvas.axes.get_ylim() != (0.1, 0.4)

    window.canvas.axes.set_xlim(0.15, 0.55)
    window.canvas.axes.set_ylim(0.0, 0.9)
    QTest.mouseClick(window.reference_button, Qt.MouseButton.LeftButton)

    assert window.canvas.current_aspect == "packet_rate"
    assert window.canvas.axes.get_xlim() == (0.15, 0.55)
    assert window.canvas.axes.get_ylim() == (0.0, 0.9)


def test_failed_second_load_preserves_previous_valid_run_and_plot(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    first_run = copy_checked_dashboard_run(tmp_path / "first")
    window = _loaded_window(qtbot, first_run)
    errors: list[tuple[str, str]] = []

    def record_error(parent: object, title: str, text: str) -> int:
        del parent
        errors.append((title, text))
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "critical", record_error)
    bad_run = tmp_path / "bad"
    bad_run.mkdir()
    window.open_run(bad_run)
    qtbot.waitUntil(lambda: window.state.loading_run is False)

    assert window.state.run is not None
    assert window.state.run.directory == first_run
    assert window.canvas.current_aspect == "throughput"
    assert errors
    assert "invalid required artifact capture.json" in errors[-1][1]


def test_window_export_flow_uses_selected_format_and_suffix(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = _loaded_window(qtbot, copy_checked_dashboard_run(tmp_path / "run"))
    recorded: list[tuple[Path, str]] = []

    def choose_export_file(*args: object, **kwargs: object) -> tuple[str, str]:
        del args, kwargs
        return str(tmp_path / "exported-plot"), "SVG (*.svg)"

    monkeypatch.setattr(window_module.QFileDialog, "getSaveFileName", choose_export_file)

    def export_stub(figure: object, destination: Path, format: str) -> None:
        del figure
        recorded.append((destination, format))

    window.exporter = export_stub
    QTest.mouseClick(window.export_button, Qt.MouseButton.LeftButton)

    assert recorded == [(tmp_path / "exported-plot.svg", "svg")]


def test_load_failure_dialog_includes_corrective_action(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    window = _loaded_window(qtbot, copy_checked_dashboard_run(tmp_path / "run"))
    messages: list[str] = []

    def raising_loader(path: Path) -> DashboardRun:
        raise TrafficlabError(
            f"could not read required artifact {path / 'capture.json'}",
            corrective_action="verify capture.json exists and is readable",
        )

    def record_error(parent: object, title: str, text: str) -> int:
        del parent, title
        messages.append(text)
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "critical", record_error)
    window.loader = raising_loader
    broken = tmp_path / "broken"
    broken.mkdir()

    window.open_run(broken)
    qtbot.waitUntil(lambda: window.state.loading_run is False)

    assert any("verify capture.json exists and is readable" in message for message in messages)
