from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from trafficlab.cleanup import cleanup_project
from trafficlab.docker_cli import (
    CommandResult,
    DockerCompose,
    ProcessHandle,
    SubprocessBoundary,
)

pytestmark = pytest.mark.integration


class _ShortWaitHandle:
    def __init__(self, handle: ProcessHandle) -> None:
        self._handle = handle
        self.actions: list[str] = []
        self.waits: list[float] = []

    def wait(self, *, timeout: float) -> CommandResult | None:
        self.actions.append("wait")
        self.waits.append(timeout)
        return self._handle.wait(timeout=min(timeout, 0.03))

    def terminate(self) -> None:
        self.actions.append("terminate")
        self._handle.terminate()

    def kill(self) -> None:
        self.actions.append("kill")
        self._handle.kill()


class _HangingBoundary:
    def __init__(self) -> None:
        self.starts: list[tuple[str, ...]] = []
        self.runs: list[tuple[str, ...]] = []
        self.handle: _ShortWaitHandle | None = None

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
        environment: Mapping[str, str] | None,
    ) -> CommandResult:
        self.runs.append(argv)
        raise AssertionError(f"cleanup queried Docker after its process hung: {argv} {timeout} {environment}")

    def start(self, argv: tuple[str, ...], *, environment: Mapping[str, str] | None) -> ProcessHandle:
        self.starts.append(argv)
        process = SubprocessBoundary().start(
            (
                sys.executable,
                "-c",
                "import signal,time;signal.signal(signal.SIGTERM,lambda *_: None);time.sleep(30)",
            ),
            environment=environment,
        )
        self.handle = _ShortWaitHandle(process)
        return self.handle


class _RealProcessHandle:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            (
                sys.executable,
                "-c",
                "import signal,time;signal.signal(signal.SIGTERM,lambda *_: None);time.sleep(30)",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def wait(self, *, timeout: float) -> CommandResult | None:
        try:
            stdout, stderr = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        assert self.process.returncode is not None
        return CommandResult(self.process.returncode, stdout.decode(), stderr.decode())

    def terminate(self) -> None:
        self.process.terminate()

    def kill(self) -> None:
        self.process.kill()


class _RealProcessBoundary:
    def __init__(self) -> None:
        self.handle: _RealProcessHandle | None = None
        self.queries = 0

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
        environment: Mapping[str, str] | None,
    ) -> CommandResult:
        self.queries += 1
        raise AssertionError(f"cleanup queried Docker after expiry: {argv} {timeout} {environment}")

    def start(self, argv: tuple[str, ...], *, environment: Mapping[str, str] | None) -> ProcessHandle:
        assert argv[-3:] == ("down", "--volumes", "--remove-orphans")
        assert environment is None
        self.handle = _RealProcessHandle()
        return self.handle


def test_hanging_cleanup_terminates_then_kills_local_cli_without_later_docker_query(tmp_path: Path) -> None:
    """A hung Compose CLI must be reaped promptly without a post-timeout daemon query or broader project scope."""
    boundary = _HangingBoundary()
    compose = DockerCompose(boundary=boundary)
    compose_path = (tmp_path / "compose.json").resolve()
    started = time.monotonic()

    result = cleanup_project(
        compose,
        compose_path,
        "trafficlab-run",
        deadline=started + 2.0,
        clock=time.monotonic,
    )

    assert time.monotonic() - started < 0.5
    assert result.timed_out
    assert not result.success
    assert boundary.starts == [
        (
            "docker",
            "compose",
            "--project-name",
            "trafficlab-run",
            "--file",
            str(compose_path),
            "down",
            "--volumes",
            "--remove-orphans",
        )
    ]
    assert boundary.runs == []
    assert boundary.handle is not None
    assert boundary.handle.actions == ["wait", "terminate", "wait", "kill", "wait"]
    assert all(timeout > 0.0 for timeout in boundary.handle.waits)
    assert boundary.handle.waits[0] < 2.0


def test_zero_budget_with_real_compose_adapter_starts_no_boundary_command(tmp_path: Path) -> None:
    """The top-level zero-budget guard must precede even the concrete Docker adapter's scope validation and launch."""
    boundary = _HangingBoundary()
    result = cleanup_project(
        DockerCompose(boundary=boundary),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=100.0,
        clock=lambda: 100.0,
    )

    assert result.timed_out
    assert boundary.starts == []
    assert boundary.runs == []


def test_full_initial_wait_reserves_time_to_kill_and_reap_real_process(tmp_path: Path) -> None:
    """Giving the first wait the whole budget leaves no time to reap an uncooperative local Compose CLI."""
    boundary = _RealProcessBoundary()
    started = time.monotonic()

    try:
        result = cleanup_project(
            DockerCompose(boundary=boundary),
            (tmp_path / "compose.json").resolve(),
            "trafficlab-run",
            deadline=started + 0.25,
            clock=time.monotonic,
        )

        assert result.timed_out
        assert boundary.handle is not None
        assert boundary.handle.process.returncode is not None
        assert boundary.handle.process.returncode < 0
        assert boundary.queries == 0
        assert time.monotonic() - started < 0.5
    finally:
        if boundary.handle is not None and boundary.handle.process.returncode is None:
            boundary.handle.process.kill()
            boundary.handle.process.communicate(timeout=1.0)
