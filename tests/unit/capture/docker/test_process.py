from __future__ import annotations

import os
import signal
import subprocess
from typing import NoReturn, cast

import pytest

from trafficlab.capture.docker.process import SubprocessBoundary
from trafficlab.capture.docker.types import CommandResult, ProcessHandle
from trafficlab.common.errors import TrafficlabError


def test_subprocess_boundary_runs_without_shell_and_decodes_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.update({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, stdout="вывод".encode(), stderr="ошибка".encode())

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = SubprocessBoundary().run(
        ("docker", "info"),
        timeout=2.5,
        environment={"DOCKER_HOST": "unix:///socket"},
    )

    assert result == CommandResult(0, "вывод", "ошибка")
    assert seen == {
        "argv": ("docker", "info"),
        "capture_output": True,
        "check": False,
        "env": {"DOCKER_HOST": "unix:///socket"},
        "shell": False,
        "timeout": 2.5,
    }


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FileNotFoundError("missing"), "Docker executable was not found"),
        (subprocess.TimeoutExpired(("docker", "info"), 1.0), "timed out"),
        (PermissionError("denied"), "could not launch Docker command"),
    ],
)
def test_subprocess_boundary_translates_run_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    message: str,
) -> None:
    def fail_run(*args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(TrafficlabError, match=message):
        SubprocessBoundary().run(("docker", "info"), timeout=1.0, environment=None)


def test_subprocess_boundary_rejects_invalid_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(("docker", "info"), 0, stdout=b"\xff", stderr=b"")

    monkeypatch.setattr(subprocess, "run", invalid_run)

    with pytest.raises(TrafficlabError, match="invalid UTF-8"):
        SubprocessBoundary().run(("docker", "info"), timeout=1.0, environment=None)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FileNotFoundError("missing"), "Docker executable was not found"),
        (PermissionError("denied"), "could not launch Docker cleanup command"),
    ],
)
def test_subprocess_boundary_translates_start_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    message: str,
) -> None:
    def fail_start(*args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(subprocess, "Popen", fail_start)

    with pytest.raises(TrafficlabError, match=message):
        SubprocessBoundary().start(("docker", "info"), environment=None)


class _BrokenProcess:
    def __init__(
        self,
        *,
        communicate_error: OSError | None = None,
        returncode: int | None = 0,
        signal_error: OSError | None = None,
        poll_result: int | None = 0,
    ) -> None:
        self.communicate_error = communicate_error
        self.returncode = returncode
        self.signal_error = signal_error
        self.poll_result = poll_result
        self.pid = 12345

    def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
        if self.communicate_error is not None:
            raise self.communicate_error
        return b"", b""

    def terminate(self) -> None:
        if self.signal_error is not None:
            raise self.signal_error

    def kill(self) -> None:
        if self.signal_error is not None:
            raise self.signal_error

    def poll(self) -> int | None:
        if self.signal_error is not None:
            raise self.signal_error
        return self.poll_result


def _install_process(monkeypatch: pytest.MonkeyPatch, process: _BrokenProcess) -> ProcessHandle:
    def fake_start(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        return cast("subprocess.Popen[bytes]", process)

    monkeypatch.setattr(subprocess, "Popen", fake_start)
    return SubprocessBoundary().start(("docker", "compose", "down"), environment=None)


def test_subprocess_handle_translates_wait_and_signal_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    wait_handle = _install_process(monkeypatch, _BrokenProcess(communicate_error=PermissionError("wait denied")))
    signal_handle = _install_process(monkeypatch, _BrokenProcess())

    def fail_group_signal(group: int, sent: signal.Signals) -> NoReturn:
        del group, sent
        raise PermissionError("signal denied")

    with pytest.raises(TrafficlabError, match="wait for Docker cleanup"):
        wait_handle.wait(timeout=1.0)
    monkeypatch.setattr(os, "killpg", fail_group_signal)
    with pytest.raises(TrafficlabError, match="terminate Docker cleanup"):
        signal_handle.terminate()
    with pytest.raises(TrafficlabError, match="kill Docker cleanup"):
        signal_handle.kill()


def test_subprocess_handle_rejects_missing_exit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = _install_process(monkeypatch, _BrokenProcess(returncode=None))

    with pytest.raises(TrafficlabError, match="without an exit status"):
        handle.wait(timeout=1.0)


def test_subprocess_handle_timeout_can_be_terminated_with_bounded_wait() -> None:
    handle = SubprocessBoundary().start(
        (
            "python3",
            "-c",
            "import signal,time;signal.signal(signal.SIGTERM,lambda *_: None);time.sleep(30)",
        ),
        environment=None,
    )
    try:
        assert handle.wait(timeout=0.2) is None
        handle.terminate()
        assert handle.wait(timeout=0.01) is None
        handle.kill()
        result = handle.wait(timeout=1.0)
        assert result is not None
        assert result.returncode < 0
    finally:
        handle.kill()
        handle.wait(timeout=1.0)


def test_subprocess_boundary_start_creates_isolated_cleanup_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup descendants must not share the caller's process group or escape group signalling."""
    seen: dict[str, object] = {}
    process = _BrokenProcess()

    def fake_start(argv: tuple[str, ...], **kwargs: object) -> subprocess.Popen[bytes]:
        seen.update({"argv": argv, **kwargs})
        return cast("subprocess.Popen[bytes]", process)

    monkeypatch.setattr(subprocess, "Popen", fake_start)

    SubprocessBoundary().start(("docker", "compose", "down"), environment={"DOCKER_HOST": "test"})

    assert seen == {
        "argv": ("docker", "compose", "down"),
        "env": {"DOCKER_HOST": "test"},
        "shell": False,
        "stderr": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "start_new_session": os.name == "posix",
    }


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_subprocess_handle_signals_complete_cleanup_group_and_supports_nonblocking_reap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signalling only the Compose PID would leave descendants and inherited pipes alive."""
    process = _BrokenProcess(poll_result=0)
    handle = _install_process(monkeypatch, process)
    signals: list[tuple[int, signal.Signals]] = []

    def record_signal(group: int, sent: signal.Signals) -> None:
        signals.append((group, sent))

    monkeypatch.setattr(os, "killpg", record_signal)

    handle.terminate()
    handle.kill()

    assert handle.reap()
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
