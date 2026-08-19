from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests.fixtures.paths import DOCKER_FIXTURE_ROOT
from tests.support import docker as docker_support
from tests.support import external


class _Config:
    def __init__(self, markexpr: str, *arguments: str, numprocesses: int | None = None) -> None:
        self._markexpr = markexpr
        self.invocation_params = SimpleNamespace(args=arguments)
        self.option = SimpleNamespace(numprocesses=numprocesses)

    def getoption(self, name: str) -> str:
        assert name == "markexpr"
        return self._markexpr


@pytest.mark.parametrize(
    ("markexpr", "arguments", "marker", "expected"),
    [
        ("", (), "docker", False),
        ("not docker and not internet", (), "docker", False),
        ("not (docker or internet)", (), "docker", False),
        ("docker", (), "docker", True),
        ("docker or internet", (), "internet", True),
        ("", ("tests/docker/test_capture_docker.py",), "docker", True),
        ("", ("tests/internet",), "internet", True),
        ("", ("/checkout/tests/docker/test_capture_docker.py",), "docker", True),
        ("not docker", ("tests/docker",), "docker", False),
        ("not (docker or internet)", ("tests/docker",), "docker", False),
    ],
)
def test_external_request_detection_requires_an_explicit_marker_or_path(
    markexpr: str,
    arguments: tuple[str, ...],
    marker: str,
    expected: bool,
) -> None:
    """Default collection must stay offline while an explicit external selection remains authoritative."""
    config = cast(pytest.Config, _Config(markexpr, *arguments))

    assert external.external_tests_requested(config, marker) is expected


@pytest.mark.parametrize(
    "value", [None, "", "http://example.test/", "https:///missing-host", "https://user@host.test/"]
)
def test_internet_url_rejects_missing_or_non_https_endpoints(value: str | None) -> None:
    """Accepting an implicit, plaintext, malformed, or credential-bearing URL would make the smoke test unsafe."""
    with pytest.raises(pytest.UsageError, match="--internet-url.*HTTPS"):
        external.validate_internet_url(value)


def test_internet_url_accepts_an_explicit_https_endpoint() -> None:
    assert external.validate_internet_url("https://example.test/resource?q=1") == "https://example.test/resource?q=1"


def test_remaining_resource_message_names_every_resource() -> None:
    """A cleanup assertion without names cannot identify resources needing manual recovery."""
    remaining = {
        "containers": ("capture-1", "target-1"),
        "networks": ("project_default",),
        "volumes": ("project_data",),
    }

    assert docker_support.remaining_resource_message("project", remaining) == (
        "Docker project 'project' still has labelled resources: "
        "containers=[capture-1, target-1]; networks=[project_default]; volumes=[project_data]"
    )


def test_external_command_absence_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit Docker selection must fail with installation guidance instead of skipping or leaking OSError."""

    def missing(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(external.subprocess, "run", missing)

    with pytest.raises(pytest.UsageError, match="Docker CLI was not found.*install Docker Engine.*Compose plugin"):
        external.run_external_command(("docker", "info"), purpose="check Docker Engine", timeout=10.0)


@pytest.mark.parametrize("numprocesses", [None, 0])
def test_external_tests_accept_only_a_serial_pytest_session(numprocesses: int | None) -> None:
    external.require_serial_external_tests(cast(pytest.Config, _Config("docker", numprocesses=numprocesses)))


def test_external_tests_reject_xdist_workers() -> None:
    with pytest.raises(pytest.UsageError, match="serially.*-n 0"):
        external.require_serial_external_tests(cast(pytest.Config, _Config("docker", numprocesses=4)))


def test_external_fixture_paths_are_repository_absolute() -> None:
    root = Path(__file__).parents[2]
    assert DOCKER_FIXTURE_ROOT == root / "tests" / "fixtures" / "data" / "docker"
