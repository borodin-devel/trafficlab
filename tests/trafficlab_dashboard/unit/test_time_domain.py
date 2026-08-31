from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest

from trafficlab.common.trace import CaptureMetadata, Direction, TrafficTrace
from trafficlab_dashboard.aspects.base import CalculationSettings, LinePlotData
from trafficlab_dashboard.aspects.time_domain import (
    CumulativeBytesAspect,
    CumulativePacketsAspect,
    FrameSizeTimelineAspect,
    IatTimelineAspect,
    PacketRateAspect,
    ThroughputAspect,
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
        reference=_trace(
            (0.0, Direction.OUTBOUND, 100),
            (1.0, Direction.INBOUND, 200),
            (2.0, Direction.OUTBOUND, 300),
        ),
        generated=_trace(
            (0.0, Direction.OUTBOUND, 50),
            (1.5, Direction.INBOUND, 100),
            (2.0, Direction.OUTBOUND, 150),
        ),
        window=2.0,
        similarity=None,
        best_model=None,
        history=None,
        experiment=None,
        unavailable=MappingProxyType({}),
    )


def _zero_iat_run() -> DashboardRun:
    run = _run()
    return DashboardRun(
        directory=run.directory,
        identities=run.identities,
        metadata=run.metadata,
        reference=_trace(
            (0.0, Direction.OUTBOUND, 100),
            (0.0, Direction.INBOUND, 200),
            (1.0, Direction.OUTBOUND, 300),
        ),
        generated=run.generated,
        window=run.window,
        similarity=run.similarity,
        best_model=run.best_model,
        history=run.history,
        experiment=run.experiment,
        unavailable=run.unavailable,
    )


def _dense_run() -> DashboardRun:
    timestamps = tuple(float(index) for index in range(6))
    reference = _trace(
        *(
            (timestamp, Direction.OUTBOUND, int(length))
            for timestamp, length in zip(timestamps, (5, 10, 1, 9, 3, 8), strict=True)
        )
    )
    generated = _trace(
        *(
            (timestamp, Direction.INBOUND, int(length))
            for timestamp, length in zip(timestamps, (2, 2, 2, 2, 2, 2), strict=True)
        )
    )
    return DashboardRun(
        directory=Path.cwd() / "run",
        identities=ArtifactIdentities(
            reference_sha256="4" * 64,
            generated_sha256="5" * 64,
            capture_sha256="6" * 64,
            similarity_sha256=None,
            best_model_sha256=None,
            history_sha256=None,
        ),
        metadata=CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:10"),
        reference=reference,
        generated=generated,
        window=5.0,
        similarity=None,
        best_model=None,
        history=None,
        experiment=None,
        unavailable=MappingProxyType({}),
    )


def _single_packet_generated_run() -> DashboardRun:
    run = _run()
    return DashboardRun(
        directory=run.directory,
        identities=run.identities,
        metadata=run.metadata,
        reference=run.reference,
        generated=_trace((0.0, Direction.OUTBOUND, 50)),
        window=run.window,
        similarity=run.similarity,
        best_model=run.best_model,
        history=run.history,
        experiment=run.experiment,
        unavailable=run.unavailable,
    )


def _settings(*, bins: int = 2, maximum_display_points: int = 20_000) -> CalculationSettings:
    return CalculationSettings(
        automatic_bin_minimum=bins,
        automatic_bin_maximum=bins,
        acf_lags=(1, 2, 3),
        maximum_display_points=maximum_display_points,
    )


def _assert_line_plot_metadata(data: LinePlotData, *, identifier: str, label: str, unit: str) -> None:
    assert data.identifier == identifier
    assert data.label == label
    assert data.unit == unit
    assert data.x_label == "Time (s)"
    assert data.reference_sample_count >= 0
    assert data.generated_sample_count >= 0
    assert data.x_scale == "linear"
    assert data.y_scale == "linear"
    assert label in data.title
    assert unit in data.title
    assert f"Reference n={data.reference_sample_count}" in data.title
    assert f"Generated n={data.generated_sample_count}" in data.title


def test_throughput_uses_shared_edges_closed_window_semantics_and_decimal_mbps() -> None:
    data = ThroughputAspect().calculate(_run(), _settings())

    _assert_line_plot_metadata(data, identifier="throughput", label="Throughput", unit="Mbps")
    assert data.y_label == "Throughput (Mbps)"
    assert data.bin_width == 1.0
    assert data.bin_edges is not None
    assert data.bin_edges.tolist() == pytest.approx([0.0, 1.0, 2.0])
    assert data.reference_sample_count == 3
    assert data.generated_sample_count == 3
    assert data.x_limits == (0.0, 2.0)
    assert data.y_limits == pytest.approx((0.0004, 0.004))
    assert data.series[0].label == "Reference"
    assert data.series[0].dataset == "reference"
    assert data.series[0].x.tolist() == pytest.approx([0.0, 1.0])
    assert data.series[0].y.tolist() == pytest.approx([0.0008, 0.004])
    assert data.series[1].label == "Generated"
    assert data.series[1].dataset == "generated"
    assert data.series[1].x.tolist() == pytest.approx([0.0, 1.0])
    assert data.series[1].y.tolist() == pytest.approx([0.0004, 0.002])
    assert data.series[0].sample_count == 3
    assert data.series[1].sample_count == 3


def test_packet_rate_uses_shared_edges_and_packets_per_second() -> None:
    data = PacketRateAspect().calculate(_run(), _settings())

    _assert_line_plot_metadata(data, identifier="packet_rate", label="Packet rate", unit="packets/s")
    assert data.y_label == "Packet rate (packets/s)"
    assert data.bin_width == 1.0
    assert data.bin_edges is not None
    assert data.bin_edges.tolist() == pytest.approx([0.0, 1.0, 2.0])
    assert data.x_limits == (0.0, 2.0)
    assert data.y_limits == pytest.approx((1.0, 2.0))
    assert data.series[0].x.tolist() == pytest.approx([0.0, 1.0])
    assert data.series[1].x.tolist() == pytest.approx([0.0, 1.0])
    assert data.series[0].y.tolist() == pytest.approx([1.0, 2.0])
    assert data.series[1].y.tolist() == pytest.approx([1.0, 2.0])


def test_cumulative_bytes_uses_exact_timestamps_full_totals_and_binary_mib() -> None:
    data = CumulativeBytesAspect().calculate(_run(), _settings())

    _assert_line_plot_metadata(data, identifier="cumulative_bytes", label="Cumulative bytes", unit="MiB")
    assert data.y_label == "Cumulative bytes (MiB)"
    assert data.bin_width is None
    assert data.bin_edges is None
    assert data.x_limits == (0.0, 2.0)
    assert data.series[0].x.tolist() == pytest.approx([0.0, 1.0, 2.0])
    assert data.series[1].x.tolist() == pytest.approx([0.0, 1.5, 2.0])
    assert data.series[0].y.tolist() == pytest.approx([100.0 / 1_048_576.0, 300.0 / 1_048_576.0, 600.0 / 1_048_576.0])
    assert data.series[1].y.tolist() == pytest.approx([50.0 / 1_048_576.0, 150.0 / 1_048_576.0, 300.0 / 1_048_576.0])
    assert data.series[0].y[-1] == pytest.approx(600.0 / 1_048_576.0)
    assert data.series[1].y[-1] == pytest.approx(300.0 / 1_048_576.0)


def test_cumulative_packets_include_both_window_endpoints() -> None:
    data = CumulativePacketsAspect().calculate(_run(), _settings())

    _assert_line_plot_metadata(data, identifier="cumulative_packets", label="Cumulative packets", unit="packets")
    assert data.y_label == "Cumulative packets"
    assert data.x_limits == (0.0, 2.0)
    assert data.series[0].x.tolist() == pytest.approx([0.0, 1.0, 2.0])
    assert data.series[1].x.tolist() == pytest.approx([0.0, 1.5, 2.0])
    assert data.series[0].y.tolist() == pytest.approx([1.0, 2.0, 3.0])
    assert data.series[1].y.tolist() == pytest.approx([1.0, 2.0, 3.0])


def test_frame_size_timeline_uses_columnar_points_and_bytes() -> None:
    data = FrameSizeTimelineAspect().calculate(_run(), _settings())

    _assert_line_plot_metadata(data, identifier="frame_size_timeline", label="Frame size versus time", unit="bytes")
    assert data.y_label == "Frame size (bytes)"
    assert data.x_limits == (0.0, 2.0)
    assert data.y_limits == pytest.approx((50.0, 300.0))
    assert data.series[0].x.tolist() == pytest.approx([0.0, 1.0, 2.0])
    assert data.series[0].y.tolist() == pytest.approx([100.0, 200.0, 300.0])
    assert data.series[1].x.tolist() == pytest.approx([0.0, 1.5, 2.0])
    assert data.series[1].y.tolist() == pytest.approx([50.0, 100.0, 150.0])


def test_iat_timeline_uses_trace_iats_and_retains_zero_intervals() -> None:
    base = IatTimelineAspect().calculate(_run(), _settings())
    zero_iat = IatTimelineAspect().calculate(_zero_iat_run(), _settings())

    _assert_line_plot_metadata(base, identifier="iat_timeline", label="IAT versus time", unit="s")
    assert base.y_label == "Inter-arrival time (s)"
    assert base.reference_sample_count == 2
    assert base.generated_sample_count == 2
    assert base.x_limits == (1.0, 2.0)
    assert base.y_limits == pytest.approx((0.5, 1.5))
    assert base.series[0].x.tolist() == pytest.approx([1.0, 2.0])
    assert base.series[0].y.tolist() == pytest.approx([1.0, 1.0])
    assert base.series[1].x.tolist() == pytest.approx([1.5, 2.0])
    assert base.series[1].y.tolist() == pytest.approx([1.5, 0.5])
    assert zero_iat.series[0].x.tolist() == pytest.approx([0.0, 1.0])
    assert zero_iat.series[0].y.tolist() == pytest.approx([0.0, 1.0])


def test_iat_timeline_allows_an_empty_series_for_a_single_packet_trace() -> None:
    data = IatTimelineAspect().calculate(_single_packet_generated_run(), _settings())

    assert data.generated_sample_count == 0
    assert data.series[1].sample_count == 0
    assert data.series[1].x.tolist() == []
    assert data.series[1].y.tolist() == []


def test_display_reduction_respects_point_limits_and_full_sample_totals() -> None:
    run = _dense_run()
    reduced_throughput = ThroughputAspect().calculate(run, _settings(bins=5, maximum_display_points=4))
    reduced_cumulative = CumulativeBytesAspect().calculate(run, _settings(bins=5, maximum_display_points=4))

    assert len(reduced_throughput.series[0].x) <= 4
    assert len(reduced_throughput.series[1].x) <= 4
    assert reduced_throughput.series[0].x[0] == 0.0
    assert reduced_throughput.series[0].x[-1] == 4.0
    assert max(reduced_throughput.series[0].y) == pytest.approx(0.000088)
    assert reduced_cumulative.series[0].sample_count == 6
    assert reduced_cumulative.series[1].sample_count == 6
    assert len(reduced_cumulative.series[0].x) <= 4
    assert reduced_cumulative.series[0].x[0] == 0.0
    assert reduced_cumulative.series[0].x[-1] == 5.0
    assert reduced_cumulative.series[0].y[-1] == pytest.approx(sum((5, 10, 1, 9, 3, 8)) / 1_048_576.0)
    assert reduced_cumulative.series[1].y[-1] == pytest.approx(12.0 / 1_048_576.0)


def test_frame_size_timeline_reduction_preserves_constant_series_endpoints() -> None:
    data = FrameSizeTimelineAspect().calculate(_dense_run(), _settings(bins=5, maximum_display_points=4))

    assert len(data.series[1].x) <= 4
    assert data.series[1].x[0] == 0.0
    assert data.series[1].x[-1] == 5.0
    assert set(data.series[1].y.tolist()) == {2.0}


def test_time_domain_aspects_return_immutable_arrays() -> None:
    run = _run()
    aspects = (
        ThroughputAspect(),
        PacketRateAspect(),
        CumulativeBytesAspect(),
        CumulativePacketsAspect(),
        FrameSizeTimelineAspect(),
        IatTimelineAspect(),
    )

    for aspect in aspects:
        data = aspect.calculate(run, _settings())
        for series in data.series:
            assert not series.x.flags.writeable
            assert not series.y.flags.writeable
        if data.bin_edges is not None:
            assert not data.bin_edges.flags.writeable


def test_time_domain_aspects_stay_on_the_columnar_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_event_materialization(self: TrafficTrace) -> tuple[object, ...]:
        raise AssertionError("time-domain aspects must not call to_events()")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)
    run = _run()
    aspects = (
        ThroughputAspect(),
        PacketRateAspect(),
        CumulativeBytesAspect(),
        CumulativePacketsAspect(),
        FrameSizeTimelineAspect(),
        IatTimelineAspect(),
    )

    for aspect in aspects:
        data = aspect.calculate(run, _settings())
        assert data.reference_sample_count >= 0
