# pyright: reportArgumentType=false
"""Tests for injected unprivileged Linux resource observation."""

from pathlib import Path

import pytest

from trafficlab.libs.resource_management import (
    ProbeFailure,
    ResourceObservation,
    probe_resources,
)


class _Stat:
    f_frsize = 4
    f_bavail = 8


class _OverflowStat:
    f_frsize = 1 << 62
    f_bavail = 4


@pytest.mark.unit
def test_probe_returns_injected_cpu_memory_and_storage_observation() -> None:
    result = probe_resources(
        Path("/storage"),
        cpu_count=lambda: 4,
        read_meminfo=lambda: "MemAvailable: 8 kB\n",
        statvfs=lambda _: _Stat(),
    )

    assert result == ResourceObservation(4, 8_192, 32, Path("/storage"))


@pytest.mark.unit
def test_probe_returns_failure_for_unavailable_observation() -> None:
    result = probe_resources(
        Path("/storage"),
        cpu_count=lambda: None,
        read_meminfo=lambda: "",
        statvfs=lambda _: _Stat(),
    )

    assert isinstance(result, ProbeFailure)


@pytest.mark.unit
def test_probe_returns_failure_for_overflowed_storage() -> None:
    result = probe_resources(
        Path("/storage"),
        cpu_count=lambda: 1,
        read_meminfo=lambda: "MemAvailable: 1 kB\n",
        statvfs=lambda _: _OverflowStat(),
    )

    assert isinstance(result, ProbeFailure)
