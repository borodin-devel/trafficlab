"""Typed command, process, and service-state boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
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


class DockerResult(Protocol):
    """Read-only decoded Docker command result shared by operation protocols."""

    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


class CaptureLifecycleOperations(Protocol):
    """Docker operations used only by capture lifecycle transitions."""

    def service_state(
        self, compose_path: Path, project_name: str, service: str, *, deadline: float
    ) -> ServiceState | None: ...

    def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> DockerResult: ...

    def kill_target(self, compose_path: Path, project_name: str, *, deadline: float) -> DockerResult: ...

    def kill_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> DockerResult: ...


class CaptureLogOperations(Protocol):
    """Docker operation used only to retain capture failure diagnostics."""

    def service_logs(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> str: ...


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
