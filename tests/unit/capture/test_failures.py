import json
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.artifacts.capture as artifact_module
import trafficlab.capture.failures as failure_module
import trafficlab.capture.lifecycle as lifecycle_module
import trafficlab.capture.stage as capture_module
from tests.support.capture import Clock, DockerDouble, prepared_capture
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.capture.docker.types import ServiceState
from trafficlab.capture.policy import CaptureFailureOrigin, CaptureOutcome, FailureKind
from trafficlab.capture.stage import capture_experiment
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata


def test_readiness_log_failure_is_ordered_secondary_evidence(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later log-read failure must not disappear after readiness already selected the primary failure."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("readiness_timeout_logs_error")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value) == (
        "capture readiness timed out; secondary: could not read capture logs after readiness failure: "
        "injected capture logs failure"
    )
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == [
        "could not read capture logs after readiness failure: injected capture logs failure"
    ]
    assert docker.calls[-1:] == ["start_down"]


def test_readiness_log_jsonl_failure_is_ordered_secondary_evidence(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure to persist retrieved logs must remain visible after the readiness primary."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("readiness_timeout")
    real_append = failure_module.append_event

    def fail_capture_log_record(run_directory: Path, event: str, **detail: object) -> None:
        if event == "capture_logs":
            raise TrafficlabError("injected run-log failure", corrective_action="test")
        real_append(run_directory, event, **detail)

    monkeypatch.setattr(failure_module, "append_event", fail_capture_log_record)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert "could not record capture logs after readiness failure: injected run-log failure" in str(caught.value)
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == [
        "could not record capture logs after readiness failure: injected run-log failure"
    ]


def test_capture_failure_log_append_preserves_the_existing_primary_outcome(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_path, _prepared_result = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("target_nonzero")
    original_append = capture_module.append_event

    def fail_capture_failure_record(*args: object, **kwargs: object) -> None:
        if args[1] == "capture_failed":
            raise TrafficlabError("injected final run-log failure", corrective_action="repair run log")
        original_append(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(capture_module, "append_event", fail_capture_failure_record)

    with pytest.raises(TrafficlabError, match="target exited naturally with status 23") as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert caught.value.exit_code == 23
    assert "could not append capture failure to run.log: injected final run-log failure" in str(caught.value)
    outcomes = caught.value.failure_outcomes
    assert len(outcomes) == 2
    primary = outcomes[0]
    secondary = outcomes[1]
    assert primary.kind == "target_failed"
    assert primary.authority == "primary"
    assert primary.evidence_state == "diagnostic_only"
    assert secondary.kind == "publication_failed"
    assert secondary.affected_evidence == "run.log"
    assert secondary.authority == "secondary"


def test_natural_nonzero_status_is_exact_primary_and_output_is_diagnostic_only(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing the target status or publishing it as reusable would misclassify a failed workload."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("target_nonzero")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value) == "target exited naturally with status 23"
    assert caught.value.exit_code == 23
    assert "kill_target" not in docker.calls
    assert docker.calls.count("signal_capture") == 1
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert (prepared.run_directory / "diagnostic-reference.pcapng").exists()
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["primary_status"] == 23
    assert records[-1]["secondary_failures"] == []


def test_simultaneous_target_zero_and_capture_stop_rejects_output_without_signal(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natural target priority must not turn an already-stopped capture into a reusable reference."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("simultaneous_target_capture")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value) == (
        "capture stopped during target workload; secondary: target was also observed naturally exited with status 0"
    )
    assert "signal_capture" not in docker.calls
    assert "kill_capture" not in docker.calls
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert caught.value.failure_outcome is not None
    assert (
        caught.value.failure_outcome.kind,
        caught.value.failure_outcome.evidence_state,
        caught.value.failure_outcome.corrective_action,
        caught.value.failure_outcome.status,
    ) == (
        "capture_failed",
        "not_published",
        "inspect capture status without SIGINT or flush wait",
        7,
    )
    assert len(caught.value.failure_outcomes) == 1
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_failures"] == [
        {
            "detail": "target was also observed naturally exited with status 0",
            "kind": "natural_target_status",
            "status": 0,
        }
    ]


def test_capture_early_exit_kills_target_as_immediate_next_command(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any Docker action before target kill could leave an unobserved workload running after capture loss."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("capture_exit")

    with pytest.raises(TrafficlabError, match="capture stopped during target workload"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    stopped_at = docker.calls.index("state_capture", docker.calls.index("start_target"))
    assert docker.calls[stopped_at + 1] == "kill_target"
    assert docker.calls[stopped_at + 3] == "logs_capture"
    assert "signal_capture" not in docker.calls
    assert not (prepared.run_directory / "reference.pcapng").exists()


@pytest.mark.parametrize(
    ("scenario", "secondary_fragment"),
    [
        ("capture_exit_kill_error", "could not kill target after capture stopped"),
        ("capture_exit_state_error", "could not inspect target after requested kill"),
        ("capture_exit_logs_error", "could not read capture logs after capture stopped"),
    ],
)
def test_capture_stop_preserves_each_later_boundary_failure_and_continues_diagnostics(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    secondary_fragment: str,
) -> None:
    """Kill, state, and log failures are secondary and must not abort the remaining bounded diagnostics."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble(scenario)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    stopped_at = docker.calls.index("state_capture", docker.calls.index("start_target"))
    assert docker.calls[stopped_at + 1 : stopped_at + 4] == ["kill_target", "state_target", "logs_capture"]
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    secondaries = cast(list[str], records[-1]["secondary_details"])
    assert any(secondary_fragment in detail for detail in secondaries)
    assert all(detail in str(caught.value) for detail in secondaries)


def test_capture_stop_with_missing_target_after_kill_continues_to_logs_and_cleanup(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disappeared killed target has no invented status and does not abort later diagnostics."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("capture_exit_target_missing")

    with pytest.raises(TrafficlabError, match="capture stopped during target workload"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert "logs_capture" in docker.calls
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == []


def test_start_target_boundary_error_is_contextual_and_cleanup_still_runs(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broad lifecycle boundary must translate an ordinary Docker error and retain unconditional cleanup."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("start_target_error")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value) == "could not start target service: injected target start failure"
    assert "kill_target" not in docker.calls
    assert docker.calls[-1:] == ["start_down"]
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "validation_failed"


def test_outer_post_event_failure_remains_secondary_to_natural_target_status(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected post-event boundary failure must be contextual evidence, never a replacement primary."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("target_nonzero")

    def fail_flush(*args: object, **kwargs: object) -> CaptureOutcome:
        raise OSError("injected post-event flush boundary failure")

    monkeypatch.setattr(capture_module, "flush_capture", fail_flush)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith("target exited naturally with status 23; secondary: could not flush capture")
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "target_nonzero_exit"
    assert "post-event flush boundary failure" in records[-1]["secondary_details"][0]


def test_natural_nonzero_remains_primary_when_validation_also_fails(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact target status stays primary while ordered validation evidence remains visible."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("target_nonzero_malformed")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith("target exited naturally with status 23; secondary: capture validation failed")
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    failed = records[-1]
    assert failed["failure_kind"] == "target_nonzero_exit"
    assert len(cast(list[object], failed["secondary_details"])) == 1
    assert "capture validation failed" in cast(list[str], failed["secondary_details"])[0]


def test_target_zero_with_stage_and_total_expiry_is_retained_once_after_stage_primary(tmp_path: Path) -> None:
    """Successful natural status must remain typed evidence when a simultaneous timeout fails the capture."""
    docker = DockerDouble("normal")
    docker.target_started = True

    outcome, _capture = lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        stage_deadline=100.0,
        total_deadline=100.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )

    assert outcome.primary_kind is FailureKind.STAGE_TIMEOUT
    assert [(item.kind, item.status) for item in outcome.secondary_details] == [
        (FailureKind.NATURAL_TARGET_STATUS, 0),
        (FailureKind.TOTAL_TIMEOUT, None),
    ]


def test_target_zero_with_total_only_expiry_is_retained_once_after_total_primary(tmp_path: Path) -> None:
    """Deferring status zero must also work when total expiry is the only lower-priority failure."""
    docker = DockerDouble("normal")
    docker.target_started = True

    outcome, _capture = lifecycle_module.observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        stage_deadline=110.0,
        total_deadline=100.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )

    assert outcome.primary_kind is FailureKind.TOTAL_TIMEOUT
    assert [(item.kind, item.status) for item in outcome.secondary_details] == [
        (FailureKind.NATURAL_TARGET_STATUS, 0),
    ]


@pytest.mark.parametrize(
    ("scenario", "expected_kinds"),
    [
        ("capture_exit_kill_error", [FailureKind.VALIDATION_FAILED, FailureKind.INDUCED_TARGET_STATUS]),
        ("capture_exit_state_error", [FailureKind.VALIDATION_FAILED]),
        ("capture_exit_target_missing", []),
    ],
)
def test_interruption_transition_preserves_kill_and_status_diagnostic_failures(
    tmp_path: Path, scenario: str, expected_kinds: list[FailureKind]
) -> None:
    """Owned partial-start cleanup must retain bounded kill/status failures without skipping capture flush."""
    docker = DockerDouble(scenario)
    states = {"capture": ServiceState("capture", "capture", "capture", "running", 0)}

    outcome = lifecycle_module.interrupt_lifecycle(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        states,
        CaptureOutcome(),
        target_may_exist=True,
        total_deadline=160.0,
        flush_timeout_seconds=5.0,
        clock=lambda: 100.0,
    )

    assert outcome.primary_kind is FailureKind.USER_INTERRUPTION
    assert [detail.kind for detail in outcome.secondary_details] == expected_kinds
    assert docker.calls[:4] == ["kill_target", "state_target", "signal_capture", "state_capture"]


def test_temporary_directory_creation_and_cleanup_errors_are_translated(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filesystem failures at either TemporaryDirectory boundary must not escape as raw OSError."""
    experiment_path, _prepared_run = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    real_temporary_directory = tempfile.TemporaryDirectory

    def fail_creation(*args: object, **kwargs: object) -> object:
        raise OSError("injected temporary-directory creation failure")

    monkeypatch.setattr(lifecycle_module.tempfile, "TemporaryDirectory", fail_creation)
    with pytest.raises(TrafficlabError, match="temporary capture directory.*creation failure"):
        capture_experiment(
            experiment_path, docker=DockerDouble("normal"), clock=lambda: 100.0, interruption=lambda: False
        )

    class CleanupFailure:
        def __init__(self, *, prefix: str, dir: Path) -> None:
            self.temporary = real_temporary_directory(prefix=prefix, dir=dir)

        def __enter__(self) -> str:
            return self.temporary.__enter__()

        def __exit__(self, *args: object) -> None:
            self.temporary.cleanup()
            raise OSError("injected temporary-directory cleanup failure")

    monkeypatch.setattr(lifecycle_module.tempfile, "TemporaryDirectory", CleanupFailure)
    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(
            experiment_path,
            docker=DockerDouble("target_nonzero"),
            clock=lambda: 100.0,
            interruption=lambda: False,
        )
    assert str(caught.value).startswith("target exited naturally with status 23; secondary:")
    assert "temporary capture directory" in str(caught.value)

    with pytest.raises(TrafficlabError, match="temporary capture directory"):
        capture_experiment(
            experiment_path,
            docker=DockerDouble("normal"),
            clock=lambda: 100.0,
            interruption=lambda: False,
        )
    assert not (_prepared_run.run_directory / "capture.json").exists()
    assert not (_prepared_run.run_directory / "reference.pcapng").exists()


def test_malformed_capture_is_validation_primary_and_never_published(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating a readiness prefix as complete validation could publish corrupt experiment input."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("malformed")

    with pytest.raises(TrafficlabError, match="capture validation failed"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert docker.calls[-1:] == ["start_down"]


def test_publication_total_deadline_is_primary_and_zero_budget_cleanup_makes_no_docker_call(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publication or cleanup work after total expiry would violate the single end-to-end deadline."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("validation_deadline")

    with pytest.raises(TrafficlabError, match="capture publication copy exceeded its absolute deadline"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert "start_down" not in docker.calls
    assert "inventory" not in docker.calls
    assert not (prepared.run_directory / "reference.pcapng").exists()


def test_cleanup_failure_is_primary_after_success_and_withdraws_reusable_pair(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving a reusable pair after reporting capture failure could feed an unclean run into analysis."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("cleanup_failure")

    with pytest.raises(TrafficlabError, match="cleanup command failed with status 5"):
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()


def test_post_publication_temp_cleanup_warning_fails_capture_and_rolls_back_owned_pair(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A private-temp cleanup failure must be visible without stranding the newly reusable pair."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("normal")
    real_unlink = artifact_module._unlink_capture_temporary  # pyright: ignore[reportPrivateUsage]
    attempts: list[Path] = []

    def fail_metadata_temp(path: Path | None) -> str | None:
        if path is not None and path.name.startswith(".capture-pair.metadata."):
            attempts.append(path)
            return f"could not remove owned temporary file {path}: injected post-publication cleanup failure"
        return real_unlink(path)

    monkeypatch.setattr(artifact_module, "_unlink_capture_temporary", fail_metadata_temp)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert "capture publication cleanup warning" in str(caught.value)
    assert "injected post-publication cleanup failure" in str(caught.value)
    assert len(attempts) == 1
    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "validation_failed"


def test_post_publication_warning_rollback_preserves_unowned_replacement(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement installed before warning rollback must survive byte-for-byte."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("normal")
    real_unlink = artifact_module._unlink_capture_temporary  # pyright: ignore[reportPrivateUsage]
    real_rename = artifact_module.os.rename
    winner_metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:04")
    winner_metadata_bytes = render_capture_metadata(winner_metadata)
    winner_pcapng_bytes = encode_pcapng((TraceEvent(6.0, Direction.OUTBOUND, 96),), winner_metadata)
    winner_directory = tmp_path / "warning-winner"
    winner_directory.mkdir()
    winner_metadata_path = winner_directory / "capture.json"
    winner_pcapng_path = winner_directory / "reference.pcapng"
    winner_metadata_path.write_bytes(winner_metadata_bytes)
    winner_pcapng_path.write_bytes(winner_pcapng_bytes)
    swapped = False

    def fail_metadata_temp(path: Path | None) -> str | None:
        if path is not None and path.name.startswith(".capture-pair.metadata."):
            return f"could not remove owned temporary file {path}: injected post-publication cleanup failure"
        return real_unlink(path)

    def swap_before_rollback(source: str | Path, destination: str | Path) -> None:
        nonlocal swapped
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not swapped
            and source_path == prepared.run_directory / "capture.json"
            and destination_path.parent.name.startswith(".capture-recovery.")
        ):
            swapped = True
            artifact_module.os.replace(winner_metadata_path, source_path)
            artifact_module.os.replace(winner_pcapng_path, prepared.run_directory / "reference.pcapng")
        real_rename(source, destination)

    monkeypatch.setattr(artifact_module, "_unlink_capture_temporary", fail_metadata_temp)
    monkeypatch.setattr(artifact_module.os, "rename", swap_before_rollback)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert swapped
    assert "capture publication cleanup warning" in str(caught.value)
    assert "could not roll back owned capture publication" in str(caught.value)
    assert (prepared.run_directory / "capture.json").read_bytes() == winner_metadata_bytes
    assert (prepared.run_directory / "reference.pcapng").read_bytes() == winner_pcapng_bytes


def test_nonzero_target_keeps_diagnostics_and_records_temp_cleanup_warning_as_secondary(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnostic ownership stays false and its private-temp warning cannot replace the exact target status."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("target_nonzero")
    real_unlink = artifact_module._unlink_capture_temporary  # pyright: ignore[reportPrivateUsage]

    def fail_metadata_temp(path: Path | None) -> str | None:
        if path is not None and path.name.startswith(".capture-pair.metadata."):
            return f"could not remove owned temporary file {path}: injected diagnostic cleanup failure"
        return real_unlink(path)

    monkeypatch.setattr(artifact_module, "_unlink_capture_temporary", fail_metadata_temp)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith("target exited naturally with status 23; secondary: ")
    assert "injected diagnostic cleanup failure" in str(caught.value)
    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert (prepared.run_directory / "diagnostic-capture.json").exists()
    assert (prepared.run_directory / "diagnostic-reference.pcapng").exists()


def test_cleanup_rollback_preserves_a_concurrent_replacement_pair(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership identity must stop rollback from deleting a replacement installed at the canonical names."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    winner_metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:03")
    winner_metadata_bytes = render_capture_metadata(winner_metadata)
    winner_pcapng_bytes = encode_pcapng((TraceEvent(5.0, Direction.OUTBOUND, 80),), winner_metadata)
    winner_directory = tmp_path / "winner"
    winner_directory.mkdir()
    winner_metadata_path = winner_directory / "capture.json"
    winner_pcapng_path = winner_directory / "reference.pcapng"
    winner_metadata_path.write_bytes(winner_metadata_bytes)
    winner_pcapng_path.write_bytes(winner_pcapng_bytes)
    real_rename = artifact_module.os.rename
    swapped = False

    def swap_before_rollback(source: str | Path, destination: str | Path) -> None:
        nonlocal swapped
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not swapped
            and source_path == prepared.run_directory / "capture.json"
            and destination_path.parent.name.startswith(".capture-recovery.")
        ):
            swapped = True
            artifact_module.os.replace(winner_metadata_path, source_path)
            artifact_module.os.replace(winner_pcapng_path, prepared.run_directory / "reference.pcapng")
        real_rename(source, destination)

    monkeypatch.setattr(artifact_module.os, "rename", swap_before_rollback)
    docker = DockerDouble("cleanup_failure")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert swapped
    assert (prepared.run_directory / "capture.json").read_bytes() == winner_metadata_bytes
    assert (prepared.run_directory / "reference.pcapng").read_bytes() == winner_pcapng_bytes
    assert "capture pair changed during invalid-pair recovery" in str(caught.value)


def test_cleanup_failure_is_secondary_after_capture_failure(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup failure must never replace the earlier event that terminated the workload."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("capture_exit_cleanup_failure")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith(
        "capture stopped during target workload; secondary: "
        "target exited after Trafficlab requested termination with status 137"
    )
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == [
        "target exited after Trafficlab requested termination with status 137",
        "cleanup command failed with status 5: cleanup failed",
    ]


def test_cleanup_clock_failure_is_secondary_after_capture_failure(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed post-launch cleanup clock must not escape finally and replace capture's primary failure."""
    experiment_path, prepared = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble("capture_exit_cleanup_clock_error")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith("capture stopped during target workload; secondary:")
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "capture_stopped"
    assert records[-1]["secondary_details"][-1].startswith("cleanup clock failed after launch:")


def test_capture_failure_translation_requires_an_arbitrated_primary() -> None:
    with pytest.raises(ValueError, match="existing primary failure"):
        failure_module.capture_failure_outcomes(CaptureOutcome())


@pytest.mark.parametrize(
    ("later_kinds", "evidence_state", "corrective_action"),
    [
        (
            (FailureKind.CAPTURE_STOPPED, FailureKind.TOTAL_TIMEOUT),
            "not_published",
            "inspect target first, then capture and budget",
        ),
        (
            (FailureKind.CLEANUP_FAILED,),
            "diagnostic_only",
            "inspect target then remove project",
        ),
    ],
)
def test_target_failure_translation_accounts_for_later_capture_and_cleanup_failures(
    later_kinds: tuple[FailureKind, ...], evidence_state: str, corrective_action: str
) -> None:
    translated = cast(Any, failure_module)._capture_failure_outcome(
        FailureKind.TARGET_NONZERO_EXIT,
        "target exited naturally with status 23",
        status=23,
        origin=CaptureFailureOrigin.WORKLOAD,
        authority="primary",
        all_kinds=(FailureKind.TARGET_NONZERO_EXIT, *later_kinds),
        natural_target_succeeded=False,
    )

    assert translated.evidence_state == evidence_state
    assert translated.corrective_action == corrective_action


@pytest.mark.parametrize("interruption_point", ["readiness", "target-start"])
@pytest.mark.parametrize("publication_outcome", ["deadline", "error", "warning"])
def test_interruption_diagnostic_publication_failures_remain_secondary(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_point: str,
    publication_outcome: str,
) -> None:
    """Diagnostic deadline, validation, and cleanup warnings cannot replace user interruption."""
    experiment_path, _prepared_run = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    scenario = "readiness_timeout" if interruption_point == "readiness" else "partial_start_interrupt"
    docker = DockerDouble(scenario)
    warning_metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    warning_metadata_path = tmp_path / "warning-capture.json"
    warning_pcapng_path = tmp_path / "warning-reference.pcapng"
    warning_metadata_path.write_bytes(render_capture_metadata(warning_metadata))
    warning_pcapng_path.write_bytes(encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 64),), warning_metadata))
    warning_inspection = artifact_module.validate_capture_pair(
        warning_metadata_path,
        warning_pcapng_path,
        deadline=None,
        clock=lambda: 0.0,
    )

    def controlled_publish(*args: object, **kwargs: object) -> object:
        if publication_outcome == "deadline":
            raise DeadlineExceededError("injected diagnostic deadline", corrective_action="test")
        if publication_outcome == "error":
            raise TrafficlabError("injected diagnostic validation", corrective_action="test")
        del args, kwargs
        return artifact_module.CapturePublication(
            warning_inspection,
            False,
            None,
            ("injected diagnostic cleanup warning",),
        )

    monkeypatch.setattr(capture_module, "publish_capture_pair", controlled_publish)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(
            experiment_path,
            docker=docker,
            clock=Clock(docker),
            interruption=(lambda: True) if interruption_point == "readiness" else (lambda: False),
        )

    assert caught.value.exit_code == 130
    assert "injected diagnostic" in str(caught.value)


@pytest.mark.parametrize("clock", [lambda: float("nan"), lambda: (_ for _ in ()).throw(OverflowError())])
def test_deadline_rejects_nonfinite_and_arithmetic_failure(clock: object) -> None:
    """An invalid clock must fail directly instead of allowing an unbounded Docker operation."""
    with pytest.raises(TrafficlabError, match="deadline"):
        lifecycle_module.future_deadline(cast(Any, clock), 1.0, stage="test")


@pytest.mark.parametrize(
    ("scenario", "interruption", "expected"),
    [
        ("readiness_capture_exit", lambda: False, "capture stopped before readiness"),
        ("readiness_timeout", lambda: True, "capture interrupted during readiness"),
    ],
)
def test_readiness_stop_and_interruption_are_direct_failures(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    interruption: object,
    expected: str,
) -> None:
    """Readiness must stop immediately when capture exits or the user interrupts."""
    experiment_path, _prepared_run = prepared_capture(valid_config_data, tmp_path, monkeypatch)
    docker = DockerDouble(scenario)

    with pytest.raises(TrafficlabError, match=expected):
        capture_experiment(
            experiment_path,
            docker=docker,
            clock=Clock(docker),
            interruption=cast(Any, interruption),
        )

    assert "start_target" not in docker.calls
