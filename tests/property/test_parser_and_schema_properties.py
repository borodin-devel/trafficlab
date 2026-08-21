"""Properties for parser and strict JSON boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given

from tests.property.strategies import PcapngCase, json_documents, pcapng_cases
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import CaptureMetadata, parse_capture_metadata, render_capture_metadata


@given(json_documents())
def test_capture_metadata_strict_json_is_accepted_or_rejected_deterministically(document: bytes) -> None:
    source = Path("capture.json")
    try:
        parsed = parse_capture_metadata(document, source=source)
    except TrafficlabError:
        with pytest.raises(TrafficlabError):
            parse_capture_metadata(document, source=source)
    else:
        assert parse_capture_metadata(render_capture_metadata(parsed), source=source) == parsed


@given(pcapng_cases())
def test_pcapng_cases_round_trip_or_reject_deterministically(case: PcapngCase) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:01")
    if case.events is None:
        with pytest.raises(TrafficlabError):
            read_pcapng_bytes(case.content, metadata, source=Path("capture.pcapng"))
        with pytest.raises(TrafficlabError):
            read_pcapng_bytes(case.content, metadata, source=Path("capture.pcapng"))
    else:
        assert read_pcapng_bytes(case.content, metadata, source=Path("capture.pcapng")).to_events() == case.events
