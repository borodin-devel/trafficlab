"""Immutable contracts shared across preflight owners."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, cast

from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError

if TYPE_CHECKING:
    from trafficlab.capture.docker.image import CaptureImageLock, CapturePlatform, ImageIdentity
    from trafficlab.capture.docker.types import ProcessHandle, ServiceState


class SupportsFree(Protocol):
    """A disk-usage result exposing available bytes."""

    @property
    def free(self) -> int: ...


class DiskUsage(Protocol):
    """Callable boundary for checking available disk space."""

    def __call__(self, path: Path) -> SupportsFree: ...


class Writable(Protocol):
    """Callable boundary for checking directory writability."""

    def __call__(self, path: Path) -> bool: ...


class DockerResult(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


# This narrow protocol is also the seam used by in-process tests.  Keeping it at
# Docker operations rather than subprocess primitives lets preflight validate
# orchestration order without starting containers.
class DockerPreflight(Protocol):
    """Docker operations needed by full preflight without importing the concrete adapter."""

    def info(self, *, deadline: float) -> DockerResult: ...

    def compose_version(self, *, deadline: float) -> DockerResult: ...

    def image_inspect(self, image: str, *, deadline: float) -> DockerResult: ...

    def image_pull(self, image: str, *, deadline: float) -> DockerResult: ...

    def config(self, compose_path: Path, project_name: str, *, deadline: float) -> DockerResult: ...

    def create_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> DockerResult: ...

    def start_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> DockerResult: ...

    def start_target(self, compose_path: Path, project_name: str, *, deadline: float) -> DockerResult: ...

    def service_state(
        self, compose_path: Path, project_name: str, service: str, *, deadline: float
    ) -> ServiceState | None: ...

    def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> DockerResult: ...

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> ProcessHandle: ...


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    """Result of one local preflight check."""

    name: str
    ok: bool
    detail: str
    corrective_action: str | None = None


@dataclass(frozen=True, slots=True)
class MountedInputIdentity:
    """One path-independent immutable-input identity bound to its container mount semantics."""

    target: str
    read_only: bool
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if type(self.target) is not str or not self.target.strip() or not PurePosixPath(self.target).is_absolute():
            raise ValueError("mounted input target must be a nonempty absolute POSIX path")
        if type(self.read_only) is not bool:
            raise TypeError("mounted input read_only must be a boolean")
        ContentIdentity(size=self.size, sha256=self.sha256)

    def as_dict(self) -> dict[str, object]:
        """Return the canonical flat JSON record."""
        return {
            "target": self.target,
            "read_only": self.read_only,
            "size": self.size,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> MountedInputIdentity:
        """Strictly parse one persisted flat mounted-input record."""
        if type(value) is not dict:
            raise TypeError("mounted input identity must be an object")
        document = cast(dict[str, object], value)
        if set(document) != {"target", "read_only", "size", "sha256"}:
            raise ValueError("mounted input identity fields are not canonical")
        return cls(
            target=cast(str, document["target"]),
            read_only=cast(bool, document["read_only"]),
            size=cast(int, document["size"]),
            sha256=cast(str, document["sha256"]),
        )


@dataclass(frozen=True, slots=True)
class CaptureEnvironmentIdentity:
    """Resolved image and capture-tool identity required by a fresh capture."""

    host_architecture: CapturePlatform
    target_reference: str
    target_content_id: str
    capture_reference: str
    capture_content_id: str
    capture_tool_version: str
    mounted_inputs: tuple[MountedInputIdentity, ...] = ()

    def __post_init__(self) -> None:
        if type(self.mounted_inputs) is not tuple or any(
            type(identity) is not MountedInputIdentity for identity in self.mounted_inputs
        ):
            raise TypeError("mounted_inputs must be a tuple of MountedInputIdentity values")


def capture_environment_identity(
    *,
    target: ImageIdentity,
    capture: ImageIdentity,
    lock: CaptureImageLock,
    execution_platform: CapturePlatform,
) -> CaptureEnvironmentIdentity:
    """Bind resolved images to the checked lock, rejecting any capture mismatch."""
    from trafficlab.capture.docker.image import CAPTURE_PLATFORM, CaptureImageLockError, validate_capture_platform

    if execution_platform != CAPTURE_PLATFORM:
        raise CaptureImageLockError(
            f"unsupported Docker execution platform {execution_platform!r}; required platform is {CAPTURE_PLATFORM}"
        )
    for name, identity in (("target", target), ("capture", capture)):
        validate_capture_platform(
            identity.operating_system,
            identity.architecture,
            source=f"{name} image",
        )

    if capture.content_id != lock.expected_capture_image_id:
        raise CaptureImageLockError(
            "resolved capture image does not match the expected capture image "
            f"content ID: expected {lock.expected_capture_image_id}, "
            f"resolved {capture.content_id}"
        )
    return CaptureEnvironmentIdentity(
        host_architecture=execution_platform,
        target_reference=target.reference,
        target_content_id=target.content_id,
        capture_reference=capture.reference,
        capture_content_id=capture.content_id,
        capture_tool_version=lock.capture_tool_version,
    )


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """All local preflight findings for one validated configuration."""

    config: ExperimentConfig
    findings: tuple[PreflightFinding, ...]
    environment_identity: CaptureEnvironmentIdentity | None = None

    def require_success(self) -> None:
        """Raise a direct stage error when any local check failed."""
        failures = [finding for finding in self.findings if not finding.ok]
        if failures:
            detail = "; ".join(f"{item.name}: {item.detail}" for item in failures)
            raise TrafficlabError(
                detail,
                corrective_action=failures[0].corrective_action or "correct the reported preflight failures",
            )


@dataclass(frozen=True, slots=True)
class PreparedExperiment:
    """A locally validated experiment with its initial artifacts published."""

    source: Path
    config: ExperimentConfig
    report: PreflightReport
    run_directory: Path
    portable_config: ExperimentConfig = cast(ExperimentConfig, None)

    def __post_init__(self) -> None:
        if cast(object, self.portable_config) is None:
            object.__setattr__(self, "portable_config", self.config)
