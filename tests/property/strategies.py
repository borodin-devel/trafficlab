"""Reusable finite inputs for the locked Hypothesis profile."""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent


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
    return trace_events().flatmap(
        lambda events: st.just(
            encode_pcapng(events, CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:01"))
        ).flatmap(
            lambda valid: st.one_of(
                st.just(PcapngCase(valid, events)),
                st.integers(min_value=0, max_value=len(valid) - 1).map(lambda length: PcapngCase(valid[:length], None)),
            )
        )
    )


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
