from __future__ import annotations

import os
import signal
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

    def reap(self) -> bool:
        self.actions.append("reap")
        return self._handle.reap()


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

    def reap(self) -> bool:
        return self.process.poll() is not None


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
    assert boundary.handle.actions == ["wait", "terminate", "wait", "kill", "wait", "reap"]
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


class _DescendantBoundary:
    def __init__(self, parent_pid: Path, child_pid: Path) -> None:
        self.parent_pid = parent_pid
        self.child_pid = child_pid

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
        environment: Mapping[str, str] | None,
    ) -> CommandResult:
        raise AssertionError(f"unexpected Docker query after cleanup launch: {argv} {timeout} {environment}")

    def start(self, argv: tuple[str, ...], *, environment: Mapping[str, str] | None) -> ProcessHandle:
        del argv
        child_program = (
            "import os,pathlib,signal,sys,time;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
            "time.sleep(30)"
        )
        parent_program = (
            "import os,pathlib,signal,subprocess,sys,time;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
            "subprocess.Popen([sys.executable,'-c',sys.argv[3],sys.argv[2]],stdout=sys.stdout,stderr=sys.stderr);"
            "time.sleep(30)"
        )
        return SubprocessBoundary().start(
            (
                sys.executable,
                "-c",
                parent_program,
                str(self.parent_pid),
                str(self.child_pid),
                child_program,
            ),
            environment=environment,
        )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill_exact_test_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _pid_gone_within(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _pid_exists(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def test_cleanup_kills_and_reaps_sigterm_ignoring_process_group_with_inherited_pipes(tmp_path: Path) -> None:
    """Cleanup timeout must kill the Compose process group so inherited pipes close and every descendant exits."""
    parent_pid_path = tmp_path / "parent.pid"
    child_pid_path = tmp_path / "child.pid"
    boundary = _DescendantBoundary(parent_pid_path, child_pid_path)
    started = time.monotonic()
    parent_pid: int | None = None
    child_pid: int | None = None

    try:
        result = cleanup_project(
            DockerCompose(boundary=boundary),
            (tmp_path / "compose.json").resolve(),
            "trafficlab-run",
            deadline=started + 2.0,
            clock=time.monotonic,
        )
        parent_pid = int(parent_pid_path.read_text())
        child_pid = int(child_pid_path.read_text())

        assert result.timed_out
        assert time.monotonic() - started < 2.5
        assert _pid_gone_within(parent_pid, 0.5)
        assert _pid_gone_within(child_pid, 0.5)
    finally:
        if child_pid is None and child_pid_path.exists():
            child_pid = int(child_pid_path.read_text())
        if parent_pid is None and parent_pid_path.exists():
            parent_pid = int(parent_pid_path.read_text())
        if child_pid is not None:
            _kill_exact_test_pid(child_pid)
        if parent_pid is not None:
            _kill_exact_test_pid(parent_pid)
