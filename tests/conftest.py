from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlsplit

import pytest

from tests.fixtures.paths import DOCKER_FIXTURE_ROOT
from trafficlab.compose import ComposePaths
from trafficlab.config import ExperimentConfig
from trafficlab.docker_cli import (
    CaptureImageLockError,
    CommandResult,
    DockerCompose,
    load_capture_image_lock,
    parse_image_inspect,
    validate_capture_platform,
)

REPOSITORY_ROOT = Path(__file__).parents[1].resolve()
CAPTURE_IMAGE = "trafficlab-capture:docker-capture-test"
ENDPOINT_IMAGE = "trafficlab-endpoint:docker-capture-test"
CLIENT_IMAGE = "trafficlab-client:docker-capture-test"
NO_SHELL_IMAGE = "trafficlab-no-shell:docker-capture-test"
_EXTERNAL_MARKERS = frozenset({"docker", "internet"})
_MARK_EXPRESSION_TOKEN = re.compile(r"\b(?:not|and|or|[A-Za-z_][A-Za-z0-9_]*)\b|[()]")
_DUMPCAP_VERSION = re.compile(r"^Dumpcap \(Wireshark\) ([0-9]+(?:\.[0-9]+){2})(?:\s|$)")
_TEST_BODY_FAILURE = pytest.StashKey[BaseException]()


class _ItemFixtureRequest(Protocol):
    node: pytest.Item


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--internet-url",
        action="store",
        default=None,
        help="explicit HTTPS endpoint used only by the opt-in Internet capture smoke test",
    )
    parser.addoption(
        "--capture-image",
        action="store",
        default=None,
        help="checked prebuilt capture image used only by a validation-study prerequisite test sequence",
    )


def external_tests_requested(config: pytest.Config, marker: str) -> bool:
    """Return whether one external marker was named positively or by its test path."""
    if marker not in _EXTERNAL_MARKERS:
        raise ValueError(f"unknown external marker {marker!r}")
    expression = cast(str, config.getoption("markexpr"))
    negated = False
    pending_not = False
    negation_stack: list[bool] = []
    positive = False
    negative = False
    for token in _MARK_EXPRESSION_TOKEN.findall(expression):
        if token == "not":
            pending_not = not pending_not
        elif token == "(":
            negation_stack.append(negated)
            negated ^= pending_not
            pending_not = False
        elif token == ")":
            negated = negation_stack.pop() if negation_stack else False
            pending_not = False
        elif token in {"and", "or"}:
            pending_not = False
        else:
            if token == marker:
                if negated ^ pending_not:
                    negative = True
                else:
                    positive = True
            pending_not = False
    path_fragment = f"/tests/{marker}"
    path_selected = any(
        (normalized := "/" + str(argument).replace("\\", "/").lstrip("/")).startswith(f"/tests/{marker}")
        or path_fragment in normalized
        for argument in config.invocation_params.args
    )
    return (positive or path_selected) and not negative


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    docker_requested = external_tests_requested(config, "docker")
    internet_requested = external_tests_requested(config, "internet")
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        is_docker = item.get_closest_marker("docker") is not None
        is_internet = item.get_closest_marker("internet") is not None
        requested = (is_internet and internet_requested) or (is_docker and docker_requested)
        if (is_docker or is_internet) and not requested:
            deselected.append(item)
        else:
            selected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    """Retain a test-body exception so Docker teardown can report both failures."""
    yield
    if call.when == "call" and call.excinfo is not None:
        item.stash[_TEST_BODY_FAILURE] = call.excinfo.value


def test_body_failure(request: pytest.FixtureRequest) -> BaseException | None:
    """Return the retained call-phase failure for fixture cleanup arbitration."""

    node = cast(_ItemFixtureRequest, request).node
    return node.stash.get(_TEST_BODY_FAILURE, None)


def validate_internet_url(value: str | None) -> str:
    """Require an operator-supplied credential-free HTTPS URL with a hostname."""
    if value is None or not value:
        raise pytest.UsageError("--internet-url must supply an explicit HTTPS URL for the Internet smoke test")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise pytest.UsageError("--internet-url must supply a valid HTTPS URL with a hostname") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise pytest.UsageError("--internet-url must supply a credential-free HTTPS URL with a hostname")
    return value


def run_external_command(
    argv: Sequence[str],
    *,
    purpose: str,
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one bounded external-test command and translate unavailable tooling actionably."""
    try:
        result = subprocess.run(
            tuple(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise pytest.UsageError(
            f"Docker CLI was not found while attempting to {purpose}; install Docker Engine with a supported Compose plugin "
            "and ensure docker is available without sudo"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise pytest.UsageError(f"timed out after {timeout:g}s while attempting to {purpose}") from error
    except OSError as error:
        raise pytest.UsageError(f"could not {purpose}: {error}") from error
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise pytest.UsageError(f"could not {purpose} (status {result.returncode}): {detail}")
    return result


def require_serial_external_tests(config: pytest.Config) -> None:
    """Reject parallel execution because external tests own real project resources."""
    numprocesses = cast(int | None, getattr(config.option, "numprocesses", None))
    if numprocesses not in (None, 0):
        raise pytest.UsageError("Docker and Internet tests must run serially; invoke pytest with -n 0")


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


@pytest.fixture(scope="session")
def docker_test_environment(pytestconfig: pytest.Config) -> Iterator[DockerTestEnvironment]:
    """Require and build the real-Docker test environment; never skip explicit selection."""
    if not (external_tests_requested(pytestconfig, "docker") or external_tests_requested(pytestconfig, "internet")):
        raise pytest.UsageError("Docker fixtures may only be used by an explicitly selected external test scope")
    require_serial_external_tests(pytestconfig)
    run_external_command(("docker", "info"), purpose="reach a functioning Docker Engine", timeout=20.0)
    run_external_command(
        ("docker", "compose", "version", "--format", "json"),
        purpose="verify the Docker Compose plugin",
        timeout=20.0,
    )
    supplied_capture_image = cast(str | None, pytestconfig.getoption("capture_image"))
    environment = DockerTestEnvironment(
        capture_image=(
            require_checked_prebuilt_capture_image(supplied_capture_image)
            if supplied_capture_image is not None
            else f"{CAPTURE_IMAGE}-{uuid.uuid4().hex}"
        )
    )
    capture_built = False
    try:
        if supplied_capture_image is None:
            build_test_image(
                environment.capture_image,
                REPOSITORY_ROOT / "docker" / "capture",
                reproducible_capture=True,
            )
            capture_built = True
        build_test_image(environment.client_image, environment.fixture_root / "images" / "client")
        if external_tests_requested(pytestconfig, "docker"):
            build_test_image(environment.endpoint_image, environment.fixture_root / "images" / "endpoint")
            build_test_image(environment.no_shell_image, environment.fixture_root / "images" / "no_shell")
        yield environment
    finally:
        if capture_built:
            run_external_command(
                ("docker", "image", "rm", "--force", environment.capture_image),
                purpose=f"remove owned capture test image {environment.capture_image}",
                timeout=20.0,
            )


@pytest.fixture(scope="session")
def internet_url(pytestconfig: pytest.Config) -> str:
    return validate_internet_url(cast(str | None, pytestconfig.getoption("internet_url")))


ResourceNames = Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class DockerResourceInspection:
    resources: ResourceNames
    diagnostics: tuple[str, ...]


def inspect_project_resources(project_name: str) -> DockerResourceInspection:
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
            result = run_external_command(argv, purpose=f"inspect {project_name} {kind}", timeout=15.0)
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


def _remove_remaining(remaining: ResourceNames) -> tuple[str, ...]:
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
                    timeout=30.0,
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
class DockerProjectTracker:
    projects: set[str] = field(default_factory=lambda: set[str]())

    def add(self, project_name: str) -> None:
        self.projects.add(project_name)

    def assert_clean(self) -> None:
        failures: list[str] = []
        for project_name in sorted(self.projects):
            inspection = inspect_project_resources(project_name)
            failures.extend(inspection.diagnostics)
            remaining = inspection.resources
            if any(remaining.values()):
                failures.append(remaining_resource_message(project_name, remaining))
                failures.extend(_remove_remaining(remaining))
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


@pytest.fixture
def docker_project_tracker(request: pytest.FixtureRequest) -> Iterator[DockerProjectTracker]:
    tracker = DockerProjectTracker()
    try:
        yield tracker
    finally:
        node = cast(_ItemFixtureRequest, request).node
        tracker.finish(node.stash.get(_TEST_BODY_FAILURE, None))


def merge_endpoint_overlay(production: bytes) -> bytes:
    """Merge the checked-in endpoint into a test document without changing production rendering."""
    production_document = cast(dict[str, object], json.loads(production))
    overlay_document = cast(dict[str, object], json.loads((DOCKER_FIXTURE_ROOT / "compose.endpoint.json").read_bytes()))
    production_services = cast(dict[str, object], production_document["services"])
    overlay_services = cast(dict[str, object], overlay_document["services"])
    production_services.update(overlay_services)
    production_document["networks"] = overlay_document["networks"]
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


def _install_endpoint_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    import trafficlab.capture as capture_module
    import trafficlab.preflight as preflight_module
    from trafficlab.compose import write_production_compose

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


@pytest.fixture
def endpoint_docker(
    docker_test_environment: DockerTestEnvironment,
    docker_project_tracker: DockerProjectTracker,
    monkeypatch: pytest.MonkeyPatch,
) -> EndpointDockerCompose:
    del docker_test_environment
    _install_endpoint_overlay(monkeypatch)
    return EndpointDockerCompose(docker_project_tracker)


@pytest.fixture
def internet_docker(
    docker_test_environment: DockerTestEnvironment,
    docker_project_tracker: DockerProjectTracker,
) -> TrackedDockerCompose:
    del docker_test_environment
    return TrackedDockerCompose(docker_project_tracker)


@pytest.fixture
def valid_config_data(tmp_path: Path) -> dict[str, object]:
    return {
        "run": {
            "directory": str(tmp_path / "run"),
            "minimum_free_bytes": 1_048_576,
            "master_seed": 12345,
            "final_seed": 54321,
        },
        "target": {
            "image": "curlimages/curl:8.10.1",
            "argv": ["https://example.invalid/data"],
            "environment": {"LANG": "C"},
            "working_directory": "/work",
            "mounts": [],
        },
        "capture": {
            "image": "trafficlab-capture:local",
            "network_probe_url": "https://example.invalid/",
            "readiness_timeout_seconds": 10.0,
            "workload_timeout_seconds": 30.0,
            "flush_timeout_seconds": 5.0,
            "total_timeout_seconds": 60.0,
        },
        "generation": {
            "trial": {"max_packets": 2_000, "max_output_bytes": 4_000_000, "max_wall_seconds": 5.0},
            "final": {"max_packets": 20_000, "max_output_bytes": 40_000_000, "max_wall_seconds": 30.0},
        },
        "genetic": {
            "population_size": 9,
            "generation_count": 3,
            "tournament_size": 3,
            "elite_count": 1,
            "trial_seeds": [101, 102],
            "duplicate_mutation_attempts": 3,
            "early_stopping_generations": 0,
            "resume": False,
        },
        "models": {
            "enabled": ["poisson_empirical", "markov_renewal", "mmpp"],
            "poisson_empirical": {
                "crossover_probability": 0.9,
                "mutation_probability": 1.0,
                "mutation_scale": 0.1,
                "c_lambda": {"lower": 0.25, "upper": 4.0},
            },
            "markov_renewal": {
                "crossover_probability": 0.9,
                "mutation_probability": 0.2,
                "mutation_scale": 0.1,
                "q1": {"lower": 0.1, "upper": 0.4},
                "q2": {"lower": 0.6, "upper": 0.9},
                "alpha": {"lower": 0.0, "upper": 2.0},
                "r": {"lower": 1, "upper": 8},
                "c_t": {"lower": 0.25, "upper": 4.0},
            },
            "mmpp": {
                "crossover_probability": 0.9,
                "mutation_probability": 0.25,
                "mutation_scale": 0.1,
                "q01": {"lower": 0.01, "upper": 10.0},
                "q10": {"lower": 0.01, "upper": 10.0},
                "lambda0": {"lower": 0.01, "upper": 100.0},
                "lambda1": {"lower": 0.1, "upper": 1_000.0},
            },
        },
        "similarity": {
            "iat_diagnostic_quantile": 0.95,
            "acf_lags": [1],
            "acf_lag_weights": [1.0],
            "acf_iat_weight": 0.5,
            "acf_size_weight": 0.5,
            "multiscale_widths_seconds": [0.1, 1.0],
            "multiscale_scale_weights": [0.5, 0.5],
            "multiscale_packet_weight": 0.5,
            "multiscale_byte_weight": 0.5,
            "max_direction_bin_cells": 100_000,
            "method_weights": {
                "frame_size_ks": 0.25,
                "iat_ks": 0.25,
                "autocorrelation": 0.25,
                "multiscale_rate": 0.25,
            },
        },
    }
