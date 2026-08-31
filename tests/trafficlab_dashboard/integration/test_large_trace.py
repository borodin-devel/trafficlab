from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from tests.trafficlab_dashboard.support.dashboard_fixtures import copy_checked_dashboard_run
from trafficlab.common.trace import TrafficTrace
from trafficlab_dashboard.aspects.base import CalculationSettings, LinePlotData, TraceVisibility
from trafficlab_dashboard.aspects.time_domain import FrameSizeTimelineAspect
from trafficlab_dashboard.run_data import DashboardRun
from trafficlab_dashboard.run_loader import load_dashboard_run
from trafficlab_dashboard.window import DashboardWindow

_PACKET_COUNT = 200_000
_LOAD_SECONDS_LIMIT = 15.0
_CACHED_REDRAW_SECONDS_LIMIT = 2.0


def _dense_times(*, count: int, offset: float) -> np.ndarray:
    return np.linspace(offset, offset + 199.999, num=count, dtype=np.float64)


def _large_trace(*, offset: float) -> TrafficTrace:
    timestamps = _dense_times(count=_PACKET_COUNT, offset=offset)
    directions = (np.arange(_PACKET_COUNT, dtype=np.uint8) % 2).astype(np.uint8)
    frame_lengths = np.where(np.arange(_PACKET_COUNT) % 2 == 0, 64, 1514).astype(np.uint32)
    return TrafficTrace(timestamps=timestamps, directions=directions, frame_lengths=frame_lengths)


def _large_run(root: Path) -> DashboardRun:
    template = load_dashboard_run(copy_checked_dashboard_run(root / "checked"))
    directory = root / "large-run"
    directory.mkdir()
    reference = _large_trace(offset=10.0)
    generated = _large_trace(offset=1_000.0)
    return replace(
        template,
        directory=directory,
        reference=reference,
        generated=generated,
        window=float(reference.timestamps[-1] - reference.timestamps[0]),
    )


@dataclass(slots=True)
class _CountingFrameSizeTimelineAspect:
    identifier: str = "frame_size_timeline"
    label: str = "Frame size versus time"
    category: str = "Time domain"
    trace_controls: bool = True
    calls: int = 0

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        self.calls += 1
        return FrameSizeTimelineAspect().calculate(run, settings)


def test_large_trace_calculates_full_totals_but_bounds_display(tmp_path: Path) -> None:
    aspect = FrameSizeTimelineAspect()
    data = aspect.calculate(_large_run(tmp_path), CalculationSettings.default())

    assert data.reference_sample_count == _PACKET_COUNT
    assert data.generated_sample_count == _PACKET_COUNT
    assert len(data.series[0].x) <= 20_000
    assert len(data.series[1].x) <= 20_000


def test_large_trace_window_stays_responsive_and_visibility_redraw_uses_cache(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    aspect = _CountingFrameSizeTimelineAspect()
    run = _large_run(tmp_path)
    window = DashboardWindow(
        initial_run_directory=run.directory,
        aspects=(aspect,),
        loader=lambda _: run,
    )
    qtbot.addWidget(window)
    ticks = [0]
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: ticks.__setitem__(0, ticks[0] + 1))

    start = perf_counter()
    heartbeat.start()
    window.show()
    qtbot.waitUntil(
        lambda: window.state.run is not None and window.canvas.current_aspect == "frame_size_timeline",
        timeout=20_000,
    )
    heartbeat.stop()
    load_elapsed = perf_counter() - start
    current_plot = window._current_plot  # pyright: ignore[reportPrivateUsage]

    assert load_elapsed < _LOAD_SECONDS_LIMIT
    assert ticks[0] > 0
    assert isinstance(current_plot, LinePlotData)
    assert current_plot.reference_sample_count == _PACKET_COUNT
    assert current_plot.generated_sample_count == _PACKET_COUNT
    assert len(current_plot.series[0].x) <= 20_000
    assert len(current_plot.series[1].x) <= 20_000
    assert aspect.calls == 1

    redraw_started = perf_counter()
    QTest.mouseClick(window.reference_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: (
            window.state.visibility == TraceVisibility(reference=False, generated=True)
            and len(window.canvas.axes.lines) == 1
        )
    )
    redraw_elapsed = perf_counter() - redraw_started

    assert redraw_elapsed < _CACHED_REDRAW_SECONDS_LIMIT
    assert aspect.calls == 1
