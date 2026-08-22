"""Docker prerequisite decisions for full preflight."""

from __future__ import annotations

import json
import math
import re
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from trafficlab.capture.topology import ComposePaths, write_production_compose
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import FailureAuthority, FailureOutcome, TrafficlabError
from trafficlab.preflight.local import check_mounts
from trafficlab.preflight.probe import render_probe_compose, run_probe, write_probe_compose
from trafficlab.preflight.types import (
    DockerPreflight,
    PreflightFinding,
    PreflightReport,
    capture_environment_identity,
)

if TYPE_CHECKING:
    from trafficlab.capture.docker.image import ImageIdentity
    from trafficlab.capture.docker.types import DockerResult, ServiceState

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_CAPTURE_IMAGE_LOCK_PATH = _REPOSITORY_ROOT / "docker" / "capture" / "image-lock.json"
_CAPTURE_DOCKERFILE_PATH = _REPOSITORY_ROOT / "docker" / "capture" / "Dockerfile"
_COMPOSE_PLUGIN_VERSION = re.compile(r"v[25]\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")


def _deadline_error() -> TrafficlabError:
    return TrafficlabError(
        "Docker preflight exceeded the total-run deadline",
        corrective_action="increase capture.total_timeout_seconds and retry full preflight",
    )


def require_deadline(deadline: float, clock: Callable[[], float]) -> float:
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


def finding_from_error(name: str, error: TrafficlabError) -> PreflightFinding:
    return PreflightFinding(name, False, str(error), error.corrective_action)


def _parse_compose_plugin_version(result: DockerResult) -> str:
    """Validate a supported Compose v2/v5 machine-readable version response."""
    if result.returncode != 0:
        raise TrafficlabError(
            "Docker Compose version is incompatible",
            corrective_action="provide the named required Docker and Compose features",
        )
    try:
        document = cast(object, json.loads(result.stdout))
    except json.JSONDecodeError as error:
        raise TrafficlabError(
            "Docker Compose version is incompatible",
            corrective_action="provide the named required Docker and Compose features",
        ) from error
    if not isinstance(document, dict):
        raise TrafficlabError(
            "Docker Compose version is incompatible",
            corrective_action="provide the named required Docker and Compose features",
        )
    typed_document = cast(dict[str, object], document)
    version = typed_document.get("version")
    if not isinstance(version, str) or _COMPOSE_PLUGIN_VERSION.fullmatch(version) is None:
        raise TrafficlabError(
            "Docker Compose version is incompatible",
            corrective_action="provide the named required Docker and Compose features",
        )
    return version


def preflight_failure_outcome(finding: PreflightFinding, *, authority: FailureAuthority = "primary") -> FailureOutcome:
    """Render a direct preflight finding without changing its existing error path."""
    # These checks form one Docker capability boundary.  Callers can distinguish
    # configuration failures from daemon/image/network failures without parsing
    # human-readable detail strings.
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


def _image_ready(
    compose: DockerPreflight,
    image: str,
    *,
    deadline: float,
    unavailable_detail: str,
    unavailable_action: str,
) -> ImageIdentity:
    from trafficlab.capture.docker.image import CaptureImageLockError, parse_image_inspect, validate_capture_platform

    try:
        inspected = compose.image_inspect(image, deadline=deadline)
        if inspected.returncode != 0:
            raise TrafficlabError(unavailable_detail, corrective_action=unavailable_action)
    except TrafficlabError:
        try:
            pulled = compose.image_pull(image, deadline=deadline)
            if pulled.returncode != 0:
                raise TrafficlabError(unavailable_detail, corrective_action=unavailable_action)
            inspected = compose.image_inspect(image, deadline=deadline)
            if inspected.returncode != 0:
                raise TrafficlabError(unavailable_detail, corrective_action=unavailable_action)
        except TrafficlabError as error:
            raise TrafficlabError(unavailable_detail, corrective_action=unavailable_action) from error
    try:
        identity = parse_image_inspect(image, inspected.stdout)
        validate_capture_platform(
            identity.operating_system,
            identity.architecture,
            source=f"Docker image {image!r}",
        )
        return identity
    except CaptureImageLockError as error:
        raise TrafficlabError(
            f"could not resolve image identity for {image!r}: {error}",
            corrective_action="pull the exact configured image and retry without changing the checked capture lock",
        ) from error


def check_docker(
    config: ExperimentConfig,
    compose: DockerPreflight,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> PreflightReport:
    """Run sequential Docker checks and one disposable capture/network probe within one deadline."""
    from trafficlab.capture.cleanup import cleanup_project
    from trafficlab.capture.docker.image import (
        CaptureImageLockError,
        load_capture_image_lock,
        parse_docker_info_platform,
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
            findings=(finding_from_error("capture_image_lock", translated),),
        )
    findings.append(
        PreflightFinding(
            "capture_image_lock",
            True,
            "capture base, Debian snapshot, packages, tool, and expected image ID are locked",
        )
    )

    try:
        require_deadline(deadline, clock)
        docker_info = compose.info(deadline=deadline)
    except TrafficlabError:
        translated = TrafficlabError(
            "Docker Engine is unavailable",
            corrective_action="restore Docker Engine and Compose availability",
        )
        findings.append(finding_from_error("docker_daemon", translated))
        return PreflightReport(config=config, findings=tuple(findings))
    if docker_info.returncode != 0:
        translated = TrafficlabError(
            "Docker Engine is unavailable",
            corrective_action="restore Docker Engine and Compose availability",
        )
        findings.append(finding_from_error("docker_daemon", translated))
        return PreflightReport(config=config, findings=tuple(findings))
    findings.append(PreflightFinding("docker_daemon", True, "Docker daemon is reachable"))

    try:
        capture_platform = parse_docker_info_platform(docker_info.stdout)
    except CaptureImageLockError as error:
        translated = TrafficlabError(
            str(error),
            corrective_action="use a Docker daemon executing the linux/amd64 platform and retry full preflight",
        )
        findings.append(finding_from_error("capture_platform", translated))
        return PreflightReport(config=config, findings=tuple(findings))
    findings.append(
        PreflightFinding(
            "capture_platform",
            True,
            f"Docker daemon executes the supported capture platform {capture_platform}",
        )
    )

    target_identity: ImageIdentity | None = None
    capture_identity: ImageIdentity | None = None

    for name, success_detail, action in (
        ("docker_compose", "Docker Compose plugin is available", lambda: compose.compose_version(deadline=deadline)),
        (
            "target_image",
            "target image is locally available",
            lambda: _image_ready(
                compose,
                config.target.image,
                deadline=deadline,
                unavailable_detail=f"target image {config.target.image} is unavailable",
                unavailable_action="make the named image reference available",
            ),
        ),
        (
            "capture_image",
            "capture image is locally available",
            lambda: _image_ready(
                compose,
                config.capture.image,
                deadline=deadline,
                unavailable_detail="capture image identity is incompatible",
                unavailable_action="restore the declared image content identity and architecture",
            ),
        ),
    ):
        try:
            require_deadline(deadline, clock)
        except TrafficlabError as error:
            findings.append(finding_from_error(name, error))
            return PreflightReport(config=config, findings=tuple(findings))
        try:
            result = action()
            if name == "docker_compose":
                _parse_compose_plugin_version(cast("DockerResult", result))
        except TrafficlabError as source_error:
            error = source_error
            if name == "docker_compose":
                error = TrafficlabError(
                    "Docker Compose version is incompatible",
                    corrective_action="provide the named required Docker and Compose features",
                )
            findings.append(finding_from_error(name, error))
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
            execution_platform=capture_platform,
        )
    except CaptureImageLockError:
        translated = TrafficlabError(
            "capture image identity is incompatible",
            corrective_action="restore the declared image content identity and architecture",
        )
        findings[-1] = finding_from_error("capture_image", translated)
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
                target_image=environment_identity.target_content_id,
                capture_image=environment_identity.capture_content_id,
            )
            require_deadline(deadline, clock)
            mount_finding = check_mounts(config)
            if not mount_finding.ok:
                findings.append(mount_finding)
                return PreflightReport(
                    config=config,
                    findings=tuple(findings),
                    environment_identity=environment_identity,
                )
            try:
                configured = compose.config(production_path, production_name, deadline=deadline)
            except TrafficlabError as error:
                if config.target.mounts:
                    mount = config.target.mounts[0]
                    raise TrafficlabError(
                        f"mount target {mount.target} is incompatible",
                        corrective_action="correct the declared container target and mode",
                    ) from error
                raise
            if configured.returncode != 0:
                if config.target.mounts:
                    mount = config.target.mounts[0]
                    raise TrafficlabError(
                        f"mount target {mount.target} is incompatible",
                        corrective_action="correct the declared container target and mode",
                    )
                raise TrafficlabError(
                    "production Compose configuration is incompatible",
                    corrective_action="correct the generated Compose configuration and retry",
                )
            findings.append(PreflightFinding("compose_config", True, "production Compose configuration is valid"))

            probe_name = f"trafficlab-preflight-{uuid.uuid4().hex}"
            probe_output = root / "probe-output"
            probe_output.mkdir()
            probe_path = root / "probe.json"
            write_probe_compose(
                probe_path,
                render_probe_compose(
                    config,
                    ComposePaths(probe_name, probe_output),
                    capture_image=environment_identity.capture_content_id,
                ),
            )
            observed: dict[str, ServiceState] = {}
            try:
                require_deadline(deadline, clock)
                compose.config(probe_path, probe_name, deadline=deadline)
                require_deadline(deadline, clock)
                compose.create_capture(probe_path, probe_name, deadline=deadline)
                run_probe(
                    config,
                    compose,
                    probe_path,
                    probe_name,
                    probe_output,
                    deadline=deadline,
                    clock=clock,
                    observed=observed,
                    require_deadline=require_deadline,
                )
            except TrafficlabError as error:
                findings.append(finding_from_error("network_probe", error))
            else:
                findings.append(
                    PreflightFinding("network_probe", True, "capture image, eth0, dumpcap, DNS, and HTTP are ready")
                )

            finally:
                cleanup = cleanup_project(
                    compose,
                    probe_path,
                    probe_name,
                    deadline=deadline,
                    clock=clock,
                )
                if not cleanup.success:
                    findings.append(
                        PreflightFinding(
                            "probe_cleanup",
                            False,
                            cleanup.detail,
                            "remove the uniquely named preflight Compose project and retry",
                        )
                    )
                else:
                    findings.append(PreflightFinding("probe_cleanup", True, "disposable probe project was removed"))
    except TrafficlabError as error:
        name = "compose_config" if not any(item.name == "compose_config" for item in findings) else "network_probe"
        findings.append(finding_from_error(name, error))
    except OSError as error:
        translated = TrafficlabError(
            f"could not create disposable preflight files: {error}",
            corrective_action="verify the run parent is writable and retry",
        )
        findings.append(finding_from_error("compose_config", translated))
    return PreflightReport(
        config=config,
        findings=tuple(findings),
        environment_identity=environment_identity,
    )
