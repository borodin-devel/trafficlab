from __future__ import annotations

import pytest

from trafficlab.common.scapy_io import encode_pcapng
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace


@pytest.fixture
def metadata() -> CaptureMetadata:
    return CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")


@pytest.fixture
def generated_events() -> tuple[TraceEvent, ...]:
    return (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.3333333336, Direction.INBOUND, 80),
        TraceEvent(1.0, Direction.OUTBOUND, 100),
    )


@pytest.fixture
def encoded(metadata: CaptureMetadata, generated_events: tuple[TraceEvent, ...]) -> bytes:
    return encode_pcapng(TrafficTrace.from_events(generated_events), metadata, observation_window_seconds=1.0).content
