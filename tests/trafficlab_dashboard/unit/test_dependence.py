from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest

from trafficlab.common.trace import CaptureMetadata, Direction, TrafficTrace
from trafficlab_dashboard.aspects.base import CalculationSettings, HexbinPlotData, LinePlotData
from trafficlab_dashboard.aspects.dependence import (
    FrameSizeAutocorrelationAspect,
    FrameSizeIatHexbinAspect,
    IatAutocorrelationAspect,
)
from trafficlab_dashboard.aspects.registry import ASPECTS, aspect_by_id
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun


def _trace(*events: tuple[float, Direction, int]) -> TrafficTrace:
    return TrafficTrace(
        timestamps=np.array([timestamp for timestamp, _, _ in events], dtype=np.float64),
        directions=np.array([0 for _timestamp, _direction, _length in events], dtype=np.uint8),
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


def _tiny_acf_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 1),
            (1.0, Direction.OUTBOUND, 2),
            (3.0, Direction.OUTBOUND, 1),
        ),
        _trace(
            (0.0, Direction.OUTBOUND, 1),
            (1.0, Direction.OUTBOUND, 2),
            (3.0, Direction.OUTBOUND, 1),
        ),
        window=3.0,
    )


def _paired_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 1),
            (1.0, Direction.OUTBOUND, 2),
            (3.0, Direction.OUTBOUND, 1),
            (6.0, Direction.OUTBOUND, 2),
            (10.0, Direction.OUTBOUND, 1),
        ),
        _trace(
            (0.0, Direction.OUTBOUND, 1),
            (1.0, Direction.OUTBOUND, 1),
            (4.0, Direction.OUTBOUND, 2),
            (6.0, Direction.OUTBOUND, 2),
            (10.0, Direction.OUTBOUND, 1),
        ),
        window=10.0,
    )


def _short_generated_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 1),
            (1.0, Direction.OUTBOUND, 2),
            (3.0, Direction.OUTBOUND, 1),
        ),
        _trace(
            (0.0, Direction.OUTBOUND, 7),
            (1.0, Direction.OUTBOUND, 7),
        ),
        window=3.0,
    )


def _constant_run() -> DashboardRun:
    return _run(
        _trace(
            (0.0, Direction.OUTBOUND, 7),
            (1.0, Direction.OUTBOUND, 7),
            (2.0, Direction.OUTBOUND, 7),
            (3.0, Direction.OUTBOUND, 7),
        ),
        _trace(
            (0.0, Direction.OUTBOUND, 7),
            (1.0, Direction.OUTBOUND, 7),
            (2.0, Direction.OUTBOUND, 7),
            (3.0, Direction.OUTBOUND, 7),
        ),
        window=3.0,
    )


def _large_run() -> DashboardRun:
    sample_count = 20_001
    reference_timestamps = np.arange(sample_count, dtype=np.float64)
    generated_timestamps = np.arange(sample_count, dtype=np.float64)
    reference_lengths = np.where(np.arange(sample_count) % 2 == 0, 64, 1514).astype(np.uint32)
    generated_lengths = np.where(np.arange(sample_count) % 3 == 0, 128, 1024).astype(np.uint32)
    return _run(
        TrafficTrace(
            timestamps=reference_timestamps,
            directions=np.zeros(sample_count, dtype=np.uint8),
            frame_lengths=reference_lengths,
        ),
        TrafficTrace(
            timestamps=generated_timestamps,
            directions=np.zeros(sample_count, dtype=np.uint8),
            frame_lengths=generated_lengths,
        ),
        window=float(sample_count - 1),
    )


def _settings_with_lags(lags: tuple[int, ...], *, maximum_display_points: int = 20_000) -> CalculationSettings:
    return CalculationSettings(
        automatic_bin_minimum=2,
        automatic_bin_maximum=2,
        acf_lags=lags,
        maximum_display_points=maximum_display_points,
    )


def _assert_lag_metadata(
    data: LinePlotData,
    *,
    requested_lags: tuple[int, ...],
    reference_available: tuple[bool, ...],
    generated_available: tuple[bool, ...],
) -> None:
    assert data.x_label == "Lag"
    assert data.y_label == "Autocorrelation"
    assert data.unit == "unitless"
    assert data.x_scale == "linear"
    assert data.y_scale == "linear"
    assert data.lag_range == (requested_lags[0], requested_lags[-1])
    assert data.requested_lags == requested_lags
    assert data.reference_available == reference_available
    assert data.generated_available == generated_available


def test_frame_size_acf_uses_trafficlab_estimator() -> None:
    data = FrameSizeAutocorrelationAspect().calculate(_tiny_acf_run(), _settings_with_lags((1, 2)))

    assert data.identifier == "frame_size_acf"
    assert data.label == "Frame-size autocorrelation"
    assert data.reference_sample_count == 3
    assert data.generated_sample_count == 3
    assert data.x_limits == (1.0, 2.0)
    _assert_lag_metadata(
        data,
        requested_lags=(1, 2),
        reference_available=(True, True),
        generated_available=(True, True),
    )
    assert data.series[0].x.tolist() == pytest.approx([1.0, 2.0])
    assert data.series[0].y.tolist() == pytest.approx([-2.0 / 3.0, 1.0 / 6.0])
    assert data.series[1].x.tolist() == pytest.approx([1.0, 2.0])
    assert data.series[1].y.tolist() == pytest.approx([-2.0 / 3.0, 1.0 / 6.0])
    assert "Lags 1-2" in data.title


def test_dependence_acf_two_lag_oracles_match_core_contract_values() -> None:
    frame_size = FrameSizeAutocorrelationAspect().calculate(_paired_run(), _settings_with_lags((1, 2)))
    iat = IatAutocorrelationAspect().calculate(_paired_run(), _settings_with_lags((1, 2)))

    _assert_lag_metadata(
        frame_size,
        requested_lags=(1, 2),
        reference_available=(True, True),
        generated_available=(True, True),
    )
    _assert_lag_metadata(
        iat,
        requested_lags=(1, 2),
        reference_available=(True, True),
        generated_available=(True, True),
    )
    assert frame_size.reference_sample_count == 5
    assert frame_size.generated_sample_count == 5
    assert frame_size.series[0].y.tolist() == pytest.approx([-4.0 / 5.0, 17.0 / 30.0])
    assert frame_size.series[1].y.tolist() == pytest.approx([1.0 / 30.0, -3.0 / 5.0])
    assert iat.reference_sample_count == 4
    assert iat.generated_sample_count == 4
    assert iat.series[0].y.tolist() == pytest.approx([1.0 / 4.0, -3.0 / 10.0])
    assert iat.series[1].y.tolist() == pytest.approx([-7.0 / 20.0, 3.0 / 10.0])


def test_acf_unavailable_lags_are_marked_not_zero() -> None:
    data = FrameSizeAutocorrelationAspect().calculate(_short_generated_run(), _settings_with_lags((1, 2)))

    _assert_lag_metadata(
        data,
        requested_lags=(1, 2),
        reference_available=(True, True),
        generated_available=(True, False),
    )
    assert data.unavailable_reason == "lag must be smaller than sample length"
    assert data.series[0].x.tolist() == pytest.approx([1.0, 2.0])
    assert data.series[1].x.tolist() == pytest.approx([1.0])
    assert data.series[1].y.tolist() == pytest.approx([0.0])
    assert 2.0 not in data.series[1].x.tolist()


def test_constant_acf_zeroes_remain_available() -> None:
    frame_size = FrameSizeAutocorrelationAspect().calculate(_constant_run(), _settings_with_lags((1, 2, 3)))
    iat = IatAutocorrelationAspect().calculate(_constant_run(), _settings_with_lags((1, 2)))

    _assert_lag_metadata(
        frame_size,
        requested_lags=(1, 2, 3),
        reference_available=(True, True, True),
        generated_available=(True, True, True),
    )
    _assert_lag_metadata(
        iat,
        requested_lags=(1, 2),
        reference_available=(True, True),
        generated_available=(True, True),
    )
    assert frame_size.unavailable_reason is None
    assert iat.unavailable_reason is None
    assert frame_size.series[0].y.tolist() == pytest.approx([0.0, 0.0, 0.0])
    assert iat.series[0].y.tolist() == pytest.approx([0.0, 0.0])


def test_frame_size_iat_relation_uses_frame_lengths_from_second_packet_and_union_bounds() -> None:
    data = FrameSizeIatHexbinAspect().calculate(_paired_run(), _settings_with_lags((1, 2)))

    assert isinstance(data, HexbinPlotData)
    assert data.identifier == "frame_size_iat_hexbin"
    assert data.label == "Frame size versus IAT"
    assert data.x_label == "Frame size (bytes)"
    assert data.y_label == "Inter-arrival time (s)"
    assert data.x_limits == (1.0, 2.0)
    assert data.y_limits == (1.0, 4.0)
    assert data.reference_sample_count == 4
    assert data.generated_sample_count == 4
    assert data.reference_x.tolist() == pytest.approx([2.0, 1.0, 2.0, 1.0])
    assert data.reference_y.tolist() == pytest.approx([1.0, 2.0, 3.0, 4.0])
    assert data.generated_x.tolist() == pytest.approx([1.0, 2.0, 2.0, 1.0])
    assert data.generated_y.tolist() == pytest.approx([1.0, 3.0, 2.0, 4.0])
    assert data.render_mode == "scatter"


def test_large_size_iat_relation_uses_hexbin_full_samples() -> None:
    run = _large_run()
    data = FrameSizeIatHexbinAspect().calculate(run, CalculationSettings.default())

    assert data.reference_sample_count == len(run.reference) - 1
    assert data.generated_sample_count == len(run.generated) - 1
    assert len(data.reference_x) == len(run.reference) - 1
    assert len(data.reference_y) == len(run.reference) - 1
    assert len(data.generated_x) == len(run.generated) - 1
    assert len(data.generated_y) == len(run.generated) - 1
    assert data.render_mode == "hexbin"


def test_dependence_aspects_stay_on_the_columnar_fast_path_and_return_immutable_arrays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_event_materialization(self: TrafficTrace) -> tuple[object, ...]:
        raise AssertionError("dependence aspects must not call to_events()")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)
    run = _paired_run()
    frame_size = FrameSizeAutocorrelationAspect().calculate(run, _settings_with_lags((1, 2)))
    iat = IatAutocorrelationAspect().calculate(run, _settings_with_lags((1, 2)))
    relation = FrameSizeIatHexbinAspect().calculate(run, _settings_with_lags((1, 2)))

    for data in (frame_size, iat):
        for series in data.series:
            assert not series.x.flags.writeable
            assert not series.y.flags.writeable
    assert not relation.reference_x.flags.writeable
    assert not relation.reference_y.flags.writeable
    assert not relation.generated_x.flags.writeable
    assert not relation.generated_y.flags.writeable


def test_dependence_registry_replaces_placeholders_with_concrete_aspects() -> None:
    assert type(aspect_by_id("frame_size_acf")) is FrameSizeAutocorrelationAspect
    assert type(aspect_by_id("iat_acf")) is IatAutocorrelationAspect
    assert type(aspect_by_id("frame_size_iat_hexbin")) is FrameSizeIatHexbinAspect
    assert tuple(type(aspect) for aspect in ASPECTS[14:17]) == (
        FrameSizeAutocorrelationAspect,
        IatAutocorrelationAspect,
        FrameSizeIatHexbinAspect,
    )
