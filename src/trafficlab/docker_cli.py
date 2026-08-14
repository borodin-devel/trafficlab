"""Typed, bounded subprocess boundary for Docker and Docker Compose."""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

from trafficlab.errors import TrafficlabError

_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_PRODUCTION_SERVICES = frozenset({"capture", "target"})


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


@dataclass(frozen=True, slots=True)
class ProjectInventory:
    """Last observed resources belonging to one Compose project."""

    containers: tuple[ServiceState, ...]
    networks: tuple[str, ...] = ()
    volumes: tuple[str, ...] = ()


class ProcessHandle(Protocol):
    """Controllable command used only for deadline-bounded cleanup."""

    def wait(self, *, timeout: float) -> CommandResult | None:
        """Wait at most *timeout* seconds, returning ``None`` on timeout."""

    def terminate(self) -> None:
        """Request termination of the local command process."""

    def kill(self) -> None:
        """Forcefully stop the local command process."""


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


def _process_error(action: str, error: OSError) -> TrafficlabError:
    return TrafficlabError(
        f"could not {action}: {error}",
        corrective_action="verify the Docker executable and local process permissions, then retry",
    )


def _decode_output(data: bytes, *, stream: str) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise TrafficlabError(
            f"Docker command returned invalid UTF-8 on {stream}",
            corrective_action="verify the Docker CLI installation and locale, then retry",
        ) from error


class _SubprocessHandle:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self._result: CommandResult | None = None

    def wait(self, *, timeout: float) -> CommandResult | None:
        _require_positive_finite(timeout, "process wait timeout")
        if self._result is not None:
            return self._result
        try:
            stdout, stderr = self._process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        except OSError as error:
            raise _process_error("wait for Docker cleanup command", error) from error
        returncode = self._process.returncode
        if returncode is None:
            raise TrafficlabError(
                "Docker cleanup command ended without an exit status",
                corrective_action="inspect the local Docker CLI process and retry cleanup",
            )
        self._result = CommandResult(
            returncode=returncode,
            stdout=_decode_output(stdout, stream="stdout"),
            stderr=_decode_output(stderr, stream="stderr"),
        )
        return self._result

    def terminate(self) -> None:
        try:
            self._process.terminate()
        except OSError as error:
            raise _process_error("terminate Docker cleanup command", error) from error

    def kill(self) -> None:
        try:
            self._process.kill()
        except OSError as error:
            raise _process_error("kill Docker cleanup command", error) from error


class SubprocessBoundary:
    """Standard-library implementation of the direct command boundary."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
        environment: Mapping[str, str] | None,
    ) -> CommandResult:
        _require_positive_finite(timeout, "command timeout")
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                check=False,
                env=None if environment is None else dict(environment),
                shell=False,
                timeout=timeout,
            )
        except FileNotFoundError as error:
            raise TrafficlabError(
                "Docker executable was not found",
                corrective_action="install Docker Engine with the Compose v2 plugin and retry",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise TrafficlabError(
                f"Docker command timed out after {timeout:g} seconds",
                corrective_action="check Docker daemon responsiveness and retry",
            ) from error
        except OSError as error:
            raise _process_error("launch Docker command", error) from error
        return CommandResult(
            returncode=completed.returncode,
            stdout=_decode_output(completed.stdout, stream="stdout"),
            stderr=_decode_output(completed.stderr, stream="stderr"),
        )

    def start(
        self,
        argv: tuple[str, ...],
        *,
        environment: Mapping[str, str] | None,
    ) -> ProcessHandle:
        try:
            process = subprocess.Popen(
                argv,
                env=None if environment is None else dict(environment),
                shell=False,
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise TrafficlabError(
                "Docker executable was not found",
                corrective_action="install Docker Engine with the Compose v2 plugin and retry",
            ) from error
        except OSError as error:
            raise _process_error("launch Docker cleanup command", error) from error
        return _SubprocessHandle(process)


def _require_positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrafficlabError(
            f"{name} must be a positive finite number",
            corrective_action=f"set {name} to a positive finite number",
        )
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise TrafficlabError(
            f"{name} must be a positive finite number",
            corrective_action=f"set {name} to a positive finite number",
        ) from error
    if not math.isfinite(converted) or converted <= 0.0:
        raise TrafficlabError(
            f"{name} must be a positive finite number",
            corrective_action=f"set {name} to a positive finite number",
        )
    return converted


def _budget(*, timeout: float | None, deadline: float | None, clock: Callable[[], float]) -> float:
    if (timeout is None) == (deadline is None):
        raise TrafficlabError(
            "provide exactly one of timeout or deadline",
            corrective_action="provide one positive timeout or one future absolute deadline",
        )
    if timeout is not None:
        return _require_positive_finite(timeout, "timeout")
    validated_deadline = _require_positive_finite(deadline, "deadline")
    try:
        remaining = validated_deadline - clock()
    except ArithmeticError as error:
        raise TrafficlabError(
            "could not calculate the Docker command deadline",
            corrective_action="use a finite monotonic clock and future deadline",
        ) from error
    if not math.isfinite(remaining) or remaining <= 0.0:
        raise TrafficlabError(
            "Docker command deadline expired before launch",
            corrective_action="retry with a future deadline and enough total-run budget",
        )
    return remaining


def _validate_compose_scope(compose_path: Path, project_name: str) -> tuple[str, ...]:
    if not compose_path.is_absolute():
        raise TrafficlabError(
            f"Docker Compose file path must be absolute: {compose_path}",
            corrective_action="use the absolute generated Compose file path",
        )
    if _PROJECT_NAME.fullmatch(project_name) is None:
        raise TrafficlabError(
            f"invalid Docker Compose project name: {project_name!r}",
            corrective_action="use lowercase letters, digits, hyphens, or underscores, starting with a letter or digit",
        )
    return (
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--file",
        str(compose_path),
    )


def _validate_service(service: str) -> None:
    if service not in _PRODUCTION_SERVICES:
        raise TrafficlabError(
            f"Docker Compose service must be capture or target, got {service!r}",
            corrective_action="use the production capture or target service",
        )


def _parse_service(item: object) -> ServiceState:
    if not isinstance(item, dict):
        raise ValueError("container entry is not an object")
    typed_item = cast(dict[object, object], item)
    required = {"ID": str, "Name": str, "Service": str, "State": str, "ExitCode": int}
    values: dict[str, object] = {}
    for key, expected_type in required.items():
        value = typed_item.get(key)
        if not isinstance(value, expected_type) or isinstance(value, bool):
            raise ValueError(f"{key} has the wrong type")
        values[key] = value
    identifier = cast(str, values["ID"])
    name = cast(str, values["Name"])
    service = cast(str, values["Service"])
    state = cast(str, values["State"])
    exit_code = cast(int, values["ExitCode"])
    if not identifier or not name or not service or not state:
        raise ValueError("container fields must be nonempty")
    if not 0 <= exit_code <= 255:
        raise ValueError("ExitCode must be between 0 and 255")
    return ServiceState(identifier, name, service, state, exit_code)


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while rejecting keys that would otherwise be overwritten."""
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate object key {key!r}")
        document[key] = value
    return document


def _load_inventory_json(text: str) -> object:
    return cast(object, json.loads(text, object_pairs_hook=_reject_duplicate_json_keys))


def _parse_inventory(stdout: str) -> ProjectInventory:
    try:
        stripped = stdout.strip()
        if not stripped:
            items: tuple[object, ...] = ()
        else:
            try:
                document = _load_inventory_json(stripped)
            except json.JSONDecodeError as json_lines_trigger:
                documents = tuple(_load_inventory_json(line) for line in stdout.splitlines() if line.strip())
                if any(not isinstance(item, dict) for item in documents):
                    raise ValueError("every JSON Lines entry must be an object") from json_lines_trigger
                items = documents
            else:
                if isinstance(document, list):
                    items = tuple(cast(list[object], document))
                elif isinstance(document, dict):
                    items = (cast(object, document),)
                else:
                    raise ValueError("top level must be an object, array, or JSON Lines objects")
        containers = tuple(_parse_service(item) for item in items)
        identifiers = tuple(container.identifier for container in containers)
        names = tuple(container.name for container in containers)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("container IDs must be unique")
        if len(names) != len(set(names)):
            raise ValueError("container names must be unique")
    except (json.JSONDecodeError, ValueError) as error:
        raise TrafficlabError(
            f"invalid Docker Compose service inventory: {error}",
            corrective_action="verify Docker Compose v2 returns valid JSON from ps --format json",
        ) from error
    return ProjectInventory(
        containers=tuple(sorted(containers, key=lambda item: (item.service, item.name, item.identifier)))
    )


def _parse_resource_names(stdout: str, *, kind: str) -> tuple[str, ...]:
    try:
        documents = tuple(_load_inventory_json(line) for line in stdout.splitlines() if line.strip())
        names: list[str] = []
        for document in documents:
            if not isinstance(document, dict):
                raise ValueError("every JSON Lines entry must be an object")
            typed_document = cast(dict[object, object], document)
            name = typed_document.get("Name")
            if not isinstance(name, str) or not name:
                raise ValueError("Name must be a nonempty string")
            if kind == "network":
                identifier = typed_document.get("ID")
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError("ID must be a nonempty string")
            names.append(name)
        if len(names) != len(set(names)):
            raise ValueError(f"{kind} names must be unique")
    except (json.JSONDecodeError, ValueError) as error:
        raise TrafficlabError(
            f"invalid Docker project {kind} inventory: {error}",
            corrective_action=f"verify Docker returns valid JSON from {kind} ls --format json",
        ) from error
    return tuple(sorted(names))


def _validate_config_json(stdout: str) -> None:
    try:
        document = cast(object, json.loads(stdout))
        if not isinstance(document, dict):
            raise ValueError("top level is not an object")
    except (json.JSONDecodeError, ValueError) as error:
        raise TrafficlabError(
            f"invalid Docker Compose config JSON: {error}",
            corrective_action="verify Docker Compose v2 supports config --format json",
        ) from error


class DockerCompose:
    """Direct, bounded Docker CLI operations used by capture orchestration."""

    def __init__(
        self,
        boundary: CommandBoundary | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._boundary = boundary or SubprocessBoundary()
        self._clock = clock
        self._environment = None if environment is None else MappingProxyType(dict(environment))

    def _remaining(self, *, timeout: float | None, deadline: float | None) -> float:
        return _budget(timeout=timeout, deadline=deadline, clock=self._clock)

    def _run(
        self,
        argv: tuple[str, ...],
        operation: str,
        *,
        timeout: float | None,
        deadline: float | None,
    ) -> CommandResult:
        remaining = self._remaining(timeout=timeout, deadline=deadline)
        try:
            result = self._boundary.run(argv, timeout=remaining, environment=self._environment)
        except TrafficlabError:
            raise
        except FileNotFoundError as error:
            raise TrafficlabError(
                "Docker executable was not found",
                corrective_action="install Docker Engine with the Compose v2 plugin and retry",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise TrafficlabError(
                f"{operation} timed out",
                corrective_action="check Docker daemon responsiveness and retry within the configured timeout",
            ) from error
        except UnicodeDecodeError as error:
            raise TrafficlabError(
                f"{operation} returned invalid UTF-8",
                corrective_action="verify the Docker CLI installation and locale, then retry",
            ) from error
        except OSError as error:
            raise TrafficlabError(
                f"could not launch Docker command for {operation}: {error}",
                corrective_action="verify Docker is installed, accessible without sudo, and retry",
            ) from error
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no command output"
            raise TrafficlabError(
                f"{operation} failed with status {result.returncode}: {detail}",
                corrective_action="verify Docker daemon access and inspect the reported Docker command failure",
            )
        return result

    def _compose_run(
        self,
        compose_path: Path,
        project_name: str,
        arguments: tuple[str, ...],
        operation: str,
        *,
        timeout: float | None,
        deadline: float | None,
    ) -> CommandResult:
        prefix = _validate_compose_scope(compose_path, project_name)
        return self._run(prefix + arguments, operation, timeout=timeout, deadline=deadline)

    def info(self, *, timeout: float | None = None, deadline: float | None = None) -> CommandResult:
        """Check Docker daemon availability."""
        return self._run(("docker", "info"), "Docker info", timeout=timeout, deadline=deadline)

    def compose_version(self, *, timeout: float | None = None, deadline: float | None = None) -> CommandResult:
        """Check Docker Compose v2 availability."""
        return self._run(("docker", "compose", "version"), "Docker Compose version", timeout=timeout, deadline=deadline)

    def image_inspect(
        self,
        image: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Inspect one image in the local Docker cache."""
        return self._run(
            ("docker", "image", "inspect", image), "Docker image inspect", timeout=timeout, deadline=deadline
        )

    def image_pull(
        self,
        image: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Pull one image into the local Docker cache."""
        return self._run(("docker", "image", "pull", image), "Docker image pull", timeout=timeout, deadline=deadline)

    def config(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Render a Compose file as canonical JSON through Compose."""
        result = self._compose_run(
            compose_path,
            project_name,
            ("config", "--format", "json"),
            "Docker Compose config",
            timeout=timeout,
            deadline=deadline,
        )
        _validate_config_json(result.stdout)
        return result

    def create_capture(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Create only the capture service container."""
        return self._compose_run(
            compose_path,
            project_name,
            ("create", "capture"),
            "Docker Compose create capture",
            timeout=timeout,
            deadline=deadline,
        )

    def start_capture(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Start the previously created capture service."""
        return self._compose_run(
            compose_path,
            project_name,
            ("start", "capture"),
            "Docker Compose start capture",
            timeout=timeout,
            deadline=deadline,
        )

    def start_target(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Create and start target without starting dependencies."""
        return self._compose_run(
            compose_path,
            project_name,
            ("up", "--detach", "--no-deps", "target"),
            "Docker Compose start target",
            timeout=timeout,
            deadline=deadline,
        )

    def service_state(
        self,
        compose_path: Path,
        project_name: str,
        service: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> ServiceState | None:
        """Return the current container state for one production service."""
        _validate_service(service)
        result = self._compose_run(
            compose_path,
            project_name,
            ("ps", "--all", "--format", "json", service),
            f"Docker Compose inspect {service}",
            timeout=timeout,
            deadline=deadline,
        )
        inventory = _parse_inventory(result.stdout)
        if not inventory.containers:
            return None
        if len(inventory.containers) != 1:
            raise TrafficlabError(
                f"expected at most one container for service {service}, got {len(inventory.containers)}",
                corrective_action="remove stale containers for the unique Compose project and retry",
            )
        state = inventory.containers[0]
        if state.service != service:
            raise TrafficlabError(
                f"Docker Compose returned service {state.service!r} for requested service {service}",
                corrective_action="verify the generated Compose project and retry",
            )
        return state

    def service_logs(
        self,
        compose_path: Path,
        project_name: str,
        service: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> str:
        """Return UTF-8 logs for one production service."""
        _validate_service(service)
        result = self._compose_run(
            compose_path,
            project_name,
            ("logs", "--no-color", service),
            f"Docker Compose logs {service}",
            timeout=timeout,
            deadline=deadline,
        )
        return result.stdout

    def kill_target(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Kill the whole target container."""
        return self._compose_run(
            compose_path,
            project_name,
            ("kill", "target"),
            "Docker Compose kill target",
            timeout=timeout,
            deadline=deadline,
        )

    def signal_capture(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Send SIGINT to capture so dumpcap can flush PCAPNG."""
        return self._compose_run(
            compose_path,
            project_name,
            ("kill", "--signal", "SIGINT", "capture"),
            "Docker Compose signal capture",
            timeout=timeout,
            deadline=deadline,
        )

    def kill_capture(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        """Kill the whole capture container."""
        return self._compose_run(
            compose_path,
            project_name,
            ("kill", "capture"),
            "Docker Compose kill capture",
            timeout=timeout,
            deadline=deadline,
        )

    def project_inventory(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> ProjectInventory:
        """Return a typed snapshot of every labelled project resource."""
        containers_result = self._compose_run(
            compose_path,
            project_name,
            ("ps", "--all", "--format", "json"),
            "Docker Compose project inventory",
            timeout=timeout,
            deadline=deadline,
        )
        label = f"label=com.docker.compose.project={project_name}"
        networks_result = self._run(
            ("docker", "network", "ls", "--filter", label, "--format", "json"),
            "Docker project network inventory",
            timeout=timeout,
            deadline=deadline,
        )
        volumes_result = self._run(
            ("docker", "volume", "ls", "--filter", label, "--format", "json"),
            "Docker project volume inventory",
            timeout=timeout,
            deadline=deadline,
        )
        containers = _parse_inventory(containers_result.stdout).containers
        return ProjectInventory(
            containers=containers,
            networks=_parse_resource_names(networks_result.stdout, kind="network"),
            volumes=_parse_resource_names(volumes_result.stdout, kind="volume"),
        )

    def start_down(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> ProcessHandle:
        """Start project-scoped cleanup and return its controllable local process."""
        prefix = _validate_compose_scope(compose_path, project_name)
        self._remaining(timeout=timeout, deadline=deadline)
        argv = prefix + ("down", "--volumes", "--remove-orphans")
        try:
            return self._boundary.start(argv, environment=self._environment)
        except TrafficlabError:
            raise
        except FileNotFoundError as error:
            raise TrafficlabError(
                "Docker executable was not found",
                corrective_action="install Docker Engine with the Compose v2 plugin and retry",
            ) from error
        except OSError as error:
            raise TrafficlabError(
                f"could not launch Docker cleanup command: {error}",
                corrective_action="verify Docker is installed, accessible without sudo, and retry",
            ) from error
