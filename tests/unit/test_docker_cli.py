from __future__ import annotations

import math
import os
import signal
import subprocess
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import NoReturn, cast

import pytest

from trafficlab.capture.docker_cli import (
    CommandBoundary,
    CommandResult,
    DockerCompose,
    ProcessHandle,
    ServiceState,
    SubprocessBoundary,
)
from trafficlab.common.errors import TrafficlabError


class _Handle:
    def __init__(self, results: list[CommandResult | None] | None = None) -> None:
        self.results = list(results or [])
        self.wait_timeouts: list[float] = []
        self.terminated = False
        self.killed = False

    def wait(self, *, timeout: float) -> CommandResult | None:
        self.wait_timeouts.append(timeout)
        return self.results.pop(0) if self.results else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def reap(self) -> bool:
        return True


class _RecordingBoundary:
    def __init__(
        self,
        results: list[CommandResult] | None = None,
        *,
        handle: ProcessHandle | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.results = list(results or [])
        self.handle = handle or _Handle()
        self.error = error
        self.runs: list[tuple[tuple[str, ...], float, Mapping[str, str] | None]] = []
        self.starts: list[tuple[tuple[str, ...], Mapping[str, str] | None]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
        environment: Mapping[str, str] | None,
    ) -> CommandResult:
        self.runs.append((argv, timeout, environment))
        if self.error is not None:
            raise self.error
        if self.results:
            return self.results.pop(0)
        return CommandResult(returncode=0, stdout="", stderr="")

    def start(
        self,
        argv: tuple[str, ...],
        *,
        environment: Mapping[str, str] | None,
    ) -> ProcessHandle:
        self.starts.append((argv, environment))
        if self.error is not None:
            raise self.error
        return self.handle


def _docker(
    boundary: CommandBoundary,
    *,
    now: float = 100.0,
    environment: Mapping[str, str] | None = None,
) -> DockerCompose:
    return DockerCompose(boundary=boundary, clock=lambda: now, environment=environment)


def _compose_prefix(compose_path: Path, project: str = "trafficlab-run_1") -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(compose_path),
    )


def test_command_values_are_immutable() -> None:
    result = CommandResult(returncode=0, stdout="ok\n", stderr="")
    service = ServiceState(identifier="abc", name="project-capture-1", service="capture", state="running", exit_code=0)

    with pytest.raises(FrozenInstanceError):
        result.stdout = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        service.state = "exited"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("info", ("docker", "info", "--format", "{{json .}}")),
        ("compose_version", ("docker", "compose", "version", "--format", "json")),
        (
            "image_inspect",
            ("docker", "image", "inspect", "registry.example/target:1"),
        ),
        (
            "image_pull",
            ("docker", "image", "pull", "registry.example/target:1"),
        ),
        (
            "config",
            (
                "docker",
                "compose",
                "--project-name",
                "trafficlab-run_1",
                "--file",
                "{path}",
                "config",
                "--format",
                "json",
            ),
        ),
        (
            "create_capture",
            ("docker", "compose", "--project-name", "trafficlab-run_1", "--file", "{path}", "create", "capture"),
        ),
        (
            "start_capture",
            ("docker", "compose", "--project-name", "trafficlab-run_1", "--file", "{path}", "start", "capture"),
        ),
        (
            "start_target",
            (
                "docker",
                "compose",
                "--project-name",
                "trafficlab-run_1",
                "--file",
                "{path}",
                "up",
                "--detach",
                "--no-deps",
                "target",
            ),
        ),
        (
            "service_logs",
            (
                "docker",
                "compose",
                "--project-name",
                "trafficlab-run_1",
                "--file",
                "{path}",
                "logs",
                "--no-color",
                "capture",
            ),
        ),
        (
            "kill_target",
            ("docker", "compose", "--project-name", "trafficlab-run_1", "--file", "{path}", "kill", "target"),
        ),
        (
            "signal_capture",
            (
                "docker",
                "compose",
                "--project-name",
                "trafficlab-run_1",
                "--file",
                "{path}",
                "kill",
                "--signal",
                "SIGINT",
                "capture",
            ),
        ),
        (
            "kill_capture",
            ("docker", "compose", "--project-name", "trafficlab-run_1", "--file", "{path}", "kill", "capture"),
        ),
    ],
)
def test_bounded_commands_use_exact_direct_argv(
    tmp_path: Path,
    operation: str,
    expected: tuple[str, ...],
) -> None:
    results = [CommandResult(0, "{}", "")] if operation == "config" else None
    boundary = _RecordingBoundary(results)
    docker = _docker(boundary)
    compose_path = tmp_path / "compose.json"

    if operation == "info":
        docker.info(timeout=3.0)
    elif operation == "compose_version":
        docker.compose_version(timeout=3.0)
    elif operation == "image_inspect":
        docker.image_inspect("registry.example/target:1", timeout=3.0)
    elif operation == "image_pull":
        docker.image_pull("registry.example/target:1", timeout=3.0)
    elif operation == "config":
        docker.config(compose_path, "trafficlab-run_1", timeout=3.0)
    elif operation == "create_capture":
        docker.create_capture(compose_path, "trafficlab-run_1", timeout=3.0)
    elif operation == "start_capture":
        docker.start_capture(compose_path, "trafficlab-run_1", timeout=3.0)
    elif operation == "start_target":
        docker.start_target(compose_path, "trafficlab-run_1", timeout=3.0)
    elif operation == "service_logs":
        docker.service_logs(compose_path, "trafficlab-run_1", "capture", timeout=3.0)
    elif operation == "kill_target":
        docker.kill_target(compose_path, "trafficlab-run_1", timeout=3.0)
    elif operation == "signal_capture":
        docker.signal_capture(compose_path, "trafficlab-run_1", timeout=3.0)
    else:
        docker.kill_capture(compose_path, "trafficlab-run_1", timeout=3.0)

    wanted = tuple(str(compose_path) if item == "{path}" else item for item in expected)
    assert boundary.runs == [(wanted, 3.0, None)]
    assert wanted[0] == "docker"
    assert "sudo" not in wanted


def test_command_environment_is_copied_and_passed_to_boundary(tmp_path: Path) -> None:
    environment = {"DOCKER_HOST": "unix:///tmp/docker.sock"}
    boundary = _RecordingBoundary([CommandResult(0, "{}", "")])
    docker = _docker(boundary, environment=environment)
    environment["DOCKER_HOST"] = "changed"

    docker.config(tmp_path / "compose.json", "trafficlab-run", timeout=2.0)

    assert boundary.runs[0][2] == {"DOCKER_HOST": "unix:///tmp/docker.sock"}


def test_command_result_preserves_utf8_output() -> None:
    boundary = _RecordingBoundary([CommandResult(0, "Docker ✓\n", "warning ✓\n")])

    result = _docker(boundary).info(timeout=1.0)

    assert result == CommandResult(returncode=0, stdout="Docker ✓\n", stderr="warning ✓\n")


@pytest.mark.parametrize("stdout", ["not-json", "[]"])
def test_compose_config_rejects_invalid_json_document(tmp_path: Path, stdout: str) -> None:
    boundary = _RecordingBoundary([CommandResult(0, stdout, "")])

    with pytest.raises(TrafficlabError, match="invalid Docker Compose config JSON"):
        _docker(boundary).config(tmp_path / "compose.json", "trafficlab-run", timeout=1.0)


def test_service_state_decodes_one_typed_container(tmp_path: Path) -> None:
    stdout = (
        '[{"ID":"abc123","Name":"trafficlab-capture-1","Service":"capture",'
        '"State":"running","ExitCode":0,"Publishers":[]}]'
    )
    boundary = _RecordingBoundary([CommandResult(0, stdout, "")])
    compose_path = tmp_path / "compose.json"

    state = _docker(boundary).service_state(compose_path, "trafficlab-run", "capture", deadline=103.5)

    assert state == ServiceState(
        identifier="abc123",
        name="trafficlab-capture-1",
        service="capture",
        state="running",
        exit_code=0,
    )
    assert boundary.runs == [
        (_compose_prefix(compose_path, "trafficlab-run") + ("ps", "--all", "--format", "json", "capture"), 3.5, None)
    ]


def test_service_state_decodes_compose_json_lines_one_object_per_line(tmp_path: Path) -> None:
    stdout = (
        '{"ID":"abc123","Name":"trafficlab-capture-1","Service":"capture",'
        '"State":"running","ExitCode":0,"Publishers":[]}\n'
    )
    boundary = _RecordingBoundary([CommandResult(0, stdout, "")])

    state = _docker(boundary).service_state(tmp_path / "compose.json", "trafficlab-run", "capture", timeout=1.0)

    assert state == ServiceState("abc123", "trafficlab-capture-1", "capture", "running", 0)


def test_service_state_returns_none_when_service_is_absent(tmp_path: Path) -> None:
    boundary = _RecordingBoundary([CommandResult(0, "", "")])

    state = _docker(boundary).service_state(tmp_path / "compose.json", "trafficlab-run", "target", timeout=1.0)

    assert state is None


@pytest.mark.parametrize(
    "stdout",
    [
        "not-json",
        "{}",
        "1",
        "[1]",
        '[{"ID":"x"}]',
        '[{"ID":1,"Name":"n","Service":"capture","State":"running","ExitCode":0}]',
        '[{"ID":"x","Name":"n","Service":"capture","State":"running","ExitCode":true}]',
        '[{"ID":"x","Name":"n","Service":"capture","State":"running","ExitCode":-1}]',
        '[{"ID":"x","Name":"n","Service":"capture","State":"running","ExitCode":256}]',
        '[{"ID":"x","Name":"n","Service":"capture","State":"","ExitCode":0}]',
    ],
)
def test_invalid_inventory_json_is_an_actionable_error(tmp_path: Path, stdout: str) -> None:
    boundary = _RecordingBoundary([CommandResult(0, stdout, "")])

    with pytest.raises(TrafficlabError, match="invalid Docker Compose service inventory") as caught:
        _docker(boundary).service_state(tmp_path / "compose.json", "trafficlab-run", "capture", timeout=1.0)

    assert "Docker Compose" in caught.value.corrective_action


@pytest.mark.parametrize(
    "stdout",
    [
        '{"ID":"a","Name":"n1","Service":"capture","State":"running","ExitCode":0}\nnot-json\n',
        '{"ID":"a","Name":"n1","Service":"capture","State":"running","ExitCode":0}\n1\n',
        '{"ID":"a","ID":"b","Name":"n1","Service":"capture","State":"running","ExitCode":0}\n',
        '{"ID":"a","Name":"n1","Service":"capture","State":"running","ExitCode":0}\n'
        '{"ID":"a","Name":"n2","Service":"target","State":"running","ExitCode":0}\n',
        '{"ID":"a","Name":"same","Service":"capture","State":"running","ExitCode":0}\n'
        '{"ID":"b","Name":"same","Service":"target","State":"running","ExitCode":0}\n',
    ],
)
def test_inventory_rejects_malformed_json_lines_and_duplicate_keys_or_rows(tmp_path: Path, stdout: str) -> None:
    boundary = _RecordingBoundary([CommandResult(0, stdout, "")])

    with pytest.raises(TrafficlabError, match="invalid Docker Compose service inventory"):
        _docker(boundary).service_state(tmp_path / "compose.json", "trafficlab-run", "capture", timeout=1.0)


def test_service_state_rejects_duplicate_and_wrong_service_rows(tmp_path: Path) -> None:
    rows = (
        '[{"ID":"a","Name":"n1","Service":"target","State":"running","ExitCode":0},'
        '{"ID":"b","Name":"n2","Service":"target","State":"running","ExitCode":0}]'
    )
    duplicate = _RecordingBoundary([CommandResult(0, rows, "")])
    wrong = _RecordingBoundary(
        [CommandResult(0, '[{"ID":"a","Name":"n","Service":"target","State":"running","ExitCode":0}]', "")]
    )

    with pytest.raises(TrafficlabError, match="expected at most one"):
        _docker(duplicate).service_state(tmp_path / "compose.json", "trafficlab-run", "target", timeout=1.0)
    with pytest.raises(TrafficlabError, match="requested service capture"):
        _docker(wrong).service_state(tmp_path / "compose.json", "trafficlab-run", "capture", timeout=1.0)


@pytest.mark.parametrize(
    ("timeout", "deadline", "message"),
    [
        (None, None, "exactly one"),
        (1.0, 102.0, "exactly one"),
        (0.0, None, "positive finite"),
        (-1.0, None, "positive finite"),
        (math.inf, None, "positive finite"),
        (math.nan, None, "positive finite"),
        (True, None, "positive finite"),
        (None, 0.0, "positive finite"),
        (None, math.inf, "positive finite"),
    ],
)
def test_invalid_budgets_fail_before_launch(
    timeout: float | None,
    deadline: float | None,
    message: str,
) -> None:
    boundary = _RecordingBoundary()

    with pytest.raises(TrafficlabError, match=message):
        _docker(boundary).info(timeout=timeout, deadline=deadline)

    assert boundary.runs == []


def test_exhausted_deadline_launches_no_process() -> None:
    boundary = _RecordingBoundary()

    with pytest.raises(TrafficlabError, match="deadline expired"):
        _docker(boundary).info(deadline=100.0)

    assert boundary.runs == []


def test_huge_integer_timeout_and_deadline_fail_before_launch() -> None:
    timeout_boundary = _RecordingBoundary()
    deadline_boundary = _RecordingBoundary()

    with pytest.raises(TrafficlabError, match="timeout must be a positive finite number"):
        _docker(timeout_boundary).info(timeout=10**10_000)
    with pytest.raises(TrafficlabError, match="deadline must be a positive finite number"):
        _docker(deadline_boundary).info(deadline=10**10_000)

    assert timeout_boundary.runs == []
    assert deadline_boundary.runs == []


@pytest.mark.parametrize("project", ["", "UPPER", "-leading", "bad space", "bad.name"])
def test_invalid_project_name_fails_before_launch(tmp_path: Path, project: str) -> None:
    boundary = _RecordingBoundary()

    with pytest.raises(TrafficlabError, match="invalid Docker Compose project name"):
        _docker(boundary).config(tmp_path / "compose.json", project, timeout=1.0)

    assert boundary.runs == []


def test_relative_compose_path_fails_before_launch() -> None:
    boundary = _RecordingBoundary()

    with pytest.raises(TrafficlabError, match="absolute"):
        _docker(boundary).config(Path("compose.json"), "trafficlab-run", timeout=1.0)

    assert boundary.runs == []


@pytest.mark.parametrize("service", ["", "worker", "--help"])
def test_unknown_production_service_fails_before_launch(tmp_path: Path, service: str) -> None:
    boundary = _RecordingBoundary()

    with pytest.raises(TrafficlabError, match="capture or target"):
        _docker(boundary).service_logs(tmp_path / "compose.json", "trafficlab-run", service, timeout=1.0)

    assert boundary.runs == []


def test_nonzero_command_is_an_actionable_error(tmp_path: Path) -> None:
    boundary = _RecordingBoundary([CommandResult(17, "", "daemon unavailable\n")])

    with pytest.raises(TrafficlabError, match="Docker info failed with status 17.*daemon unavailable") as caught:
        _docker(boundary).info(timeout=1.0)

    assert "Docker daemon" in caught.value.corrective_action


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FileNotFoundError("docker missing"), "Docker executable was not found"),
        (PermissionError("denied"), "could not launch Docker command"),
        (subprocess.TimeoutExpired(("docker", "info"), 1.0), "Docker info timed out"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), "invalid UTF-8"),
    ],
)
def test_boundary_failures_never_escape_raw(error: BaseException, message: str) -> None:
    boundary = _RecordingBoundary(error=error)

    with pytest.raises(TrafficlabError, match=message):
        _docker(boundary).info(timeout=1.0)


def test_existing_trafficlab_boundary_error_is_preserved() -> None:
    expected = TrafficlabError("specific boundary failure", corrective_action="specific repair")

    with pytest.raises(TrafficlabError) as caught:
        _docker(_RecordingBoundary(error=expected)).info(timeout=1.0)

    assert caught.value is expected


def test_deadline_clock_arithmetic_failure_is_actionable() -> None:
    def broken_clock() -> float:
        raise OverflowError("clock overflow")

    boundary = _RecordingBoundary()
    docker = DockerCompose(boundary=boundary, clock=broken_clock)

    with pytest.raises(TrafficlabError, match="calculate.*deadline"):
        docker.info(deadline=10.0)

    assert boundary.runs == []


def test_start_down_uses_a_direct_process_and_exact_cleanup_scope(tmp_path: Path) -> None:
    handle = _Handle()
    boundary = _RecordingBoundary(handle=handle)
    compose_path = tmp_path / "compose.json"

    returned = _docker(boundary).start_down(compose_path, "trafficlab-run", deadline=102.0)

    assert returned is handle
    assert boundary.starts == [
        (
            _compose_prefix(compose_path, "trafficlab-run") + ("down", "--volumes", "--remove-orphans"),
            None,
        )
    ]
    assert boundary.runs == []


def test_start_down_rejects_exhausted_deadline_without_starting(tmp_path: Path) -> None:
    boundary = _RecordingBoundary()

    with pytest.raises(TrafficlabError, match="deadline expired"):
        _docker(boundary).start_down(tmp_path / "compose.json", "trafficlab-run", deadline=100.0)

    assert boundary.starts == []


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (TrafficlabError("preserved", corrective_action="repair"), "preserved"),
        (FileNotFoundError("missing"), "Docker executable was not found"),
        (PermissionError("denied"), "could not launch Docker cleanup command"),
    ],
)
def test_start_down_translates_launch_failures(tmp_path: Path, error: BaseException, message: str) -> None:
    boundary = _RecordingBoundary(error=error)

    with pytest.raises(TrafficlabError, match=message):
        _docker(boundary).start_down(tmp_path / "compose.json", "trafficlab-run", timeout=1.0)


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
