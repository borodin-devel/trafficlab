import json
from pathlib import Path
from typing import cast

import pytest

import trafficlab.capture.lifecycle as lifecycle_module
from tests.support.capture import Clock, DockerDouble, prepared_capture
from trafficlab.capture.docker.types import CommandResult, ServiceState
from trafficlab.capture.policy import CaptureOutcome, FailureKind, record_natural_target_status
from trafficlab.capture.stage import capture_experiment
from trafficlab.common.errors import TrafficlabError


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("workload_timeout", "target workload timed out"), ("interruption", "capture interrupted during target workload")],
)
def test_workload_stop_kills_target_records_induced_status_and_flushes_live_capture_once(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected: str,
) -> None:
    """A workload stop without container kill or one bounded flush could leak work or truncate diagnostics."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble(scenario)
    interruption = (lambda: docker.target_started) if scenario == "interruption" else (lambda: False)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=interruption)

    assert str(caught.value).startswith(f"{expected}; secondary: target exited after Trafficlab requested termination")
    assert docker.calls.count("kill_target") == 1
    assert docker.calls.count("signal_capture") == 1
    assert caught.value.exit_code == (130 if scenario == "interruption" else 2)
    assert (prepared.run_directory / "diagnostic-capture.json").exists()
    assert (prepared.run_directory / "diagnostic-reference.pcapng").exists()
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == ["target exited after Trafficlab requested termination with status 137"]
    assert records[-1]["secondary_failures"] == [
        {
            "detail": "target exited after Trafficlab requested termination with status 137",
            "kind": "induced_target_status",
            "status": 137,
        }
    ]


def test_workload_and_flush_docker_calls_use_the_active_stage_deadline(tmp_path: Path) -> None:
    """A hanging Docker poll or signal must not receive budget beyond its active stage."""
    docker = DockerDouble("normal")
    observed_deadlines: list[tuple[str, float]] = []
    original_state = docker.service_state
    original_signal = docker.signal_capture

    def state(compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        observed_deadlines.append((f"state_{service}", deadline))
        return original_state(compose_path, project_name, service, deadline=deadline)

    def signal(compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        observed_deadlines.append(("signal_capture", deadline))
        return original_signal(compose_path, project_name, deadline=deadline)

    docker.service_state = state  # type: ignore[method-assign]
    docker.signal_capture = signal  # type: ignore[method-assign]
    lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        stage_deadline=105.0,
        total_deadline=160.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )
    lifecycle_module.flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=106.0,
        total_deadline=160.0,
        clock=lambda: 100.0,
    )

    assert observed_deadlines[:2] == [("state_target", 105.0), ("state_capture", 105.0)]
    assert observed_deadlines[2:] == [("signal_capture", 106.0), ("state_capture", 106.0)]


def test_workload_poll_expiry_retains_the_last_known_live_capture_for_flush(tmp_path: Path) -> None:
    """Failure before the capture poll must not hide a previously observed live capture from the flush transition."""
    docker = DockerDouble("normal")
    live_capture = ServiceState("capture", "capture", "capture", "running", 0)
    states = {"capture": live_capture}

    def expired(*args: object, deadline: float, **kwargs: object) -> ServiceState:
        del args, kwargs
        assert deadline == 105.0
        raise TrafficlabError("Docker command deadline expired before launch", corrective_action="test")

    docker.service_state = expired  # type: ignore[method-assign]
    outcome, capture = lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        states,
        stage_deadline=105.0,
        total_deadline=160.0,
        clock=lambda: 105.0,
        interruption=lambda: False,
    )

    assert outcome.primary_kind is FailureKind.STAGE_TIMEOUT
    assert capture is live_capture


def test_flush_timeout_signals_once_then_kills_capture_without_validation(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waiting indefinitely or validating an unflushed PCAPNG could hang capture or publish truncated data."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("flush_timeout")

    with pytest.raises(TrafficlabError, match="capture flush timed out"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert docker.calls.count("signal_capture") == 1
    assert docker.calls.count("kill_capture") == 1
    assert docker.calls.index("kill_capture") > docker.calls.index("signal_capture")
    assert not (prepared.run_directory / "reference.pcapng").exists()


def test_total_timeout_and_nonzero_flush_exit_use_their_direct_policy_transitions(tmp_path: Path) -> None:
    """Folding these into stage timeout or success would record the wrong primary failure."""
    docker = DockerDouble("workload_timeout")
    docker.target_started = True
    outcome, _capture = lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        stage_deadline=200.0,
        total_deadline=160.0,
        clock=lambda: 160.0,
        interruption=lambda: False,
    )
    assert outcome.primary_kind is FailureKind.TOTAL_TIMEOUT

    docker = DockerDouble("flush_nonzero")
    docker.capture_signalled = True
    original_state = docker.service_state

    def nonzero_capture(compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        if service == "capture":
            return ServiceState("capture", "capture", "capture", "exited", 9)
        return original_state(compose_path, project_name, service, deadline=deadline)

    docker.service_state = nonzero_capture  # type: ignore[method-assign]
    flushed = lifecycle_module.flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=150.0,
        total_deadline=160.0,
        clock=lambda: 100.0,
    )
    assert flushed.primary_kind is FailureKind.FLUSH_FAILED
    assert flushed.primary_detail == "capture exited with status 9 during flush"


@pytest.mark.parametrize("operation", ["signal", "state", "kill"])
@pytest.mark.parametrize("target_status", [0, 23], ids=["target-zero", "target-nonzero"])
def test_flush_docker_errors_are_contextual_and_preserve_natural_target_precedence(
    tmp_path: Path, operation: str, target_status: int
) -> None:
    """An escaped boundary error would be mislabeled or dropped instead of becoming ordered flush evidence."""
    suffix = "_nonzero" if target_status else ""
    docker = DockerDouble(f"flush_{operation}_error{suffix}")
    outcome = record_natural_target_status(CaptureOutcome(), target_status)

    result = lifecycle_module.flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        outcome,
        stage_deadline=105.0,
        total_deadline=160.0,
        clock=Clock(docker),
    )

    if target_status == 0 and operation != "kill":
        assert result.primary_kind is FailureKind.FLUSH_FAILED
        details = [cast(str, result.primary_detail), *(item.detail for item in result.secondary_details)]
    else:
        expected_primary = FailureKind.TARGET_NONZERO_EXIT if target_status else FailureKind.STAGE_TIMEOUT
        assert result.primary_kind is expected_primary
        assert result.primary_status == (23 if target_status else None)
        expected_secondary = (
            [FailureKind.STAGE_TIMEOUT, FailureKind.FLUSH_FAILED]
            if target_status and operation == "kill"
            else [FailureKind.FLUSH_FAILED]
        )
        assert [item.kind for item in result.secondary_details] == expected_secondary
        details = [item.detail for item in result.secondary_details]
    assert any(operation in detail and "injected" in detail for detail in details)


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("flush_disappeared", "disappeared"),
        ("flush_unknown", "paused"),
        ("flush_stopped", "dead"),
    ],
)
def test_flush_nonrunning_states_fail_immediately(tmp_path: Path, scenario: str, expected: str) -> None:
    """A disappeared or non-running capture must not spin until a timeout."""
    docker = DockerDouble(scenario)
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 100.0

    result = lifecycle_module.flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=105.0,
        total_deadline=160.0,
        clock=clock,
    )

    assert result.primary_kind is FailureKind.FLUSH_FAILED
    assert expected in cast(str, result.primary_detail)
    assert docker.calls == ["signal_capture", "state_capture"]
    assert clock_calls <= 1


def test_flush_timeout_at_total_deadline_records_inability_to_kill_without_expired_docker_call(tmp_path: Path) -> None:
    """Passing an expired deadline to capture kill violates the total-run boundary."""
    docker = DockerDouble("flush_timeout")
    readings = iter((100.0, 160.0))

    result = lifecycle_module.flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=160.0,
        total_deadline=160.0,
        clock=lambda: next(readings),
    )

    assert result.primary_kind is FailureKind.STAGE_TIMEOUT
    assert [item.kind for item in result.secondary_details] == [FailureKind.TOTAL_TIMEOUT]
    assert "could not be killed" in result.secondary_details[0].detail
    assert "kill_capture" not in docker.calls


def test_expired_flush_stage_kills_capture_without_sending_a_late_signal(tmp_path: Path) -> None:
    """A stage already expired before SIGINT still requires bounded rejection of the live capture."""
    docker = DockerDouble("normal")

    result = lifecycle_module.flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=105.0,
        total_deadline=160.0,
        clock=lambda: 105.0,
    )

    assert result.primary_kind is FailureKind.STAGE_TIMEOUT
    assert docker.calls == ["kill_capture"]


@pytest.mark.parametrize("operation", ["signal", "state"])
@pytest.mark.parametrize(
    ("stage_deadline", "total_deadline", "expected"),
    [(105.0, 160.0, FailureKind.STAGE_TIMEOUT), (170.0, 160.0, FailureKind.TOTAL_TIMEOUT)],
)
def test_flush_boundary_expiry_is_classified_by_the_deadline_that_bounded_the_command(
    tmp_path: Path,
    operation: str,
    stage_deadline: float,
    total_deadline: float,
    expected: FailureKind,
) -> None:
    """A hanging flush command must remain a lifecycle timeout instead of a generic flush failure."""
    docker = DockerDouble("normal")

    def expired(*args: object, deadline: float, **kwargs: object) -> CommandResult | ServiceState:
        del args, kwargs
        assert deadline == min(stage_deadline, total_deadline)
        raise TrafficlabError("Docker command deadline expired before launch", corrective_action="test")

    if operation == "signal":
        docker.signal_capture = expired  # type: ignore[method-assign]
    else:
        docker.service_state = expired  # type: ignore[method-assign]
    readings = iter((100.0, min(stage_deadline, total_deadline)))

    result = lifecycle_module.flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=stage_deadline,
        total_deadline=total_deadline,
        clock=lambda: next(readings),
    )

    assert result.primary_kind is expected
    if expected is FailureKind.STAGE_TIMEOUT:
        assert docker.calls[-1] == "kill_capture"
    else:
        assert "kill_capture" not in docker.calls


def test_flush_rejects_expired_deadline_before_signal_and_total_expiry_while_waiting(tmp_path: Path) -> None:
    """Both total-deadline guards must stop Docker work and retain exact timeout context."""
    compose_path = tmp_path / "compose.json"
    before_signal = DockerDouble("normal")

    expired = lifecycle_module.flush_capture(
        before_signal,
        compose_path,
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=170.0,
        total_deadline=160.0,
        clock=lambda: 160.0,
    )

    assert expired.primary_kind is FailureKind.TOTAL_TIMEOUT
    assert before_signal.calls == []

    while_waiting = DockerDouble("flush_timeout")
    readings = iter((100.0, 160.0))
    expired = lifecycle_module.flush_capture(
        while_waiting,
        compose_path,
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=170.0,
        total_deadline=160.0,
        clock=lambda: next(readings),
    )

    assert expired.primary_kind is FailureKind.TOTAL_TIMEOUT
    assert "during flush" in cast(str, expired.primary_detail)
    assert while_waiting.calls == ["signal_capture", "state_capture"]


def test_flush_poll_continues_while_capture_is_running_before_clean_exit(tmp_path: Path) -> None:
    """One healthy flush observation must not be treated as success or failure."""
    docker = DockerDouble("normal")
    capture_observations = 0
    original_state = docker.service_state

    def running_then_exited(compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        nonlocal capture_observations
        if service == "capture":
            capture_observations += 1
            if capture_observations == 1:
                return ServiceState("capture", "capture", "capture", "running", 0)
            return ServiceState("capture", "capture", "capture", "exited", 0)
        return original_state(compose_path, project_name, service, deadline=deadline)

    docker.service_state = running_then_exited  # type: ignore[method-assign]
    outcome = lifecycle_module.flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        CaptureOutcome(),
        stage_deadline=150.0,
        total_deadline=160.0,
        clock=lambda: 100.0,
    )

    assert outcome.primary_kind is None
    assert capture_observations == 2
