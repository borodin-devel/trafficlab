import json
from pathlib import Path
from typing import cast

import pytest

import trafficlab.capture.lifecycle as lifecycle_module
import trafficlab.capture.lineage as lineage_module
import trafficlab.capture.stage as capture_module
from tests.support.capture import Clock, DockerDouble, prepared_capture
from trafficlab.capture.docker.types import ServiceState
from trafficlab.capture.policy import CaptureOutcome, FailureKind, record_flush_failure
from trafficlab.capture.stage import capture_experiment
from trafficlab.common.config import MountConfig
from trafficlab.common.errors import TrafficlabError


def test_readiness_timeout_reports_capture_logs_and_never_starts_target(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting target or hiding capture logs would make a readiness failure unsafe and opaque."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("readiness_timeout")

    with pytest.raises(TrafficlabError, match="readiness timed out"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert "start_target" not in docker.calls
    assert "logs_capture" in docker.calls
    assert docker.calls[-1:] == ["start_down"]
    assert not (prepared.run_directory / "reference.pcapng").exists()


@pytest.mark.parametrize(
    ("stage_deadline", "total_deadline", "expected"),
    [(105.0, 160.0, FailureKind.STAGE_TIMEOUT), (170.0, 160.0, FailureKind.TOTAL_TIMEOUT)],
)
def test_workload_poll_expiry_is_classified_by_the_deadline_that_bounded_the_command(
    tmp_path: Path, stage_deadline: float, total_deadline: float, expected: FailureKind
) -> None:
    """A Docker poll timeout at the active bound must remain a lifecycle timeout, not validation failure."""
    docker = DockerDouble("normal")
    seen: list[float] = []

    def expired(*args: object, deadline: float, **kwargs: object) -> ServiceState:
        del args, kwargs
        seen.append(deadline)
        raise TrafficlabError("Docker command deadline expired before launch", corrective_action="test")

    docker.service_state = expired  # type: ignore[method-assign]
    outcome, _capture = lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        stage_deadline=stage_deadline,
        total_deadline=total_deadline,
        clock=lambda: min(stage_deadline, total_deadline),
        interruption=lambda: False,
    )

    assert outcome.primary_kind is expected
    assert seen == [min(stage_deadline, total_deadline)]


def test_workload_records_every_simultaneously_visible_event_in_priority_order(tmp_path: Path) -> None:
    """Choosing one primary must not discard lower-priority statuses and deadline evidence."""
    docker = DockerDouble("simultaneous_target_capture")
    docker.target_started = True

    outcome, _capture = lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        stage_deadline=100.0,
        total_deadline=100.0,
        clock=lambda: 100.0,
        interruption=lambda: True,
    )

    assert outcome.primary_kind is FailureKind.USER_INTERRUPTION
    assert [(item.kind, item.status) for item in outcome.secondary_details] == [
        (FailureKind.NATURAL_TARGET_STATUS, 0),
        (FailureKind.CAPTURE_STOPPED, None),
        (FailureKind.STAGE_TIMEOUT, None),
        (FailureKind.TOTAL_TIMEOUT, None),
    ]


def test_readiness_interruption_wins_over_ready_or_stopped_capture_and_never_starts_target(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A visible user interruption must be selected before readiness can launch the target."""
    experiment_path, _prepared_run = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    for scenario, expected_signals in (("normal", 1), ("readiness_capture_exit", 0)):
        docker = DockerDouble(scenario)
        with pytest.raises(TrafficlabError) as caught:
            capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: True)
        assert caught.value.exit_code == 130
        assert "interrupted during readiness" in str(caught.value)
        assert "start_target" not in docker.calls
        assert docker.calls.count("signal_capture") == expected_signals


def test_failed_capture_close_never_validates_or_retains_diagnostics(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natural target failure cannot make unclosed capture output eligible for diagnostic retention."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("target_nonzero")
    publish_calls = 0

    def fail_close(*args: object, **kwargs: object) -> CaptureOutcome:
        return record_flush_failure(cast(CaptureOutcome, args[4]), "injected close failure")

    def unexpected_publish(*args: object, **kwargs: object) -> object:
        nonlocal publish_calls
        publish_calls += 1
        raise AssertionError("unclosed capture must not be validated")

    monkeypatch.setattr(capture_module, "flush_capture", fail_close)
    monkeypatch.setattr(capture_module, "publish_capture_pair", unexpected_publish)

    with pytest.raises(TrafficlabError, match="target exited naturally with status 23"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert publish_calls == 0
    assert not (prepared.run_directory / "diagnostic-reference.pcapng").exists()


def test_keyboard_interrupt_inside_workload_runs_owned_interruption_transition(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real signal-raised KeyboardInterrupt must not bypass kill, flush, diagnostics, or cleanup."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("interruption")
    original_state = docker.service_state
    interrupted = False

    def interrupt_once(compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        nonlocal interrupted
        if service == "target" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return original_state(compose_path, project_name, service, deadline=deadline)

    docker.service_state = interrupt_once  # type: ignore[method-assign]

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert caught.value.exit_code == 130
    assert docker.calls.count("kill_target") == 1
    assert docker.calls.count("signal_capture") == 1
    assert docker.calls[-1:] == ["start_down"]
    assert (prepared.run_directory / "diagnostic-capture.json").exists()
    assert (prepared.run_directory / "diagnostic-reference.pcapng").exists()
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "user_interruption"


def test_keyboard_interrupt_during_partial_target_start_kills_before_capture_action(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partially started target remains owned even when SIGINT interrupts the start command itself."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("partial_start_interrupt")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    started_at = docker.calls.index("start_target")
    assert docker.calls[started_at + 1 : started_at + 5] == [
        "kill_target",
        "state_target",
        "signal_capture",
        "state_capture",
    ]
    assert docker.calls[-1:] == ["start_down"]
    assert caught.value.exit_code == 130
    assert (prepared.run_directory / "diagnostic-capture.json").exists()
    assert (prepared.run_directory / "diagnostic-reference.pcapng").exists()
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "user_interruption"
    assert records[-1]["secondary_failures"] == [
        {
            "detail": "target exited after Trafficlab requested termination with status 137",
            "kind": "induced_target_status",
            "status": 137,
        }
    ]


def test_writable_file_and_directory_mounts_are_not_immutable_inputs(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writable_file = tmp_path / "output.txt"
    writable_file.write_bytes(b"initial")
    writable_directory = tmp_path / "output"
    writable_directory.mkdir()
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mounts = (
        MountConfig(source=writable_file, target="/work/output.txt", read_only=False),
        MountConfig(source=writable_directory, target="/work/output", read_only=False),
    )
    config = prepared.config.model_copy(update={"target": prepared.config.target.model_copy(update={"mounts": mounts})})

    assert lineage_module.identify_mounted_inputs(config) == ()


def test_readiness_and_workload_state_errors_are_classified_at_the_observation_boundary(
    tmp_path: Path,
) -> None:
    """State-query errors must be direct evidence, with simultaneous timeouts retaining both facts."""
    docker = DockerDouble("normal")

    def fail_state(*args: object, **kwargs: object) -> ServiceState:
        del args, kwargs
        raise TrafficlabError("injected state observation failure", corrective_action="test")

    docker.service_state = fail_state  # type: ignore[method-assign]
    ready = lifecycle_module.wait_readiness(
        docker,
        tmp_path / "compose.json",
        "project",
        tmp_path / "capture.json",
        tmp_path / "reference.pcapng",
        {},
        deadline=101.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )
    assert ready.primary_kind is FailureKind.VALIDATION_FAILED

    timed_out = lifecycle_module.wait_readiness(
        docker,
        tmp_path / "compose.json",
        "project",
        tmp_path / "capture.json",
        tmp_path / "reference.pcapng",
        {},
        deadline=100.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )
    assert timed_out.primary_kind is FailureKind.STAGE_TIMEOUT
    assert [item.kind for item in timed_out.secondary_details] == [FailureKind.VALIDATION_FAILED]

    workload, capture = lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "project",
        {},
        stage_deadline=101.0,
        total_deadline=102.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )
    assert workload.primary_kind is FailureKind.VALIDATION_FAILED
    assert capture is None


def test_keyboard_interrupt_before_capture_state_is_known_skips_diagnostic_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a known cleanly closed capture, interruption diagnostics must not be invented."""
    experiment_path, _prepared_run = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("normal")

    def interrupt_state(*args: object, **kwargs: object) -> ServiceState:
        del args, kwargs
        raise KeyboardInterrupt

    def unexpected_publish(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("unexpected diagnostic publication")

    docker.service_state = interrupt_state  # type: ignore[method-assign]
    monkeypatch.setattr(capture_module, "publish_capture_pair", unexpected_publish)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert caught.value.exit_code == 130


def test_readiness_and_workload_poll_again_when_no_event_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Healthy intermediate observations must loop instead of inventing a lifecycle event."""
    docker = DockerDouble("normal")
    readiness_checks = iter((False, True))

    def capture_ready(*args: object) -> bool:
        return next(readiness_checks)

    monkeypatch.setattr(lifecycle_module, "_capture_ready", capture_ready)

    ready = lifecycle_module.wait_readiness(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        tmp_path / "capture.json",
        tmp_path / "reference.pcapng.tmp",
        {},
        deadline=160.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )

    assert ready.primary_kind is None
    assert docker.calls.count("state_capture") == 2

    target_observations = 0
    original_state = docker.service_state

    def running_then_exited(compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        nonlocal target_observations
        if service == "target":
            target_observations += 1
            if target_observations == 1:
                return ServiceState("target", "target", "target", "running", 0)
        return original_state(compose_path, project_name, service, deadline=deadline)

    docker.service_state = running_then_exited  # type: ignore[method-assign]
    observed, _capture = lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        stage_deadline=150.0,
        total_deadline=160.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )

    assert observed.primary_kind is None
    assert target_observations == 2
