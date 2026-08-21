from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

import pytest

from tests.docker.support import assert_tracked_projects_clean, capture_log, write_docker_experiment
from tests.support.docker import DockerTestEnvironment, EndpointDockerCompose
from trafficlab.capture.docker.types import CommandResult, ServiceState
from trafficlab.capture.stage import capture_prepared_experiment
from trafficlab.capture.topology import ComposePaths
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.preflight.stage import run_preflight

pytestmark = [pytest.mark.docker, pytest.mark.integration]


class _RecordingDocker(EndpointDockerCompose):
    def __init__(self, original: EndpointDockerCompose) -> None:
        super().__init__(original.tracker)
        self.calls: list[str] = []
        self.capture_stopped_at: float | None = None
        self.target_killed_at: float | None = None

    def service_state(
        self,
        compose_path: Path,
        project_name: str,
        service: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> ServiceState | None:
        state = super().service_state(compose_path, project_name, service, timeout=timeout, deadline=deadline)
        self.calls.append(f"state:{service}:{state.state if state else 'absent'}")
        if service == "capture" and state is not None and state.state != "running" and self.capture_stopped_at is None:
            self.capture_stopped_at = time.monotonic()
        return state

    def start_target(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        self.calls.append("start:target")
        return super().start_target(compose_path, project_name, timeout=timeout, deadline=deadline)

    def kill_target(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        self.calls.append("kill:target")
        self.target_killed_at = time.monotonic()
        return super().kill_target(compose_path, project_name, timeout=timeout, deadline=deadline)

    def signal_capture(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        self.calls.append("signal:capture")
        return super().signal_capture(compose_path, project_name, timeout=timeout, deadline=deadline)

    def kill_capture(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        self.calls.append("kill:capture")
        return super().kill_capture(compose_path, project_name, timeout=timeout, deadline=deadline)


def _replace_capture_entrypoint(monkeypatch: pytest.MonkeyPatch, entrypoint: list[str]) -> None:
    import trafficlab.capture.stage as capture_module

    def mutate(content: bytes, *, target_image: str, capture_image: str) -> bytes:
        document = cast(dict[str, object], json.loads(content))
        services = cast(dict[str, object], document["services"])
        capture = cast(dict[str, object], services["capture"])
        target = cast(dict[str, object], services["target"])
        assert target["image"] == target_image
        assert capture["image"] == capture_image
        capture["entrypoint"] = entrypoint
        return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()

    original_write = capture_module.write_production_compose

    def write(
        path: Path,
        config: ExperimentConfig,
        paths: ComposePaths,
        *,
        target_image: str,
        capture_image: str,
    ) -> None:
        original_write(
            path,
            config,
            paths,
            target_image=target_image,
            capture_image=capture_image,
        )
        path.write_bytes(
            mutate(
                path.read_bytes(),
                target_image=target_image,
                capture_image=capture_image,
            )
        )

    monkeypatch.setattr(capture_module, "write_production_compose", write)


def _assert_failed_run(run_directory: Path, kind: str, *, induced_status: bool) -> None:
    failed = capture_log(run_directory)[-1]
    assert failed["event"] == "capture_failed"
    assert failed["failure_kind"] == kind
    secondaries = cast(list[str], failed["secondary_details"])
    assert (
        any("after Trafficlab requested termination with status" in detail for detail in secondaries) is induced_status
    )


def test_natural_nonzero_status_is_exact_and_only_diagnostic_capture_remains(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "nonzero.toml",
        valid_config_data,
        docker_test_environment,
        argv=["traffic", "--tcp-count", "1", "--udp-count", "1", "--exit-code", "23"],
    )

    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    docker = _RecordingDocker(endpoint_docker)

    with pytest.raises(TrafficlabError, match="target exited naturally with status 23"):
        capture_prepared_experiment(experiment, prepared, docker=docker)

    run_directory = tmp_path / "nonzero-run"
    assert not (run_directory / "reference.pcapng").exists()
    assert (run_directory / "diagnostic-reference.pcapng").exists()
    assert "kill:target" not in docker.calls
    assert docker.calls.count("signal:capture") == 1
    assert_tracked_projects_clean(docker.tracker)


@pytest.mark.parametrize(
    "argv",
    [
        ["hold", "--seconds", "300"],
        ["background", "--seconds", "300", "--parent-seconds", "5"],
    ],
)
def test_workload_timeout_kills_target_and_any_child(
    argv: list[str],
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / f"timeout-{argv[0]}.toml",
        valid_config_data,
        docker_test_environment,
        argv=argv,
        workload_timeout=0.5,
        total_timeout=15.0,
    )

    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    docker = _RecordingDocker(endpoint_docker)

    with pytest.raises(TrafficlabError, match="target workload timed out"):
        capture_prepared_experiment(experiment, prepared, docker=docker)

    _assert_failed_run(tmp_path / f"timeout-{argv[0]}-run", "stage_timeout", induced_status=True)
    assert docker.calls.count("kill:target") == 1
    assert docker.calls.count("signal:capture") == 1
    assert_tracked_projects_clean(docker.tracker)


def test_capture_early_exit_kills_long_target_next_and_within_five_seconds(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "capture-exit.toml",
        valid_config_data,
        docker_test_environment,
        argv=["hold", "--seconds", "300"],
        workload_timeout=30.0,
        total_timeout=45.0,
    )
    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    _replace_capture_entrypoint(
        monkeypatch,
        [
            "/bin/sh",
            "-c",
            "/usr/local/bin/trafficlab-capture & child=$!; sleep 1; kill -TERM $$child; wait $$child || true; exit 7",
        ],
    )
    docker = _RecordingDocker(endpoint_docker)

    with pytest.raises(TrafficlabError, match="capture stopped during target workload"):
        capture_prepared_experiment(experiment, prepared, docker=docker)

    assert docker.capture_stopped_at is not None
    assert docker.target_killed_at is not None
    assert docker.target_killed_at - docker.capture_stopped_at < 5.0
    stopped_index = next(index for index, call in enumerate(docker.calls) if call.startswith("state:capture:exited"))
    assert docker.calls[stopped_index + 1] == "kill:target"
    assert "signal:capture" not in docker.calls
    _assert_failed_run(tmp_path / "capture-exit-run", "capture_stopped", induced_status=True)
    assert_tracked_projects_clean(docker.tracker)


def test_capture_ignoring_sigint_reaches_flush_timeout_and_rejects_output(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "ignore-int.toml",
        valid_config_data,
        docker_test_environment,
        argv=["traffic", "--tcp-count", "1", "--udp-count", "1"],
        flush_timeout=0.5,
        total_timeout=20.0,
    )

    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    _replace_capture_entrypoint(
        monkeypatch,
        ["/bin/sh", "-c", "trap '' INT; exec /usr/local/bin/trafficlab-capture"],
    )
    docker = _RecordingDocker(endpoint_docker)

    with pytest.raises(TrafficlabError, match="capture flush timed out"):
        capture_prepared_experiment(experiment, prepared, docker=docker)

    assert not (tmp_path / "ignore-int-run" / "reference.pcapng").exists()
    assert docker.calls.count("signal:capture") == 1
    assert docker.calls.count("kill:capture") == 1
    assert_tracked_projects_clean(docker.tracker)


def test_malformed_output_is_rejected_after_bounded_flush(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "malformed.toml",
        valid_config_data,
        docker_test_environment,
        argv=["0"],
        target_image=docker_test_environment.no_shell_image,
    )
    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    _replace_capture_entrypoint(
        monkeypatch,
        [
            "/bin/sh",
            "-c",
            'mac=$(cat /sys/class/net/eth0/address); printf \'{"interface":"eth0","target_mac":"%s"}\\n\' '
            '"$$mac" > /trafficlab/capture.json; printf "\\012\\015\\015\\012bad" '
            "> /trafficlab/reference.pcapng.tmp; "
            "trap 'exit 0' INT; while :; do sleep 0.1; done",
        ],
    )

    with pytest.raises(TrafficlabError, match="capture validation failed"):
        capture_prepared_experiment(experiment, prepared, docker=endpoint_docker)

    assert not (tmp_path / "malformed-run" / "reference.pcapng").exists()
    assert_tracked_projects_clean(endpoint_docker.tracker)


def test_readiness_failure_never_starts_target_and_cleans_project(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "readiness.toml",
        valid_config_data,
        docker_test_environment,
        argv=["0"],
        target_image=docker_test_environment.no_shell_image,
        readiness_timeout=0.5,
        total_timeout=15.0,
    )

    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    _replace_capture_entrypoint(monkeypatch, ["/bin/sh", "-c", "sleep 300"])
    docker = _RecordingDocker(endpoint_docker)

    with pytest.raises(TrafficlabError, match="readiness timed out"):
        capture_prepared_experiment(experiment, prepared, docker=docker)

    records = capture_log(tmp_path / "readiness-run")
    assert all(record.get("event") != "capture_ready" for record in records)
    assert "start:target" not in docker.calls
    assert_tracked_projects_clean(docker.tracker)


def test_interruption_kills_target_flushes_once_and_returns_interruption_primary(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
) -> None:
    experiment = write_docker_experiment(
        tmp_path / "interrupt.toml",
        valid_config_data,
        docker_test_environment,
        argv=["hold", "--seconds", "300"],
    )

    prepared = run_preflight(experiment, config_only=False, docker=endpoint_docker)
    docker = _RecordingDocker(endpoint_docker)

    with pytest.raises(TrafficlabError, match="capture interrupted during target workload"):
        capture_prepared_experiment(
            experiment,
            prepared,
            docker=docker,
            interruption=lambda: "start:target" in docker.calls,
        )

    _assert_failed_run(tmp_path / "interrupt-run", "user_interruption", induced_status=True)
    assert docker.calls.count("kill:target") == 1
    assert docker.calls.count("signal:capture") == 1
    assert_tracked_projects_clean(docker.tracker)
