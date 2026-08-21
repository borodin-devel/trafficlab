from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from tests.fixtures.paths import DOCKER_FIXTURE_ROOT
from tests.support.external import external_tests_requested, require_serial_external_tests, run_external_command
from trafficlab.capture.docker.compose import DockerCompose
from trafficlab.capture.docker.image import (
    CaptureImageLockError,
    load_capture_image_lock,
    parse_image_inspect,
    validate_capture_platform,
)
from trafficlab.capture.docker.types import CommandResult
from trafficlab.capture.topology import ComposePaths
from trafficlab.common.config import ExperimentConfig

REPOSITORY_ROOT = Path(__file__).parents[2].resolve()
CAPTURE_IMAGE = "trafficlab-capture:docker-capture-test"
ENDPOINT_IMAGE = "trafficlab-endpoint:docker-capture-test"
CLIENT_IMAGE = "trafficlab-client:docker-capture-test"
NO_SHELL_IMAGE = "trafficlab-no-shell:docker-capture-test"
_DUMPCAP_VERSION = re.compile(r"^Dumpcap \(Wireshark\) ([0-9]+(?:\.[0-9]+){2})(?:\s|$)")


@dataclass(frozen=True, slots=True)
class DockerTestEnvironment:
    capture_image: str = CAPTURE_IMAGE
    endpoint_image: str = ENDPOINT_IMAGE
    client_image: str = CLIENT_IMAGE
    no_shell_image: str = NO_SHELL_IMAGE
    fixture_root: Path = DOCKER_FIXTURE_ROOT


def build_test_image(
    tag: str,
    context: Path,
    *,
    dockerfile: Path | None = None,
    reproducible_capture: bool = False,
) -> None:
    argv = ["docker", "build"]
    if reproducible_capture:
        argv.extend(
            (
                "--pull",
                "--no-cache",
                "--provenance=false",
                "--platform",
                "linux/amd64",
                "--output",
                "type=image,rewrite-timestamp=true,unpack=false",
            )
        )
    else:
        argv.append("--pull=false")
    argv.extend(("--tag", tag))
    if dockerfile is not None:
        argv.extend(("--file", str(dockerfile)))
    argv.append(str(context))
    run_external_command(argv, purpose=f"build required test image {tag}", timeout=300.0)


def require_checked_prebuilt_capture_image(reference: str) -> str:
    """Require a borrowed capture image to match the checked ID, platform, and tool contract."""

    if not reference:
        raise pytest.UsageError("--capture-image must name one nonempty checked capture image")
    try:
        inspected = run_external_command(
            ("docker", "image", "inspect", reference),
            purpose=f"inspect provided capture image {reference}",
            timeout=20.0,
        )
        identity = parse_image_inspect(reference, inspected.stdout)
        lock = load_capture_image_lock(REPOSITORY_ROOT / "docker" / "capture" / "image-lock.json")
        validate_capture_platform(
            identity.operating_system,
            identity.architecture,
            source="provided capture test image",
        )
    except CaptureImageLockError as error:
        raise pytest.UsageError(f"provided --capture-image is incompatible: {error}") from error
    if identity.content_id != lock.expected_capture_image_id:
        raise pytest.UsageError(
            "provided --capture-image content ID does not match the checked lock: "
            f"expected {lock.expected_capture_image_id}, resolved {identity.content_id}"
        )
    tool = run_external_command(
        ("docker", "run", "--rm", "--entrypoint", "dumpcap", reference, "--version"),
        purpose=f"verify provided capture image {reference} capture tool version",
        timeout=20.0,
    )
    match = _DUMPCAP_VERSION.match(tool.stdout)
    if match is None or match.group(1) != lock.capture_tool_version:
        raise pytest.UsageError(
            "provided --capture-image capture tool version does not match the checked lock: "
            f"expected {lock.capture_tool_version}"
        )
    return reference


def provision_docker_test_environment(
    config: pytest.Config,
    *,
    project_registry: DockerProjectRegistry | None = None,
) -> Generator[DockerTestEnvironment, None, None]:
    """Build one external-test environment and remove every image it owns."""

    if not (external_tests_requested(config, "docker") or external_tests_requested(config, "internet")):
        raise pytest.UsageError("Docker fixtures may only be used by an explicitly selected external test scope")
    require_serial_external_tests(config)
    run_external_command(("docker", "info"), purpose="reach a functioning Docker Engine", timeout=20.0)
    run_external_command(
        ("docker", "compose", "version", "--format", "json"),
        purpose="verify the Docker Compose plugin",
        timeout=20.0,
    )
    supplied_capture_image = cast(str | None, config.getoption("capture_image"))
    session_suffix = uuid.uuid4().hex
    environment = DockerTestEnvironment(
        capture_image=(
            require_checked_prebuilt_capture_image(supplied_capture_image)
            if supplied_capture_image is not None
            else f"{CAPTURE_IMAGE}-{session_suffix}"
        ),
        endpoint_image=f"{ENDPOINT_IMAGE}-{session_suffix}",
        client_image=f"{CLIENT_IMAGE}-{session_suffix}",
        no_shell_image=f"{NO_SHELL_IMAGE}-{session_suffix}",
    )
    built_images: list[str] = []
    try:
        if supplied_capture_image is None:
            build_test_image(
                environment.capture_image,
                REPOSITORY_ROOT / "docker" / "capture",
                reproducible_capture=True,
            )
            built_images.append(environment.capture_image)
        build_test_image(environment.client_image, environment.fixture_root / "images" / "client")
        built_images.append(environment.client_image)
        if external_tests_requested(config, "docker"):
            build_test_image(environment.endpoint_image, environment.fixture_root / "images" / "endpoint")
            built_images.append(environment.endpoint_image)
            build_test_image(environment.no_shell_image, environment.fixture_root / "images" / "no_shell")
            built_images.append(environment.no_shell_image)
        yield environment
    finally:
        cleanup_errors: list[BaseException] = []
        if project_registry is not None:
            try:
                project_registry.sweep()
            except BaseException as error:
                cleanup_errors.append(error)
        for image in reversed(built_images):
            try:
                run_external_command(
                    ("docker", "image", "rm", "--force", image),
                    purpose=f"remove owned test image {image}",
                    timeout=20.0,
                )
            except BaseException as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            primary = cleanup_errors[0]
            for secondary in cleanup_errors[1:]:
                primary.add_note(f"additional owned image cleanup failure: {secondary}")
            raise primary


ResourceNames = Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class DockerResourceInspection:
    resources: ResourceNames
    diagnostics: tuple[str, ...]


def _bounded_timeout(
    maximum: float,
    *,
    deadline: float | None,
    clock: Callable[[], float],
) -> float:
    if deadline is None:
        return maximum
    remaining = deadline - clock()
    if remaining <= 0.0:
        raise pytest.UsageError("Docker project cleanup sweep deadline expired")
    return min(maximum, remaining)


def inspect_project_resources(
    project_name: str,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DockerResourceInspection:
    """Inspect every labelled resource kind without letting one Docker failure abort the rest."""
    label = f"label=com.docker.compose.project={project_name}"
    commands = {
        "containers": ("docker", "ps", "--all", "--filter", label, "--format", "{{.Names}}"),
        "networks": ("docker", "network", "ls", "--filter", label, "--format", "{{.Name}}"),
        "volumes": ("docker", "volume", "ls", "--filter", label, "--format", "{{.Name}}"),
    }
    resources: dict[str, tuple[str, ...]] = {}
    diagnostics: list[str] = []
    for kind, argv in commands.items():
        try:
            result = run_external_command(
                argv,
                purpose=f"inspect {project_name} {kind}",
                timeout=_bounded_timeout(15.0, deadline=deadline, clock=clock),
            )
        except pytest.UsageError as error:
            resources[kind] = ()
            diagnostics.append(f"could not inspect Docker project {project_name!r} {kind}: {error}")
        else:
            resources[kind] = tuple(sorted(name for name in result.stdout.splitlines() if name))
    return DockerResourceInspection(resources=resources, diagnostics=tuple(diagnostics))


def remaining_resource_message(project_name: str, remaining: ResourceNames) -> str:
    rendered = "; ".join(
        f"{kind}=[{', '.join(remaining.get(kind, ()))}]" for kind in ("containers", "networks", "volumes")
    )
    return f"Docker project {project_name!r} still has labelled resources: {rendered}"


def _remove_remaining(
    remaining: ResourceNames,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[str, ...]:
    commands = {
        "containers": ("docker", "rm", "--force"),
        "networks": ("docker", "network", "rm"),
        "volumes": ("docker", "volume", "rm", "--force"),
    }
    diagnostics: list[str] = []
    for kind in ("containers", "networks", "volumes"):
        names = remaining.get(kind, ())
        if names:
            try:
                result = run_external_command(
                    (*commands[kind], *names),
                    purpose=f"remove leaked test {kind}",
                    timeout=_bounded_timeout(30.0, deadline=deadline, clock=clock),
                    check=False,
                )
            except pytest.UsageError as error:
                diagnostics.append(f"could not remove leaked test {kind} {list(names)!r}: {error}")
            else:
                if result.returncode != 0:
                    detail = result.stderr.strip() or result.stdout.strip() or "no command output"
                    diagnostics.append(
                        f"could not remove leaked test {kind} {list(names)!r} (status {result.returncode}): {detail}"
                    )
    return tuple(diagnostics)


@dataclass(slots=True)
class DockerProjectRegistry:
    """Session-owned exact project names eligible for one bounded recovery sweep."""

    projects: set[str] = field(default_factory=lambda: set[str]())

    def add(self, project_name: str) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name) is None:
            raise ValueError("invalid Docker test project name")
        self.projects.add(project_name)

    def sweep(self, *, timeout: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        """Inspect, remove, and re-inspect only projects registered by this test session."""
        deadline = clock() + timeout
        failures: list[str] = []
        for project_name in sorted(self.projects):
            inspection = inspect_project_resources(project_name, deadline=deadline, clock=clock)
            failures.extend(inspection.diagnostics)
            remaining = inspection.resources
            if any(remaining.values()):
                failures.append(remaining_resource_message(project_name, remaining))
                failures.extend(_remove_remaining(remaining, deadline=deadline, clock=clock))
                final = inspect_project_resources(project_name, deadline=deadline, clock=clock)
                failures.extend(final.diagnostics)
                if any(final.resources.values()):
                    failures.append(remaining_resource_message(project_name, final.resources))
        if failures:
            pytest.fail("\n".join(failures), pytrace=False)


@dataclass(slots=True)
class DockerProjectTracker:
    registry: DockerProjectRegistry | None = None
    projects: set[str] = field(default_factory=lambda: set[str]())

    def add(self, project_name: str) -> None:
        if self.registry is not None:
            self.registry.add(project_name)
        self.projects.add(project_name)

    def assert_clean(self) -> None:
        failures: list[str] = []
        for project_name in sorted(self.projects):
            inspection = inspect_project_resources(project_name)
            failures.extend(inspection.diagnostics)
            remaining = inspection.resources
            if any(remaining.values()):
                failures.append(remaining_resource_message(project_name, remaining))
        if failures:
            pytest.fail("\n".join(failures), pytrace=False)

    def finish(self, body_error: BaseException | None) -> None:
        """Always check cleanup while retaining an original test-body failure."""
        try:
            self.assert_clean()
        except BaseException as cleanup_error:
            if body_error is not None:
                raise BaseExceptionGroup(
                    "Docker test body and cleanup both failed",
                    (body_error, cleanup_error),
                ) from None
            raise


def merge_endpoint_overlay(production: bytes) -> bytes:
    """Merge the checked-in endpoint into a test document without changing production rendering."""
    production_document = cast(dict[str, object], json.loads(production))
    overlay_document = cast(dict[str, object], json.loads((DOCKER_FIXTURE_ROOT / "compose.endpoint.json").read_bytes()))
    production_services = cast(dict[str, object], production_document["services"])
    overlay_services = cast(dict[str, object], overlay_document["services"])
    production_services.update(overlay_services)
    production_document["networks"] = overlay_document["networks"]
    production_document["volumes"] = {"lifecycle": {}}
    endpoint = cast(dict[str, object], production_services["endpoint"])
    endpoint["volumes"] = [{"type": "volume", "source": "lifecycle", "target": "/trafficlab-test-volume"}]
    return (json.dumps(production_document, sort_keys=True, separators=(",", ":")) + "\n").encode()


class TrackedDockerCompose(DockerCompose):
    """Real adapter that records each resource-owning unique test project."""

    def __init__(self, tracker: DockerProjectTracker) -> None:
        super().__init__()
        self.tracker = tracker

    def create_capture(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        self.tracker.add(project_name)
        return super().create_capture(compose_path, project_name, timeout=timeout, deadline=deadline)


class EndpointDockerCompose(TrackedDockerCompose):
    """Test-only adapter that starts the overlay endpoint in the capture project."""

    def create_capture(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        self.tracker.add(project_name)
        if deadline is None:
            remaining = timeout
        else:
            remaining = deadline - time.monotonic()
        if remaining is None or remaining <= 0.0:
            raise pytest.UsageError(f"test endpoint deadline expired before project {project_name!r} could start")
        run_external_command(
            (
                "docker",
                "compose",
                "--project-name",
                project_name,
                "--file",
                str(compose_path),
                "up",
                "--detach",
                "--wait",
                "--wait-timeout",
                "10",
                "--no-deps",
                "endpoint",
                "noise",
                "orphan",
            ),
            purpose=f"start controlled endpoint for project {project_name}",
            timeout=remaining,
        )
        while True:
            if deadline is None:
                log_timeout = remaining
            else:
                log_timeout = deadline - time.monotonic()
            if log_timeout <= 0.0:
                raise pytest.UsageError(
                    f"controlled unrelated traffic did not become ready for project {project_name!r}"
                )
            logs = run_external_command(
                (
                    "docker",
                    "compose",
                    "--project-name",
                    project_name,
                    "--file",
                    str(compose_path),
                    "logs",
                    "--no-color",
                    "endpoint",
                ),
                purpose=f"check controlled endpoint readiness for project {project_name}",
                timeout=log_timeout,
            )
            if "noise-exchange-ready" in logs.stdout:
                break
            time.sleep(0.05)
        document = cast(dict[str, object], json.loads(compose_path.read_bytes()))
        services = cast(dict[str, object], document["services"])
        del services["orphan"]
        compose_path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return DockerCompose.create_capture(self, compose_path, project_name, timeout=timeout, deadline=deadline)


def install_endpoint_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    import trafficlab.capture.stage as capture_module
    import trafficlab.preflight.stage as preflight_module
    from trafficlab.capture.topology import write_production_compose

    original_probe_document = preflight_module._probe_document  # pyright: ignore[reportPrivateUsage]

    def write_with_endpoint(
        path: Path,
        config: ExperimentConfig,
        paths: ComposePaths,
        *,
        target_image: str,
        capture_image: str,
    ) -> None:
        write_production_compose(
            path,
            config,
            paths,
            target_image=target_image,
            capture_image=capture_image,
        )
        path.write_bytes(merge_endpoint_overlay(path.read_bytes()))

    def probe_with_endpoint(
        config: ExperimentConfig,
        paths: ComposePaths,
        *,
        capture_image: str,
    ) -> bytes:
        return merge_endpoint_overlay(original_probe_document(config, paths, capture_image=capture_image))

    monkeypatch.setattr(capture_module, "write_production_compose", write_with_endpoint)
    monkeypatch.setattr(preflight_module, "_probe_document", probe_with_endpoint)
