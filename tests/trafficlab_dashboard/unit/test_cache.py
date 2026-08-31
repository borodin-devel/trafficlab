from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest

from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace
from trafficlab_dashboard.aspects.base import CalculationSettings, LinePlotData, LineSeries, TraceVisibility
from trafficlab_dashboard.cache import AspectCache
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun


def _trace(*timestamps: float) -> TrafficTrace:
    return TrafficTrace.from_events(
        tuple(
            TraceEvent(
                timestamp=float(timestamp),
                direction=Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
                frame_length=100 + index,
            )
            for index, timestamp in enumerate(timestamps)
        )
    )


def _run() -> DashboardRun:
    return DashboardRun(
        directory=Path.cwd() / "run",
        identities=ArtifactIdentities(
            reference_sha256="1" * 64,
            generated_sha256="2" * 64,
            capture_sha256="3" * 64,
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
        unavailable=MappingProxyType({}),
    )


def _plot_data() -> LinePlotData:
    return LinePlotData(
        identifier="throughput",
        label="Throughput",
        title="Throughput",
        x_label="Time (s)",
        y_label="Rate",
        unit="Mbps",
        series=(
            LineSeries(
                label="Reference",
                x=np.array([0.0, 1.0], dtype=np.float64),
                y=np.array([0.25, 0.5], dtype=np.float64),
                sample_count=3,
                dataset="reference",
            ),
            LineSeries(
                label="Generated",
                x=np.array([0.0, 1.0], dtype=np.float64),
                y=np.array([0.2, 0.4], dtype=np.float64),
                sample_count=2,
                dataset="generated",
            ),
        ),
        x_limits=(0.0, 3.0),
        y_limits=(0.2, 0.5),
        bin_width=1.0,
        reference_sample_count=3,
        generated_sample_count=2,
    )


def test_plot_records_own_nonwritable_arrays_and_disable_auto_equality() -> None:
    x = np.array([0.0, 1.0], dtype=np.float64)
    y = np.array([1.0, 2.0], dtype=np.float64)

    series = LineSeries(label="Reference", x=x, y=y, sample_count=2, dataset="reference")
    data = _plot_data()
    x[0] = 9.0
    y[0] = 9.0

    assert series.x.tolist() == [0.0, 1.0]
    assert series.y.tolist() == [1.0, 2.0]
    assert not series.x.flags.writeable
    assert not series.y.flags.writeable
    assert not data.bin_edges.flags.writeable if data.bin_edges is not None else True
    assert not (series == LineSeries(label="Reference", x=series.x, y=series.y, sample_count=2, dataset="reference"))
    assert not (
        data
        == LinePlotData(
            identifier="throughput",
            label="Throughput",
            title="Throughput",
            x_label="Time (s)",
            y_label="Rate",
            unit="Mbps",
            series=data.series,
            x_limits=data.x_limits,
            y_limits=data.y_limits,
            bin_width=data.bin_width,
            reference_sample_count=data.reference_sample_count,
            generated_sample_count=data.generated_sample_count,
        )
    )

    with pytest.raises(ValueError):
        series.x.setflags(write=True)


def test_visibility_change_does_not_change_cache_key() -> None:
    run = _run()
    settings = CalculationSettings.default()
    first = TraceVisibility(reference=True, generated=True)
    second = TraceVisibility(reference=False, generated=True)

    assert first != second
    assert AspectCache.key(run, "throughput", settings) == (run.identities, "throughput", settings)
    assert AspectCache.key(run, "throughput", settings) == AspectCache.key(run, "throughput", settings)


def test_calculation_settings_are_hashable_and_visibility_free() -> None:
    settings = CalculationSettings.default()

    assert isinstance(hash(settings), int)
    assert tuple(field.name for field in fields(CalculationSettings)) == (
        "automatic_bin_minimum",
        "automatic_bin_maximum",
        "acf_lags",
        "maximum_display_points",
    )


def test_cache_get_put_and_clear() -> None:
    run = _run()
    settings = CalculationSettings.default()
    cache = AspectCache()
    key = AspectCache.key(run, "throughput", settings)
    value = _plot_data()

    assert cache.get(key) is None

    cache.put(key, value)

    assert cache.get(key) is value

    cache.clear()

    assert cache.get(key) is None


def test_cache_misses_when_aspect_or_settings_change() -> None:
    run = _run()
    settings = CalculationSettings.default()
    cache = AspectCache()
    cache.put(AspectCache.key(run, "throughput", settings), _plot_data())

    different_aspect = AspectCache.key(run, "packet_rate", settings)
    different_settings = AspectCache.key(
        run,
        "throughput",
        CalculationSettings(
            automatic_bin_minimum=10,
            automatic_bin_maximum=20,
            acf_lags=(1, 2),
            maximum_display_points=500,
        ),
    )

    assert cache.get(different_aspect) is None
    assert cache.get(different_settings) is None
