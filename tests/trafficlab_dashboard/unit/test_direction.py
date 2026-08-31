from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest

from trafficlab.common.trace import CaptureMetadata, Direction, TrafficTrace
from trafficlab_dashboard.aspects.base import BarPlotData, CalculationSettings, LinePlotData
from trafficlab_dashboard.aspects.direction import (
    DirectionalPacketRateAspect,
    DirectionalThroughputAspect,
    DirectionBalanceAspect,
)
from trafficlab_dashboard.aspects.registry import ASPECTS, aspect_by_id
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


def _direction_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 100),
            (1.0, Direction.INBOUND, 200),
            (2.0, Direction.OUTBOUND, 300),
        ),
        _trace(
            (0.0, Direction.INBOUND, 50),
            (1.5, Direction.OUTBOUND, 100),
            (2.0, Direction.INBOUND, 150),
        ),
        window=2.0,
    )


def _empty_subset_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 100),
            (1.0, Direction.OUTBOUND, 200),
            (2.0, Direction.OUTBOUND, 300),
        ),
        _trace(
            (0.0, Direction.INBOUND, 50),
            (1.0, Direction.INBOUND, 150),
            (2.0, Direction.INBOUND, 250),
        ),
        window=2.0,
    )


def _settings(*, bins: int = 2, maximum_display_points: int = 20_000) -> CalculationSettings:
    return CalculationSettings(
        automatic_bin_minimum=bins,
        automatic_bin_maximum=bins,
        acf_lags=(1, 2),
        maximum_display_points=maximum_display_points,
    )


def _assert_visible_terms(texts: tuple[str, ...]) -> None:
    visible = " ".join(texts).lower()
    assert "uplink" in visible or "downlink" in visible
    assert "outbound" not in visible
    assert "inbound" not in visible


def _assert_direction_line_metadata(data: LinePlotData, *, identifier: str, label: str, unit: str, y_label: str) -> None:
    assert data.identifier == identifier
    assert data.label == label
    assert data.unit == unit
    assert data.x_label == "Time (s)"
    assert data.y_label == y_label
    assert data.x_scale == "linear"
    assert data.y_scale == "linear"
    assert data.bin_width == 1.0
    assert data.bin_edges is not None
    assert data.bin_edges.tolist() == pytest.approx([0.0, 1.0, 2.0])
    assert data.x_limits == (0.0, 2.0)
    _assert_visible_terms((data.label, data.title, *(series.label for series in data.series)))


def test_directional_throughput_uses_shared_edges_closed_window_semantics_and_uplink_labels() -> None:
    data = DirectionalThroughputAspect().calculate(_direction_run(), _settings())

    _assert_direction_line_metadata(
        data,
        identifier="directional_throughput",
        label="Uplink/downlink throughput",
        unit="Mbps",
        y_label="Throughput (Mbps)",
    )
    assert data.reference_sample_count == 3
    assert data.generated_sample_count == 3
    assert data.y_limits == pytest.approx((0.0, 0.0024))
    assert [(series.label, series.dataset, series.line_style, series.sample_count) for series in data.series] == [
        ("Reference uplink", "reference", "solid", 2),
        ("Reference downlink", "reference", "dashed", 1),
        ("Generated uplink", "generated", "solid", 1),
        ("Generated downlink", "generated", "dashed", 2),
    ]
    assert [series.x.tolist() for series in data.series] == [
        pytest.approx([0.0, 1.0]),
        pytest.approx([0.0, 1.0]),
        pytest.approx([0.0, 1.0]),
        pytest.approx([0.0, 1.0]),
    ]
    assert data.series[0].y.tolist() == pytest.approx([0.0008, 0.0024])
    assert data.series[1].y.tolist() == pytest.approx([0.0, 0.0016])
    assert data.series[2].y.tolist() == pytest.approx([0.0, 0.0008])
    assert data.series[3].y.tolist() == pytest.approx([0.0004, 0.0012])


def test_directional_packet_rate_uses_shared_edges_and_direction_dash_metadata() -> None:
    data = DirectionalPacketRateAspect().calculate(_direction_run(), _settings())

    _assert_direction_line_metadata(
        data,
        identifier="directional_packet_rate",
        label="Uplink/downlink packet rate",
        unit="packets/s",
        y_label="Packet rate (packets/s)",
    )
    assert data.y_limits == pytest.approx((0.0, 1.0))
    assert data.series[0].y.tolist() == pytest.approx([1.0, 1.0])
    assert data.series[1].y.tolist() == pytest.approx([0.0, 1.0])
    assert data.series[2].y.tolist() == pytest.approx([0.0, 1.0])
    assert data.series[3].y.tolist() == pytest.approx([1.0, 1.0])


def test_direction_balance_uses_uplink_downlink_labels_and_packet_byte_shares() -> None:
    data = DirectionBalanceAspect().calculate(_direction_run(), _settings())

    assert isinstance(data, BarPlotData)
    assert data.identifier == "direction_balance"
    assert data.label == "Direction balance"
    assert data.unit == "proportion"
    assert data.y_label == "Share"
    assert data.y_limits == (0.0, 1.0)
    assert data.categories == ("Uplink packets", "Downlink packets", "Uplink bytes", "Downlink bytes")
    assert data.series[0].label == "Reference"
    assert data.series[0].dataset == "reference"
    assert data.series[0].sample_count == 3
    assert data.series[0].values.tolist() == pytest.approx([2.0 / 3.0, 1.0 / 3.0, 400.0 / 600.0, 200.0 / 600.0])
    assert data.series[1].label == "Generated"
    assert data.series[1].dataset == "generated"
    assert data.series[1].sample_count == 3
    assert data.series[1].values.tolist() == pytest.approx([1.0 / 3.0, 2.0 / 3.0, 100.0 / 300.0, 200.0 / 300.0])
    _assert_visible_terms((data.label, data.title, *data.categories, *(series.label for series in data.series)))


def test_direction_aspects_keep_empty_direction_subsets_columnar_and_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_event_materialization(self: TrafficTrace) -> tuple[object, ...]:
        raise AssertionError("direction aspects must not call to_events()")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)
    run = _empty_subset_run()
    throughput = DirectionalThroughputAspect().calculate(run, _settings())
    packet_rate = DirectionalPacketRateAspect().calculate(run, _settings())
    balance = DirectionBalanceAspect().calculate(run, _settings())

    assert throughput.series[1].sample_count == 0
    assert throughput.series[1].y.tolist() == pytest.approx([0.0, 0.0])
    assert packet_rate.series[2].sample_count == 0
    assert packet_rate.series[2].y.tolist() == pytest.approx([0.0, 0.0])
    for data in (throughput, packet_rate):
        assert data.bin_edges is not None
        assert not data.bin_edges.flags.writeable
        for series in data.series:
            assert not series.x.flags.writeable
            assert not series.y.flags.writeable
    for series in balance.series:
        assert not series.values.flags.writeable


def test_direction_registry_replaces_placeholders_with_concrete_aspects() -> None:
    assert type(aspect_by_id("directional_throughput")) is DirectionalThroughputAspect
    assert type(aspect_by_id("directional_packet_rate")) is DirectionalPacketRateAspect
    assert type(aspect_by_id("direction_balance")) is DirectionBalanceAspect
    assert tuple(type(aspect) for aspect in ASPECTS[11:14]) == (
        DirectionalThroughputAspect,
        DirectionalPacketRateAspect,
        DirectionBalanceAspect,
    )
