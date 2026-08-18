from __future__ import annotations

import ipaddress
import json
import time
from pathlib import Path
from typing import cast

import pytest

from tests.conftest import (
    DockerTestEnvironment,
    EndpointDockerCompose,
    inspect_project_resources,
    merge_endpoint_overlay,
)
from tests.docker.support import (
    capture_lifecycle_positions,
    capture_project_name,
    require_checked_capture_image,
    write_docker_experiment,
)
from trafficlab.capture import capture_experiment, capture_prepared_experiment
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.compose import ComposePaths, render_production_compose
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_experiment
from trafficlab.docker_cli import CommandResult
from trafficlab.preflight import run_preflight
from trafficlab.trace import Direction

pytestmark = [pytest.mark.docker, pytest.mark.integration]


def test_capture_image_matches_checked_content_identity(
    docker_test_environment: DockerTestEnvironment,
) -> None:
    identity = require_checked_capture_image(docker_test_environment.capture_image)

    assert identity.reference == docker_test_environment.capture_image


def _services(document: bytes) -> dict[str, object]:
    parsed = cast(dict[str, object], json.loads(document))
    return cast(dict[str, object], parsed["services"])


class _ReadinessDocker(EndpointDockerCompose):
    def __init__(self, original: EndpointDockerCompose) -> None:
        super().__init__(original.tracker)
        self.target_started_after_ready = False

    def start_target(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        document = cast(dict[str, object], json.loads(compose_path.read_bytes()))
        services = cast(dict[str, object], document["services"])
        capture = cast(dict[str, object], services["capture"])
        volume = cast(dict[str, object], cast(list[object], capture["volumes"])[0])
        output = Path(cast(str, volume["source"]))
        self.target_started_after_ready = (output / "capture.json").exists() and (
            output / "reference.pcapng.tmp"
        ).read_bytes()[:4] == b"\x0a\x0d\x0d\x0a"
        return super().start_target(compose_path, project_name, timeout=timeout, deadline=deadline)


def test_endpoint_overlay_is_test_only_and_production_remains_two_services(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "topology.toml",
        valid_config_data,
        docker_test_environment,
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
    overlay_services = _services(merge_endpoint_overlay(production))
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
        networks = cast(dict[str, object], service.get("networks", {}))
        default_network = cast(dict[str, object], networks.get("default", {}))
        overlay_addresses.append(default_network.get("ipv4_address"))
    assert overlay_addresses == ["172.31.254.2", "172.31.254.3", "172.31.254.4"]
    target = cast(dict[str, object], _services(production)["target"])
    assert target["command"] == ["traffic", "--tcp-count", "2", "--udp-count", "3"]
    assert target["network_mode"] == "service:capture"
    assert target["init"] is True
    assert "entrypoint" not in target


def test_full_preflight_and_capture_observe_controlled_tcp_udp_and_broadcast(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "traffic.toml",
        valid_config_data,
        docker_test_environment,
        argv=["traffic", "--tcp-count", "2", "--udp-count", "3"],
    )

    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    assert all(finding.ok for finding in prepared.report.findings)
    docker = _ReadinessDocker(endpoint_docker)
    result = capture_experiment(experiment, docker=docker)
    inspection = validate_capture_pair(
        result.run_directory / "capture.json",
        result.reference_path,
        deadline=None,
    )

    assert result.target_status == 0
    assert docker.target_started_after_ready
    assert result.packet_count == inspection.packet_count > 0
    assert inspection.protocol_counts["tcp"] >= 4
    assert inspection.protocol_counts["udp"] >= 6
    assert inspection.direction_counts[Direction.OUTBOUND] > 0
    assert inspection.direction_counts[Direction.INBOUND] > 0
    assert inspection.destination_mac_counts["ff:ff:ff:ff:ff:ff"] >= 1
    assert any(
        packet.event.direction is Direction.INBOUND and packet.ethernet_frame[:6] == b"\xff\xff\xff\xff\xff\xff"
        for packet in inspection.packets
    )
    assert inspection.destination_address_counts["172.31.254.2"] >= 5
    assert inspection.source_address_counts["172.31.254.2"] >= 5
    assert any(
        packet.event.direction is Direction.OUTBOUND
        and str(ipaddress.IPv4Address(packet.ethernet_frame[30:34])) == "172.31.254.2"
        for packet in inspection.packets
        if packet.ethernet_frame[12:14] == b"\x08\x00" and len(packet.ethernet_frame) >= 34
    )
    assert any(
        packet.event.direction is Direction.INBOUND
        and str(ipaddress.IPv4Address(packet.ethernet_frame[26:30])) == "172.31.254.2"
        for packet in inspection.packets
        if packet.ethernet_frame[12:14] == b"\x08\x00" and len(packet.ethernet_frame) >= 34
    )
    observed_addresses = {
        str(ipaddress.IPv4Address(packet.ethernet_frame[offset : offset + 4]))
        for packet in inspection.packets
        if packet.ethernet_frame[12:14] == b"\x08\x00" and len(packet.ethernet_frame) >= 34
        for offset in (26, 30)
    }
    assert "172.31.254.3" not in observed_addresses
    assert all(
        ipaddress.ip_address(address) in ipaddress.ip_network("172.31.254.0/24") for address in observed_addresses
    )
    target_addresses = observed_addresses - {"172.31.254.2", "172.31.254.255"}
    assert len(target_addresses) == 1
    assert len(endpoint_docker.tracker.projects) >= 3
    assert all(project.startswith("trafficlab-") for project in endpoint_docker.tracker.projects)
    readiness, published = capture_lifecycle_positions(result.run_directory)
    assert readiness < published


def test_direct_no_shell_target_exits_zero_without_wrapper_or_exec(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "no-shell.toml",
        valid_config_data,
        docker_test_environment,
        argv=["0"],
        target_image=docker_test_environment.no_shell_image,
    )

    result = capture_experiment(experiment, docker=endpoint_docker)

    assert result.target_status == 0
    assert result.reference_path.exists()


def test_background_child_is_closed_when_direct_target_exits(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "background.toml",
        valid_config_data,
        docker_test_environment,
        argv=["background", "--seconds", "300"],
    )

    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    started = time.monotonic()
    result = capture_prepared_experiment(experiment, prepared, docker=endpoint_docker)
    elapsed = time.monotonic() - started
    project_name = capture_project_name(result.run_directory)
    inspection = inspect_project_resources(project_name)

    assert result.target_status == 0
    assert result.reference_path.exists()
    assert elapsed < 20.0
    assert inspection.diagnostics == ()
    assert inspection.resources["containers"] == ()
