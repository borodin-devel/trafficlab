"""Local preflight checks and initial experiment-artifact publication."""

from __future__ import annotations

import json
import math
import os
import platform
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from trafficlab.artifacts import append_run_log, create_run_directory
from trafficlab.compose import ComposePaths, render_production_compose, write_production_compose
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import ConfigurationPair, load_configuration_pair, load_experiment, render_effective_config
from trafficlab.errors import (
    FailureAuthority,
    FailureOutcome,
    TrafficlabError,
    attach_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.pcapng import parse_pcapng
from trafficlab.trace import load_capture_metadata

if TYPE_CHECKING:
    from trafficlab.cleanup import CleanupResult
    from trafficlab.docker_cli import (
        CaptureImageLock,
        CapturePlatform,
        ImageIdentity,
        ProcessHandle,
        ProjectInventory,
        ServiceState,
    )


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CAPTURE_IMAGE_LOCK_PATH = _REPOSITORY_ROOT / "docker" / "capture" / "image-lock.json"
_CAPTURE_DOCKERFILE_PATH = _REPOSITORY_ROOT / "docker" / "capture" / "Dockerfile"


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

    def project_inventory(self, compose_path: Path, project_name: str, *, deadline: float) -> ProjectInventory: ...


def default_writable(path: Path) -> bool:
    """Return whether the current process can write to *path*."""
    return os.access(path, os.W_OK)


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    """Result of one local preflight check."""

    name: str
    ok: bool
    detail: str
    corrective_action: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureEnvironmentIdentity:
    """Resolved image and capture-tool identity required by a fresh capture."""

    host_architecture: CapturePlatform
    target_reference: str
    target_content_id: str
    capture_reference: str
    capture_content_id: str
    capture_tool_version: str


def capture_environment_identity(
    *,
    target: ImageIdentity,
    capture: ImageIdentity,
    lock: CaptureImageLock,
    host_architecture: str | None = None,
) -> CaptureEnvironmentIdentity:
    """Bind resolved images to the checked lock, rejecting any capture mismatch."""
    from trafficlab.docker_cli import CaptureImageLockError, normalize_capture_platform

    capture_platform = normalize_capture_platform(
        platform.machine() if host_architecture is None else host_architecture
    )

    if capture.content_id != lock.expected_capture_image_id:
        raise CaptureImageLockError(
            "resolved capture image does not match the expected capture image "
            f"content ID: expected {lock.expected_capture_image_id}, "
            f"resolved {capture.content_id}"
        )
    return CaptureEnvironmentIdentity(
        host_architecture=capture_platform,
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


def _nearest_existing_parent(path: Path) -> Path:
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    return parent


def _check_mounts(config: ExperimentConfig) -> PreflightFinding:
    missing = [mount.source for mount in config.target.mounts if not mount.source.exists()]
    if missing:
        detail = "missing mount source(s): " + ", ".join(str(path) for path in missing)
        return PreflightFinding("mounts", False, detail)
    return PreflightFinding("mounts", True, "all mount sources exist")


def _check_run_directory(config: ExperimentConfig, writable: Writable) -> PreflightFinding:
    run_directory = config.run.directory
    if run_directory.exists():
        return PreflightFinding("run_directory", False, f"run directory already exists: {run_directory}")

    parent = _nearest_existing_parent(run_directory)
    if not parent.is_dir():
        return PreflightFinding("run_directory", False, f"nearest existing parent is not a directory: {parent}")
    if not writable(parent):
        return PreflightFinding("run_directory", False, f"nearest existing parent is not writable: {parent}")
    return PreflightFinding("run_directory", True, "run directory is absent and its parent is writable")


def _check_free_space(config: ExperimentConfig, disk_usage: DiskUsage) -> PreflightFinding:
    parent = _nearest_existing_parent(config.run.directory)
    try:
        available = disk_usage(parent).free
    except OSError as error:
        return PreflightFinding("free_space", False, f"could not inspect free space at {parent}: {error}")
    minimum = config.run.minimum_free_bytes
    if available < minimum:
        return PreflightFinding(
            "free_space",
            False,
            f"available free space at {parent} is {available} bytes; requires at least {minimum} bytes",
        )
    return PreflightFinding("free_space", True, "available free space is sufficient")


def check_local(
    config: ExperimentConfig,
    *,
    disk_usage: DiskUsage = shutil.disk_usage,
    writable: Writable = default_writable,
) -> PreflightReport:
    """Evaluate all independent local checks for a validated configuration."""
    findings = (
        _check_mounts(config),
        _check_run_directory(config, writable),
        _check_free_space(config, disk_usage),
    )
    return PreflightReport(config=config, findings=findings)


def _deadline_error() -> TrafficlabError:
    return TrafficlabError(
        "Docker preflight exceeded the total-run deadline",
        corrective_action="increase capture.total_timeout_seconds and retry full preflight",
    )


def _require_deadline(deadline: float, clock: Callable[[], float]) -> float:
    try:
        now = clock()
        remaining = deadline - now
    except ArithmeticError as error:
        raise TrafficlabError(
            "could not calculate the Docker preflight deadline",
            corrective_action="use a finite monotonic clock and retry",
        ) from error
    if not math.isfinite(deadline) or not math.isfinite(now) or not math.isfinite(remaining) or remaining <= 0.0:
        raise _deadline_error()
    return remaining


def _failure(name: str, error: TrafficlabError) -> PreflightFinding:
    return PreflightFinding(name, False, str(error), error.corrective_action)


def _preflight_failure_outcome(
    finding: PreflightFinding, *, authority: FailureAuthority = "primary"
) -> FailureOutcome:
    """Render a direct preflight finding without changing its existing error path."""
    docker_findings = {
        "capture_image_lock",
        "capture_image",
        "capture_platform",
        "capture_tool",
        "compose_config",
        "docker_compose",
        "docker_daemon",
        "docker_engine",
        "docker_version",
        "mounts",
        "network_probe",
        "target_image",
    }
    if finding.name == "probe_cleanup":
        return FailureOutcome(
            kind="cleanup_failed",
            stage="preflight",
            detail=finding.detail,
            affected_evidence="inventory",
            evidence_state="possibly_remaining",
            corrective_action=finding.corrective_action or "remove the reported preflight resources and retry",
            authority=authority,
        )
    if finding.name == "run_log":
        return FailureOutcome(
            kind="publication_failed",
            stage="preflight",
            detail=finding.detail,
            affected_evidence="run.log",
            evidence_state="not_published",
            corrective_action=finding.corrective_action or "restore run.log durability and retry",
            authority=authority,
        )
    is_docker = finding.name in docker_findings
    return FailureOutcome(
        kind="docker_preflight_failed" if is_docker else "configuration_invalid",
        stage="preflight",
        detail=finding.detail,
        affected_evidence="capture evidence" if is_docker else "run evidence",
        evidence_state="not_published",
        corrective_action=finding.corrective_action or "correct the reported preflight failure",
        authority=authority,
    )


def _cleanup_detail(cleanup: CleanupResult) -> str:
    if not cleanup.secondary_details:
        return cleanup.detail
    return f"{cleanup.detail}; secondary: {'; '.join(cleanup.secondary_details)}"


def _image_ready(
    compose: DockerPreflight,
    image: str,
    *,
    deadline: float,
) -> ImageIdentity:
    from trafficlab.docker_cli import CaptureImageLockError, parse_image_inspect

    try:
        inspected = compose.image_inspect(image, deadline=deadline)
    except TrafficlabError:
        compose.image_pull(image, deadline=deadline)
        inspected = compose.image_inspect(image, deadline=deadline)
    try:
        return parse_image_inspect(image, inspected.stdout)
    except CaptureImageLockError as error:
        raise TrafficlabError(
            f"could not resolve image identity for {image!r}: {error}",
            corrective_action="pull the exact configured image and retry without changing the checked capture lock",
        ) from error


def _probe_document(config: ExperimentConfig, paths: ComposePaths) -> bytes:
    document = cast(dict[str, object], json.loads(render_production_compose(config, paths)))
    services = cast(dict[str, object], document["services"])
    target = cast(dict[str, object], services["target"])
    target.clear()
    target.update(
        {
            "command": [],
            "entrypoint": [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--connect-timeout",
                f"{config.capture.total_timeout_seconds:g}",
                "--max-time",
                f"{config.capture.total_timeout_seconds:g}",
                config.capture.network_probe_url,
            ],
            "image": config.capture.image,
            "init": True,
            "network_mode": "service:capture",
        }
    )
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_bytes(path: Path, content: bytes) -> None:
    try:
        path.write_bytes(content)
    except OSError as error:
        raise TrafficlabError(
            f"could not write preflight Compose file {path}: {error}",
            corrective_action="verify the run parent is writable and retry",
        ) from error


def _capture_ready(output: Path) -> bool:
    metadata_path = output / "capture.json"
    capture_path = output / "reference.pcapng.tmp"
    if not metadata_path.exists() or not capture_path.exists():
        return False
    load_capture_metadata(metadata_path)
    try:
        header = capture_path.read_bytes()[:28]
    except OSError as error:
        raise TrafficlabError(
            f"could not inspect preflight capture header: {error}",
            corrective_action="verify the capture image can write its output bind mount",
        ) from error
    if len(header) < 28 or header[:4] != b"\x0a\x0d\x0d\x0a":
        raise TrafficlabError(
            "capture probe did not create a valid nonempty PCAPNG header",
            corrective_action="verify the capture image contains a working dumpcap executable",
        )
    return True


def _wait_capture_ready(
    compose: DockerPreflight,
    compose_path: Path,
    project_name: str,
    output: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    observed: dict[str, ServiceState],
) -> None:
    while True:
        _require_deadline(deadline, clock)
        state = compose.service_state(compose_path, project_name, "capture", deadline=deadline)
        if state is not None:
            observed[state.service] = state
        if state is None or state.state != "running":
            raise TrafficlabError(
                "capture probe stopped before dumpcap became ready",
                corrective_action="verify the capture image can read eth0 and start dumpcap",
            )
        if _capture_ready(output):
            return


def _wait_target(
    compose: DockerPreflight,
    compose_path: Path,
    project_name: str,
    *,
    deadline: float,
    clock: Callable[[], float],
    observed: dict[str, ServiceState],
) -> None:
    while True:
        _require_deadline(deadline, clock)
        state = compose.service_state(compose_path, project_name, "target", deadline=deadline)
        if state is not None:
            observed[state.service] = state
        if state is None or state.state == "running":
            continue
        if state.state != "exited":
            raise TrafficlabError(
                f"network probe target entered unexpected state {state.state!r}",
                corrective_action="inspect the capture image and Docker Compose probe project",
            )
        if state.exit_code != 0:
            raise TrafficlabError(
                f"network probe target exited with status {state.exit_code}",
                corrective_action="verify DNS and the configured probe endpoint are reachable from Docker",
            )
        return


def _finish_capture_probe(
    compose: DockerPreflight,
    compose_path: Path,
    project_name: str,
    output: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    observed: dict[str, ServiceState],
) -> None:
    _require_deadline(deadline, clock)
    state = compose.service_state(compose_path, project_name, "capture", deadline=deadline)
    if state is not None:
        observed[state.service] = state
    if state is None or state.state != "running":
        raise TrafficlabError(
            "capture probe stopped unexpectedly during the network request",
            corrective_action="verify the capture image can keep dumpcap running on eth0",
        )
    compose.signal_capture(compose_path, project_name, deadline=deadline)
    while True:
        _require_deadline(deadline, clock)
        state = compose.service_state(compose_path, project_name, "capture", deadline=deadline)
        if state is not None:
            observed[state.service] = state
        if state is not None and state.state == "running":
            continue
        if state is None or state.state != "exited" or state.exit_code != 0:
            raise TrafficlabError(
                "capture probe did not flush successfully",
                corrective_action="verify dumpcap handles SIGINT and writes a complete PCAPNG",
            )
        break
    metadata = load_capture_metadata(output / "capture.json")
    events = parse_pcapng(output / "reference.pcapng.tmp", metadata, deadline=deadline, clock=clock)
    if not events:
        raise TrafficlabError(
            "network probe completed without captured Ethernet traffic",
            corrective_action="verify the probe endpoint is reached through capture service eth0",
        )


def _run_probe(
    config: ExperimentConfig,
    compose: DockerPreflight,
    compose_path: Path,
    project_name: str,
    output: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    observed: dict[str, ServiceState],
) -> None:
    compose.start_capture(compose_path, project_name, deadline=deadline)
    _wait_capture_ready(
        compose,
        compose_path,
        project_name,
        output,
        deadline=deadline,
        clock=clock,
        observed=observed,
    )
    compose.start_target(compose_path, project_name, deadline=deadline)
    _wait_target(compose, compose_path, project_name, deadline=deadline, clock=clock, observed=observed)
    _finish_capture_probe(
        compose,
        compose_path,
        project_name,
        output,
        deadline=deadline,
        clock=clock,
        observed=observed,
    )


def check_docker(
    config: ExperimentConfig,
    compose: DockerPreflight,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> PreflightReport:
    """Run sequential Docker checks and one disposable capture/network probe within one deadline."""
    from trafficlab.cleanup import cleanup_project
    from trafficlab.docker_cli import (
        CaptureImageLockError,
        ProjectInventory,
        load_capture_image_lock,
        normalize_capture_platform,
        validate_capture_dockerfile,
    )

    findings: list[PreflightFinding] = []

    try:
        lock = load_capture_image_lock(_CAPTURE_IMAGE_LOCK_PATH)
        dockerfile = _CAPTURE_DOCKERFILE_PATH.read_text(encoding="utf-8")
        validate_capture_dockerfile(dockerfile, lock)
    except (CaptureImageLockError, OSError) as error:
        translated = TrafficlabError(
            f"invalid checked capture image inputs: {error}",
            corrective_action="restore the reviewed capture Dockerfile and image lock; never refresh the lock during preflight",
        )
        return PreflightReport(
            config=config,
            findings=(_failure("capture_image_lock", translated),),
        )
    findings.append(
        PreflightFinding(
            "capture_image_lock",
            True,
            "capture base, Debian snapshot, packages, tool, and expected image ID are locked",
        )
    )

    try:
        capture_platform = normalize_capture_platform(platform.machine())
    except CaptureImageLockError as error:
        translated = TrafficlabError(
            str(error),
            corrective_action="run capture preflight on a linux/amd64 host and rebuild the checked capture image there",
        )
        findings.append(_failure("capture_platform", translated))
        return PreflightReport(config=config, findings=tuple(findings))
    findings.append(
        PreflightFinding(
            "capture_platform",
            True,
            f"host architecture resolves to supported capture platform {capture_platform}",
        )
    )

    target_identity: ImageIdentity | None = None
    capture_identity: ImageIdentity | None = None

    for name, success_detail, action in (
        ("docker_daemon", "Docker daemon is reachable", lambda: compose.info(deadline=deadline)),
        ("docker_compose", "Docker Compose v2 is available", lambda: compose.compose_version(deadline=deadline)),
        (
            "target_image",
            "target image is locally available",
            lambda: _image_ready(compose, config.target.image, deadline=deadline),
        ),
        (
            "capture_image",
            "capture image is locally available",
            lambda: _image_ready(compose, config.capture.image, deadline=deadline),
        ),
    ):
        try:
            _require_deadline(deadline, clock)
            result = action()
        except TrafficlabError as error:
            findings.append(_failure(name, error))
            return PreflightReport(config=config, findings=tuple(findings))
        if name == "target_image":
            target_identity = cast("ImageIdentity", result)
        elif name == "capture_image":
            capture_identity = cast("ImageIdentity", result)
        findings.append(PreflightFinding(name, True, success_detail))

    try:
        environment_identity = capture_environment_identity(
            target=cast("ImageIdentity", target_identity),
            capture=cast("ImageIdentity", capture_identity),
            lock=lock,
            host_architecture=capture_platform,
        )
    except CaptureImageLockError as error:
        translated = TrafficlabError(
            str(error),
            corrective_action="build or load the exact checked capture image; do not refresh the expected ID during preflight",
        )
        findings[-1] = _failure("capture_image", translated)
        return PreflightReport(config=config, findings=tuple(findings))

    try:
        with tempfile.TemporaryDirectory(prefix="trafficlab-preflight-", dir=config.run.directory.parent) as temporary:
            root = Path(temporary).resolve()
            production_output = root / "production-output"
            production_output.mkdir()
            production_name = f"trafficlab-config-{uuid.uuid4().hex}"
            production_path = root / "production.json"
            write_production_compose(
                production_path,
                config,
                ComposePaths(project_name=production_name, output_directory=production_output),
            )
            _require_deadline(deadline, clock)
            compose.config(production_path, production_name, deadline=deadline)
            findings.append(PreflightFinding("compose_config", True, "production Compose configuration is valid"))

            probe_name = f"trafficlab-preflight-{uuid.uuid4().hex}"
            probe_output = root / "probe-output"
            probe_output.mkdir()
            probe_path = root / "probe.json"
            _write_bytes(probe_path, _probe_document(config, ComposePaths(probe_name, probe_output)))
            observed: dict[str, ServiceState] = {}
            probe_created = False

            try:
                _require_deadline(deadline, clock)
                compose.config(probe_path, probe_name, deadline=deadline)
                _require_deadline(deadline, clock)
                probe_created = True
                compose.create_capture(probe_path, probe_name, deadline=deadline)
                _run_probe(
                    config,
                    compose,
                    probe_path,
                    probe_name,
                    probe_output,
                    deadline=deadline,
                    clock=clock,
                    observed=observed,
                )
            except TrafficlabError as error:
                findings.append(_failure("network_probe", error))
            else:
                findings.append(
                    PreflightFinding("network_probe", True, "capture image, eth0, dumpcap, DNS, and HTTP are ready")
                )

            finally:
                cleanup = cleanup_project(
                    compose,
                    probe_path,
                    probe_name,
                    ProjectInventory(
                        containers=tuple(
                            sorted(
                                observed.values(),
                                key=lambda item: (item.service, item.name, item.identifier),
                            )
                        ),
                        networks=(f"{probe_name}_default",) if probe_created else (),
                    ),
                    deadline=deadline,
                    clock=clock,
                )
                if not cleanup.success:
                    findings.append(
                        PreflightFinding(
                            "probe_cleanup",
                            False,
                            _cleanup_detail(cleanup),
                            "remove the uniquely named preflight Compose project and retry",
                        )
                    )
                else:
                    findings.append(PreflightFinding("probe_cleanup", True, "disposable probe project was removed"))
    except TrafficlabError as error:
        name = "compose_config" if not any(item.name == "compose_config" for item in findings) else "network_probe"
        findings.append(_failure(name, error))
    except OSError as error:
        translated = TrafficlabError(
            f"could not create disposable preflight files: {error}",
            corrective_action="verify the run parent is writable and retry",
        )
        findings.append(_failure("compose_config", translated))
    return PreflightReport(
        config=config,
        findings=tuple(findings),
        environment_identity=environment_identity,
    )


def _prepare_configuration_pair(path: Path, pair: ConfigurationPair, *, writable: Writable) -> PreparedExperiment:
    config = pair.realized
    report = check_local(config, writable=writable)
    report.require_success()
    run_directory = create_run_directory(config)
    return PreparedExperiment(
        source=path,
        portable_config=pair.portable,
        config=config,
        report=report,
        run_directory=run_directory,
    )


def prepare_experiment(path: Path, *, writable: Writable = default_writable) -> PreparedExperiment:
    """Load, locally validate, and publish a new experiment run directory."""
    return _prepare_configuration_pair(path, load_configuration_pair(path), writable=writable)


def _initial_run_records(run_directory: Path) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "event": "effective_config_published",
            "path": str(run_directory / "experiment.toml"),
            "stage": "preflight",
        },
        {"event": "run_prepared", "path": str(run_directory), "stage": "preflight"},
    )


def _validate_existing_run(config: ExperimentConfig) -> None:
    run_directory = config.run.directory
    snapshot_path = run_directory / "experiment.toml"
    log_path = run_directory / "run.log"
    try:
        expected_snapshot = render_effective_config(config)
        actual_snapshot = snapshot_path.read_bytes()
        if actual_snapshot != expected_snapshot:
            raise ValueError("experiment.toml bytes do not match the current effective configuration")
        if load_experiment(snapshot_path) != config:
            raise ValueError("experiment.toml does not parse as the current effective configuration")

        log_bytes = log_path.read_bytes()
        log_text = log_bytes.decode("utf-8", errors="strict")
        if not log_text.endswith("\n"):
            raise ValueError("run.log is not newline terminated")
        records: list[object] = [json.loads(line) for line in log_text.splitlines()]
        if len(records) < 2 or tuple(records[:2]) != _initial_run_records(run_directory):
            raise ValueError("run.log does not contain the required initial records")
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("run.log contains a record that is not an object")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TrafficlabError, ValueError) as error:
        raise TrafficlabError(
            f"existing run is not reusable: {error}",
            corrective_action="use the original matching experiment or choose a new run.directory",
        ) from error


def open_or_prepare_experiment(path: Path, *, writable: Writable = default_writable) -> PreparedExperiment:
    """Prepare an absent run or reopen an exact, authoritative prepared run without mutation."""
    pair = load_configuration_pair(path)
    config = pair.realized
    if not config.run.directory.exists():
        return _prepare_configuration_pair(path, pair, writable=writable)
    if not config.run.directory.is_dir():
        raise TrafficlabError(
            f"existing run is not reusable: run path is not a directory: {config.run.directory}",
            corrective_action="choose a new run.directory",
        )

    _validate_existing_run(config)
    if writable(config.run.directory):
        run_directory_finding = PreflightFinding(
            "run_directory",
            True,
            "existing prepared run matches the effective configuration and is writable",
        )
    else:
        run_directory_finding = PreflightFinding(
            "run_directory",
            False,
            f"existing run directory is not writable: {config.run.directory}",
            "make the existing run directory writable or choose a new run.directory",
        )
    report = PreflightReport(
        config=config,
        findings=(
            _check_mounts(config),
            run_directory_finding,
            _check_free_space(config, shutil.disk_usage),
        ),
    )
    report.require_success()
    return PreparedExperiment(
        source=path,
        portable_config=pair.portable,
        config=config,
        report=report,
        run_directory=config.run.directory,
    )


def run_preflight(
    path: Path,
    *,
    config_only: bool,
    docker: DockerPreflight | None = None,
    clock: Callable[[], float] = time.monotonic,
    writable: Writable = default_writable,
) -> PreparedExperiment:
    """Run local preparation and, unless disabled, the injected Docker preflight."""
    try:
        prepared = open_or_prepare_experiment(path, writable=writable)
    except TrafficlabError as error:
        if error.failure_outcome is None:
            outcome = failure_outcome_from_error(
                error,
                kind="configuration_invalid",
                stage="preflight",
                affected_evidence="run evidence",
                evidence_state="not_published",
            )
            error.failure_outcomes = (outcome,)
            error.failure_outcome = outcome
        raise
    if config_only:
        return prepared

    if docker is None:
        from trafficlab.docker_cli import DockerCompose

        docker = cast(DockerPreflight, DockerCompose(clock=clock))
    try:
        started = clock()
        deadline = started + prepared.config.capture.total_timeout_seconds
    except ArithmeticError as error:
        raise attach_failure_outcome(
            TrafficlabError(
                "could not calculate the Docker preflight deadline",
                corrective_action="use a finite monotonic clock and retry",
            ),
            kind="docker_preflight_failed",
            stage="preflight",
            affected_evidence="capture evidence",
            evidence_state="not_published",
        ) from error
    if not math.isfinite(started) or not math.isfinite(deadline) or deadline <= started:
        raise attach_failure_outcome(
            TrafficlabError(
                "could not calculate a finite future Docker preflight deadline",
                corrective_action="use a finite monotonic clock and positive total timeout",
            ),
            kind="docker_preflight_failed",
            stage="preflight",
            affected_evidence="capture evidence",
            evidence_state="not_published",
        )

    docker_report = check_docker(prepared.config, docker, deadline=deadline, clock=clock)
    prepared_findings = list(prepared.report.findings)
    docker_findings = list(docker_report.findings)
    findings = [*prepared_findings, *docker_findings]
    for index, finding in enumerate(docker_findings):
        try:
            record: dict[str, object] = {
                "detail": finding.detail,
                "event": "preflight_check",
                "name": finding.name,
                "ok": finding.ok,
                "stage": "preflight",
            }
            if not finding.ok:
                earlier_failure = any(
                    not previous.ok for previous in (*prepared_findings, *docker_findings[:index])
                )
                authority: FailureAuthority = (
                    "secondary" if finding.name == "probe_cleanup" and earlier_failure else "primary"
                )
                record["failure_outcome"] = _preflight_failure_outcome(finding, authority=authority).as_dict()
            append_run_log(prepared.run_directory, record)
        except TrafficlabError as error:
            findings.append(_failure("run_log", error))
            break

    environment_identity = docker_report.environment_identity
    if environment_identity is not None and not any(
        finding.name == "run_log" and not finding.ok for finding in findings
    ):
        try:
            append_run_log(
                prepared.run_directory,
                {
                    "capture_content_id": environment_identity.capture_content_id,
                    "capture_reference": environment_identity.capture_reference,
                    "capture_tool_version": environment_identity.capture_tool_version,
                    "event": "capture_environment_identity",
                    "host_architecture": environment_identity.host_architecture,
                    "stage": "preflight",
                    "target_content_id": environment_identity.target_content_id,
                    "target_reference": environment_identity.target_reference,
                },
            )
        except TrafficlabError as error:
            findings.append(_failure("run_log", error))

    report = PreflightReport(
        config=prepared.config,
        findings=tuple(findings),
        environment_identity=environment_identity,
    )
    try:
        report.require_success()
    except TrafficlabError as error:
        if error.failure_outcome is None:
            failed_findings = tuple(finding for finding in findings if not finding.ok)
            primary = _preflight_failure_outcome(failed_findings[0])
            secondary = tuple(
                _preflight_failure_outcome(finding, authority="secondary")
                for finding in failed_findings[1:]
                if finding.name in {"probe_cleanup", "run_log"}
            )
            error.failure_outcomes = (primary, *secondary)
            error.failure_outcome = primary
        raise
    return PreparedExperiment(
        source=prepared.source,
        portable_config=prepared.portable_config,
        config=prepared.config,
        report=report,
        run_directory=prepared.run_directory,
    )
