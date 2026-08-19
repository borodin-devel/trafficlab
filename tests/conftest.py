from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, cast

import pytest

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
