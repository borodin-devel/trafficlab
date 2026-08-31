from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import cast

import numpy as np
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
from trafficlab_dashboard.aspects.base import CalculationSettings, LinePlotData, LineSeries
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun
from trafficlab_dashboard.run_loader import load_dashboard_run
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


def _named_checked_run(root: Path, name: str) -> Path:
    copied = copy_checked_dashboard_run(root)
    named = root.parent / name
    copied.rename(named)
    return named


def _retag_run(run: DashboardRun, directory: Path, *, seed: str) -> DashboardRun:
    return replace(
        run,
        directory=directory,
        identities=ArtifactIdentities(
            reference_sha256=(seed + "1") * 32,
            generated_sha256=(seed + "2") * 32,
            capture_sha256=(seed + "3") * 32,
            similarity_sha256=None,
            best_model_sha256=None,
            history_sha256=None,
        ),
    )


class _ReplacementAspect:
    identifier = "throughput"
    label = "Throughput"
    category = "Tests"
    trace_controls = True

    def __init__(self, *, gate: Event, started: Event) -> None:
        self._gate = gate
        self._started = started
        self.calls = 0

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        del settings
        self.calls += 1
        if self.calls == 2:
            self._started.set()
            if not self._gate.wait(timeout=5.0):
                raise RuntimeError("replacement plot gate timed out")
            raise RuntimeError(f"first replacement plot failed for {run.directory.name}")
        return LinePlotData(
            identifier=self.identifier,
            label=self.label,
            title=f"{run.directory.name} throughput",
            x_label="Time (s)",
            y_label="Rate",
            unit="arb.",
            series=(
                LineSeries(
                    label="Reference",
                    x=np.array([0.0, 1.0], dtype=np.float64),
                    y=np.array([1.0, 2.0], dtype=np.float64),
                    sample_count=2,
                    dataset="reference",
                ),
                LineSeries(
                    label="Generated",
                    x=np.array([0.0, 1.0], dtype=np.float64),
                    y=np.array([0.5, 1.5], dtype=np.float64),
                    sample_count=2,
                    dataset="generated",
                ),
            ),
            x_limits=(0.0, 1.0),
            y_limits=(0.0, 2.0),
            reference_sample_count=2,
            generated_sample_count=2,
        )


def test_window_control_row_keeps_the_specified_order(qtbot: QtBot, tmp_path: Path) -> None:
    window = _loaded_window(qtbot, copy_checked_dashboard_run(tmp_path))

    names = [_widget_at(window, index).objectName() for index in range(window.controls_layout.count())]

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


def test_replacement_run_failure_keeps_previous_run_plot_cache_and_controls_live(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    template_a = load_dashboard_run(_named_checked_run(tmp_path / "copy-a", "run-a-source"))
    template_b = load_dashboard_run(_named_checked_run(tmp_path / "copy-b", "run-b-source"))
    run_a = _retag_run(template_a, tmp_path / "run-a", seed="a")
    run_b = _retag_run(template_b, tmp_path / "run-b", seed="d")
    gate = Event()
    started = Event()
    aspect = _ReplacementAspect(gate=gate, started=started)
    exports: list[Path] = []
    errors: list[str] = []

    def choose_export_file(*args: object, **kwargs: object) -> tuple[str, str]:
        del args, kwargs
        return str(tmp_path / "pending-export"), "PNG (*.png)"

    def export_stub(figure: object, destination: Path, format: str) -> None:
        del figure, format
        exports.append(destination)

    def loader(path: Path) -> DashboardRun:
        if path == run_a.directory:
            return run_a
        if path == run_b.directory:
            return run_b
        raise AssertionError(path)

    def record_error(parent: object, title: str, text: str) -> int:
        del parent, title
        errors.append(text)
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(window_module.QFileDialog, "getSaveFileName", choose_export_file)
    monkeypatch.setattr(QMessageBox, "critical", record_error)
    window = DashboardWindow(initial_run_directory=None, aspects=(aspect,), loader=loader, exporter=export_stub)
    window._initial_action_pending = False  # pyright: ignore[reportPrivateUsage]
    qtbot.addWidget(window)
    window.show()

    window.open_run(run_a.directory)
    qtbot.waitUntil(lambda: window.state.run is not None and window.state.run.directory == run_a.directory)
    qtbot.waitUntil(lambda: window.canvas.axes.get_title() == "run-a throughput")
    original_cache_key = window.cache.keys()[0]

    window.canvas.axes.set_xlim(0.25, 0.75)
    QTest.mouseClick(window.export_button, Qt.MouseButton.LeftButton)
    assert exports == [tmp_path / "pending-export.png"]

    window.open_run(run_b.directory)
    qtbot.waitUntil(started.is_set)

    assert window.state.run is not None
    assert window.state.run.directory == run_a.directory
    assert window.state.pending_run is not None
    assert window.state.pending_run.directory == run_b.directory
    assert window.statusBar().currentMessage().startswith("run-a")
    assert window.canvas.axes.get_title() == "run-a throughput"
    assert window.cache.get(original_cache_key) is not None
    assert window.reset_button.isEnabled() is True
    assert window.export_button.isEnabled() is True
    assert window.progress_overlay.isVisible() is True

    QTest.mouseClick(window.reset_button, Qt.MouseButton.LeftButton)
    assert window.canvas.axes.get_xlim() == pytest.approx((0.0, 1.0))

    gate.set()
    qtbot.waitUntil(lambda: not window.state.calculating and window.state.pending_run is None)

    assert window.state.run is not None
    assert window.state.run.directory == run_a.directory
    assert window.statusBar().currentMessage().startswith("run-a")
    assert window.canvas.axes.get_title() == "run-a throughput"
    assert window.cache.get(original_cache_key) is not None
    assert window.reset_button.isEnabled() is True
    assert window.export_button.isEnabled() is True
    assert errors
    assert "first replacement plot failed for run-b" in errors[-1]
