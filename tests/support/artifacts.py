"""Shared builders for artifact persistence tests."""

from pathlib import Path

from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata


def capture_sources(directory: Path, *, timestamp: float = 0.0) -> tuple[Path, Path]:
    """Write one valid temporary metadata/PCAPNG source pair."""
    directory.mkdir(parents=True, exist_ok=True)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    metadata_path = directory / "temporary-capture.json"
    pcapng_path = directory / "temporary-reference.pcapng"
    metadata_path.write_bytes(render_capture_metadata(metadata))
    pcapng_path.write_bytes(encode_pcapng((TraceEvent(timestamp, Direction.OUTBOUND, 14),), metadata))
    return metadata_path, pcapng_path
