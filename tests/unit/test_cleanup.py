from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from trafficlab.cleanup import CleanupResult, cleanup_project
from trafficlab.docker_cli import CommandResult, ProcessHandle, ProjectInventory, ServiceState
from trafficlab.errors import TrafficlabError


def _inventory(project: str = "trafficlab-run") -> ProjectInventory:
    return ProjectInventory(
        containers=(
            ServiceState(
                identifier="capture-id",
                name=f"{project}-capture-1",
                service="capture",
                state="running",
                exit_code=0,
            ),
        )
    )


class _NoDocker:
    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> ProcessHandle:
        raise AssertionError(f"zero-budget cleanup launched Docker for {compose_path} {project_name} {deadline}")

    def project_inventory(self, compose_path: Path, project_name: str, *, deadline: float) -> ProjectInventory:
        raise AssertionError(f"zero-budget cleanup queried Docker for {compose_path} {project_name} {deadline}")


class _Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _BrokenFloatInt(int):
    def __float__(self) -> float:
        raise TypeError("cannot convert")


class _SequenceHandle:
    def __init__(
        self,
        clock: _Clock,
        results: list[tuple[float, CommandResult | None]],
        *,
        wait_error: BaseException | None = None,
        wait_errors: list[BaseException | None] | None = None,
        terminate_error: BaseException | None = None,
        kill_error: BaseException | None = None,
        terminate_advance: float = 0.0,
        kill_advance: float = 0.0,
    ) -> None:
        self.clock = clock
        self.results = results
        self.wait_error = wait_error
        self.wait_errors = wait_errors
        self.terminate_error = terminate_error
        self.kill_error = kill_error
        self.terminate_advance = terminate_advance
        self.kill_advance = kill_advance
        self.waits: list[float] = []
        self.actions: list[str] = []

    def wait(self, *, timeout: float) -> CommandResult | None:
        self.actions.append("wait")
        self.waits.append(timeout)
        error = self.wait_error if self.wait_errors is None else self.wait_errors.pop(0)
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


class _Docker:
    def __init__(
        self,
        handle: ProcessHandle,
        inventory: ProjectInventory,
        *,
        clock: _Clock,
        launch_advance: float = 0.0,
        start_error: BaseException | None = None,
        inventory_error: BaseException | None = None,
        inventory_advance: float = 0.0,
    ) -> None:
        self.handle = handle
        self.inventory = inventory
        self.clock = clock
        self.launch_advance = launch_advance
        self.start_error = start_error
        self.inventory_error = inventory_error
        self.inventory_advance = inventory_advance
        self.calls: list[tuple[str, Path, str, float]] = []

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> ProcessHandle:
        self.calls.append(("start_down", compose_path, project_name, deadline))
        if self.start_error is not None:
            raise self.start_error
        self.clock.now += self.launch_advance
        return self.handle

    def project_inventory(self, compose_path: Path, project_name: str, *, deadline: float) -> ProjectInventory:
        self.calls.append(("project_inventory", compose_path, project_name, deadline))
        self.clock.now += self.inventory_advance
        if self.inventory_error is not None:
            raise self.inventory_error
        return self.inventory


def test_cleanup_result_is_strict_and_immutable() -> None:
    """Mutable or contradictory cleanup evidence could later overstate whether resources were removed."""
    empty = ProjectInventory(containers=())
    result = CleanupResult(success=True, timed_out=False, detail="project resources removed", possibly_remaining=empty)

    with pytest.raises(FrozenInstanceError):
        result.success = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="successful cleanup cannot be timed out"):
        CleanupResult(success=True, timed_out=True, detail="contradiction", possibly_remaining=empty)
    with pytest.raises(ValueError, match="successful cleanup cannot report possibly remaining resources"):
        CleanupResult(success=True, timed_out=False, detail="contradiction", possibly_remaining=_inventory())
    with pytest.raises(TypeError, match="success must be a boolean"):
        CleanupResult(success=1, timed_out=False, detail="wrong type", possibly_remaining=empty)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="timed_out must be a boolean"):
        CleanupResult(success=False, timed_out=1, detail="wrong type", possibly_remaining=empty)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="detail must be a string"):
        CleanupResult(success=False, timed_out=False, detail=1, possibly_remaining=empty)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="detail must be nonempty"):
        CleanupResult(success=False, timed_out=False, detail=" ", possibly_remaining=empty)
    with pytest.raises(TypeError, match="possibly_remaining must be a ProjectInventory"):
        CleanupResult(success=False, timed_out=False, detail="wrong type", possibly_remaining=())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="containers must contain ServiceState"):
        CleanupResult(
            success=False,
            timed_out=False,
            detail="wrong contents",
            possibly_remaining=ProjectInventory(containers=(object(),)),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="secondary_details must be a tuple"):
        CleanupResult(
            success=False,
            timed_out=False,
            detail="wrong secondary type",
            possibly_remaining=empty,
            secondary_details=[],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="secondary_details must contain nonempty strings"):
        CleanupResult(
            success=False,
            timed_out=False,
            detail="empty secondary",
            possibly_remaining=empty,
            secondary_details=("",),
        )
    with pytest.raises(ValueError, match="successful cleanup cannot report secondary failure details"):
        CleanupResult(
            success=True,
            timed_out=False,
            detail="contradiction",
            possibly_remaining=empty,
            secondary_details=("later failure",),
        )


def test_cleanup_success_rejects_remaining_network_or_volume() -> None:
    """Container-only success would hide Compose networks or volumes left behind."""
    with pytest.raises(ValueError, match="possibly remaining resources"):
        CleanupResult(
            success=True,
            timed_out=False,
            detail="contradiction",
            possibly_remaining=ProjectInventory(containers=(), networks=("project_default",)),
        )
    with pytest.raises(ValueError, match="possibly remaining resources"):
        CleanupResult(
            success=True,
            timed_out=False,
            detail="contradiction",
            possibly_remaining=ProjectInventory(containers=(), volumes=("project_data",)),
        )


def test_cleanup_reports_fresh_noncontainer_resources_after_successful_down(tmp_path: Path) -> None:
    """A zero-status down is not clean while a labelled network or volume remains."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(0.0, CommandResult(0, "", ""))])
    remaining = ProjectInventory(containers=(), networks=("project_default",), volumes=("project_data",))
    docker = _Docker(handle, remaining, clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        ProjectInventory(containers=()),
        deadline=160.0,
        clock=clock,
    )

    assert not result.success
    assert result.possibly_remaining == remaining
    assert result.detail == "cleanup completed but project resources remain"


@pytest.mark.parametrize(
    "deadline",
    [True, "160", 0.0, float("inf"), 10**10_000, _BrokenFloatInt(160)],
    ids=["bool", "string", "zero", "infinite", "huge-int", "conversion-error"],
)
def test_invalid_deadline_is_rejected_before_docker_access(tmp_path: Path, deadline: object) -> None:
    """Invalid arithmetic inputs must fail locally instead of becoming an unbounded Docker operation."""
    with pytest.raises((TypeError, ValueError), match="deadline must be a positive finite number"):
        cleanup_project(
            _NoDocker(),
            (tmp_path / "compose.json").resolve(),
            "trafficlab-run",
            _inventory(),
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
            _inventory(),
            deadline=160.0,
            clock=clock,  # type: ignore[arg-type]
        )


def test_clock_arithmetic_failure_is_translated_before_docker_access(tmp_path: Path) -> None:
    """A broken monotonic boundary must become a local validation failure rather than a raw exception."""

    def broken_clock() -> float:
        raise OverflowError("overflow")

    with pytest.raises(ValueError, match="clock must return a finite number"):
        cleanup_project(
            _NoDocker(),
            (tmp_path / "compose.json").resolve(),
            "trafficlab-run",
            _inventory(),
            deadline=160.0,
            clock=broken_clock,
        )


def test_zero_budget_makes_no_docker_call_and_preserves_last_known_inventory(tmp_path: Path) -> None:
    """Launching or querying Docker after expiry would block cleanup and erase the only trustworthy inventory."""
    known = _inventory()

    result = cleanup_project(
        _NoDocker(),
        tmp_path / "compose.json",
        "trafficlab-run",
        known,
        deadline=100.0,
        clock=lambda: 100.0,
    )

    assert result == CleanupResult(
        success=False,
        timed_out=True,
        detail="cleanup deadline expired before launch; project resources may remain",
        possibly_remaining=known,
    )


@pytest.mark.parametrize(
    ("compose_path", "project_name", "inventory", "error"),
    [
        ("/tmp/compose.json", "trafficlab-run", _inventory(), "compose_path must be a Path"),
        (Path("relative.json"), "trafficlab-run", _inventory(), "compose_path must be absolute"),
        (Path("/tmp/compose.json"), True, _inventory(), "project_name must be a string"),
        (Path("/tmp/compose.json"), "Wrong Project", _inventory(), "invalid cleanup project name"),
        (
            Path("/tmp/compose.json"),
            "trafficlab-run",
            ProjectInventory(containers=[]),  # type: ignore[arg-type]
            "containers must be a tuple",
        ),
    ],
    ids=["path-type", "relative-path", "project-type", "unsafe-project", "mutable-inventory"],
)
def test_invalid_cleanup_scope_is_rejected_before_docker_access(
    compose_path: object,
    project_name: object,
    inventory: ProjectInventory,
    error: str,
) -> None:
    """An ambiguous scope or mutable inventory could target the wrong project or corrupt failure evidence."""
    with pytest.raises((TypeError, ValueError), match=error):
        cleanup_project(
            _NoDocker(),
            compose_path,  # type: ignore[arg-type]
            project_name,  # type: ignore[arg-type]
            inventory,
            deadline=160.0,
            clock=lambda: 100.0,
        )


def test_success_recomputes_budget_after_launch_and_proves_inventory_empty(tmp_path: Path) -> None:
    """Using a stale launch budget or trusting only exit zero could overrun the deadline or hide resources."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(3.0, CommandResult(0, "removed", ""))])
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock, launch_advance=2.0)
    compose_path = (tmp_path / "compose.json").resolve()

    result = cleanup_project(
        docker,
        compose_path,
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert result == CleanupResult(
        success=True,
        timed_out=False,
        detail="project resources removed",
        possibly_remaining=ProjectInventory(containers=()),
    )
    assert handle.waits == [57.0]
    assert docker.calls == [
        ("start_down", compose_path, "trafficlab-run", 160.0),
        ("project_inventory", compose_path, "trafficlab-run", 160.0),
    ]


def test_already_absent_project_cleanup_is_idempotent(tmp_path: Path) -> None:
    """Repeating cleanup for an absent project must remain a successful no-resource result."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(0.0, CommandResult(0, "", ""))])
    empty = ProjectInventory(containers=())
    docker = _Docker(handle, empty, clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        empty,
        deadline=160.0,
        clock=clock,
    )

    assert result.success
    assert result.possibly_remaining == empty


def test_nonzero_cleanup_is_visible_and_reports_fresh_inventory(tmp_path: Path) -> None:
    """Ignoring Compose's nonzero status would falsely report a failed project removal as success."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(1.0, CommandResult(23, "", "daemon refused"))])
    remaining = _inventory()
    docker = _Docker(handle, remaining, clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert not result.success
    assert not result.timed_out
    assert result.detail == "cleanup command failed with status 23: daemon refused"
    assert result.possibly_remaining == remaining
    assert [call[0] for call in docker.calls] == ["start_down", "project_inventory"]


def test_nonzero_cleanup_remains_primary_when_its_wait_reaches_deadline(tmp_path: Path) -> None:
    """A known Compose failure must not be replaced by the later lack of inventory-verification budget."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(60.0, CommandResult(23, "", "daemon refused"))])
    known = _inventory()
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        known,
        deadline=160.0,
        clock=clock,
    )

    assert not result.success
    assert not result.timed_out
    assert result.detail == "cleanup command failed with status 23: daemon refused"
    assert result.secondary_details == ("cleanup deadline expired before inventory verification",)
    assert result.possibly_remaining == known
    assert [call[0] for call in docker.calls] == ["start_down"]


@pytest.mark.parametrize(
    ("inventory_error", "secondary"),
    [
        (PermissionError("query denied"), "could not inspect cleanup result: query denied"),
        (
            TrafficlabError("inventory query timed out", corrective_action="retry cleanup"),
            "inventory query timed out",
        ),
    ],
    ids=["query-error", "query-timeout"],
)
def test_nonzero_cleanup_remains_primary_when_inventory_query_fails(
    tmp_path: Path,
    inventory_error: BaseException,
    secondary: str,
) -> None:
    """A later query failure is secondary once Compose has already returned a known nonzero result."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(1.0, CommandResult(23, "", "daemon refused"))])
    known = _inventory()
    docker = _Docker(
        handle,
        ProjectInventory(containers=()),
        clock=clock,
        inventory_error=inventory_error,
    )

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        known,
        deadline=160.0,
        clock=clock,
    )

    assert result.detail == "cleanup command failed with status 23: daemon refused"
    assert result.secondary_details == (secondary,)
    assert result.possibly_remaining == known


def test_nonzero_cleanup_retains_fresh_inventory_when_query_reaches_deadline(tmp_path: Path) -> None:
    """Fresh inventory remains useful evidence even when its query consumes the remaining verification budget."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(0.0, CommandResult(23, "", "daemon refused"))])
    fresh = _inventory("trafficlab-fresh")
    docker = _Docker(handle, fresh, clock=clock, inventory_advance=60.0)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert result.detail == "cleanup command failed with status 23: daemon refused"
    assert result.secondary_details == ("cleanup deadline expired while verifying removal",)
    assert result.possibly_remaining == fresh


def test_zero_status_with_remaining_resources_is_not_success(tmp_path: Path) -> None:
    """A successful local CLI exit is not proof that daemon-owned project resources disappeared."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(0.0, CommandResult(0, "", ""))])
    remaining = _inventory()
    docker = _Docker(handle, remaining, clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        remaining,
        deadline=160.0,
        clock=clock,
    )

    assert result == CleanupResult(
        success=False,
        timed_out=False,
        detail="cleanup completed but project resources remain",
        possibly_remaining=remaining,
    )


def test_deadline_expiry_during_launch_stops_process_without_wait_or_query(tmp_path: Path) -> None:
    """A launch consuming the final budget must stop its CLI without a nonpositive wait or Docker query."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [])
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock, launch_advance=60.0)
    known = _inventory()

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        known,
        deadline=160.0,
        clock=clock,
    )

    assert not result.success
    assert result.timed_out
    assert result.possibly_remaining == known
    assert handle.actions == ["terminate", "kill"]
    assert [call[0] for call in docker.calls] == ["start_down"]


def test_budget_smaller_than_stop_reserve_terminates_and_reaps_without_an_initial_wait(tmp_path: Path) -> None:
    """A tiny positive budget must be spent stopping and reaping, not waiting normally until nothing remains."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(0.02, None), (0.01, CommandResult(-9, "", ""))],
    )
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=100.05,
        clock=clock,
    )

    assert result.timed_out
    assert handle.actions == ["terminate", "wait", "kill", "wait"]
    assert handle.waits == pytest.approx([0.025, 0.03])
    assert all(timeout > 0.0 for timeout in handle.waits)
    assert [call[0] for call in docker.calls] == ["start_down"]


def test_deadline_after_successful_down_does_not_query_or_claim_removal(tmp_path: Path) -> None:
    """Process exit zero at the deadline cannot justify a later inventory query or removal claim."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(60.0, CommandResult(0, "", ""))])
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock)
    known = _inventory()

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        known,
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert not result.success
    assert result.possibly_remaining == known
    assert [call[0] for call in docker.calls] == ["start_down"]


def test_deadline_during_inventory_query_reports_the_fresh_inventory(tmp_path: Path) -> None:
    """If verification itself reaches the deadline, its fresh resource evidence must replace the older snapshot."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(0.0, CommandResult(0, "", ""))])
    fresh = _inventory("trafficlab-fresh")
    docker = _Docker(handle, fresh, clock=clock, inventory_advance=60.0)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert result.detail == "cleanup deadline expired while verifying removal"
    assert result.possibly_remaining == fresh


@pytest.mark.parametrize(
    ("results", "expected_actions", "expected_waits"),
    [
        (
            [(10.0, None), (10.0, CommandResult(0, "", ""))],
            ["wait", "terminate", "wait"],
            [59.0, 25.0],
        ),
        (
            [(10.0, None), (10.0, None), (5.0, CommandResult(-9, "", ""))],
            ["wait", "terminate", "wait", "kill", "wait"],
            [59.0, 25.0, 40.0],
        ),
    ],
    ids=["terminated-and-reaped", "killed-and-reaped"],
)
def test_hanging_cleanup_uses_fresh_budgets_and_never_queries(
    tmp_path: Path,
    results: list[tuple[float, CommandResult | None]],
    expected_actions: list[str],
    expected_waits: list[float],
) -> None:
    """Reusing a stale timeout or querying after a hang would exceed cleanup's bounded failure contract."""
    clock = _Clock()
    handle = _SequenceHandle(clock, results)
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock)
    known = _inventory()

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        known,
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert not result.success
    assert result.possibly_remaining == known
    assert handle.actions == expected_actions
    assert handle.waits == expected_waits
    assert all(timeout > 0.0 for timeout in handle.waits)
    assert [call[0] for call in docker.calls] == ["start_down"]


@pytest.mark.parametrize("expiry_signal", ["terminate", "kill"])
def test_signal_time_is_deducted_before_the_next_wait(tmp_path: Path, expiry_signal: str) -> None:
    """A stale pre-signal budget could pass a positive wait even though signalling used the final total budget."""
    clock = _Clock()
    if expiry_signal == "terminate":
        handle = _SequenceHandle(clock, [(10.0, None)], terminate_advance=50.0)
        expected_actions = ["wait", "terminate", "kill"]
        expected_waits = [59.0]
    else:
        handle = _SequenceHandle(clock, [(10.0, None), (10.0, None)], kill_advance=40.0)
        expected_actions = ["wait", "terminate", "wait", "kill"]
        expected_waits = [59.0, 25.0]
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert handle.actions == expected_actions
    assert handle.waits == expected_waits
    assert [call[0] for call in docker.calls] == ["start_down"]


def test_expiry_ignores_kill_error_and_still_reports_timeout(tmp_path: Path) -> None:
    """A local kill error after expiry must not escape or permit a later Docker query."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(10.0, None)],
        terminate_advance=50.0,
        kill_error=PermissionError("kill denied"),
    )
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert result.timed_out
    assert handle.actions == ["wait", "terminate", "kill"]
    assert [call[0] for call in docker.calls] == ["start_down"]


def test_initial_wait_error_ignores_signal_errors_and_preserves_failure(tmp_path: Path) -> None:
    """Best-effort stopping must not replace the original local wait failure or query Docker afterward."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(0.0, CommandResult(0, "", ""))],
        wait_error=PermissionError("wait denied"),
        terminate_error=PermissionError("terminate denied"),
        kill_error=PermissionError("kill denied"),
    )
    docker = _Docker(handle, ProjectInventory(containers=()), clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert result.detail == "could not wait for cleanup command: wait denied"
    assert handle.actions == ["wait", "terminate", "kill"]
    assert [call[0] for call in docker.calls] == ["start_down"]


@pytest.mark.parametrize(
    ("mode", "message", "expected_actions"),
    [
        (
            "terminate",
            "could not terminate cleanup command: terminate denied",
            ["wait", "terminate", "kill", "wait"],
        ),
        (
            "terminate-and-kill",
            "could not terminate cleanup command: terminate denied",
            ["wait", "terminate", "kill"],
        ),
        (
            "terminated-reap",
            "could not reap terminated cleanup command: reap denied",
            ["wait", "terminate", "wait", "terminate", "kill"],
        ),
        (
            "kill",
            "could not kill cleanup command: kill denied",
            ["wait", "terminate", "wait", "kill"],
        ),
        (
            "killed-reap",
            "could not reap killed cleanup command: kill reap denied",
            ["wait", "terminate", "wait", "kill", "wait"],
        ),
    ],
    ids=["terminate", "terminate-and-kill", "terminated-reap", "kill", "killed-reap"],
)
def test_stop_boundary_errors_remain_visible_without_inventory_query(
    tmp_path: Path,
    mode: str,
    message: str,
    expected_actions: list[str],
) -> None:
    """Local signal and reap errors must stay visible and must never trigger a later Docker query."""
    clock = _Clock()
    if mode == "terminate":
        process = _SequenceHandle(
            clock,
            [(1.0, None), (1.0, CommandResult(-9, "", ""))],
            terminate_error=PermissionError("terminate denied"),
        )
    elif mode == "terminate-and-kill":
        process = _SequenceHandle(
            clock,
            [(1.0, None)],
            terminate_error=PermissionError("terminate denied"),
            kill_error=PermissionError("kill denied"),
        )
    elif mode == "terminated-reap":
        process = _SequenceHandle(
            clock,
            [(1.0, None), (1.0, CommandResult(0, "", ""))],
            wait_errors=[None, PermissionError("reap denied")],
        )
    elif mode == "kill":
        process = _SequenceHandle(
            clock,
            [(1.0, None), (1.0, None)],
            kill_error=PermissionError("kill denied"),
        )
    else:
        process = _SequenceHandle(
            clock,
            [(1.0, None), (1.0, None), (1.0, CommandResult(-9, "", ""))],
            wait_errors=[None, None, PermissionError("kill reap denied")],
        )
    docker = _Docker(process, ProjectInventory(containers=()), clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert not result.success
    assert not result.timed_out
    assert result.detail == message
    assert process.actions == expected_actions
    assert [call[0] for call in docker.calls] == ["start_down"]


@pytest.mark.parametrize(
    ("command", "detail"),
    [
        (CommandResult(17, "stdout detail", ""), "cleanup command failed with status 17: stdout detail"),
        (CommandResult(17, "", ""), "cleanup command failed with status 17: no command output"),
    ],
)
def test_nonzero_cleanup_uses_available_command_detail(tmp_path: Path, command: CommandResult, detail: str) -> None:
    """Cleanup failures without stderr must still produce an actionable visible status."""
    clock = _Clock()
    handle = _SequenceHandle(clock, [(0.0, command)])
    docker = _Docker(handle, _inventory(), clock=clock)

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        _inventory(),
        deadline=160.0,
        clock=clock,
    )

    assert result.detail == detail


@pytest.mark.parametrize(
    ("start_error", "wait_error", "inventory_error", "message"),
    [
        (PermissionError("launch denied"), None, None, "could not launch cleanup command: launch denied"),
        (None, PermissionError("wait denied"), None, "could not wait for cleanup command: wait denied"),
        (None, None, PermissionError("query denied"), "could not inspect cleanup result: query denied"),
        (TrafficlabError("specific", corrective_action="repair"), None, None, "specific"),
    ],
    ids=["launch", "wait", "inventory", "trafficlab-error"],
)
def test_boundary_failures_become_visible_cleanup_results(
    tmp_path: Path,
    start_error: BaseException | None,
    wait_error: BaseException | None,
    inventory_error: BaseException | None,
    message: str,
) -> None:
    """Raw boundary exceptions would bypass cleanup precedence and lose possibly-remaining evidence."""
    clock = _Clock()
    handle = _SequenceHandle(
        clock,
        [(0.0, CommandResult(0, "", ""))],
        wait_error=wait_error,
    )
    known = _inventory()
    docker = _Docker(
        handle,
        ProjectInventory(containers=()),
        clock=clock,
        start_error=start_error,
        inventory_error=inventory_error,
    )

    result = cleanup_project(
        docker,
        (tmp_path / "compose.json").resolve(),
        "trafficlab-run",
        known,
        deadline=160.0,
        clock=clock,
    )

    assert not result.success
    assert not result.timed_out
    assert result.detail == message
    assert result.possibly_remaining == known
