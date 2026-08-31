from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import numpy as np
import pytest

from tests.trafficlab_dashboard.support.dashboard_fixtures import copy_checked_dashboard_run
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace
from trafficlab_dashboard.aspects.base import (
    AxisScale,
    BarPlotData,
    BarSeries,
    CalculationSettings,
    HexbinPlotData,
    HistogramPlotData,
    HistogramSeries,
    LinePlotData,
    LineSeries,
    TraceVisibility,
)
from trafficlab_dashboard.cache import AspectCache
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun
from trafficlab_dashboard.run_loader import load_dashboard_run


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


def _histogram_plot_data(
    *,
    identifier: str = "distribution",
    label: str = "Distribution",
    title: str = "Distribution",
    x_label: str = "Value",
    y_label: str = "Density",
    unit: str = "s",
    x_scale: AxisScale = "linear",
    y_scale: AxisScale = "linear",
) -> HistogramPlotData:
    return HistogramPlotData(
        identifier=identifier,
        label=label,
        title=title,
        x_label=x_label,
        y_label=y_label,
        unit=unit,
        series=(
            HistogramSeries(
                label="Reference",
                edges=np.array([0.5, 1.0, 2.0], dtype=np.float64),
                values=np.array([0.25, 0.75], dtype=np.float64),
                sample_count=3,
                dataset="reference",
                positive_sample_count=3,
            ),
        ),
        x_limits=(0.5, 2.0),
        y_limits=(0.0, 1.0),
        x_scale=x_scale,
        y_scale=y_scale,
    )


def _bar_plot_data(*, x_scale: AxisScale = "linear", y_scale: AxisScale = "linear") -> BarPlotData:
    return BarPlotData(
        identifier="scores",
        label="Scores",
        title="Scores",
        categories=("A", "B"),
        series=(BarSeries(label="Pair", values=np.array([0.2, 0.8], dtype=np.float64), sample_count=2),),
        y_label="Score",
        unit="ratio",
        y_limits=(0.0, 1.0),
        x_scale=x_scale,
        y_scale=y_scale,
    )


def _hexbin_plot_data(*, x_scale: AxisScale = "linear", y_scale: AxisScale = "linear") -> HexbinPlotData:
    return HexbinPlotData(
        identifier="relation",
        label="Relation",
        title="Relation",
        x_label="X",
        y_label="Y",
        unit="arb.",
        reference_x=np.array([1.0, 2.0], dtype=np.float64),
        reference_y=np.array([2.0, 3.0], dtype=np.float64),
        generated_x=np.array([1.5], dtype=np.float64),
        generated_y=np.array([2.5], dtype=np.float64),
        x_limits=(1.0, 2.0),
        y_limits=(2.0, 3.0),
        reference_sample_count=2,
        generated_sample_count=1,
        x_scale=x_scale,
        y_scale=y_scale,
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


def test_plot_data_axis_scale_defaults_are_linear_across_record_families() -> None:
    line = _plot_data()
    histogram = _histogram_plot_data()
    bar = _bar_plot_data()
    hexbin = _hexbin_plot_data()

    assert line.x_scale == "linear"
    assert line.y_scale == "linear"
    assert histogram.x_scale == "linear"
    assert histogram.y_scale == "linear"
    assert bar.x_scale == "linear"
    assert bar.y_scale == "linear"
    assert hexbin.x_scale == "linear"
    assert hexbin.y_scale == "linear"


def test_plot_data_rejects_unknown_axis_scale_values() -> None:
    with pytest.raises(ValueError, match="axis scale"):
        HistogramPlotData(
            identifier="distribution",
            label="Distribution",
            title="Distribution",
            x_label="Value",
            y_label="Density",
            unit="s",
            series=(
                HistogramSeries(
                    label="Reference",
                    edges=np.array([0.5, 1.0, 2.0], dtype=np.float64),
                    values=np.array([0.25, 0.75], dtype=np.float64),
                    sample_count=3,
                    dataset="reference",
                ),
            ),
            x_limits=(0.5, 2.0),
            y_limits=(0.0, 1.0),
            x_scale=cast(Any, "symlog"),
        )

    with pytest.raises(ValueError, match="axis scale"):
        _plot_data().__class__(
            identifier="throughput",
            label="Throughput",
            title="Throughput",
            x_label="Time (s)",
            y_label="Rate",
            unit="Mbps",
            series=_plot_data().series,
            x_limits=(0.0, 3.0),
            y_limits=(0.2, 0.5),
            y_scale=cast(Any, "symlog"),
            reference_sample_count=3,
            generated_sample_count=2,
        )


def test_positive_iat_histogram_can_request_a_log_x_scale_without_aspect_id_inference() -> None:
    data = _histogram_plot_data(
        identifier="custom-positive-iats",
        label="Positive IATs",
        title="Positive IATs",
        x_label="IAT (s)",
        x_scale="log",
    )

    assert data.identifier == "custom-positive-iats"
    assert data.x_scale == "log"
    assert data.y_scale == "linear"
    assert data.series[0].edges.tolist() == [0.5, 1.0, 2.0]
    assert not data.series[0].edges.flags.writeable


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


def test_replacement_run_experiment_bytes_separate_cache_identity(tmp_path: Path) -> None:
    first_directory = copy_checked_dashboard_run(tmp_path / "first")
    second_directory = copy_checked_dashboard_run(tmp_path / "second")
    experiment_path = second_directory / "experiment.toml"
    experiment_path.write_text(
        experiment_path.read_text(encoding="utf-8").replace(
            "early_stopping_tolerance = 0.0",
            "early_stopping_tolerance = 0.01",
        ),
        encoding="utf-8",
    )
    first = load_dashboard_run(first_directory)
    second = load_dashboard_run(second_directory)

    assert first.history is not None
    assert second.history is not None
    assert first.identities.experiment_sha256 != second.identities.experiment_sha256
    assert first.identities.reference_sha256 == second.identities.reference_sha256
    assert first.identities.generated_sha256 == second.identities.generated_sha256
    assert first.identities.capture_sha256 == second.identities.capture_sha256
    assert first.identities.similarity_sha256 == second.identities.similarity_sha256
    assert first.identities.best_model_sha256 == second.identities.best_model_sha256
    assert first.identities.history_sha256 == second.identities.history_sha256
    assert AspectCache.key(first, "ga_fitness_history", CalculationSettings.default()) != AspectCache.key(
        second,
        "ga_fitness_history",
        CalculationSettings.default(),
    )
