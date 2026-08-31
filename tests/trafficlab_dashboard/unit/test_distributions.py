from __future__ import annotations

import warnings
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import matplotlib.pyplot as plt
import numpy as np
import pytest

from trafficlab.common.trace import CaptureMetadata, Direction, TrafficTrace
from trafficlab_dashboard.aspects.base import CalculationSettings, HistogramPlotData, LinePlotData
from trafficlab_dashboard.aspects.distributions import (
    FrameSizeEcdfAspect,
    FrameSizeHistogramAspect,
    IatEcdfAspect,
    IatHistogramAspect,
    ThroughputEcdfAspect,
)
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun


def _trace(*events: tuple[float, Direction, int]) -> TrafficTrace:
    return TrafficTrace(
        timestamps=np.array([timestamp for timestamp, _, _ in events], dtype=np.float64),
        directions=np.array(
            [0 if direction is Direction.OUTBOUND else 1 for _, direction, _ in events], dtype=np.uint8
        ),
        frame_lengths=np.array([frame_length for _, _, frame_length in events], dtype=np.uint32),
    )


def _run(reference: TrafficTrace, generated: TrafficTrace, *, window: float) -> DashboardRun:
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
        reference=reference,
        generated=generated,
        window=window,
        similarity=None,
        best_model=None,
        history=None,
        experiment=None,
        unavailable=MappingProxyType({}),
    )


def _distribution_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 100),
            (1.0, Direction.INBOUND, 200),
            (1.0, Direction.OUTBOUND, 200),
            (3.0, Direction.INBOUND, 300),
        ),
        _trace(
            (0.0, Direction.OUTBOUND, 100),
            (0.0, Direction.INBOUND, 150),
            (2.0, Direction.OUTBOUND, 200),
            (3.0, Direction.INBOUND, 200),
        ),
        window=3.0,
    )


def _throughput_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 100),
            (1.0, Direction.INBOUND, 200),
            (2.0, Direction.OUTBOUND, 300),
        ),
        _trace(
            (0.0, Direction.OUTBOUND, 50),
            (1.5, Direction.INBOUND, 100),
            (2.0, Direction.OUTBOUND, 150),
        ),
        window=2.0,
    )


def _constant_frame_size_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 128),
            (1.0, Direction.INBOUND, 128),
            (2.0, Direction.OUTBOUND, 128),
        ),
        _trace(
            (0.0, Direction.OUTBOUND, 128),
            (1.0, Direction.INBOUND, 128),
        ),
        window=2.0,
    )


def _all_zero_iat_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 100),
            (0.0, Direction.INBOUND, 200),
            (0.0, Direction.OUTBOUND, 300),
        ),
        _trace(
            (0.0, Direction.OUTBOUND, 150),
            (0.0, Direction.INBOUND, 250),
        ),
        window=1.0,
    )


def _settings(*, bins: int = 2, maximum_display_points: int = 20_000) -> CalculationSettings:
    return CalculationSettings(
        automatic_bin_minimum=bins,
        automatic_bin_maximum=bins,
        acf_lags=(1, 2, 3),
        maximum_display_points=maximum_display_points,
    )


def _assert_ecdf_metadata(data: LinePlotData, *, identifier: str, label: str, unit: str, x_label: str) -> None:
    assert data.identifier == identifier
    assert data.label == label
    assert data.unit == unit
    assert data.x_label == x_label
    assert data.y_label == "ECDF"
    assert data.x_scale == "linear"
    assert data.y_scale == "linear"
    assert f"Reference n={data.reference_sample_count}" in data.title
    assert f"Generated n={data.generated_sample_count}" in data.title


def _assert_histogram_metadata(
    data: HistogramPlotData,
    *,
    identifier: str,
    label: str,
    unit: str,
    x_label: str,
    x_scale: str,
) -> None:
    assert data.identifier == identifier
    assert data.label == label
    assert data.unit == unit
    assert data.x_label == x_label
    assert data.y_label == "Density"
    assert data.x_scale == x_scale
    assert data.y_scale == "linear"
    assert f"Reference n={data.reference_sample_count}" in data.title
    assert f"Generated n={data.generated_sample_count}" in data.title


def test_frame_size_ecdf_retains_ties_and_full_sample_metadata() -> None:
    data = FrameSizeEcdfAspect().calculate(_distribution_run(), _settings())

    _assert_ecdf_metadata(
        data,
        identifier="frame_size_ecdf",
        label="Frame-size ECDF",
        unit="bytes",
        x_label="Frame size (bytes)",
    )
    assert data.reference_sample_count == 4
    assert data.generated_sample_count == 4
    assert data.x_limits == (100.0, 300.0)
    assert data.y_limits == (0.0, 1.0)
    assert data.series[0].x.tolist() == pytest.approx([100.0, 200.0, 200.0, 300.0])
    assert data.series[0].y.tolist() == pytest.approx([0.25, 0.5, 0.75, 1.0])
    assert data.series[1].x.tolist() == pytest.approx([100.0, 150.0, 200.0, 200.0])
    assert data.series[1].y.tolist() == pytest.approx([0.25, 0.5, 0.75, 1.0])
    assert data.series[0].sample_count == 4
    assert data.series[1].sample_count == 4


def test_iat_ecdf_retains_zero_iats() -> None:
    data = IatEcdfAspect().calculate(_distribution_run(), _settings())

    _assert_ecdf_metadata(data, identifier="iat_ecdf", label="IAT ECDF", unit="s", x_label="Inter-arrival time (s)")
    assert data.reference_sample_count == 3
    assert data.generated_sample_count == 3
    assert data.x_limits == (0.0, 2.0)
    assert data.series[0].x.tolist() == pytest.approx([0.0, 1.0, 2.0])
    assert data.series[0].y.tolist() == pytest.approx([1.0 / 3.0, 2.0 / 3.0, 1.0])
    assert data.series[1].x.tolist() == pytest.approx([0.0, 1.0, 2.0])
    assert data.series[1].y.tolist() == pytest.approx([1.0 / 3.0, 2.0 / 3.0, 1.0])


def test_frame_size_ecdf_display_reduction_preserves_endpoints_and_final_probability() -> None:
    data = FrameSizeEcdfAspect().calculate(_distribution_run(), _settings(maximum_display_points=2))

    assert data.series[0].sample_count == 4
    assert data.series[0].x.tolist() == pytest.approx([100.0, 300.0])
    assert data.series[0].y.tolist() == pytest.approx([0.25, 1.0])
    assert data.series[1].x.tolist() == pytest.approx([100.0, 200.0])
    assert data.series[1].y.tolist() == pytest.approx([0.25, 1.0])


def test_frame_size_histogram_uses_combined_edges_and_unit_density() -> None:
    data = FrameSizeHistogramAspect().calculate(_distribution_run(), _settings())

    _assert_histogram_metadata(
        data,
        identifier="frame_size_histogram",
        label="Frame-size normalized histogram",
        unit="bytes",
        x_label="Frame size (bytes)",
        x_scale="linear",
    )
    expected_edges = np.array([100.0, 150.0, 200.0, 250.0, 300.0], dtype=np.float64)
    expected_reference = np.array([0.005, 0.0, 0.01, 0.005], dtype=np.float64)
    expected_generated = np.array([0.005, 0.005, 0.01, 0.0], dtype=np.float64)

    assert data.reference_sample_count == 4
    assert data.generated_sample_count == 4
    assert data.x_limits == (100.0, 300.0)
    assert data.series[0].edges.tolist() == pytest.approx(expected_edges.tolist())
    assert data.series[1].edges.tolist() == pytest.approx(expected_edges.tolist())
    assert data.series[0].values.tolist() == pytest.approx(expected_reference.tolist())
    assert data.series[1].values.tolist() == pytest.approx(expected_generated.tolist())
    assert np.sum(data.series[0].values * np.diff(data.series[0].edges)) == pytest.approx(1.0)
    assert np.sum(data.series[1].values * np.diff(data.series[1].edges)) == pytest.approx(1.0)


def test_iat_histogram_annotates_zeros_and_uses_common_log_edges() -> None:
    data = IatHistogramAspect().calculate(
        _run(
            _trace(
                (0.0, Direction.OUTBOUND, 100),
                (0.0, Direction.INBOUND, 200),
                (1.0, Direction.OUTBOUND, 300),
            ),
            _trace(
                (0.0, Direction.OUTBOUND, 100),
                (0.0, Direction.INBOUND, 150),
                (2.0, Direction.OUTBOUND, 200),
            ),
            window=2.0,
        ),
        _settings(),
    )

    _assert_histogram_metadata(
        data,
        identifier="iat_histogram",
        label="IAT normalized histogram",
        unit="s",
        x_label="Inter-arrival time (s)",
        x_scale="log",
    )
    expected_edges = np.array([1.0, 1.414213562373095, 2.0], dtype=np.float64)

    assert data.reference_sample_count == 2
    assert data.generated_sample_count == 2
    assert data.series[0].zero_count == 1
    assert data.series[1].zero_count == 1
    assert data.series[0].positive_sample_count == 1
    assert data.series[1].positive_sample_count == 1
    assert data.series[0].edges.tolist() == pytest.approx(expected_edges.tolist())
    assert data.series[1].edges.tolist() == pytest.approx(expected_edges.tolist())
    assert np.all(data.series[0].edges > 0.0)
    assert data.series[0].values.tolist() == pytest.approx([2.4142135623730945, 0.0])
    assert data.series[1].values.tolist() == pytest.approx([0.0, 1.707106781186548])
    assert np.sum(data.series[0].values * np.diff(data.series[0].edges)) == pytest.approx(1.0)
    assert np.sum(data.series[1].values * np.diff(data.series[1].edges)) == pytest.approx(1.0)


def test_all_zero_iat_histogram_returns_only_zero_annotations() -> None:
    data = IatHistogramAspect().calculate(_all_zero_iat_run(), _settings())

    assert data.reference_sample_count == 2
    assert data.generated_sample_count == 1
    assert data.x_scale == "log"
    assert data.x_limits[0] > 0.0
    assert data.x_limits[1] > data.x_limits[0]
    assert data.y_limits == (0.0, 0.0)
    assert data.series[0].sample_count == 2
    assert data.series[1].sample_count == 1
    assert data.series[0].zero_count == 2
    assert data.series[1].zero_count == 1
    assert data.series[0].positive_sample_count == 0
    assert data.series[1].positive_sample_count == 0
    assert data.series[0].edges.tolist() == []
    assert data.series[1].edges.tolist() == []
    assert data.series[0].values.tolist() == []
    assert data.series[1].values.tolist() == []

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        figure, axes = plt.subplots()
        axes = cast(Any, axes)
        try:
            axes.set_xscale(data.x_scale)
            axes.set_xlim(*data.x_limits)
            assert axes.get_xlim() == pytest.approx(data.x_limits)
        finally:
            plt.close(figure)

    assert caught == []


def test_constant_frame_size_histogram_uses_numpy_fallback_edges() -> None:
    data = FrameSizeHistogramAspect().calculate(_constant_frame_size_run(), _settings())

    assert data.series[0].edges.tolist() == pytest.approx([127.5, 128.5])
    assert data.series[1].edges.tolist() == pytest.approx([127.5, 128.5])
    assert data.series[0].values.tolist() == pytest.approx([1.0])
    assert data.series[1].values.tolist() == pytest.approx([1.0])


def test_throughput_ecdf_reuses_task4_shared_time_binning() -> None:
    data = ThroughputEcdfAspect().calculate(_throughput_run(), _settings(bins=2))

    _assert_ecdf_metadata(
        data,
        identifier="throughput_ecdf",
        label="Throughput ECDF",
        unit="Mbps",
        x_label="Throughput (Mbps)",
    )
    assert data.reference_sample_count == 2
    assert data.generated_sample_count == 2
    assert data.series[0].x.tolist() == pytest.approx([0.0008, 0.004])
    assert data.series[0].y.tolist() == pytest.approx([0.5, 1.0])
    assert data.series[1].x.tolist() == pytest.approx([0.0004, 0.002])
    assert data.series[1].y.tolist() == pytest.approx([0.5, 1.0])


def test_distribution_aspects_return_immutable_arrays_and_stay_on_the_columnar_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_event_materialization(self: TrafficTrace) -> tuple[object, ...]:
        raise AssertionError("distribution aspects must not call to_events()")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)
    run = _distribution_run()
    throughput_run = _throughput_run()
    aspects = (
        FrameSizeEcdfAspect(),
        IatEcdfAspect(),
        FrameSizeHistogramAspect(),
        IatHistogramAspect(),
    )

    for aspect in aspects:
        data = aspect.calculate(run, _settings())
        if isinstance(data, LinePlotData):
            for series in data.series:
                assert not series.x.flags.writeable
                assert not series.y.flags.writeable
        else:
            for series in data.series:
                assert not series.edges.flags.writeable
                assert not series.values.flags.writeable

    throughput = ThroughputEcdfAspect().calculate(throughput_run, _settings(bins=2))
    for series in throughput.series:
        assert not series.x.flags.writeable
        assert not series.y.flags.writeable
