from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import cast

import numpy as np
import pytest
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace
from trafficlab_dashboard.aspects.base import CalculationSettings, LinePlotData, LineSeries, TraceVisibility
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun
from trafficlab_dashboard.state import begin_aspect_request, begin_run_load
from trafficlab_dashboard.window import DashboardWindow
from trafficlab_dashboard.workers import (
    CalculateAspectFailure,
    CalculateAspectSuccess,
    LoadRunFailure,
    LoadRunSuccess,
)


def _trace(*timestamps: float) -> TrafficTrace:
    return TrafficTrace.from_events(
        tuple(
            TraceEvent(
                timestamp=float(timestamp),
                direction=Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
                frame_length=120 + index,
            )
            for index, timestamp in enumerate(timestamps)
        )
    )


def _run(directory: Path, *, suffix: str, unavailable: dict[str, str] | None = None) -> DashboardRun:
    return DashboardRun(
        directory=directory,
        identities=ArtifactIdentities(
            reference_sha256=("1" if suffix == "a" else "4") * 64,
            generated_sha256=("2" if suffix == "a" else "5") * 64,
            capture_sha256=("3" if suffix == "a" else "6") * 64,
            similarity_sha256=None,
            best_model_sha256=None,
            history_sha256=None,
        ),
        metadata=CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:10"),
        reference=_trace(0.0, 1.0, 3.0),
        generated=_trace(0.0, 1.0),
        window=3.0,
        similarity=None,
        best_model=None,
        history=None,
        experiment=None,
        unavailable=MappingProxyType({} if unavailable is None else unavailable),
    )


def _plot(identifier: str) -> LinePlotData:
    return LinePlotData(
        identifier=identifier,
        label=identifier.replace("_", " ").title(),
        title=identifier,
        x_label="Time (s)",
        y_label="Rate",
        unit="arb.",
        series=(
            LineSeries(
                label="Reference",
                x=np.array([0.0, 1.0], dtype=np.float64),
                y=np.array([1.0, 2.0], dtype=np.float64),
                sample_count=3,
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
        reference_sample_count=3,
        generated_sample_count=2,
    )


class _CountingAspect:
    category = "Tests"
    trace_controls = True

    def __init__(self, identifier: str) -> None:
        self.identifier = identifier
        self.label = identifier.title()
        self.calls = 0

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        del run, settings
        self.calls += 1
        return _plot(self.identifier)


class _RecordingThreadPool:
    def __init__(self) -> None:
        self.started: list[object] = []
        self.cleared = False

    def start(self, worker: object) -> None:
        self.started.append(worker)

    def clear(self) -> None:
        self.cleared = True


def _window(qtbot: QtBot, *aspects: _CountingAspect) -> DashboardWindow:
    window = DashboardWindow(initial_run_directory=None, aspects=aspects)
    window._initial_action_pending = False  # pyright: ignore[reportPrivateUsage]
    qtbot.addWidget(window)
    window.show()
    return window


def test_stale_worker_result_cannot_replace_newer_aspect(qtbot: QtBot, tmp_path: Path) -> None:
    aspect = _CountingAspect("throughput")
    window = _window(qtbot, aspect)
    run = _run(tmp_path / "run-a", suffix="a")
    window.state = begin_run_load(window.state, run.directory)
    window.accept_load(LoadRunSuccess(token=window.state.generation, directory=run.directory, run=run))
    qtbot.waitUntil(lambda: window.canvas.current_aspect == "throughput")
    stale_token = window.state.generation
    window.state = begin_aspect_request(window.state, "other")

    window.accept_calculation(
        CalculateAspectSuccess(token=stale_token, aspect_id="throughput", data=_plot("throughput"))
    )

    assert window.state.selected_aspect == "throughput"
    assert window.state.requested_aspect == "other"
    assert window.canvas.current_aspect == "throughput"


def test_stale_failure_does_not_show_dialog(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    aspect = _CountingAspect("throughput")
    window = _window(qtbot, aspect)
    run = _run(tmp_path / "run-a", suffix="a")
    window.state = begin_run_load(window.state, run.directory)
    window.accept_load(LoadRunSuccess(token=window.state.generation, directory=run.directory, run=run))
    stale_token = window.state.generation
    window.state = begin_run_load(window.state, tmp_path / "run-b")
    calls: list[str] = []

    def record_dialog(parent: object, title: str, text: str) -> int:
        del parent, title
        calls.append(text)
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "critical", record_dialog)

    window.accept_load(LoadRunFailure(token=stale_token, directory=run.directory, error=RuntimeError("old failure")))

    assert calls == []


def test_visibility_redraw_uses_cached_plot_without_recalculation(qtbot: QtBot, tmp_path: Path) -> None:
    aspect = _CountingAspect("throughput")
    window = _window(qtbot, aspect)
    run = _run(tmp_path / "run-a", suffix="a")
    window.state = begin_run_load(window.state, run.directory)
    window.accept_load(LoadRunSuccess(token=window.state.generation, directory=run.directory, run=run))
    qtbot.waitUntil(lambda: window.canvas.current_aspect == "throughput")
    calls_after_initial_render = aspect.calls

    QTest.mouseClick(window.reference_button, Qt.MouseButton.LeftButton)

    assert window.state.visibility == TraceVisibility(reference=False, generated=True)
    assert aspect.calls == calls_after_initial_render


def test_cache_clears_only_on_matching_successful_run_replacement(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    aspect = _CountingAspect("throughput")
    window = _window(qtbot, aspect)
    run_a = _run(tmp_path / "run-a", suffix="a")
    run_b = _run(tmp_path / "run-b", suffix="b")
    window.state = begin_run_load(window.state, run_a.directory)
    window.accept_load(LoadRunSuccess(token=window.state.generation, directory=run_a.directory, run=run_a))
    qtbot.waitUntil(lambda: window.canvas.current_aspect == "throughput")
    assert len(window.cache) == 1
    cached_key = window.cache.keys()[0]

    def ignore_dialog(parent: object, title: str, text: str) -> int:
        del parent, title, text
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "critical", ignore_dialog)
    window.state = begin_run_load(window.state, tmp_path / "broken")
    window.accept_load(
        LoadRunFailure(token=window.state.generation, directory=tmp_path / "broken", error=RuntimeError("broken"))
    )
    assert window.cache.get(cached_key) is not None

    window.state = begin_run_load(window.state, run_b.directory)
    replacement_token = window.state.generation
    window.accept_load(LoadRunSuccess(token=replacement_token, directory=run_b.directory, run=run_b))

    assert window.state.run is not None
    assert window.state.run.directory == run_a.directory
    assert window.state.pending_run is not None
    assert window.state.pending_run.directory == run_b.directory
    assert window.cache.get(cached_key) is not None

    window.accept_calculation(
        CalculateAspectSuccess(token=replacement_token, aspect_id="throughput", data=_plot("run-b"))
    )

    assert window.state.run is not None
    assert window.state.run.directory == run_b.directory
    assert window.state.pending_run is None
    assert len(window.cache) == 1
    assert window.cache.get(cached_key) is None


def test_direct_slot_replacement_failure_keeps_the_accepted_run_and_cache(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    aspect = _CountingAspect("throughput")
    thread_pool = _RecordingThreadPool()
    window = DashboardWindow(
        initial_run_directory=None,
        aspects=(aspect,),
        thread_pool=cast(QThreadPool, thread_pool),
    )
    window._initial_action_pending = False  # pyright: ignore[reportPrivateUsage]
    qtbot.addWidget(window)
    window.show()
    run_a = _run(tmp_path / "run-a", suffix="a")
    run_b = _run(tmp_path / "run-b", suffix="b")

    window.state = begin_run_load(window.state, run_a.directory)
    window.accept_load(LoadRunSuccess(token=window.state.generation, directory=run_a.directory, run=run_a))
    first_token = window.state.generation
    assert len(thread_pool.started) == 1
    window.accept_calculation(CalculateAspectSuccess(token=first_token, aspect_id="throughput", data=_plot("run-a")))
    assert window.state.run is not None
    assert window.state.run.directory == run_a.directory
    cached_key = window.cache.keys()[0]

    window.state = begin_run_load(window.state, run_b.directory)
    replacement_token = window.state.generation
    window.accept_load(LoadRunSuccess(token=replacement_token, directory=run_b.directory, run=run_b))

    assert window.state.run is not None
    assert window.state.run.directory == run_a.directory
    assert window.state.pending_run is not None
    assert window.cache.get(cached_key) is not None

    def ignore_dialog(parent: object, title: str, text: str) -> int:
        del parent, title, text
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "critical", ignore_dialog)
    window.accept_calculation(
        CalculateAspectFailure(
            token=replacement_token, aspect_id="throughput", error=RuntimeError("replacement failed")
        )
    )

    assert window.state.run is not None
    assert window.state.run.directory == run_a.directory
    assert window.state.pending_run is None
    assert window.cache.get(cached_key) is not None


def test_stale_replacement_plot_cannot_commit_after_a_newer_run_request(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    aspect = _CountingAspect("throughput")
    thread_pool = _RecordingThreadPool()
    window = DashboardWindow(
        initial_run_directory=None,
        aspects=(aspect,),
        thread_pool=cast(QThreadPool, thread_pool),
    )
    window._initial_action_pending = False  # pyright: ignore[reportPrivateUsage]
    qtbot.addWidget(window)
    window.show()
    run_a = _run(tmp_path / "run-a", suffix="a")
    run_b = _run(tmp_path / "run-b", suffix="b")
    run_c = _run(tmp_path / "run-c", suffix="c")

    window.state = begin_run_load(window.state, run_a.directory)
    window.accept_load(LoadRunSuccess(token=window.state.generation, directory=run_a.directory, run=run_a))
    accepted_token = window.state.generation
    window.accept_calculation(CalculateAspectSuccess(token=accepted_token, aspect_id="throughput", data=_plot("run-a")))

    window.state = begin_run_load(window.state, run_b.directory)
    b_token = window.state.generation
    window.accept_load(LoadRunSuccess(token=b_token, directory=run_b.directory, run=run_b))
    assert window.state.pending_run is not None

    QTest.mouseClick(window.reference_button, Qt.MouseButton.LeftButton)
    assert window.state.run is not None
    assert window.state.run.directory == run_a.directory

    window.state = begin_run_load(window.state, run_c.directory)
    c_token = window.state.generation
    window.accept_load(LoadRunSuccess(token=c_token, directory=run_c.directory, run=run_c))

    window.accept_calculation(CalculateAspectSuccess(token=b_token, aspect_id="throughput", data=_plot("run-b")))

    assert window.state.run is not None
    assert window.state.run.directory == run_a.directory
    assert window.state.pending_run is not None
    assert window.state.pending_run.directory == run_c.directory

    window.accept_calculation(CalculateAspectSuccess(token=c_token, aspect_id="throughput", data=_plot("run-c")))

    assert window.state.run is not None
    assert window.state.run.directory == run_c.directory
    assert window.state.pending_run is None


def test_shutdown_invalidates_late_results_before_they_touch_the_canvas(qtbot: QtBot, tmp_path: Path) -> None:
    aspect = _CountingAspect("throughput")
    window = _window(qtbot, aspect)
    run = _run(tmp_path / "run-a", suffix="a")
    window.state = begin_run_load(window.state, run.directory)
    window.accept_load(LoadRunSuccess(token=window.state.generation, directory=run.directory, run=run))
    qtbot.waitUntil(lambda: window.canvas.current_aspect == "throughput")
    prior_generation = window.state.generation

    window.close()
    window.accept_calculation(
        CalculateAspectFailure(token=prior_generation, aspect_id="throughput", error=RuntimeError("late failure"))
    )
    window.accept_calculation(
        CalculateAspectSuccess(token=prior_generation, aspect_id="throughput", data=_plot("throughput"))
    )

    assert window.state.generation == prior_generation + 1
    assert window.canvas.current_aspect == "throughput"
