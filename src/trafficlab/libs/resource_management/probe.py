"""Read-only injected Linux resource observations."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from .errors import InvalidResourceValueError
from .values import ProbeFailure, ResourceObservation


def probe_resources(
    storage_path: Path,
    *,
    cpu_count: Callable[[], int | None] = os.cpu_count,
    read_meminfo: Callable[[], str] | None = None,
    statvfs: Callable[[Path], os.statvfs_result] = os.statvfs,
) -> ResourceObservation | ProbeFailure:
    """Observe CPU, MemAvailable, and explicit-path available storage."""

    try:
        if not storage_path.is_absolute():
            return ProbeFailure("storage path is not absolute")
        cpus = cpu_count()
        text = (
            read_meminfo()
            if read_meminfo is not None
            else Path("/proc/meminfo").read_text()
        )
        memory = _mem_available(text)
        stats = statvfs(storage_path)
        storage = stats.f_frsize * stats.f_bavail
        if cpus is None or cpus <= 0 or memory <= 0 or storage <= 0:
            return ProbeFailure("resource observation is unavailable")
        return ResourceObservation(cpus, memory, storage, storage_path)
    except (InvalidResourceValueError, OSError, ValueError, TypeError):
        return ProbeFailure("resource probe failed")


def _mem_available(text: str) -> int:
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        if name == "MemAvailable" and separator:
            parts = value.split()
            if len(parts) == 2 and parts[1] == "kB":
                return int(parts[0]) * 1024
    raise ValueError("MemAvailable is missing")
