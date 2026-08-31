from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest

from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace
from trafficlab_dashboard.aspects.base import Aspect, CalculationSettings, LinePlotData, LineSeries
from trafficlab_dashboard.aspects.registry import ASPECTS, aspect_by_id
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun

EXPECTED_INITIAL_ASPECT_IDS = (
    "throughput",
    "packet_rate",
    "cumulative_bytes",
    "cumulative_packets",
    "frame_size_timeline",
    "iat_timeline",
    "frame_size_ecdf",
    "iat_ecdf",
    "frame_size_histogram",
    "iat_histogram",
    "throughput_ecdf",
    "directional_throughput",
    "directional_packet_rate",
    "direction_balance",
    "frame_size_acf",
    "iat_acf",
    "frame_size_iat_hexbin",
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
        reference=TrafficTrace.from_events(
            (
                TraceEvent(0.0, Direction.OUTBOUND, 100),
                TraceEvent(1.0, Direction.INBOUND, 200),
            )
        ),
        generated=TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 50),)),
        window=1.0,
        similarity=None,
        best_model=None,
        history=None,
        experiment=None,
        unavailable=MappingProxyType({}),
    )


class _DummyAspect:
    identifier = "dummy"
    label = "Dummy"
    category = "Tests"
    trace_controls = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        del run, settings
        return LinePlotData(
            identifier="dummy",
            label="Dummy",
            title="Dummy",
            x_label="Time (s)",
            y_label="Value",
            unit="arb.",
            series=(
                LineSeries(
                    label="Reference",
                    x=np.array([0.0], dtype=np.float64),
                    y=np.array([1.0], dtype=np.float64),
                    sample_count=1,
                    dataset="reference",
                ),
            ),
            x_limits=(0.0, 0.0),
            y_limits=(1.0, 1.0),
            reference_sample_count=1,
            generated_sample_count=0,
        )


def test_aspect_protocol_is_runtime_checkable() -> None:
    aspect = _DummyAspect()

    assert isinstance(aspect, Aspect)
    assert aspect.calculate(_run(), CalculationSettings.default()).identifier == "dummy"


def test_registry_order_matches_the_initial_dashboard_plan() -> None:
    assert tuple(aspect.identifier for aspect in ASPECTS) == EXPECTED_INITIAL_ASPECT_IDS


def test_registry_aspects_conform_to_the_protocol() -> None:
    assert ASPECTS
    assert all(isinstance(aspect, Aspect) for aspect in ASPECTS)
    assert all(aspect.trace_controls for aspect in ASPECTS)


def test_aspect_lookup_raises_exact_keyerror_for_unknown_identifier() -> None:
    with pytest.raises(KeyError) as caught:
        aspect_by_id("unknown-aspect")

    assert caught.value.args == ("unknown-aspect",)
