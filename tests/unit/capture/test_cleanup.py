from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from trafficlab.capture.cleanup import CleanupResult, cleanup_project
from trafficlab.capture.docker_cli import CommandResult, ProcessHandle
from trafficlab.common.errors import TrafficlabError


class _Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _SequenceClock:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def __call__(self) -> object:
        return self.values.pop(0)


class _BrokenFloatInt(int):
    def __float__(self) -> float:
        raise TypeError("cannot convert")


class _SequenceHandle:
    def __init__(
        self,
        clock: _Clock,
        results: list[tuple[float, CommandResult | None]],
        *,
        wait_errors: list[BaseException | None] | None = None,
        terminate_error: BaseException | None = None,
        kill_error: BaseException | None = None,
        reap_error: BaseException | None = None,
        reap_result: bool = True,
        terminate_advance: float = 0.0,
        kill_advance: float = 0.0,
    ) -> None:
        self.clock = clock
        self.results = results
        self.wait_errors = wait_errors
        self.terminate_error = terminate_error
        self.kill_error = kill_error
        self.reap_error = reap_error
        self.reap_result = reap_result
        self.terminate_advance = terminate_advance
        self.kill_advance = kill_advance
        self.waits: list[float] = []
        self.actions: list[str] = []

    def wait(self, *, timeout: float) -> CommandResult | None:
        self.actions.append("wait")
        self.waits.append(timeout)
        if self.wait_errors:
            error = self.wait_errors.pop(0)
            if error is not None:
                raise error
        advance, result = self.results.pop(0)
        self.clock.now += advance
        return result

    def terminate(self) -> None:
        self.actions.append("terminate")
        self.clock.now += self.terminate_advance
        if self.terminate_error is not None:
            raise self.terminate_error

    def kill(self) -> None:
        self.actions.append("kill")
        self.clock.now += self.kill_advance
        if self.kill_error is not None:
            raise self.kill_error

    def reap(self) -> bool:
        self.actions.append("reap")
        if self.reap_error is not None:
            raise self.reap_error
        return self.reap_result


class _Docker:
    def __init__(
        self,
        handle: ProcessHandle,
        *,
        clock: _Clock,
        launch_advance: float = 0.0,
        start_error: BaseException | None = None,
    ) -> None:
        self.handle = handle
        self.clock = clock
        self.launch_advance = launch_advance
        self.start_error = start_error
        self.calls: list[tuple[Path, str, float]] = []

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> ProcessHandle:
        self.calls.append((compose_path, project_name, deadline))
        if self.start_error is not None:
            raise self.start_error
        self.clock.now += self.launch_advance
        return self.handle


class _NoDocker:
    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> ProcessHandle:
        raise AssertionError(f"cleanup launched Docker for {compose_path} {project_name} {deadline}")


def test_cleanup_result_is_strict_and_immutable() -> None:
    """A contradictory or mutable result could corrupt capture failure arbitration."""
    result = CleanupResult(success=True, timed_out=False, detail="project resources removed")

    with pytest.raises(FrozenInstanceError):
        result.success = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="successful cleanup cannot be timed out"):
        CleanupResult(success=True, timed_out=True, detail="contradiction")
    with pytest.raises(TypeError, match="success must be a boolean"):
        CleanupResult(success=1, timed_out=False, detail="wrong type")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="timed_out must be a boolean"):
        CleanupResult(success=False, timed_out=1, detail="wrong type")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="detail must be a string"):
        CleanupResult(success=False, timed_out=False, detail=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="detail must be nonempty"):
        CleanupResult(success=False, timed_out=False, detail=" ")


@pytest.mark.parametrize(
    "deadline",
    [True, "160", 0.0, float("inf"), 10**10_000, _BrokenFloatInt(160)],
    ids=["bool", "string", "zero", "infinite", "huge-int", "conversion-error"],
)
def test_invalid_deadline_is_rejected_before_docker_access(tmp_path: Path, deadline: object) -> None:
    """Invalid arithmetic must fail locally instead of permitting an unbounded Docker operation."""
    with pytest.raises((TypeError, ValueError), match="deadline must be a positive finite number"):
        cleanup_project(
            _NoDocker(),
            (tmp_path / "compose.json").resolve(),
            "trafficlab-run",
            deadline=deadline,  # type: ignore[arg-type]
            clock=lambda: 100.0,
        )


@pytest.mark.parametrize(
    "clock",
    [lambda: True, lambda: "100", lambda: float("nan"), lambda: 10**10_000, lambda: _BrokenFloatInt(100)],
)
def test_invalid_clock_value_is_rejected_before_docker_access(tmp_path: Path, clock: object) -> None:
    """A malformed clock value must not permit launch with an unreliable bound."""
    with pytest.raises(ValueError, match="clock must return a finite number"):
        cleanup_project(
            _NoDocker(),
            (tmp_path / "compose.json").resolve(),
            "trafficlab-run",
            deadline=160.0,
            clock=clock,  # type: ignore[arg-type]
        )


def test_clock_arithmetic_failure_is_translated_before_docker_access(tmp_path: Path) -> None:
    """A broken monotonic boundary must become a local validation failure."""

    def broken_clock() -> float:
        raise OverflowError("overflow")

    with pytest.raises(ValueError, match="clock must return a finite number"):
        cleanup_project(
            _NoDocker(),
            (tmp_path / "compose.json").resolve(),
            "trafficlab-run",
            deadline=160.0,
            clock=broken_clock,
        )


@pytest.mark.parametrize(
    ("compose_path", "project_name", "error"),
    [
        ("/tmp/compose.json", "trafficlab-run", "compose_path must be a Path"),
        (Path("relative.json"), "trafficlab-run", "compose_path must be absolute"),
        (Path("/tmp/compose.json"), True, "project_name must be a string"),
        (Path("/tmp/compose.json"), "Wrong Project", "invalid cleanup project name"),
        (Path("/tmp/compose.json"), "-trafficlab", "invalid cleanup project name"),
    ],
    ids=["path-type", "relative-path", "project-type", "unsafe-project", "leading-dash"],
)
def test_invalid_cleanup_scope_is_rejected_before_docker_access(
    compose_path: object,
    project_name: object,
    error: str,
) -> None:
    """An ambiguous scope could target a project other than the one explicitly owned."""
    with pytest.raises((TypeError, ValueError), match=error):
        cleanup_project(
            _NoDocker(),
            compose_path,  # type: ignore[arg-type]
            project_name,  # type: ignore[arg-type]
            deadline=160.0,
            clock=lambda: 100.0,
        )


def test_zero_budget_returns_timeout_without_docker_access(tmp_path: Path) -> None:
    """Cleanup must not launch a potentially blocking CLI after the absolute deadline."""
    result = cleanup_project(
        _NoDocker(),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=100.0,
        clock=lambda: 100.0,
    )

    assert result == CleanupResult(
        success=False,
        timed_out=True,
        detail="cleanup deadline expired before launch; project resources may remain",
    )


def test_success_uses_one_down_process_and_no_inventory_query(tmp_path: Path) -> None:
    """Production cleanup must issue one down and must not reintroduce post-down inventory states."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(3.0, CommandResult(0, "removed\n", ""))])
    docker = _Docker(handle, clock=clock, launch_advance=2.0)
    compose_path = (tmp_path / "compose.json").resolve()

    result = cleanup_project(
        docker,
        compose_path,
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result == CleanupResult(success=True, timed_out=False, detail="project resources removed: removed")
    assert docker.calls == [(compose_path, "trafficlab-run", 160.0)]
    assert handle.actions == ["wait", "reap"]
    assert handle.waits == [57.0]


@pytest.mark.parametrize(
    ("command", "detail"),
    [
        (CommandResult(17, "ignored stdout", "daemon refused\n"), "daemon refused"),
        (CommandResult(17, "stdout detail\n", ""), "stdout detail"),
        (CommandResult(17, "", ""), "no command output"),
    ],
)
def test_nonzero_cleanup_reports_status_and_actionable_command_output(
    tmp_path: Path,
    command: CommandResult,
    detail: str,
) -> None:
    """A failed down must retain the status and best available command diagnostic."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(0.0, command)])
    docker = _Docker(handle, clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result == CleanupResult(
        success=False,
        timed_out=False,
        detail=f"cleanup command failed with status 17: {detail}",
    )
    assert len(docker.calls) == 1
    assert handle.actions == ["wait", "reap"]


@pytest.mark.parametrize(
    ("start_error", "detail"),
    [
        (PermissionError("launch denied"), "could not launch cleanup command: launch denied"),
        (TrafficlabError("specific launch failure", corrective_action="repair"), "specific launch failure"),
    ],
)
def test_launch_failure_is_returned_as_actionable_result(
    tmp_path: Path,
    start_error: BaseException,
    detail: str,
) -> None:
    """A local launch failure must enter normal cleanup arbitration instead of escaping raw."""
    clock = _Clock()
    docker = _Docker(_SequenceHandle(clock, []), clock=clock, start_error=start_error)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result == CleanupResult(success=False, timed_out=False, detail=detail)


def test_initial_wait_failure_stops_local_cli_and_preserves_wait_error(tmp_path: Path) -> None:
    """A wait boundary failure must not strand the local CLI or replace its primary diagnostic."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [],
        wait_errors=[PermissionError("wait denied")],
        terminate_error=PermissionError("terminate denied"),
        kill_error=PermissionError("kill denied"),
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result.detail.startswith("could not wait for cleanup command: wait denied")
    assert "could not terminate cleanup command: terminate denied" in result.detail
    assert "could not kill cleanup command: kill denied" in result.detail
    assert handle.actions == ["wait", "terminate", "kill", "reap"]


def test_hanging_cleanup_terminates_and_reaps_with_fresh_budget(tmp_path: Path) -> None:
    """A cooperative hung CLI must be terminated and reaped before the absolute deadline."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(10.0, None), (4.0, CommandResult(-15, "", ""))],
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert result.detail == "cleanup command exceeded its deadline; project resources may remain"
    assert handle.actions == ["wait", "terminate", "wait", "reap"]
    assert handle.waits == [59.0, 25.0]


def test_uncooperative_cleanup_is_killed_and_reaped_with_fresh_budgets(tmp_path: Path) -> None:
    """A SIGTERM-ignoring CLI must be killed and reaped without a later Docker query."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [
            (10.0, None),
            (10.0, None),
            (5.0, CommandResult(-9, "", "")),
        ],
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert result.detail == "cleanup command exceeded its deadline; project resources may remain"
    assert handle.actions == ["wait", "terminate", "wait", "kill", "wait", "reap"]
    assert handle.waits == [59.0, 25.0, 40.0]


def test_launch_consuming_budget_stops_process_without_nonpositive_wait(tmp_path: Path) -> None:
    """A process returned at deadline must be stopped without passing a nonpositive wait timeout."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [])

    result = cleanup_project(
        _Docker(handle, clock=clock, launch_advance=60.0),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert handle.actions == ["terminate", "kill", "reap"]
    assert handle.waits == []


def test_signal_time_is_deducted_before_next_wait(tmp_path: Path) -> None:
    """A signal that consumes the budget must prevent a stale positive reap wait."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(10.0, None)],
        terminate_advance=50.0,
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert handle.actions == ["wait", "terminate", "kill", "reap"]
    assert handle.waits == [59.0]


def test_terminate_failure_is_actionable_after_kill_and_reap(tmp_path: Path) -> None:
    """A failed graceful stop must remain visible even when forceful containment succeeds."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(1.0, None), (1.0, CommandResult(-9, "", ""))],
        terminate_error=PermissionError("terminate denied"),
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result == CleanupResult(
        success=False,
        timed_out=False,
        detail="could not terminate cleanup command: terminate denied",
    )
    assert handle.actions == ["wait", "terminate", "kill", "wait", "reap"]


def test_tiny_positive_budget_starts_in_stop_state_and_reaps_without_nonpositive_wait(tmp_path: Path) -> None:
    """A sub-reserve budget must contain and reap rather than enter the ordinary wait."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(0.02, None), (0.01, CommandResult(-9, "", ""))],
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=100.05,
        clock=clock,
    )

    assert result.timed_out
    assert handle.actions == ["terminate", "wait", "kill", "wait", "reap"]
    assert handle.waits == pytest.approx([0.025, 0.03])
    assert all(timeout > 0.0 for timeout in handle.waits)


def test_kill_consuming_remaining_budget_still_enters_nonblocking_reap(tmp_path: Path) -> None:
    """Budget expiry during kill must not return without a final nonblocking reap attempt."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(10.0, None), (10.0, None)],
        kill_advance=40.0,
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert handle.actions == ["wait", "terminate", "wait", "kill", "reap"]
    assert handle.waits == [59.0, 25.0]


def test_kill_failure_remains_actionable_and_still_attempts_nonblocking_reap(tmp_path: Path) -> None:
    """A failed SIGKILL must be visible without skipping the direct-child reap state."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(1.0, None), (1.0, None)],
        kill_error=PermissionError("kill denied"),
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result.detail == "could not kill cleanup command: kill denied"
    assert handle.actions == ["wait", "terminate", "wait", "kill", "reap"]


@pytest.mark.parametrize(
    ("results", "wait_errors", "detail", "actions"),
    [
        (
            [(1.0, None), (1.0, CommandResult(0, "", ""))],
            [None, PermissionError("terminated reap denied")],
            "could not reap terminated cleanup command: terminated reap denied",
            ["wait", "terminate", "wait", "terminate", "kill", "reap"],
        ),
        (
            [(1.0, None), (1.0, None), (1.0, CommandResult(-9, "", ""))],
            [None, None, PermissionError("killed reap denied")],
            "could not reap killed cleanup command: killed reap denied",
            ["wait", "terminate", "wait", "kill", "wait", "reap"],
        ),
    ],
    ids=["terminated-reap", "killed-reap"],
)
def test_reap_wait_failures_remain_actionable_after_nonblocking_reap(
    tmp_path: Path,
    results: list[tuple[float, CommandResult | None]],
    wait_errors: list[BaseException | None],
    detail: str,
    actions: list[str],
) -> None:
    """A bounded reap-wait error must retain its diagnostic and still enter nonblocking reap."""
    clock = _Clock()
    handle = _SequenceHandle(clock, results, wait_errors=wait_errors)

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert result.detail == detail
    assert handle.actions == actions


@pytest.mark.parametrize(
    "clock_values",
    [
        [100.0, float("nan")],
        [100.0, 100.0, float("nan")],
        [100.0, 100.0, 100.0, float("nan")],
    ],
    ids=["after-launch", "after-terminate", "after-kill"],
)
def test_post_launch_clock_failures_are_contained_as_cleanup_results(
    tmp_path: Path,
    clock_values: list[object],
) -> None:
    """A malformed cleanup clock after launch must not escape before local process containment."""
    clock = _SequenceClock(clock_values)
    handle = _SequenceHandle(
        _Clock(),
        [(0.0, None), (0.0, None)],
    )

    result = cleanup_project(
        _Docker(handle, clock=_Clock()),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,  # type: ignore[arg-type]
    )

    assert not result.success
    assert "cleanup clock failed after launch" in result.detail
    assert handle.actions[-2:] == ["kill", "reap"]


def test_clock_failure_after_failed_terminate_is_secondary_to_signal_error(tmp_path: Path) -> None:
    """The terminate-error recovery branch must also contain malformed clock reads after launch."""
    clock = _SequenceClock([100.0, 100.0, float("nan")])
    handle = _SequenceHandle(
        _Clock(),
        [(0.0, None)],
        terminate_error=PermissionError("terminate denied"),
    )

    result = cleanup_project(
        _Docker(handle, clock=_Clock()),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,  # type: ignore[arg-type]
    )

    assert result.detail.startswith("could not terminate cleanup command: terminate denied")
    assert "cleanup clock failed after launch" in result.detail
    assert handle.actions == ["wait", "terminate", "kill", "reap"]


@pytest.mark.parametrize(
    ("reap_error", "reap_result", "detail"),
    [
        (PermissionError("reap denied"), True, "could not reap cleanup command: reap denied"),
        (None, False, "local cleanup command has not exited after nonblocking reap"),
    ],
    ids=["reap-error", "still-running"],
)
def test_completed_command_requires_successful_explicit_reap_state(
    tmp_path: Path,
    reap_error: BaseException | None,
    reap_result: bool,
    detail: str,
) -> None:
    """A completed down cannot report success when its explicit direct-child reap state fails."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(0.0, CommandResult(0, "", ""))],
        reap_error=reap_error,
        reap_result=reap_result,
    )

    result = cleanup_project(
        _Docker(handle, clock=clock),
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        deadline=160.0,
        clock=clock,
    )

    assert not result.success
    assert result.detail == f"project resources removed; {detail}"
    assert handle.actions == ["wait", "reap"]
