from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, cast

import pytest

from tests.support.config import valid_config_data as build_valid_config_data
from tests.support.docker import (
    DockerProjectTracker,
    DockerTestEnvironment,
    EndpointDockerCompose,
    TrackedDockerCompose,
    install_endpoint_overlay,
    provision_docker_test_environment,
)
from tests.support.external import (
    external_tests_requested,
    validate_internet_url,
)

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


@pytest.fixture(scope="session")
def docker_test_environment(pytestconfig: pytest.Config) -> Iterator[DockerTestEnvironment]:
    """Require and build the real-Docker test environment; never skip explicit selection."""
    yield from provision_docker_test_environment(pytestconfig)


@pytest.fixture(scope="session")
def internet_url(pytestconfig: pytest.Config) -> str:
    return validate_internet_url(cast(str | None, pytestconfig.getoption("internet_url")))


@pytest.fixture
def docker_project_tracker(request: pytest.FixtureRequest) -> Iterator[DockerProjectTracker]:
    tracker = DockerProjectTracker()
    try:
        yield tracker
    finally:
        tracker.finish(test_body_failure(request))


@pytest.fixture
def endpoint_docker(
    docker_test_environment: DockerTestEnvironment,
    docker_project_tracker: DockerProjectTracker,
    monkeypatch: pytest.MonkeyPatch,
) -> EndpointDockerCompose:
    del docker_test_environment
    install_endpoint_overlay(monkeypatch)
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
    return build_valid_config_data(tmp_path)
