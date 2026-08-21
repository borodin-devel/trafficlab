"""Reusable finite inputs for the locked Hypothesis profile."""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from trafficlab.common.scapy_io import encode_pcapng
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace


@dataclass(frozen=True, slots=True)
class PcapngCase:
    """One parser input with its expected canonical events, when valid."""

    content: bytes
    events: tuple[TraceEvent, ...] | None


def trace_events(*, min_size: int = 1) -> SearchStrategy[tuple[TraceEvent, ...]]:
    """Return a nonempty canonical trace with integer-nanosecond timestamps."""
    return st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=1_000_000),
            st.sampled_from((Direction.OUTBOUND, Direction.INBOUND)),
            st.integers(min_value=14, max_value=1_500),
        ),
        min_size=min_size,
        max_size=12,
    ).map(
        lambda rows: tuple(
            TraceEvent(timestamp / 1_000_000_000, direction, frame_length)
            for timestamp, direction, frame_length in sorted(rows, key=lambda row: row[0])
        )
    )


def pcapng_cases() -> SearchStrategy[PcapngCase]:
    """Return either a valid rendered capture or a structurally malformed prefix."""
    metadata = CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:01")

    def cases(events: tuple[TraceEvent, ...]) -> SearchStrategy[PcapngCase]:
        trace = TrafficTrace.from_events(events)
        encoded = encode_pcapng(
            trace,
            metadata,
            observation_window_seconds=max(1.0, float(trace.timestamps[-1])),
        )
        return st.one_of(
            st.just(PcapngCase(encoded.content, encoded.trace.to_events())),
            st.integers(min_value=0, max_value=len(encoded.content) - 1).map(
                lambda length: PcapngCase(encoded.content[:length], None)
            ),
        )

    return trace_events().flatmap(cases)


def json_documents() -> SearchStrategy[bytes]:
    """Return strict capture-metadata documents, including malformed variants."""

    def documents(octets: list[int]) -> SearchStrategy[bytes]:
        normalized = [octets[0] & 0xFE, *octets[1:]]
        if not any(normalized):
            normalized[-1] = 1
        target_mac = ":".join(f"{octet:02x}" for octet in normalized)
        valid = f'{{"interface":"eth0","target_mac":"{target_mac}"}}'.encode()
        return st.one_of(st.just(valid), st.sampled_from((b"", b"{", valid[:-1], valid.replace(b"eth0", b"eth1"))))

    return st.lists(st.integers(min_value=0, max_value=255), min_size=6, max_size=6).flatmap(documents)
