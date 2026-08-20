"""Deadline-bounded, project-scoped Docker Compose cleanup."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from trafficlab.docker_cli import CommandResult, ProcessHandle
from trafficlab.errors import TrafficlabError

_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_LOCAL_STOP_RESERVE_SECONDS = 1.0
_TIMEOUT_DETAIL = "cleanup command exceeded its deadline; project resources may remain"


class CleanupCompose(Protocol):
    """The single Docker operation used by production cleanup."""

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> ProcessHandle: ...


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Compact result from the one project-scoped Compose down command."""

    success: bool
    timed_out: bool
    detail: str

    def __post_init__(self) -> None:
        if type(self.success) is not bool:
            raise TypeError("success must be a boolean")
        if type(self.timed_out) is not bool:
            raise TypeError("timed_out must be a boolean")
        if type(self.detail) is not str:
            raise TypeError("detail must be a string")
        if not self.detail.strip():
            raise ValueError("detail must be nonempty")
        if self.success and self.timed_out:
            raise ValueError("successful cleanup cannot be timed out")


def _remaining(deadline: object, clock: Callable[[], object]) -> float:
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise TypeError("cleanup deadline must be a positive finite number")
    try:
        converted_deadline = float(deadline)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("cleanup deadline must be a positive finite number") from error
    if not math.isfinite(converted_deadline) or converted_deadline <= 0.0:
        raise ValueError("cleanup deadline must be a positive finite number")
    try:
        now = clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("cleanup clock must return a finite number")
        converted_now = float(now)
    except (ArithmeticError, TypeError, ValueError) as error:
        raise ValueError("cleanup clock must return a finite number") from error
    if not math.isfinite(converted_now):
        raise ValueError("cleanup clock must return a finite number")
    return converted_deadline - converted_now


def _failure(detail: str) -> CleanupResult:
    return CleanupResult(success=False, timed_out=False, detail=detail)


def _timeout(detail: str) -> CleanupResult:
    return CleanupResult(success=False, timed_out=True, detail=detail)


def _boundary_detail(action: str, error: TrafficlabError | OSError) -> str:
    if isinstance(error, TrafficlabError):
        return str(error)
    return f"could not {action}: {error}"


def _wait(handle: ProcessHandle, remaining: float) -> CommandResult | None:
    return handle.wait(timeout=remaining)


def _stop_without_wait(handle: ProcessHandle) -> None:
    """Best-effort containment when no deadline budget remains for reaping."""
    try:
        handle.terminate()
    except (TrafficlabError, OSError):
        pass
    try:
        handle.kill()
    except (TrafficlabError, OSError):
        pass


def _reap_after_kill(
    handle: ProcessHandle,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CleanupResult:
    remaining = _remaining(deadline, clock)
    if remaining <= 0.0:
        return _timeout(_TIMEOUT_DETAIL)
    try:
        _wait(handle, remaining)
    except (TrafficlabError, OSError) as error:
        return _failure(_boundary_detail("reap killed cleanup command", error))
    return _timeout(_TIMEOUT_DETAIL)


def _stop_and_reap(
    handle: ProcessHandle,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CleanupResult:
    try:
        handle.terminate()
    except (TrafficlabError, OSError) as terminate_error:
        terminate_detail = _boundary_detail("terminate cleanup command", terminate_error)
        try:
            handle.kill()
        except (TrafficlabError, OSError) as kill_error:
            return _failure(f"{terminate_detail}; {_boundary_detail('kill cleanup command', kill_error)}")
        remaining = _remaining(deadline, clock)
        if remaining <= 0.0:
            return _failure(terminate_detail)
        try:
            _wait(handle, remaining)
        except (TrafficlabError, OSError) as reap_error:
            return _failure(f"{terminate_detail}; {_boundary_detail('reap killed cleanup command', reap_error)}")
        return _failure(terminate_detail)

    remaining = _remaining(deadline, clock)
    if remaining <= 0.0:
        try:
            handle.kill()
        except (TrafficlabError, OSError):
            pass
        return _timeout(_TIMEOUT_DETAIL)
    try:
        result = _wait(handle, remaining / 2.0)
    except (TrafficlabError, OSError) as error:
        _stop_without_wait(handle)
        return _failure(_boundary_detail("reap terminated cleanup command", error))
    if result is not None:
        return _timeout(_TIMEOUT_DETAIL)
    try:
        handle.kill()
    except (TrafficlabError, OSError) as error:
        return _failure(_boundary_detail("kill cleanup command", error))
    return _reap_after_kill(
        handle,
        deadline=deadline,
        clock=clock,
    )


def _validate_scope(compose_path: object, project_name: object) -> tuple[Path, str]:
    if not isinstance(compose_path, Path):
        raise TypeError("compose_path must be a Path")
    if not compose_path.is_absolute():
        raise ValueError("compose_path must be absolute")
    if type(project_name) is not str:
        raise TypeError("project_name must be a string")
    if _PROJECT_NAME.fullmatch(project_name) is None:
        raise ValueError("invalid cleanup project name")
    return compose_path, project_name


def _command_detail(result: CommandResult) -> str:
    return result.stderr.strip() or result.stdout.strip() or "no command output"


def cleanup_project(
    compose: CleanupCompose,
    compose_path: Path,
    project_name: str,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CleanupResult:
    """Run one owned Compose down command without working past its absolute deadline."""
    validated_path, validated_project = _validate_scope(compose_path, project_name)
    if _remaining(deadline, clock) <= 0.0:
        return _timeout("cleanup deadline expired before launch; project resources may remain")
    try:
        handle = compose.start_down(validated_path, validated_project, deadline=deadline)
    except (TrafficlabError, OSError) as error:
        return _failure(_boundary_detail("launch cleanup command", error))

    remaining = _remaining(deadline, clock)
    if remaining <= 0.0:
        _stop_without_wait(handle)
        return _timeout("cleanup deadline expired during launch; project resources may remain")
    normal_wait = remaining - min(_LOCAL_STOP_RESERVE_SECONDS, remaining)
    if normal_wait <= 0.0:
        return _stop_and_reap(handle, deadline=deadline, clock=clock)
    try:
        result = _wait(handle, normal_wait)
    except (TrafficlabError, OSError) as error:
        _stop_without_wait(handle)
        return _failure(_boundary_detail("wait for cleanup command", error))
    if result is None:
        return _stop_and_reap(handle, deadline=deadline, clock=clock)
    if result.returncode != 0:
        return _failure(f"cleanup command failed with status {result.returncode}: {_command_detail(result)}")
    output = _command_detail(result)
    detail = "project resources removed" if output == "no command output" else f"project resources removed: {output}"
    return CleanupResult(success=True, timed_out=False, detail=detail)
