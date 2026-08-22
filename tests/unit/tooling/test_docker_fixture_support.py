from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Protocol, Self, cast

import pytest

from tests.docker import support
from tests.support import docker as docker_support
from trafficlab.artifacts.io import append_run_log
from trafficlab.capture.topology import ComposePaths, render_production_compose
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment

_CAPTURE_IMAGE_ID = "sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c"


def _services(document: bytes) -> dict[str, object]:
    parsed = cast(dict[str, object], json.loads(document))
    return cast(dict[str, object], parsed["services"])


def test_endpoint_overlay_is_test_only_and_production_remains_two_services(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    environment = docker_support.DockerTestEnvironment()
    experiment = support.write_docker_experiment(
        tmp_path / "topology.toml",
        valid_config_data,
        environment,
        argv=["traffic", "--tcp-count", "2", "--udp-count", "3"],
    )
    config: ExperimentConfig = load_experiment(experiment)
    production = render_production_compose(
        config,
        ComposePaths("trafficlab-contract", tmp_path.resolve()),
        target_image=config.target.image,
        capture_image=config.capture.image,
    )

    assert set(_services(production)) == {"capture", "target"}
    endpoint_image = "trafficlab-endpoint:session-owned"
    overlay_services = _services(docker_support.merge_endpoint_overlay(production, endpoint_image=endpoint_image))
    assert set(overlay_services) == {
        "capture",
        "endpoint",
        "noise",
        "orphan",
        "target",
    }
    overlay_addresses: list[object] = []
    for service_name in ("endpoint", "noise", "orphan"):
        service = cast(dict[str, object], overlay_services[service_name])
        assert service["image"] == endpoint_image
        networks = cast(dict[str, object], service.get("networks", {}))
        default_network = cast(dict[str, object], networks.get("default", {}))
        overlay_addresses.append(default_network.get("ipv4_address"))
    assert overlay_addresses == ["172.31.254.2", "172.31.254.3", "172.31.254.4"]
    merged = cast(
        dict[str, object],
        json.loads(docker_support.merge_endpoint_overlay(production, endpoint_image=endpoint_image)),
    )
    assert merged["volumes"] == {"lifecycle": {}}
    endpoint = cast(dict[str, object], overlay_services["endpoint"])
    assert endpoint["volumes"] == [{"type": "volume", "source": "lifecycle", "target": "/trafficlab-test-volume"}]
    assert "volumes" not in cast(dict[str, object], overlay_services["orphan"])
    target = cast(dict[str, object], _services(production)["target"])
    assert target["command"] == ["traffic", "--tcp-count", "2", "--udp-count", "3"]
    assert target["network_mode"] == "service:capture"
    assert target["init"] is True
    assert "entrypoint" not in target


def _capture_inspect(
    reference: str,
    *,
    content_id: str = _CAPTURE_IMAGE_ID,
    operating_system: str = "linux",
    architecture: str = "amd64",
) -> str:
    return json.dumps(
        [
            {
                "Id": content_id,
                "Architecture": architecture,
                "Os": operating_system,
                "RepoDigests": [],
                "RepoTags": [reference],
            }
        ]
    )


def test_capture_fixture_identity_uses_injected_inspect_result() -> None:
    reference = "trafficlab-capture:docker-capture-test"
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        calls.append(argv)
        stdout = json.dumps(
            [
                {
                    "Id": _CAPTURE_IMAGE_ID,
                    "Architecture": "amd64",
                    "Os": "linux",
                    "RepoDigests": [],
                    "RepoTags": [reference],
                }
            ]
        )
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    identity = support.require_checked_capture_image(reference, runner=command)

    assert identity.reference == reference
    assert identity.content_id == _CAPTURE_IMAGE_ID
    assert calls == [("docker", "image", "inspect", reference)]


def test_capture_fixture_identity_rejects_mismatch_without_rewriting_lock() -> None:
    reference = "trafficlab-capture:docker-capture-test"
    lock_path = docker_support.REPOSITORY_ROOT / "docker" / "capture" / "image-lock.json"
    before = lock_path.read_bytes()

    def command(
        argv: tuple[str, ...],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        stdout = json.dumps(
            [
                {
                    "Id": "sha256:" + ("d" * 64),
                    "Architecture": "amd64",
                    "Os": "linux",
                    "RepoDigests": [],
                    "RepoTags": [reference],
                }
            ]
        )
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    with pytest.raises(AssertionError, match="checked lock"):
        support.require_checked_capture_image(reference, runner=command)

    assert lock_path.read_bytes() == before


def test_capture_fixture_build_disables_nondeterministic_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...] | list[str],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(docker_support, "run_external_command", command)

    docker_support.build_test_image(
        "trafficlab-capture:test",
        tmp_path,
        reproducible_capture=True,
    )

    assert calls == [
        (
            "docker",
            "build",
            "--pull",
            "--no-cache",
            "--provenance=false",
            "--platform",
            "linux/amd64",
            "--output",
            "type=image,rewrite-timestamp=true,unpack=false",
            "--tag",
            "trafficlab-capture:test",
            str(tmp_path),
        )
    ]


def test_docker_environment_owns_unique_tags_and_removes_every_built_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...] | list[str],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    class _SelectedDockerConfig:
        option = SimpleNamespace(numprocesses=0)
        invocation_params = SimpleNamespace(args=("tests/docker",))

        def getoption(self, name: str) -> str | None:
            values = {"markexpr": "docker", "capture_image": None}
            return values[name]

    monkeypatch.setattr(docker_support, "run_external_command", command)
    iterator = docker_support.provision_docker_test_environment(cast(pytest.Config, _SelectedDockerConfig()))
    environment = next(iterator)
    iterator.close()

    images = (
        environment.capture_image,
        environment.client_image,
        environment.endpoint_image,
        environment.no_shell_image,
    )
    for image, prefix in zip(
        images,
        (
            docker_support.CAPTURE_IMAGE,
            docker_support.CLIENT_IMAGE,
            docker_support.ENDPOINT_IMAGE,
            docker_support.NO_SHELL_IMAGE,
        ),
        strict=True,
    ):
        assert image.startswith(f"{prefix}-")
        assert image != prefix
    assert len(set(images)) == 4
    assert calls[-4:] == [
        ("docker", "image", "rm", "--force", environment.no_shell_image),
        ("docker", "image", "rm", "--force", environment.endpoint_image),
        ("docker", "image", "rm", "--force", environment.client_image),
        ("docker", "image", "rm", "--force", environment.capture_image),
    ]


@pytest.mark.parametrize("marker", ("docker", "internet"))
def test_docker_environment_reuses_a_checked_prerequisite_image_without_building_or_removing_it(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    reference = "trafficlab-validation-study-1:capture"
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...] | list[str],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        actual = tuple(argv)
        calls.append(actual)
        if actual == ("docker", "image", "inspect", reference):
            return subprocess.CompletedProcess(argv, 0, _capture_inspect(reference), "")
        if actual == ("docker", "run", "--rm", "--entrypoint", "dumpcap", reference, "--version"):
            return subprocess.CompletedProcess(argv, 0, "Dumpcap (Wireshark) 4.0.17\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    class _SelectedDockerConfig:
        option = SimpleNamespace(numprocesses=0)
        invocation_params = SimpleNamespace(args=(f"tests/{marker}",))

        def getoption(self, name: str) -> str:
            values = {"markexpr": marker, "capture_image": reference}
            return values[name]

    monkeypatch.setattr(docker_support, "run_external_command", command)
    iterator = docker_support.provision_docker_test_environment(cast(pytest.Config, _SelectedDockerConfig()))
    environment = next(iterator)
    iterator.close()

    assert environment.capture_image == reference
    assert environment.client_image.startswith(f"{docker_support.CLIENT_IMAGE}-")
    assert ("docker", "image", "inspect", reference) in calls
    assert ("docker", "run", "--rm", "--entrypoint", "dumpcap", reference, "--version") in calls
    assert not any(command[:2] == ("docker", "build") and reference in command for command in calls)
    assert ("docker", "image", "rm", "--force", reference) not in calls
    removed = [command[-1] for command in calls if command[:4] == ("docker", "image", "rm", "--force")]
    expected_removed = [environment.client_image]
    if marker == "docker":
        expected_removed = [environment.no_shell_image, environment.endpoint_image, environment.client_image]
    assert removed == expected_removed


def test_docker_environment_cleans_successful_images_after_a_later_build_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...] | list[str],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        actual = tuple(argv)
        calls.append(actual)
        if actual[:2] == ("docker", "build") and "trafficlab-endpoint:" in actual[-2]:
            raise pytest.UsageError("injected endpoint build failure")
        return subprocess.CompletedProcess(argv, 0, "", "")

    class _SelectedDockerConfig:
        option = SimpleNamespace(numprocesses=0)
        invocation_params = SimpleNamespace(args=("tests/docker",))

        def getoption(self, name: str) -> str | None:
            values = {"markexpr": "docker", "capture_image": None}
            return values[name]

    monkeypatch.setattr(docker_support, "run_external_command", command)

    with pytest.raises(pytest.UsageError, match="endpoint build failure"):
        next(docker_support.provision_docker_test_environment(cast(pytest.Config, _SelectedDockerConfig())))

    removed = [command[-1] for command in calls if command[:4] == ("docker", "image", "rm", "--force")]
    assert len(removed) == 2
    assert removed[0].startswith(f"{docker_support.CLIENT_IMAGE}-")
    assert removed[1].startswith(f"{docker_support.CAPTURE_IMAGE}-")


def test_docker_environment_attempts_all_image_cleanup_and_preserves_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...] | list[str],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        actual = tuple(argv)
        calls.append(actual)
        if actual[:4] == ("docker", "image", "rm", "--force") and (
            "trafficlab-no-shell:" in actual[-1] or "trafficlab-client:" in actual[-1]
        ):
            raise pytest.UsageError(f"injected cleanup failure for {actual[-1]}")
        return subprocess.CompletedProcess(argv, 0, "", "")

    class _SelectedDockerConfig:
        option = SimpleNamespace(numprocesses=0)
        invocation_params = SimpleNamespace(args=("tests/docker",))

        def getoption(self, name: str) -> str | None:
            values = {"markexpr": "docker", "capture_image": None}
            return values[name]

    monkeypatch.setattr(docker_support, "run_external_command", command)
    iterator = docker_support.provision_docker_test_environment(cast(pytest.Config, _SelectedDockerConfig()))
    environment = next(iterator)

    with pytest.raises(pytest.UsageError, match="no-shell") as caught:
        iterator.close()

    removed = [command[-1] for command in calls if command[:4] == ("docker", "image", "rm", "--force")]
    assert removed == [
        environment.no_shell_image,
        environment.endpoint_image,
        environment.client_image,
        environment.capture_image,
    ]
    assert any("client" in note for note in getattr(caught.value, "__notes__", ()))


def test_docker_environment_rejects_an_empty_borrowed_capture_image_before_image_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...] | list[str],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    class _SelectedDockerConfig:
        option = SimpleNamespace(numprocesses=0)
        invocation_params = SimpleNamespace(args=("tests/docker",))

        def getoption(self, name: str) -> str:
            values = {"markexpr": "docker", "capture_image": ""}
            return values[name]

    monkeypatch.setattr(docker_support, "run_external_command", command)

    with pytest.raises(pytest.UsageError, match="nonempty"):
        next(docker_support.provision_docker_test_environment(cast(pytest.Config, _SelectedDockerConfig())))

    assert calls == [
        ("docker", "info"),
        ("docker", "compose", "version", "--format", "json"),
    ]


@pytest.mark.parametrize(
    ("content_id", "operating_system", "architecture", "tool_output", "diagnostic"),
    (
        ("sha256:" + ("d" * 64), "linux", "amd64", "Dumpcap (Wireshark) 4.0.17\n", "content ID"),
        (_CAPTURE_IMAGE_ID, "linux", "arm64", "Dumpcap (Wireshark) 4.0.17\n", "platform"),
        (_CAPTURE_IMAGE_ID, "linux", "amd64", "Dumpcap (Wireshark) 9.9.9\n", "capture tool"),
    ),
)
def test_docker_environment_rejects_an_unchecked_borrowed_capture_image_before_other_builds(
    monkeypatch: pytest.MonkeyPatch,
    content_id: str,
    operating_system: str,
    architecture: str,
    tool_output: str,
    diagnostic: str,
) -> None:
    reference = "trafficlab-validation-study-1:capture"
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...] | list[str],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        actual = tuple(argv)
        calls.append(actual)
        if actual == ("docker", "image", "inspect", reference):
            return subprocess.CompletedProcess(
                argv,
                0,
                _capture_inspect(
                    reference,
                    content_id=content_id,
                    operating_system=operating_system,
                    architecture=architecture,
                ),
                "",
            )
        if actual == ("docker", "run", "--rm", "--entrypoint", "dumpcap", reference, "--version"):
            return subprocess.CompletedProcess(argv, 0, tool_output, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    class _SelectedDockerConfig:
        option = SimpleNamespace(numprocesses=0)
        invocation_params = SimpleNamespace(args=("tests/docker",))

        def getoption(self, name: str) -> str:
            values = {"markexpr": "docker", "capture_image": reference}
            return values[name]

    monkeypatch.setattr(docker_support, "run_external_command", command)

    with pytest.raises(pytest.UsageError, match=diagnostic):
        next(docker_support.provision_docker_test_environment(cast(pytest.Config, _SelectedDockerConfig())))

    assert not any(command[:2] == ("docker", "build") for command in calls)
    assert ("docker", "image", "rm", "--force", reference) not in calls


@pytest.mark.parametrize(
    "diagnostic",
    [
        "snapshot.debian.org returned 404 Not Found",
        "Version '7.88.1-10+deb12u15' for 'curl' was not found",
    ],
)
def test_unavailable_locked_capture_input_fails_without_refreshing_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagnostic: str,
) -> None:
    lock_path = docker_support.REPOSITORY_ROOT / "docker" / "capture" / "image-lock.json"
    before = lock_path.read_bytes()

    def command(
        argv: tuple[str, ...] | list[str],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del argv, purpose, timeout, check
        raise pytest.UsageError(diagnostic)

    monkeypatch.setattr(docker_support, "run_external_command", command)

    with pytest.raises(pytest.UsageError) as caught:
        docker_support.build_test_image(
            "trafficlab-capture:test",
            tmp_path,
            reproducible_capture=True,
        )

    assert diagnostic in str(caught.value)
    assert lock_path.read_bytes() == before


def _load_client() -> ModuleType:
    path = docker_support.DOCKER_FIXTURE_ROOT / "images" / "client" / "client.py"
    spec = importlib.util.spec_from_file_location("trafficlab_test_client", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ClientTraffic(Protocol):
    def __call__(
        self,
        host: str,
        tcp_count: int,
        udp_count: int,
        inter_request_seconds: float = 0.0,
    ) -> None: ...


def _client_traffic(client: ModuleType) -> _ClientTraffic:
    return cast(_ClientTraffic, client._traffic)


class _TcpConnection:
    payload = b""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def sendall(self, payload: bytes) -> None:
        self.payload = payload

    def recv(self, maximum: int) -> bytes:
        assert maximum == 4096
        return b"ACK:" + self.payload


class _UdpSocket:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = iter(responses)
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        assert timeout == 5.0

    def sendto(self, payload: bytes, address: tuple[str, int]) -> None:
        self.sent.append((payload, address))

    def recv(self, maximum: int) -> bytes:
        assert maximum == 4096
        try:
            return next(self.responses)
        except StopIteration as error:
            raise TimeoutError from error


def _stub_client_network(
    client: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    udp_count: int,
) -> None:
    client_socket = cast(ModuleType, client.socket)
    responses = [
        *(f"ACK:trafficlab-udp-{index}".encode() for index in range(udp_count)),
        b"TRAFFICLAB-INBOUND-BROADCAST",
    ]
    udp = _UdpSocket(responses)

    def connection_factory(address: tuple[str, int], *, timeout: float) -> _TcpConnection:
        assert address == ("172.31.254.2", 18080)
        assert timeout == 5.0
        return _TcpConnection()

    def socket_factory(*args: object, **kwargs: object) -> _UdpSocket:
        del args, kwargs
        return udp

    monkeypatch.setattr(client_socket, "create_connection", connection_factory)
    monkeypatch.setattr(client_socket, "socket", socket_factory)


def test_client_default_traffic_performs_no_scheduling_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default capture fixtures must retain their original unslept request schedule."""
    client = _load_client()
    _stub_client_network(client, monkeypatch, udp_count=3)
    sleeps: list[float] = []
    monkeypatch.setattr(cast(ModuleType, client.time), "sleep", sleeps.append)

    _client_traffic(client)("172.31.254.2", 2, 3)

    assert sleeps == []


def test_client_positive_delay_spaces_every_request_after_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The complete-run fixture spaces one later TCP and all three UDP requests by exactly 2 ms."""
    client = _load_client()
    _stub_client_network(client, monkeypatch, udp_count=3)
    sleeps: list[float] = []
    monkeypatch.setattr(cast(ModuleType, client.time), "sleep", sleeps.append)

    _client_traffic(client)("172.31.254.2", 2, 3, 0.002)

    assert sleeps == [0.002, 0.002, 0.002, 0.002]


def test_udp_client_accepts_broadcast_between_expected_acknowledgements(monkeypatch: pytest.MonkeyPatch) -> None:
    """A queued broadcast must not be mistaken for the next request's acknowledgement."""
    client = _load_client()
    udp = _UdpSocket(
        [
            b"ACK:trafficlab-udp-0",
            b"TRAFFICLAB-INBOUND-BROADCAST",
            b"ACK:trafficlab-udp-1",
            b"ACK:trafficlab-udp-2",
        ]
    )
    client_socket = cast(ModuleType, client.socket)

    def socket_factory(*args: object, **kwargs: object) -> _UdpSocket:
        del args, kwargs
        return udp

    monkeypatch.setattr(client_socket, "socket", socket_factory)

    _client_traffic(client)("172.31.254.2", 0, 3)

    assert udp.sent == [
        (b"trafficlab-udp-0", ("172.31.254.2", 18081)),
        (b"trafficlab-udp-1", ("172.31.254.2", 18081)),
        (b"trafficlab-udp-2", ("172.31.254.2", 18081)),
    ]


def test_udp_client_requires_the_inbound_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _load_client()
    udp = _UdpSocket([b"ACK:trafficlab-udp-0"])
    client_socket = cast(ModuleType, client.socket)

    def socket_factory(*args: object, **kwargs: object) -> _UdpSocket:
        del args, kwargs
        return udp

    monkeypatch.setattr(client_socket, "socket", socket_factory)

    with pytest.raises(RuntimeError, match="broadcast"):
        _client_traffic(client)("172.31.254.2", 0, 1)


def test_capture_lifecycle_positions_use_published_run_log_schema(tmp_path: Path) -> None:
    """Looking for an invented completion event would make every successful Docker capture test fail."""
    append_run_log(tmp_path, {"event": "capture_ready", "stage": "capture"})
    append_run_log(tmp_path, {"event": "capture_published", "stage": "capture"})

    assert support.capture_lifecycle_positions(tmp_path) == (0, 1)


def test_tracker_registers_every_exact_project_with_the_session_sweep() -> None:
    """A project visible only to one test could escape the final owned-label recovery sweep."""
    registry = docker_support.DockerProjectRegistry()
    tracker = docker_support.DockerProjectTracker(registry=registry)

    tracker.add("trafficlab-capture-abc123")

    assert tracker.projects == {"trafficlab-capture-abc123"}
    assert registry.projects == {"trafficlab-capture-abc123"}


def test_per_test_assertion_reports_label_leaks_without_running_a_second_cleanup_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-test proof must observe real labels but leave recovery to the one session sweep."""
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        calls.append(argv)
        stdout = "project_default\n" if argv[1:3] == ("network", "ls") else ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(docker_support, "run_external_command", command)
    tracker = docker_support.DockerProjectTracker(projects={"project"})

    with pytest.raises(pytest.fail.Exception, match=r"networks=\[project_default\]"):
        tracker.assert_clean()

    assert not any("rm" in argv for argv in calls)


def test_session_sweep_aggregates_inventory_and_removal_errors_while_continuing_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One inventory/removal failure must not prevent other known resources from being removed."""
    calls: list[tuple[str, ...]] = []

    def command(
        argv: tuple[str, ...],
        *,
        purpose: str,
        timeout: float,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del purpose, timeout, check
        calls.append(argv)
        if argv[1:3] == ("ps", "--all"):
            raise pytest.UsageError("container inventory failed")
        if argv[1:3] == ("network", "ls"):
            return subprocess.CompletedProcess(argv, 0, "project_default\n", "")
        if argv[1:3] == ("volume", "ls"):
            return subprocess.CompletedProcess(argv, 0, "project_data\n", "")
        if argv[1:3] == ("network", "rm"):
            raise pytest.UsageError("network removal failed")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(docker_support, "run_external_command", command)
    registry = docker_support.DockerProjectRegistry(projects={"project"})

    with pytest.raises(pytest.fail.Exception) as caught:
        registry.sweep()

    message = str(caught.value)
    assert "container inventory failed" in message
    assert "networks=[project_default]" in message
    assert "volumes=[project_data]" in message
    assert "network removal failed" in message
    assert ("docker", "network", "rm", "project_default") in calls
    assert ("docker", "volume", "rm", "--force", "project_data") in calls


def test_tracker_preserves_body_and_cleanup_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A teardown failure must augment rather than replace the test's original exception."""
    tracker = docker_support.DockerProjectTracker()

    def cleanup_failure() -> None:
        pytest.fail("cleanup failed", pytrace=False)

    def fail_cleanup(tracker: docker_support.DockerProjectTracker) -> None:
        del tracker
        cleanup_failure()

    monkeypatch.setattr(docker_support.DockerProjectTracker, "assert_clean", fail_cleanup)

    with pytest.raises(BaseExceptionGroup) as caught:
        tracker.finish(RuntimeError("body failed"))

    messages = [str(error) for error in caught.value.exceptions]
    assert messages == ["body failed", "cleanup failed"]
