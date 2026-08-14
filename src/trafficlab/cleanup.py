"""Deadline-bounded, project-scoped Docker cleanup."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from trafficlab.docker_cli import CommandResult, ProcessHandle, ProjectInventory, ServiceState
from trafficlab.errors import TrafficlabError

_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_LOCAL_STOP_RESERVE_SECONDS = 1.0


class CleanupCompose(Protocol):
    """Docker operations needed by project cleanup."""

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> ProcessHandle: ...

    def project_inventory(self, compose_path: Path, project_name: str, *, deadline: float) -> ProjectInventory: ...


def _validate_inventory(inventory: object, name: str) -> ProjectInventory:
    if type(inventory) is not ProjectInventory:
        raise TypeError(f"{name} must be a ProjectInventory")
    if type(inventory.containers) is not tuple:
        raise TypeError(f"{name} containers must be a tuple")
    if not all(type(container) is ServiceState for container in inventory.containers):
        raise TypeError(f"{name} containers must contain ServiceState values")
    for resource_kind, resources in (("networks", inventory.networks), ("volumes", inventory.volumes)):
        if type(resources) is not tuple:
            raise TypeError(f"{name} {resource_kind} must be a tuple")
        if not all(type(resource) is str and resource for resource in resources):
            raise TypeError(f"{name} {resource_kind} must contain nonempty strings")
    return inventory


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Outcome and the most recent trustworthy project-resource evidence."""

    success: bool
    timed_out: bool
    detail: str
    possibly_remaining: ProjectInventory
    secondary_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.success) is not bool:
            raise TypeError("success must be a boolean")
        if type(self.timed_out) is not bool:
            raise TypeError("timed_out must be a boolean")
        if type(self.detail) is not str:
            raise TypeError("detail must be a string")
        if not self.detail.strip():
            raise ValueError("detail must be nonempty")
        _validate_inventory(self.possibly_remaining, "possibly_remaining")
        if type(self.secondary_details) is not tuple:
            raise TypeError("secondary_details must be a tuple")
        if not all(type(detail) is str and detail.strip() for detail in self.secondary_details):
            raise ValueError("secondary_details must contain nonempty strings")
        if self.success and self.timed_out:
            raise ValueError("successful cleanup cannot be timed out")
        if self.success and any(
            (
                self.possibly_remaining.containers,
                self.possibly_remaining.networks,
                self.possibly_remaining.volumes,
            )
        ):
            raise ValueError("successful cleanup cannot report possibly remaining resources")
        if self.success and self.secondary_details:
            raise ValueError("successful cleanup cannot report secondary failure details")


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


def _failure(
    detail: str,
    inventory: ProjectInventory,
    *,
    secondary_details: tuple[str, ...] = (),
) -> CleanupResult:
    return CleanupResult(
        success=False,
        timed_out=False,
        detail=detail,
        possibly_remaining=inventory,
        secondary_details=secondary_details,
    )


def _timeout(detail: str, inventory: ProjectInventory) -> CleanupResult:
    return CleanupResult(
        success=False,
        timed_out=True,
        detail=detail,
        possibly_remaining=inventory,
    )


def _boundary_detail(action: str, error: TrafficlabError | OSError) -> str:
    if isinstance(error, TrafficlabError):
        return str(error)
    return f"could not {action}: {error}"


def _stop_without_wait(handle: ProcessHandle) -> None:
    """Best-effort local stop used only when launch consumes the entire budget."""
    try:
        handle.terminate()
    except (TrafficlabError, OSError):
        pass
    try:
        handle.kill()
    except (TrafficlabError, OSError):
        pass


def _wait(handle: ProcessHandle, remaining: float) -> CommandResult | None:
    return handle.wait(timeout=remaining)


def _command_failure_detail(result: CommandResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or "no command output"
    return f"cleanup command failed with status {result.returncode}: {detail}"


def _reap_after_kill(
    handle: ProcessHandle,
    inventory: ProjectInventory,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CleanupResult:
    remaining = _remaining(deadline, clock)
    if remaining <= 0.0:
        return _timeout("cleanup command exceeded its deadline; project resources may remain", inventory)
    try:
        _wait(handle, remaining)
    except (TrafficlabError, OSError) as error:
        return _failure(_boundary_detail("reap killed cleanup command", error), inventory)
    return _timeout("cleanup command exceeded its deadline; project resources may remain", inventory)


def _stop_and_reap(
    handle: ProcessHandle,
    inventory: ProjectInventory,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CleanupResult:
    try:
        handle.terminate()
    except (TrafficlabError, OSError) as error:
        try:
            handle.kill()
        except (TrafficlabError, OSError) as kill_error:
            return _failure(
                _boundary_detail("terminate cleanup command", error),
                inventory,
                secondary_details=(_boundary_detail("kill cleanup command", kill_error),),
            )
        reap = _reap_after_kill(handle, inventory, deadline=deadline, clock=clock)
        return _failure(
            _boundary_detail("terminate cleanup command", error),
            inventory,
            secondary_details=(reap.detail,),
        )

    remaining = _remaining(deadline, clock)
    if remaining <= 0.0:
        try:
            handle.kill()
        except (TrafficlabError, OSError):
            pass
        return _timeout("cleanup command exceeded its deadline; project resources may remain", inventory)
    try:
        result = _wait(handle, remaining / 2.0)
    except (TrafficlabError, OSError) as error:
        _stop_without_wait(handle)
        return _failure(_boundary_detail("reap terminated cleanup command", error), inventory)
    if result is not None:
        return _timeout("cleanup command exceeded its deadline; project resources may remain", inventory)
    try:
        handle.kill()
    except (TrafficlabError, OSError) as error:
        return _failure(_boundary_detail("kill cleanup command", error), inventory)
    return _reap_after_kill(handle, inventory, deadline=deadline, clock=clock)


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


def cleanup_project(
    compose: CleanupCompose,
    compose_path: Path,
    project_name: str,
    last_known_inventory: ProjectInventory,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> CleanupResult:
    """Remove one Compose project without working past its absolute deadline."""
    _validate_inventory(last_known_inventory, "last_known_inventory")
    validated_path, validated_project = _validate_scope(compose_path, project_name)
    if _remaining(deadline, clock) <= 0.0:
        return _timeout(
            "cleanup deadline expired before launch; project resources may remain",
            last_known_inventory,
        )
    try:
        handle = compose.start_down(validated_path, validated_project, deadline=deadline)
    except (TrafficlabError, OSError) as error:
        return _failure(_boundary_detail("launch cleanup command", error), last_known_inventory)

    remaining = _remaining(deadline, clock)
    if remaining <= 0.0:
        _stop_without_wait(handle)
        return _timeout("cleanup deadline expired during launch; project resources may remain", last_known_inventory)
    normal_wait = remaining - min(_LOCAL_STOP_RESERVE_SECONDS, remaining)
    if normal_wait <= 0.0:
        return _stop_and_reap(handle, last_known_inventory, deadline=deadline, clock=clock)
    try:
        result = _wait(handle, normal_wait)
    except (TrafficlabError, OSError) as error:
        _stop_without_wait(handle)
        return _failure(_boundary_detail("wait for cleanup command", error), last_known_inventory)

    remaining = _remaining(deadline, clock)
    if result is None:
        return _stop_and_reap(handle, last_known_inventory, deadline=deadline, clock=clock)

    command_failure = _command_failure_detail(result) if result.returncode != 0 else None

    if remaining <= 0.0:
        if command_failure is not None:
            return _failure(
                command_failure,
                last_known_inventory,
                secondary_details=("cleanup deadline expired before inventory verification",),
            )
        return _timeout(
            "cleanup deadline expired before removal could be verified; project resources may remain",
            last_known_inventory,
        )
    try:
        inventory = compose.project_inventory(validated_path, validated_project, deadline=deadline)
    except (TrafficlabError, OSError) as error:
        query_detail = _boundary_detail("inspect cleanup result", error)
        if command_failure is not None:
            return _failure(command_failure, last_known_inventory, secondary_details=(query_detail,))
        return _failure(_boundary_detail("inspect cleanup result", error), last_known_inventory)
    if _remaining(deadline, clock) <= 0.0:
        if command_failure is not None:
            return _failure(
                command_failure,
                inventory,
                secondary_details=("cleanup deadline expired while verifying removal",),
            )
        return _timeout("cleanup deadline expired while verifying removal", inventory)

    if command_failure is not None:
        return _failure(command_failure, inventory)
    if any((inventory.containers, inventory.networks, inventory.volumes)):
        return _failure("cleanup completed but project resources remain", inventory)
    return CleanupResult(
        success=True,
        timed_out=False,
        detail="project resources removed",
        possibly_remaining=inventory,
    )
