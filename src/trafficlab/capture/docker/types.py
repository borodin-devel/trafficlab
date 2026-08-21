"""Typed command, process, and service-state boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Decoded result of one completed command."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ServiceState:
    """Small validated view of one Compose service container."""

    identifier: str
    name: str
    service: str
    state: str
    exit_code: int


class ProcessHandle(Protocol):
    """Controllable command used only for deadline-bounded cleanup."""

    def wait(self, *, timeout: float) -> CommandResult | None:
        """Wait at most *timeout* seconds, returning ``None`` on timeout."""

    def terminate(self) -> None:
        """Request termination of the local command process."""

    def kill(self) -> None:
        """Forcefully stop the local command process."""

    def reap(self) -> bool:
        """Nonblocking direct-child reap attempt; return whether it has exited."""
        ...


class CommandBoundary(Protocol):
    """Injected operating-system command boundary."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
        environment: Mapping[str, str] | None,
    ) -> CommandResult:
        """Run one bounded command without a shell."""
        ...

    def start(
        self,
        argv: tuple[str, ...],
        *,
        environment: Mapping[str, str] | None,
    ) -> ProcessHandle:
        """Start one directly controllable command without a shell."""
        ...


def reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while rejecting keys that would otherwise be overwritten."""
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate object key {key!r}")
        document[key] = value
    return document
