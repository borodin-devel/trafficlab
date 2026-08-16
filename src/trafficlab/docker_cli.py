"""Typed, bounded subprocess boundary for Docker and Docker Compose."""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, Protocol, cast

from trafficlab.errors import TrafficlabError

_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_PRODUCTION_SERVICES = frozenset({"capture", "target"})


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Decoded result of one completed command."""

    returncode: int
    stdout: str
    stderr: str


_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SNAPSHOT_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z\Z")
_CAPTURE_TOOL_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){2}\Z")
_DEBIAN_VERSION_PATTERN = re.compile(
    r"(?:[0-9]+:)?(?:[0-9][A-Za-z0-9.+:~]*|"
    r"[0-9][A-Za-z0-9.+:~-]*-[A-Za-z0-9.+~]+)\Z"
)
_CAPTURE_IMAGE_LOCK_FIELDS = frozenset(
    {
        "base_digest",
        "base_reference",
        "capture_tool_version",
        "debian_snapshot",
        "direct_packages",
        "expected_capture_image_id",
    }
)
_CAPTURE_DIRECT_PACKAGES = frozenset({"ca-certificates", "curl", "wireshark-common"})


class CaptureImageLockError(ValueError):
    """The checked capture-image contract is malformed or incompatible."""


CapturePlatform = Literal["linux/amd64"]
CAPTURE_PLATFORM: Final[CapturePlatform] = "linux/amd64"
_CAPTURE_HOST_ARCHITECTURES = frozenset({"amd64", "x86_64", CAPTURE_PLATFORM})


def cold_capture_build_argv(tag: str, iidfile: Path) -> tuple[str, ...]:
    """Return the one reproducible, cold capture-image build invocation.

    The caller supplies a project-scoped tag and an exclusive IID destination;
    the checked Dockerfile and image lock supply all remaining inputs.
    """

    if not isinstance(tag, str) or not tag:
        raise ValueError("capture image tag must be a nonempty string")
    if not isinstance(iidfile, Path):
        raise TypeError("iidfile must be a pathlib.Path")
    return (
        "docker",
        "build",
        "--pull",
        "--no-cache",
        "--provenance=false",
        "--platform",
        CAPTURE_PLATFORM,
        "--output",
        "type=image,rewrite-timestamp=true,unpack=false",
        "--tag",
        tag,
        "--iidfile",
        str(iidfile),
        "docker/capture",
    )


def normalize_capture_platform(host_architecture: str) -> CapturePlatform:
    """Map supported host architecture names to the one capture platform."""

    if host_architecture.casefold() in _CAPTURE_HOST_ARCHITECTURES:
        return CAPTURE_PLATFORM
    raise CaptureImageLockError(
        f"unsupported capture host architecture {host_architecture!r}; required platform is {CAPTURE_PLATFORM}"
    )


def validate_capture_platform(
    operating_system: str,
    architecture: str,
    *,
    source: str,
) -> CapturePlatform:
    """Require one Docker execution or image platform to be linux/amd64."""

    if operating_system.casefold() != "linux":
        raise CaptureImageLockError(
            f"unsupported {source} platform {operating_system!r}/{architecture!r}; "
            f"required platform is {CAPTURE_PLATFORM}"
        )
    try:
        return normalize_capture_platform(architecture)
    except CaptureImageLockError as error:
        raise CaptureImageLockError(
            f"unsupported {source} platform {operating_system!r}/{architecture!r}; "
            f"required platform is {CAPTURE_PLATFORM}"
        ) from error


def parse_docker_info_platform(stdout: str) -> CapturePlatform:
    """Parse the remote Docker daemon platform from formatted Docker info."""

    try:
        parsed = cast(object, json.loads(stdout))
    except json.JSONDecodeError as error:
        raise CaptureImageLockError(f"invalid Docker info JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise CaptureImageLockError("Docker info JSON must be an object")
    payload = cast(dict[str, object], parsed)
    operating_system = payload.get("OSType")
    if not isinstance(operating_system, str) or not operating_system:
        raise CaptureImageLockError(
            f"Docker info has an invalid operating system; required platform is {CAPTURE_PLATFORM}"
        )
    architecture = payload.get("Architecture")
    if not isinstance(architecture, str) or not architecture:
        raise CaptureImageLockError(f"Docker info has an invalid architecture; required platform is {CAPTURE_PLATFORM}")
    return validate_capture_platform(operating_system, architecture, source="Docker daemon")


@dataclass(frozen=True, slots=True)
class CaptureImageLock:
    """Immutable inputs and expected output for the capture image."""

    base_reference: str
    base_digest: str
    debian_snapshot: str
    source_date_epoch: int
    direct_packages: Mapping[str, str]
    capture_tool_version: str
    expected_capture_image_id: str


@dataclass(frozen=True, slots=True)
class ImageIdentity:
    """A requested image reference and its resolved Docker content ID."""

    reference: str
    content_id: str
    operating_system: str
    architecture: str


def _canonical_lock_bytes(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("ascii")


def _lock_string(payload: Mapping[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value:
        raise CaptureImageLockError(f"image-lock field {field!r} must be a string")
    return value


def load_capture_image_lock(path: Path) -> CaptureImageLock:
    """Load a strict checked lock without ever creating or refreshing it."""

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CaptureImageLockError(f"cannot read capture image lock {path}: {error}") from error
    try:
        parsed = cast(
            object,
            json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CaptureImageLockError(f"invalid capture image lock JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise CaptureImageLockError("capture image lock must be a JSON object")
    payload = cast(dict[str, object], parsed)
    fields = frozenset(payload)
    if fields != _CAPTURE_IMAGE_LOCK_FIELDS:
        missing = sorted(_CAPTURE_IMAGE_LOCK_FIELDS - fields)
        unknown = sorted(fields - _CAPTURE_IMAGE_LOCK_FIELDS)
        raise CaptureImageLockError(f"invalid image-lock fields; missing={missing!r}, unknown={unknown!r}")
    if raw != _canonical_lock_bytes(payload):
        raise CaptureImageLockError("capture image lock is not canonical JSON")

    base_reference = _lock_string(payload, "base_reference")
    if "@" in base_reference or any(character.isspace() for character in base_reference):
        raise CaptureImageLockError("base_reference must be a tag-only OCI reference")
    if ":" not in base_reference.rsplit("/", maxsplit=1)[-1]:
        raise CaptureImageLockError("base_reference must include an explicit tag")
    base_digest = _lock_string(payload, "base_digest")
    if _SHA256_PATTERN.fullmatch(base_digest) is None:
        raise CaptureImageLockError("base_digest must be a lowercase sha256 digest")
    expected_image_id = _lock_string(payload, "expected_capture_image_id")
    if _SHA256_PATTERN.fullmatch(expected_image_id) is None:
        raise CaptureImageLockError("expected_capture_image_id must be a lowercase sha256 content ID")

    snapshot = _lock_string(payload, "debian_snapshot")
    if _SNAPSHOT_PATTERN.fullmatch(snapshot) is None:
        raise CaptureImageLockError("debian_snapshot must use the YYYYMMDDTHHMMSSZ form")
    try:
        source_date_epoch = int(datetime.strptime(snapshot, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp())
    except ValueError as error:
        raise CaptureImageLockError("debian_snapshot must be a valid UTC timestamp") from error

    packages_value = payload["direct_packages"]
    if not isinstance(packages_value, dict):
        raise CaptureImageLockError("direct_packages must be a JSON object")
    packages = cast(dict[str, object], packages_value)
    if frozenset(packages) != _CAPTURE_DIRECT_PACKAGES:
        raise CaptureImageLockError("direct_packages must contain exactly ca-certificates, curl, and wireshark-common")
    package_versions: dict[str, str] = {}
    for package_name in sorted(packages):
        version = packages[package_name]
        if not isinstance(version, str) or _DEBIAN_VERSION_PATTERN.fullmatch(version) is None:
            raise CaptureImageLockError(f"direct package {package_name!r} must have one exact Debian version")
        package_versions[package_name] = version

    capture_tool_version = _lock_string(payload, "capture_tool_version")
    if _CAPTURE_TOOL_VERSION_PATTERN.fullmatch(capture_tool_version) is None:
        raise CaptureImageLockError("capture_tool_version must be a three-component numeric version")
    return CaptureImageLock(
        base_reference=base_reference,
        base_digest=base_digest,
        debian_snapshot=snapshot,
        source_date_epoch=source_date_epoch,
        direct_packages=MappingProxyType(package_versions),
        capture_tool_version=capture_tool_version,
        expected_capture_image_id=expected_image_id,
    )


def validate_capture_dockerfile(
    dockerfile: str,
    lock: CaptureImageLock,
) -> None:
    """Require the capture Dockerfile to consume only inputs named by the lock."""

    expected = f"""ARG SOURCE_DATE_EPOCH={lock.source_date_epoch}
FROM {lock.base_reference}@{lock.base_digest}

RUN printf '%s\\n' \\
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/{lock.debian_snapshot}/ bookworm main' \\
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/{lock.debian_snapshot}/ bookworm-security main' \\
    > /etc/apt/sources.list \\
 && rm -f /etc/apt/sources.list.d/debian.sources \\
 && apt-get -o Acquire::Check-Valid-Until=false update \\
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
   ca-certificates={lock.direct_packages["ca-certificates"]} \\
   curl={lock.direct_packages["curl"]} \\
   wireshark-common={lock.direct_packages["wireshark-common"]} \\
 && rm -rf /var/lib/apt/lists/* /var/cache/* /var/log/* /tmp/* /var/tmp/*

COPY --chmod=0755 capture.sh /usr/local/bin/trafficlab-capture

ENTRYPOINT ["/usr/local/bin/trafficlab-capture"]
"""
    if dockerfile != expected:
        raise CaptureImageLockError(
            "capture Dockerfile must exactly match the locked base digest, snapshot-derived "
            "SOURCE_DATE_EPOCH, snapshot APT sources, apt operations, and package versions including curl"
        )


def parse_image_inspect(reference: str, stdout: str) -> ImageIdentity:
    """Parse one Docker image-inspect record and bind it to the requested ref."""

    try:
        payload = cast(object, json.loads(stdout))
    except json.JSONDecodeError as error:
        raise CaptureImageLockError(f"invalid Docker image inspect JSON: {error}") from error
    if not isinstance(payload, list):
        raise CaptureImageLockError("Docker image inspect must return exactly one image")
    records = cast(list[object], payload)
    if len(records) != 1:
        raise CaptureImageLockError("Docker image inspect must return exactly one image")
    record = records[0]
    if not isinstance(record, dict):
        raise CaptureImageLockError("Docker image inspect record must be an object")
    typed_record = cast(dict[str, object], record)
    content_id = typed_record.get("Id")
    if not isinstance(content_id, str) or _SHA256_PATTERN.fullmatch(content_id) is None:
        raise CaptureImageLockError("Docker image inspect has an invalid content ID")
    repo_tags_value = typed_record.get("RepoTags", [])
    repo_digests_value = typed_record.get("RepoDigests", [])
    if not isinstance(repo_tags_value, list):
        raise CaptureImageLockError("Docker image inspect has invalid RepoTags")
    repo_tags = cast(list[object], repo_tags_value)
    if not all(isinstance(item, str) for item in repo_tags):
        raise CaptureImageLockError("Docker image inspect has invalid RepoTags")
    if not isinstance(repo_digests_value, list):
        raise CaptureImageLockError("Docker image inspect has invalid RepoDigests")
    repo_digests = cast(list[object], repo_digests_value)
    if not all(isinstance(item, str) for item in repo_digests):
        raise CaptureImageLockError("Docker image inspect has invalid RepoDigests")

    if reference.startswith("sha256:"):
        reference_matches = reference == content_id
    elif "@sha256:" in reference:
        reference_matches = reference in repo_digests
    else:
        reference_matches = reference in repo_tags
    if not reference_matches:
        raise CaptureImageLockError(f"Docker image inspect does not match requested reference {reference!r}")
    operating_system = typed_record.get("Os")
    if not isinstance(operating_system, str) or not operating_system:
        raise CaptureImageLockError("Docker image inspect has an invalid operating system")
    architecture = typed_record.get("Architecture")
    if not isinstance(architecture, str) or not architecture:
        raise CaptureImageLockError("Docker image inspect has an invalid architecture")
    return ImageIdentity(
        reference=reference,
        content_id=content_id,
        operating_system=operating_system,
        architecture=architecture,
    )


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
        return self._run(
            ("docker", "info", "--format", "{{json .}}"),
            "Docker info",
            timeout=timeout,
            deadline=deadline,
        )

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
