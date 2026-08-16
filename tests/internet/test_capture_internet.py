from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import DockerTestEnvironment, TrackedDockerCompose
from tests.docker.support import write_docker_experiment
from trafficlab import USER_AGENT
from trafficlab.capture import capture_experiment
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.trace import Direction

pytestmark = [pytest.mark.internet, pytest.mark.integration]


def test_real_https_capture_proves_dns_tls_bidirectional_traffic_and_teardown(
    internet_url: str,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    internet_docker: TrackedDockerCompose,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "internet.toml",
        valid_config_data,
        docker_test_environment,
        argv=["https", "--user-agent", USER_AGENT, internet_url],
        workload_timeout=60.0,
        total_timeout=120.0,
        probe_url=internet_url,
    )

    result = capture_experiment(experiment, docker=internet_docker)
    inspection = validate_capture_pair(
        result.run_directory / "capture.json",
        result.reference_path,
        deadline=None,
    )

    assert result.target_status == 0
    assert result.packet_count == inspection.packet_count > 0
    assert inspection.protocol_counts["tcp"] > 0
    assert inspection.direction_counts[Direction.OUTBOUND] > 0
    assert inspection.direction_counts[Direction.INBOUND] > 0
