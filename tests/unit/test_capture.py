import json
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest
import tomli_w

import trafficlab.artifacts as artifact_module
import trafficlab.capture as capture_module
import trafficlab.compatibility as compatibility
from trafficlab.capture import CaptureResult, capture_experiment, capture_prepared_experiment
from trafficlab.capture_policy import CaptureOutcome, FailureKind, record_flush_failure, record_natural_target_status
from trafficlab.compatibility import identify_file
from trafficlab.config import MountConfig
from trafficlab.docker_cli import CommandResult, ProjectInventory, ServiceState
from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.pcapng import encode_pcapng
from trafficlab.preflight import (
    CaptureEnvironmentIdentity,
    MountedInputIdentity,
    PreparedExperiment,
    open_or_prepare_experiment,
)
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata


@dataclass
class _CleanupHandle:
    returncode: int = 0

    def wait(self, *, timeout: float) -> CommandResult:
        return CommandResult(self.returncode, "", "cleanup failed" if self.returncode else "")

    def terminate(self) -> None:
        raise AssertionError("bounded fake cleanup should complete")

    def kill(self) -> None:
        raise AssertionError("bounded fake cleanup should complete")


class _Docker:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls: list[str] = []
        self.target_started = False
        self.target_killed = False
        self.capture_signalled = False

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def info(self, *, deadline: float) -> CommandResult:
        return CommandResult(0, "ready", "")

    def compose_version(self, *, deadline: float) -> CommandResult:
        return CommandResult(0, json.dumps({"version": "v5.4.0"}), "")

    def image_inspect(self, image: str, *, deadline: float) -> CommandResult:
        return CommandResult(0, "[]", "")

    def image_pull(self, image: str, *, deadline: float) -> CommandResult:
        raise AssertionError(f"unexpected image pull: {image}")

    def config(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        return CommandResult(0, "{}", "")

    def create_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("create_capture")
        document = cast(dict[str, object], json.loads(compose_path.read_bytes()))
        services = cast(dict[str, object], document["services"])
        capture = cast(dict[str, object], services["capture"])
        volume = cast(dict[str, object], cast(list[object], capture["volumes"])[0])
        output = Path(cast(str, volume["source"]))
        if not self.scenario.startswith("readiness_timeout"):
            metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
            (output / "capture.json").write_bytes(render_capture_metadata(metadata))
            if self.scenario in {"malformed", "target_nonzero_malformed"}:
                (output / "reference.pcapng.tmp").write_bytes(b"\x0a\x0d\x0d\x0a-invalid")
            else:
                (output / "reference.pcapng.tmp").write_bytes(
                    encode_pcapng(
                        (
                            TraceEvent(0.0, Direction.OUTBOUND, 64),
                            TraceEvent(0.1, Direction.INBOUND, 96),
                        ),
                        metadata,
                    )
                )
        return CommandResult(0, "", "")

    def start_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("start_capture")
        return CommandResult(0, "", "")

    def start_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("start_target")
        self.target_started = True
        if self.scenario == "partial_start_interrupt":
            raise KeyboardInterrupt
        if self.scenario == "start_target_error":
            raise TrafficlabError("injected target start failure", corrective_action="test")
        return CommandResult(0, "", "")

    def service_state(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        self._record(f"state_{service}")
        if service == "target":
            if self.target_killed:
                if self.scenario == "capture_exit_state_error":
                    raise TrafficlabError("injected killed-target state failure", corrective_action="test")
                if self.scenario == "capture_exit_target_missing":
                    return cast(ServiceState, None)
                return ServiceState("target", "target", "target", "exited", 137)
            if self.scenario.startswith(("workload_timeout", "interruption", "capture_exit")):
                return ServiceState("target", "target", "target", "running", 0)
            status = (
                23
                if self.scenario in {"target_nonzero", "target_nonzero_malformed"} or self.scenario.endswith("_nonzero")
                else 0
            )
            return ServiceState("target", "target", "target", "exited", status)
        if self.scenario == "readiness_capture_exit" or (
            (self.scenario.startswith("capture_exit") or self.scenario == "simultaneous_target_capture")
            and self.target_started
        ):
            return ServiceState("capture", "capture", "capture", "exited", 7)
        if (
            self.capture_signalled
            and self.scenario != "flush_timeout"
            and not self.scenario.startswith("flush_kill_error")
        ):
            if self.scenario.startswith("flush_state_error"):
                raise TrafficlabError("injected flush state failure", corrective_action="test")
            if self.scenario == "flush_disappeared":
                return cast(ServiceState, None)
            if self.scenario == "flush_unknown":
                return ServiceState("capture", "capture", "capture", "paused", 0)
            if self.scenario == "flush_stopped":
                return ServiceState("capture", "capture", "capture", "dead", 0)
            return ServiceState("capture", "capture", "capture", "exited", 0)
        return ServiceState("capture", "capture", "capture", "running", 0)

    def service_logs(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> str:
        self._record(f"logs_{service}")
        if self.scenario.endswith("logs_error"):
            raise TrafficlabError("injected capture logs failure", corrective_action="test")
        return "capture readiness details"

    def kill_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("kill_target")
        self.target_killed = True
        if self.scenario == "capture_exit_kill_error":
            raise TrafficlabError("injected target kill failure", corrective_action="test")
        return CommandResult(0, "", "")

    def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("signal_capture")
        if self.scenario.startswith("flush_signal_error"):
            raise TrafficlabError("injected SIGINT failure", corrective_action="test")
        self.capture_signalled = True
        return CommandResult(0, "", "")

    def kill_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("kill_capture")
        if self.scenario.startswith("flush_kill_error"):
            raise TrafficlabError("injected capture kill failure", corrective_action="test")
        return CommandResult(0, "", "")

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> _CleanupHandle:
        self._record("start_down")
        return _CleanupHandle(returncode=5 if "cleanup_failure" in self.scenario else 0)

    def project_inventory(self, compose_path: Path, project_name: str, *, deadline: float) -> ProjectInventory:
        self._record("inventory")
        if self.scenario == "cleanup_failure_inventory_error":
            raise TrafficlabError("injected cleanup inventory failure", corrective_action="test")
        return ProjectInventory(containers=())


class _Clock:
    def __init__(self, docker: _Docker) -> None:
        self.docker = docker

    def __call__(self) -> float:
        if self.docker.scenario == "validation_deadline" and self.docker.capture_signalled:
            return 160.0
        if self.docker.scenario.startswith("readiness_timeout") and self.docker.calls[-1:] == ["state_capture"]:
            return 111.0
        if (
            self.docker.scenario == "workload_timeout"
            and self.docker.target_started
            and not self.docker.target_killed
            and self.docker.calls[-1:] == ["state_capture"]
        ):
            return 131.0
        if (
            self.docker.scenario in {"flush_timeout", "flush_kill_error", "flush_kill_error_nonzero"}
            and self.docker.capture_signalled
            and self.docker.calls[-1:] == ["state_capture"]
        ):
            return 106.0
        return 100.0


def _prepared(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, PreparedExperiment]:
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    prepared = open_or_prepare_experiment(experiment_path)
    prepared = replace(
        prepared,
        report=replace(
            prepared.report,
            environment_identity=replace(
                CaptureEnvironmentIdentity(
                    host_architecture="linux/amd64",
                    target_reference=prepared.config.target.image,
                    target_content_id="sha256:" + ("c" * 64),
                    capture_reference=prepared.config.capture.image,
                    capture_content_id=("sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c"),
                    capture_tool_version="4.0.17",
                ),
                mounted_inputs=cast(Any, capture_module)._identify_mounted_inputs(prepared.config),
            ),
        ),
    )

    def already_prepared(*args: object, **kwargs: object) -> PreparedExperiment:
        return prepared

    monkeypatch.setattr(capture_module, "run_preflight", already_prepared)
    return experiment_path, prepared


def _seed_capture_lineage(prepared: PreparedExperiment) -> None:
    environment = prepared.report.environment_identity
    assert environment is not None
    artifact_module.append_run_log(
        prepared.run_directory,
        {
            "capture_environment_identity": {
                "host_architecture": environment.host_architecture,
                "target_reference": environment.target_reference,
                "target_content_id": environment.target_content_id,
                "capture_reference": environment.capture_reference,
                "capture_content_id": environment.capture_content_id,
                "capture_tool_version": environment.capture_tool_version,
                "mounted_inputs": [item.as_dict() for item in environment.mounted_inputs],
            },
            "capture_identity": identify_file(prepared.run_directory / "capture.json").as_dict(),
            "event": "capture_published",
            "experiment_identity": identify_file(prepared.run_directory / "experiment.toml").as_dict(),
            "reference_identity": identify_file(prepared.run_directory / "reference.pcapng").as_dict(),
            "reused": False,
            "stage": "capture",
        },
    )


def test_readiness_timeout_reports_capture_logs_and_never_starts_target(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting target or hiding capture logs would make a readiness failure unsafe and opaque."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("readiness_timeout")

    with pytest.raises(TrafficlabError, match="readiness timed out"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert "start_target" not in docker.calls
    assert "logs_capture" in docker.calls
    assert docker.calls[-2:] == ["start_down", "inventory"]
    assert not (prepared.run_directory / "reference.pcapng").exists()


def test_readiness_log_failure_is_ordered_secondary_evidence(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later log-read failure must not disappear after readiness already selected the primary failure."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("readiness_timeout_logs_error")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert str(caught.value) == (
        "capture readiness timed out; secondary: could not read capture logs after readiness failure: "
        "injected capture logs failure"
    )
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == [
        "could not read capture logs after readiness failure: injected capture logs failure"
    ]
    assert docker.calls[-2:] == ["start_down", "inventory"]


def test_readiness_log_jsonl_failure_is_ordered_secondary_evidence(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure to persist retrieved logs must remain visible after the readiness primary."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("readiness_timeout")
    real_append = capture_module._append_event  # pyright: ignore[reportPrivateUsage]

    def fail_capture_log_record(run_directory: Path, event: str, **detail: object) -> None:
        if event == "capture_logs":
            raise TrafficlabError("injected run-log failure", corrective_action="test")
        real_append(run_directory, event, **detail)

    monkeypatch.setattr(capture_module, "_append_event", fail_capture_log_record)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert "could not record capture logs after readiness failure: injected run-log failure" in str(caught.value)
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == [
        "could not record capture logs after readiness failure: injected run-log failure"
    ]


def test_capture_failure_log_append_preserves_the_existing_primary_outcome(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_path, _prepared_result = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("target_nonzero")
    original_append = capture_module._append_event  # pyright: ignore[reportPrivateUsage]

    def fail_capture_failure_record(*args: object, **kwargs: object) -> None:
        if args[1] == "capture_failed":
            raise TrafficlabError("injected final run-log failure", corrective_action="repair run log")
        original_append(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(capture_module, "_append_event", fail_capture_failure_record)

    with pytest.raises(TrafficlabError, match="target exited naturally with status 23") as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

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
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("target_nonzero")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert str(caught.value) == "target exited naturally with status 23"
    assert caught.value.exit_code == 23
    assert "kill_target" not in docker.calls
    assert docker.calls.count("signal_capture") == 1
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert (prepared.run_directory / "diagnostic-reference.pcapng").exists()
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["primary_status"] == 23
    assert records[-1]["secondary_failures"] == []


def test_capture_uses_default_docker_boundary_when_not_injected(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public default path must construct the same bounded Docker adapter used by injection tests."""
    experiment_path, _prepared_run = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("normal")
    constructed: list[object] = []

    def default_docker(*, clock: object) -> _Docker:
        constructed.append(clock)
        return docker

    monkeypatch.setattr(capture_module, "DockerCompose", default_docker)
    clock = _Clock(docker)

    result = capture_experiment(experiment_path, clock=clock, interruption=lambda: False)

    assert constructed == [clock]
    assert result.packet_count == 2


def test_capture_rejects_internal_success_without_a_reusable_result(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public boundary must never silently return when an internal result is unexpectedly absent."""
    experiment_path, _prepared_run = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("normal")

    def absent_result(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(capture_module, "CaptureResult", absent_result)

    with pytest.raises(TrafficlabError, match="completed without a reusable reference"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)


def test_simultaneous_target_zero_and_capture_stop_rejects_output_without_signal(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natural target priority must not turn an already-stopped capture into a reusable reference."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("simultaneous_target_capture")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

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
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("capture_exit")

    with pytest.raises(TrafficlabError, match="capture stopped during target workload"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

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
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker(scenario)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

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
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("capture_exit_target_missing")

    with pytest.raises(TrafficlabError, match="capture stopped during target workload"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert "logs_capture" in docker.calls
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == []


def test_start_target_boundary_error_is_contextual_and_cleanup_still_runs(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broad lifecycle boundary must translate an ordinary Docker error and retain unconditional cleanup."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("start_target_error")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert str(caught.value) == "could not start target service: injected target start failure"
    assert "kill_target" not in docker.calls
    assert docker.calls[-2:] == ["start_down", "inventory"]
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "validation_failed"


def test_outer_post_event_failure_remains_secondary_to_natural_target_status(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected post-event boundary failure must be contextual evidence, never a replacement primary."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("target_nonzero")

    def fail_flush(*args: object, **kwargs: object) -> CaptureOutcome:
        raise OSError("injected post-event flush boundary failure")

    monkeypatch.setattr(capture_module, "_flush_capture", fail_flush)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith("target exited naturally with status 23; secondary: could not flush capture")
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "target_nonzero_exit"
    assert "post-event flush boundary failure" in records[-1]["secondary_details"][0]


def test_natural_nonzero_remains_primary_when_validation_also_fails(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact target status stays primary while ordered validation evidence remains visible."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("target_nonzero_malformed")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith("target exited naturally with status 23; secondary: capture validation failed")
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    failed = records[-1]
    assert failed["failure_kind"] == "target_nonzero_exit"
    assert len(cast(list[object], failed["secondary_details"])) == 1
    assert "capture validation failed" in cast(list[str], failed["secondary_details"])[0]


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
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker(scenario)
    interruption = (lambda: docker.target_started) if scenario == "interruption" else (lambda: False)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=interruption)

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
    docker = _Docker("normal")
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
    cast(Any, capture_module)._observe_workload(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        stage_deadline=105.0,
        total_deadline=160.0,
        clock=lambda: 100.0,
        interruption=lambda: False,
    )
    cast(Any, capture_module)._flush_capture(
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


def test_last_known_capture_inventory_includes_the_created_project_network() -> None:
    """Zero-budget cleanup must report the known project network, not only observed containers."""
    states = {
        "capture": ServiceState("capture-id", "project-capture-1", "capture", "running", 0),
    }

    inventory = cast(Any, capture_module)._inventory(
        states,
        project_name="trafficlab-capture-test",
        project_may_exist=True,
    )

    assert inventory == ProjectInventory(
        containers=(states["capture"],),
        networks=("trafficlab-capture-test_default",),
    )


@pytest.mark.parametrize(
    ("stage_deadline", "total_deadline", "expected"),
    [(105.0, 160.0, FailureKind.STAGE_TIMEOUT), (170.0, 160.0, FailureKind.TOTAL_TIMEOUT)],
)
def test_workload_poll_expiry_is_classified_by_the_deadline_that_bounded_the_command(
    tmp_path: Path, stage_deadline: float, total_deadline: float, expected: FailureKind
) -> None:
    """A Docker poll timeout at the active bound must remain a lifecycle timeout, not validation failure."""
    docker = _Docker("normal")
    seen: list[float] = []

    def expired(*args: object, deadline: float, **kwargs: object) -> ServiceState:
        del args, kwargs
        seen.append(deadline)
        raise TrafficlabError("Docker command deadline expired before launch", corrective_action="test")

    docker.service_state = expired  # type: ignore[method-assign]
    outcome, _capture = cast(Any, capture_module)._observe_workload(
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


def test_workload_poll_expiry_retains_the_last_known_live_capture_for_flush(tmp_path: Path) -> None:
    """Failure before the capture poll must not hide a previously observed live capture from the flush transition."""
    docker = _Docker("normal")
    live_capture = ServiceState("capture", "capture", "capture", "running", 0)
    states = {"capture": live_capture}

    def expired(*args: object, deadline: float, **kwargs: object) -> ServiceState:
        del args, kwargs
        assert deadline == 105.0
        raise TrafficlabError("Docker command deadline expired before launch", corrective_action="test")

    docker.service_state = expired  # type: ignore[method-assign]
    outcome, capture = cast(Any, capture_module)._observe_workload(
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


def test_workload_records_every_simultaneously_visible_event_in_priority_order(tmp_path: Path) -> None:
    """Choosing one primary must not discard lower-priority statuses and deadline evidence."""
    docker = _Docker("simultaneous_target_capture")
    docker.target_started = True

    outcome, _capture = cast(Any, capture_module)._observe_workload(
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


def test_target_zero_with_stage_and_total_expiry_is_retained_once_after_stage_primary(tmp_path: Path) -> None:
    """Successful natural status must remain typed evidence when a simultaneous timeout fails the capture."""
    docker = _Docker("normal")
    docker.target_started = True

    outcome, _capture = cast(Any, capture_module)._observe_workload(
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
    docker = _Docker("normal")
    docker.target_started = True

    outcome, _capture = cast(Any, capture_module)._observe_workload(
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


def test_readiness_interruption_wins_over_ready_or_stopped_capture_and_never_starts_target(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A visible user interruption must be selected before readiness can launch the target."""
    experiment_path, _prepared_run = _prepared(valid_config_data, tmp_path, monkeypatch)
    for scenario, expected_signals in (("normal", 1), ("readiness_capture_exit", 0)):
        docker = _Docker(scenario)
        with pytest.raises(TrafficlabError) as caught:
            capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: True)
        assert caught.value.exit_code == 130
        assert "interrupted during readiness" in str(caught.value)
        assert "start_target" not in docker.calls
        assert docker.calls.count("signal_capture") == expected_signals


def test_failed_capture_close_never_validates_or_retains_diagnostics(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natural target failure cannot make unclosed capture output eligible for diagnostic retention."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("target_nonzero")
    publish_calls = 0

    def fail_close(*args: object, **kwargs: object) -> CaptureOutcome:
        return record_flush_failure(cast(CaptureOutcome, args[4]), "injected close failure")

    def unexpected_publish(*args: object, **kwargs: object) -> object:
        nonlocal publish_calls
        publish_calls += 1
        raise AssertionError("unclosed capture must not be validated")

    monkeypatch.setattr(capture_module, "_flush_capture", fail_close)
    monkeypatch.setattr(capture_module, "publish_capture_pair", unexpected_publish)

    with pytest.raises(TrafficlabError, match="target exited naturally with status 23"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert publish_calls == 0
    assert not (prepared.run_directory / "diagnostic-reference.pcapng").exists()


def test_keyboard_interrupt_inside_workload_runs_owned_interruption_transition(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real signal-raised KeyboardInterrupt must not bypass kill, flush, diagnostics, or cleanup."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("interruption")
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
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert caught.value.exit_code == 130
    assert docker.calls.count("kill_target") == 1
    assert docker.calls.count("signal_capture") == 1
    assert docker.calls[-2:] == ["start_down", "inventory"]
    assert (prepared.run_directory / "diagnostic-capture.json").exists()
    assert (prepared.run_directory / "diagnostic-reference.pcapng").exists()
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_kind"] == "user_interruption"


def test_keyboard_interrupt_during_partial_target_start_kills_before_capture_action(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partially started target remains owned even when SIGINT interrupts the start command itself."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("partial_start_interrupt")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    started_at = docker.calls.index("start_target")
    assert docker.calls[started_at + 1 : started_at + 5] == [
        "kill_target",
        "state_target",
        "signal_capture",
        "state_capture",
    ]
    assert docker.calls[-2:] == ["start_down", "inventory"]
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
    docker = _Docker(scenario)
    states = {"capture": ServiceState("capture", "capture", "capture", "running", 0)}

    outcome = cast(Any, capture_module)._interrupt_lifecycle(
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
    experiment_path, _prepared_run = _prepared(valid_config_data, tmp_path, monkeypatch)
    real_temporary_directory = tempfile.TemporaryDirectory

    def fail_creation(*args: object, **kwargs: object) -> object:
        raise OSError("injected temporary-directory creation failure")

    monkeypatch.setattr(capture_module.tempfile, "TemporaryDirectory", fail_creation)
    with pytest.raises(TrafficlabError, match="temporary capture directory.*creation failure"):
        capture_experiment(experiment_path, docker=_Docker("normal"), clock=lambda: 100.0, interruption=lambda: False)

    class CleanupFailure:
        def __init__(self, *, prefix: str, dir: Path) -> None:
            self.temporary = real_temporary_directory(prefix=prefix, dir=dir)

        def __enter__(self) -> str:
            return self.temporary.__enter__()

        def __exit__(self, *args: object) -> None:
            self.temporary.cleanup()
            raise OSError("injected temporary-directory cleanup failure")

    monkeypatch.setattr(capture_module.tempfile, "TemporaryDirectory", CleanupFailure)
    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(
            experiment_path,
            docker=_Docker("target_nonzero"),
            clock=lambda: 100.0,
            interruption=lambda: False,
        )
    assert str(caught.value).startswith("target exited naturally with status 23; secondary:")
    assert "temporary capture directory" in str(caught.value)

    with pytest.raises(TrafficlabError, match="temporary capture directory"):
        capture_experiment(
            experiment_path,
            docker=_Docker("normal"),
            clock=lambda: 100.0,
            interruption=lambda: False,
        )
    assert not (_prepared_run.run_directory / "capture.json").exists()
    assert not (_prepared_run.run_directory / "reference.pcapng").exists()


def test_flush_timeout_signals_once_then_kills_capture_without_validation(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waiting indefinitely or validating an unflushed PCAPNG could hang capture or publish truncated data."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("flush_timeout")

    with pytest.raises(TrafficlabError, match="capture flush timed out"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert docker.calls.count("signal_capture") == 1
    assert docker.calls.count("kill_capture") == 1
    assert docker.calls.index("kill_capture") > docker.calls.index("signal_capture")
    assert not (prepared.run_directory / "reference.pcapng").exists()


def test_malformed_capture_is_validation_primary_and_never_published(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating a readiness prefix as complete validation could publish corrupt experiment input."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("malformed")

    with pytest.raises(TrafficlabError, match="capture validation failed"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert docker.calls[-2:] == ["start_down", "inventory"]


def test_validation_total_deadline_is_primary_and_zero_budget_cleanup_makes_no_docker_call(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation or cleanup work after total expiry would violate the single end-to-end deadline."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("validation_deadline")

    with pytest.raises(TrafficlabError, match="capture validation failed: capture inspection exceeded"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert "start_down" not in docker.calls
    assert "inventory" not in docker.calls
    assert not (prepared.run_directory / "reference.pcapng").exists()


def test_cleanup_failure_is_primary_after_success_and_withdraws_reusable_pair(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving a reusable pair after reporting capture failure could feed an unclean run into analysis."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("cleanup_failure")

    with pytest.raises(TrafficlabError, match="cleanup command failed with status 5"):
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()


def test_post_publication_temp_cleanup_warning_fails_capture_and_rolls_back_owned_pair(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A private-temp cleanup failure must be visible without stranding the newly reusable pair."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("normal")
    real_unlink = artifact_module._unlink_capture_temporary  # pyright: ignore[reportPrivateUsage]
    attempts: list[Path] = []

    def fail_metadata_temp(path: Path | None) -> str | None:
        if path is not None and path.name.startswith(".capture-pair.metadata."):
            attempts.append(path)
            return f"could not remove owned temporary file {path}: injected post-publication cleanup failure"
        return real_unlink(path)

    monkeypatch.setattr(artifact_module, "_unlink_capture_temporary", fail_metadata_temp)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

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
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("normal")
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
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert swapped
    assert "capture publication cleanup warning" in str(caught.value)
    assert "could not roll back owned capture publication" in str(caught.value)
    assert (prepared.run_directory / "capture.json").read_bytes() == winner_metadata_bytes
    assert (prepared.run_directory / "reference.pcapng").read_bytes() == winner_pcapng_bytes


def test_nonzero_target_keeps_diagnostics_and_records_temp_cleanup_warning_as_secondary(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnostic ownership stays false and its private-temp warning cannot replace the exact target status."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("target_nonzero")
    real_unlink = artifact_module._unlink_capture_temporary  # pyright: ignore[reportPrivateUsage]

    def fail_metadata_temp(path: Path | None) -> str | None:
        if path is not None and path.name.startswith(".capture-pair.metadata."):
            return f"could not remove owned temporary file {path}: injected diagnostic cleanup failure"
        return real_unlink(path)

    monkeypatch.setattr(artifact_module, "_unlink_capture_temporary", fail_metadata_temp)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith("target exited naturally with status 23; secondary: ")
    assert "injected diagnostic cleanup failure" in str(caught.value)
    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert (prepared.run_directory / "diagnostic-capture.json").exists()
    assert (prepared.run_directory / "diagnostic-reference.pcapng").exists()


def test_pre_workload_reuse_preserves_the_preexisting_reference_pair_without_cleanup(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid existing pair is reused before any Docker project exists or needs cleanup."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    metadata_bytes = render_capture_metadata(metadata)
    pcapng_bytes = encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    (prepared.run_directory / "capture.json").write_bytes(metadata_bytes)
    (prepared.run_directory / "reference.pcapng").write_bytes(pcapng_bytes)
    _seed_capture_lineage(prepared)
    docker = _Docker("cleanup_failure")

    result = capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert result.reused is True
    assert docker.calls == []
    assert (prepared.run_directory / "capture.json").read_bytes() == metadata_bytes
    assert (prepared.run_directory / "reference.pcapng").read_bytes() == pcapng_bytes


def test_public_capture_reuses_a_locally_validated_pair_without_docker(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """The public capture boundary must not run full Docker preflight for exact reuse."""
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    prepared = open_or_prepare_experiment(experiment_path)
    prepared = replace(
        prepared,
        report=replace(
            prepared.report,
            environment_identity=CaptureEnvironmentIdentity(
                host_architecture="linux/amd64",
                target_reference=prepared.config.target.image,
                target_content_id="sha256:" + ("c" * 64),
                capture_reference=prepared.config.capture.image,
                capture_content_id=("sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c"),
                capture_tool_version="4.0.17",
            ),
        ),
    )
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    )
    _seed_capture_lineage(prepared)

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"exact public reuse touched Docker operation {name}")

    result = capture_experiment(experiment_path, docker=cast(Any, NoDocker()), clock=lambda: 100.0)

    assert result.reused is True
    assert result.packet_count == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("capture_content_id", "sha256:" + ("d" * 64)),
        ("capture_tool_version", "4.0.18"),
    ],
)
def test_public_capture_reuse_rejects_a_valid_format_environment_that_differs_from_the_checked_lock(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    """Config-only reuse must bind the recorded capture identity to the checked lock before Docker."""
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    prepared = open_or_prepare_experiment(experiment_path)
    prepared = replace(
        prepared,
        report=replace(
            prepared.report,
            environment_identity=CaptureEnvironmentIdentity(
                host_architecture="linux/amd64",
                target_reference=prepared.config.target.image,
                target_content_id="sha256:" + ("c" * 64),
                capture_reference=prepared.config.capture.image,
                capture_content_id="sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c",
                capture_tool_version="4.0.17",
            ),
        ),
    )
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    )
    _seed_capture_lineage(prepared)
    pair_before = {name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")}
    log_path = prepared.run_directory / "run.log"
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    publication = next(record for record in records if record["event"] == "capture_published")
    environment = cast(dict[str, object], publication["capture_environment_identity"])
    environment[field] = replacement
    log_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"lock-incompatible public reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=cast(Any, NoDocker()), clock=lambda: 100.0)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "affected_evidence": "capture pair",
        "authority": "primary",
        "corrective_action": "select its matching run or a new run directory",
        "detail": "capture pair has another identity",
        "evidence_state": "preserved",
        "kind": "artifact_stale",
        "stage": "capture",
    }
    assert {
        name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")
    } == pair_before


def test_capture_lineage_persists_ordered_read_only_file_and_directory_identities(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "request.txt"
    second = tmp_path / "settings.json"
    directory = tmp_path / "directory-input"
    first.write_bytes(b"request-v1")
    second.write_bytes(b'{"value":1}\n')
    directory.mkdir()
    (directory / "nested.txt").write_bytes(b"nested-input")
    target = cast(dict[str, object], valid_config_data["target"])
    target["mounts"] = [
        {"source": str(first), "target": "/work/request.txt", "read_only": True},
        {"source": str(directory), "target": "/work/directory-input", "read_only": True},
        {"source": str(second), "target": "/work/settings.json", "read_only": False},
    ]
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("normal")

    capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    publication = next(record for record in records if record["event"] == "capture_published")
    environment = cast(dict[str, object], publication["capture_environment_identity"])
    assert environment["mounted_inputs"] == [
        {
            "read_only": True,
            "sha256": identify_file(first).sha256,
            "size": len(b"request-v1"),
            "target": "/work/request.txt",
        },
        {
            "read_only": True,
            "sha256": compatibility.identify_directory(directory).sha256,
            "size": len(b"nested-input"),
            "target": "/work/directory-input",
        },
    ]


def test_public_capture_reuse_reidentifies_mounted_file_bytes_before_docker(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mounted = tmp_path / "request.txt"
    mounted.write_bytes(b"request-v1")
    target = cast(dict[str, object], valid_config_data["target"])
    target["mounts"] = [
        {"source": str(mounted), "target": "/work/request.txt", "read_only": True},
    ]
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    )
    _seed_capture_lineage(prepared)
    mounted.write_bytes(b"request-v2")

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"stale public reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=cast(Any, NoDocker()), clock=lambda: 100.0)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "affected_evidence": "capture evidence",
        "authority": "primary",
        "corrective_action": "restore the declared mounted-input content identity",
        "detail": "mounted input request.txt is incompatible",
        "evidence_state": "not_published",
        "kind": "docker_preflight_failed",
        "stage": "preflight",
    }


def test_mounted_input_comparison_rejects_changed_read_only_directory_bytes(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "directory-input"
    directory.mkdir()
    payload = directory / "request.txt"
    payload.write_bytes(b"request-v1")
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mount = MountConfig(source=directory, target="/work/input", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )
    expected = cast(Any, capture_module)._identify_mounted_inputs(config)
    payload.write_bytes(b"request-v2")

    with pytest.raises(TrafficlabError, match="mounted input input is incompatible"):
        cast(Any, capture_module)._require_matching_mounted_inputs(config, expected)


def test_writable_file_and_directory_mounts_are_not_immutable_inputs(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writable_file = tmp_path / "output.txt"
    writable_file.write_bytes(b"initial")
    writable_directory = tmp_path / "output"
    writable_directory.mkdir()
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mounts = (
        MountConfig(source=writable_file, target="/work/output.txt", read_only=False),
        MountConfig(source=writable_directory, target="/work/output", read_only=False),
    )
    config = prepared.config.model_copy(update={"target": prepared.config.target.model_copy(update={"mounts": mounts})})

    assert cast(Any, capture_module)._identify_mounted_inputs(config) == ()


def test_prepared_capture_reuse_reidentifies_an_unavailable_mounted_file(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mounted = tmp_path / "request.txt"
    mounted.write_bytes(b"request-v1")
    target = cast(dict[str, object], valid_config_data["target"])
    target["mounts"] = [
        {"source": str(mounted), "target": "/work/request.txt", "read_only": True},
    ]
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    )
    _seed_capture_lineage(prepared)
    mounted.unlink()

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"unavailable reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture_prepared_experiment(
            experiment_path,
            prepared,
            docker=cast(Any, NoDocker()),
            clock=lambda: 100.0,
        )

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.as_dict() == {
        "affected_evidence": "capture evidence",
        "authority": "primary",
        "corrective_action": "restore the named mounted input bytes",
        "detail": "mounted input request.txt is unavailable",
        "evidence_state": "not_published",
        "kind": "docker_preflight_failed",
        "stage": "preflight",
    }
    assert (prepared.run_directory / "capture.json").exists()
    assert (prepared.run_directory / "reference.pcapng").exists()


def test_capture_reuse_rejects_a_valid_pair_bound_to_another_environment(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parseable pair from another image/tool identity is stale, not reusable."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    first_docker = _Docker("normal")
    first = capture_prepared_experiment(
        experiment_path,
        prepared,
        docker=first_docker,
        clock=_Clock(first_docker),
        interruption=lambda: False,
    )
    assert first.reused is False
    before = {name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")}
    original_identity = prepared.report.environment_identity
    assert original_identity is not None
    incompatible = replace(
        prepared,
        report=replace(
            prepared.report,
            environment_identity=replace(original_identity, capture_tool_version="4.0.18"),
        ),
    )
    second_docker = _Docker("normal")

    with pytest.raises(TrafficlabError) as caught:
        capture_prepared_experiment(
            experiment_path,
            incompatible,
            docker=second_docker,
            clock=_Clock(second_docker),
            interruption=lambda: False,
        )

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
        outcome.status,
    ) == (
        "artifact_stale",
        "capture",
        "capture pair has another identity",
        "capture pair",
        "preserved",
        "select its matching run or a new run directory",
        "primary",
        None,
    )
    assert second_docker.calls == []
    assert {
        name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")
    } == before


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "malformed",
        "content-mismatch",
        "environment-not-object",
        "environment-fields",
        "environment-string",
        "environment-empty-string",
        "environment-platform",
        "environment-content-id",
        "environment-capture-content-id",
        "target-reference",
        "capture-reference",
        "mounted-inputs-not-array",
        "mounted-input-fields",
        "log-record-not-object",
    ],
)
def test_capture_reuse_rejects_missing_or_invalid_lineage_before_boundaries(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    first_docker = _Docker("normal")
    capture_prepared_experiment(
        experiment_path,
        prepared,
        docker=first_docker,
        clock=_Clock(first_docker),
        interruption=lambda: False,
    )
    pair_before = {name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")}
    log_path = prepared.run_directory / "run.log"
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    publication = next(record for record in records if record["event"] == "capture_published")
    if corruption == "missing":
        records.remove(publication)
    elif corruption == "malformed":
        publication["capture_identity"] = {"size": "wrong", "sha256": "0" * 64}
    elif corruption == "content-mismatch":
        publication["reference_identity"] = {"size": 1, "sha256": "0" * 64}
    elif corruption == "environment-not-object":
        publication["capture_environment_identity"] = []
    elif corruption == "environment-fields":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        del environment["capture_tool_version"]
    elif corruption == "environment-string":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["target_reference"] = 1
    elif corruption == "environment-empty-string":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["capture_tool_version"] = " "
    elif corruption == "environment-platform":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["host_architecture"] = "linux/arm64"
    elif corruption == "environment-content-id":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["target_content_id"] = "mutable-tag"
    elif corruption == "environment-capture-content-id":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["capture_content_id"] = "mutable-tag"
    elif corruption == "target-reference":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["target_reference"] = "example.invalid/other:tag"
    elif corruption == "capture-reference":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["capture_reference"] = "example.invalid/other:tag"
    elif corruption == "mounted-inputs-not-array":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["mounted_inputs"] = {}
    elif corruption == "mounted-input-fields":
        environment = cast(dict[str, object], publication["capture_environment_identity"])
        environment["mounted_inputs"] = [{"target": "/work/request.txt"}]
    else:
        records.append([])
    log_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"invalid lineage touched Docker operation {name}")

    def reject_clock() -> float:
        raise AssertionError("invalid lineage touched the clock")

    with pytest.raises(TrafficlabError) as caught:
        capture_prepared_experiment(
            experiment_path,
            prepared,
            docker=cast(Any, NoDocker()),
            clock=reject_clock,
        )

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
        outcome.status,
    ) == (
        "artifact_stale",
        "capture",
        "capture pair has another identity",
        "capture pair",
        "preserved",
        "select its matching run or a new run directory",
        "primary",
        None,
    )
    assert {
        name: (prepared.run_directory / name).read_bytes() for name in ("capture.json", "reference.pcapng")
    } == pair_before


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ([], "object"),
        ({"target": "/work/request.txt"}, "fields"),
        ({"target": 1, "read_only": True, "size": 1, "sha256": "0" * 64}, "target"),
        ({"target": " ", "read_only": True, "size": 1, "sha256": "0" * 64}, "target"),
        ({"target": "request.txt", "read_only": True, "size": 1, "sha256": "0" * 64}, "target"),
        ({"target": "/work/request.txt", "read_only": 1, "size": 1, "sha256": "0" * 64}, "read_only"),
    ],
)
def test_mounted_input_identity_strictly_rejects_noncanonical_records(value: object, error: str) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        MountedInputIdentity.from_dict(value)


@pytest.mark.parametrize("mounted_inputs", [[], (object(),)])
def test_capture_environment_identity_rejects_untyped_mounted_inputs(mounted_inputs: object) -> None:
    with pytest.raises(TypeError, match="mounted_inputs"):
        CaptureEnvironmentIdentity(
            host_architecture="linux/amd64",
            target_reference="example.invalid/target:tag",
            target_content_id="sha256:" + ("1" * 64),
            capture_reference="example.invalid/capture:tag",
            capture_content_id="sha256:" + ("2" * 64),
            capture_tool_version="4.0.17",
            mounted_inputs=cast(Any, mounted_inputs),
        )


@pytest.mark.parametrize("replacement", ["missing", "directory"])
def test_mounted_input_identification_classifies_a_race_at_the_stable_file_boundary(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mounted = tmp_path / "racy-request.txt"
    mounted.write_bytes(b"request-v1")
    mount = MountConfig(source=mounted, target="/work/request.txt", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )
    real_identify = capture_module.identify_file

    def replace_before_identification(path: Path) -> object:
        path.unlink()
        if replacement == "directory":
            path.mkdir()
        return real_identify(path)

    monkeypatch.setattr(capture_module, "identify_file", replace_before_identification)

    expected = "unavailable" if replacement == "missing" else "incompatible"
    with pytest.raises(TrafficlabError, match=expected):
        cast(Any, capture_module)._identify_mounted_inputs(config)


def test_mounted_input_identification_classifies_a_regular_file_read_error_as_unavailable(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mounted = tmp_path / "unreadable-request.txt"
    mounted.write_bytes(b"request-v1")
    mount = MountConfig(source=mounted, target="/work/request.txt", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )
    real_open = Path.open

    def fail_mounted_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        if path == mounted:
            raise PermissionError("injected mounted-input read failure")
        return real_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", fail_mounted_open)

    with pytest.raises(TrafficlabError, match="mounted input request.txt is unavailable") as caught:
        cast(Any, capture_module)._identify_mounted_inputs(config)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.as_dict() == {
        "affected_evidence": "capture evidence",
        "authority": "primary",
        "corrective_action": "restore the named mounted input bytes",
        "detail": "mounted input request.txt is unavailable",
        "evidence_state": "not_published",
        "kind": "docker_preflight_failed",
        "stage": "preflight",
    }
    identity_error = caught.value.__cause__
    assert isinstance(identity_error, TrafficlabError)
    assert isinstance(identity_error.__cause__, PermissionError)


def test_mounted_input_comparison_names_a_new_regular_file_at_the_same_declared_target(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    mounted = tmp_path / "directory-to-file"
    mounted.mkdir()
    mount = MountConfig(source=mounted, target="/work/request.txt", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )
    expected = cast(Any, capture_module)._identify_mounted_inputs(config)
    mounted.rmdir()
    mounted.write_bytes(b"now-a-file")

    with pytest.raises(TrafficlabError, match="mounted input request.txt is incompatible"):
        cast(Any, capture_module)._require_matching_mounted_inputs(config, expected)


def test_mounted_input_identification_rejects_a_nonregular_mount_source(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    del experiment_path
    target_file = tmp_path / "target-file"
    target_file.write_bytes(b"bytes")
    mounted = tmp_path / "request.txt"
    mounted.symlink_to(target_file)
    mount = MountConfig(source=mounted, target="/work/request.txt", read_only=True)
    config = prepared.config.model_copy(
        update={"target": prepared.config.target.model_copy(update={"mounts": (mount,)})}
    )

    with pytest.raises(TrafficlabError, match="mounted input request.txt is incompatible"):
        cast(Any, capture_module)._identify_mounted_inputs(config)


def test_cleanup_rollback_preserves_a_concurrent_replacement_pair(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership identity must stop rollback from deleting a replacement installed at the canonical names."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
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
    docker = _Docker("cleanup_failure")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert swapped
    assert (prepared.run_directory / "capture.json").read_bytes() == winner_metadata_bytes
    assert (prepared.run_directory / "reference.pcapng").read_bytes() == winner_pcapng_bytes
    assert "capture pair changed during invalid-pair recovery" in str(caught.value)


def test_cleanup_records_its_internal_secondary_failure_in_order(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known cleanup command failure must retain the later inventory-query error as secondary evidence."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("cleanup_failure_inventory_error")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == ["additional cleanup failure: injected cleanup inventory failure"]
    assert "secondary: additional cleanup failure: injected cleanup inventory failure" in str(caught.value)


def test_cleanup_failure_is_secondary_after_capture_failure(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup failure must never replace the earlier event that terminated the workload."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("capture_exit_cleanup_failure")

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert str(caught.value).startswith(
        "capture stopped during target workload; secondary: "
        "target exited after Trafficlab requested termination with status 137"
    )
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["secondary_details"] == [
        "target exited after Trafficlab requested termination with status 137",
        "cleanup command failed with status 5: cleanup failed",
    ]


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (("run", Path("/reference"), 1, 0), "run_directory"),
        ((Path("run"), Path("/reference"), 1, 0), "run_directory"),
        ((Path("/run"), "reference", 1, 0), "reference_path"),
        ((Path("/run"), Path("reference"), 1, 0), "reference_path"),
        ((Path("/run"), Path("/reference"), True, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), 0, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), -1, 0), "packet_count"),
        ((Path("/run"), Path("/reference"), 1, True), "target_status"),
    ],
)
def test_capture_result_strictly_rejects_invalid_public_values(arguments: tuple[object, ...], error: str) -> None:
    """Accepting coerced or relative result fields would break the public capture contract."""
    with pytest.raises((TypeError, ValueError), match=error):
        CaptureResult(*cast(tuple[Any, Any, Any, Any], arguments))


def test_capture_result_rejects_a_non_boolean_reuse_flag() -> None:
    """Truthiness coercion would make capture ownership ambiguous to the coordinator."""
    with pytest.raises(TypeError, match="reused"):
        CaptureResult(Path("/run"), Path("/run/reference.pcapng"), 1, 0, cast(Any, 1))


def test_capture_failure_translation_requires_an_arbitrated_primary() -> None:
    with pytest.raises(ValueError, match="existing primary failure"):
        cast(Any, capture_module)._capture_failure_outcomes(CaptureOutcome())


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
    translated = cast(Any, capture_module)._capture_failure_outcome(
        FailureKind.TARGET_NONZERO_EXIT,
        "target exited naturally with status 23",
        status=23,
        origin=capture_module.CaptureFailureOrigin.WORKLOAD,
        authority="primary",
        all_kinds=(FailureKind.TARGET_NONZERO_EXIT, *later_kinds),
        natural_target_succeeded=False,
    )

    assert translated.evidence_state == evidence_state
    assert translated.corrective_action == corrective_action


def test_prepared_capture_reuses_a_stable_pair_before_any_workload_setup(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stable reuse must not create temporary state, calculate deadlines, or touch Docker."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 64),), metadata)
    )
    _seed_capture_lineage(prepared)

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"reuse touched Docker operation {name}")

    def reject_temporary(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("reuse created a temporary capture directory")

    def reject_clock() -> float:
        raise AssertionError("reuse calculated or checked a deadline")

    monkeypatch.setattr(capture_module, "_temporary_capture_directory", reject_temporary)

    result = capture_prepared_experiment(
        experiment_path,
        prepared,
        docker=cast(Any, NoDocker()),
        clock=reject_clock,
    )

    assert result.reused is True
    assert result.target_status == 0
    assert result.packet_count == 1
    assert result.run_directory == prepared.run_directory
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1] == {
        "event": "capture_reused",
        "packet_count": 1,
        "path": str(prepared.run_directory / "reference.pcapng"),
        "reused": True,
        "stage": "capture",
    }


def test_fresh_capture_requires_full_preflight_image_identity_before_compose(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    prepared = replace(
        prepared,
        report=replace(prepared.report, environment_identity=None),
    )

    def reject_compose(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("capture without image identity rendered Compose")

    monkeypatch.setattr(capture_module, "write_production_compose", reject_compose)

    with pytest.raises(TrafficlabError, match="resolved Docker image identities") as caught:
        capture_prepared_experiment(
            experiment_path,
            prepared,
            docker=cast(Any, object()),
        )

    assert caught.value.corrective_action == "run full preflight without --config-only and retry capture"


def test_prepared_capture_removes_a_stable_stale_diagnostic_pair_before_reuse_success(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful retry must not leave failed-attempt diagnostics beside its reusable pair."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 64),), metadata)
    )
    _seed_capture_lineage(prepared)
    (prepared.run_directory / "diagnostic-capture.json").write_bytes(b"stale metadata")
    (prepared.run_directory / "diagnostic-reference.pcapng").write_bytes(b"stale pcapng")

    result = capture_prepared_experiment(experiment_path, prepared, docker=cast(Any, object()))

    assert result.reused is True
    assert not (prepared.run_directory / "diagnostic-capture.json").exists()
    assert not (prepared.run_directory / "diagnostic-reference.pcapng").exists()


@pytest.mark.parametrize("replacement", ["metadata", "pcapng"])
def test_prepared_capture_preserves_a_diagnostic_replacement_and_rejects_reuse_success(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    """Diagnostic cleanup must never delete a file replaced at its quarantine boundary."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (prepared.run_directory / "capture.json").write_bytes(render_capture_metadata(metadata))
    (prepared.run_directory / "reference.pcapng").write_bytes(
        encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 64),), metadata)
    )
    _seed_capture_lineage(prepared)
    diagnostic_metadata = prepared.run_directory / "diagnostic-capture.json"
    diagnostic_pcapng = prepared.run_directory / "diagnostic-reference.pcapng"
    diagnostic_metadata.write_bytes(b"stale metadata")
    diagnostic_pcapng.write_bytes(b"stale pcapng")
    replacement_path = diagnostic_metadata if replacement == "metadata" else diagnostic_pcapng
    replacement_bytes = f"concurrent {replacement}".encode()
    winner_path = tmp_path / f"winner-{replacement}"
    winner_path.write_bytes(replacement_bytes)
    real_rename = artifact_module.os.rename
    swapped = False

    def replace_at_quarantine(source: str | Path, destination: str | Path) -> None:
        nonlocal swapped
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not swapped
            and source_path == replacement_path
            and destination_path.parent.name.startswith(".capture-recovery.")
        ):
            swapped = True
            artifact_module.os.replace(winner_path, source_path)
        real_rename(source, destination)

    monkeypatch.setattr(artifact_module.os, "rename", replace_at_quarantine)

    with pytest.raises(TrafficlabError, match="changed during invalid-pair recovery"):
        capture_prepared_experiment(experiment_path, prepared, docker=cast(Any, object()))

    assert swapped
    assert replacement_path.read_bytes() == replacement_bytes


@pytest.mark.parametrize(
    "corruption",
    ["type", "source", "config", "run-directory", "snapshot", "log-termination", "log-prefix", "missing"],
)
def test_prepared_capture_rejects_mismatched_authoritative_inputs_before_docker(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    """A stale prepared value, snapshot, or initial log must never launch a workload."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    candidate: object = prepared
    if corruption == "type":
        candidate = object()
    elif corruption == "source":
        candidate = replace(prepared, source=tmp_path / "other.toml")
    elif corruption == "config":
        changed_run = prepared.config.run.model_copy(update={"final_seed": prepared.config.run.final_seed + 1})
        candidate = replace(prepared, config=prepared.config.model_copy(update={"run": changed_run}))
    elif corruption == "run-directory":
        candidate = replace(prepared, run_directory=tmp_path / "other-run")
    elif corruption == "snapshot":
        (prepared.run_directory / "experiment.toml").write_bytes(b"changed")
    elif corruption == "log-termination":
        log_path = prepared.run_directory / "run.log"
        log_path.write_bytes(log_path.read_bytes().rstrip(b"\n"))
    elif corruption == "log-prefix":
        (prepared.run_directory / "run.log").write_text('{"event":"wrong"}\n', encoding="utf-8")
    else:
        (prepared.run_directory / "experiment.toml").unlink()

    with pytest.raises((TypeError, TrafficlabError), match="prepared"):
        capture_prepared_experiment(experiment_path, cast(Any, candidate), docker=cast(Any, object()))


def test_public_capture_preserves_typed_snapshot_mutation_before_pair_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture cannot publish a pair under snapshot bytes changed during the workload."""
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)
    snapshot_path = prepared.run_directory / "experiment.toml"

    class SnapshotMutatingDocker(_Docker):
        def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
            result = super().signal_capture(compose_path, project_name, deadline=deadline)
            snapshot_path.write_bytes(snapshot_path.read_bytes() + b"\n")
            return result

    docker = SnapshotMutatingDocker("normal")

    with pytest.raises(TrafficlabError, match="experiment.toml changed during capture") as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    expected = {
        "affected_evidence": "experiment.toml",
        "authority": "primary",
        "corrective_action": "restore the prepared experiment snapshot and rerun capture",
        "detail": "experiment.toml changed during capture",
        "evidence_state": "preserved",
        "kind": "artifact_changed",
        "stage": "capture",
    }
    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.as_dict() == expected
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_outcome"] == expected
    assert records[-1]["secondary_details"] == []
    assert records[-1]["secondary_failures"] == []
    assert records[-1]["secondary_outcomes"] == []

    assert not (prepared.run_directory / "capture.json").exists()
    assert not (prepared.run_directory / "reference.pcapng").exists()
    assert not (prepared.run_directory / "diagnostic-capture.json").exists()
    assert not (prepared.run_directory / "diagnostic-reference.pcapng").exists()
    assert not tuple(prepared.run_directory.glob(".trafficlab-capture-*"))


@pytest.mark.parametrize(
    ("mutation", "detail", "corrective_action"),
    [
        ("remove", "mounted input request.txt is unavailable", "restore the named mounted input bytes"),
        (
            "change",
            "mounted input request.txt is incompatible",
            "restore the declared mounted-input content identity",
        ),
    ],
)
def test_public_capture_reidentifies_mounted_input_before_publication(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    detail: str,
    corrective_action: str,
) -> None:
    mounted = tmp_path / "request.txt"
    mounted.write_bytes(b"request-v1")
    target = cast(dict[str, object], valid_config_data["target"])
    target["mounts"] = [
        {"source": str(mounted), "target": "/work/request.txt", "read_only": True},
    ]
    experiment_path, prepared = _prepared(valid_config_data, tmp_path, monkeypatch)

    class MountedInputMutatingDocker(_Docker):
        def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
            result = super().signal_capture(compose_path, project_name, deadline=deadline)
            if mutation == "remove":
                mounted.unlink()
            else:
                mounted.write_bytes(b"request-v2")
            return result

    docker = MountedInputMutatingDocker("normal")

    with pytest.raises(TrafficlabError, match=detail) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    expected = {
        "affected_evidence": "capture evidence",
        "authority": "primary",
        "corrective_action": corrective_action,
        "detail": detail,
        "evidence_state": "not_published",
        "kind": "docker_preflight_failed",
        "stage": "preflight",
    }
    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.as_dict() == expected
    records = [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_outcome"] == expected
    assert records[-1]["secondary_details"] == []
    assert records[-1]["secondary_failures"] == []
    assert records[-1]["secondary_outcomes"] == []
    for name in (
        "capture.json",
        "reference.pcapng",
        "diagnostic-capture.json",
        "diagnostic-reference.pcapng",
    ):
        assert not (prepared.run_directory / name).exists()
    assert not tuple(prepared.run_directory.glob(".trafficlab-capture-*"))


def test_readiness_and_workload_state_errors_are_classified_at_the_observation_boundary(
    tmp_path: Path,
) -> None:
    """State-query errors must be direct evidence, with simultaneous timeouts retaining both facts."""
    docker = _Docker("normal")

    def fail_state(*args: object, **kwargs: object) -> ServiceState:
        del args, kwargs
        raise TrafficlabError("injected state observation failure", corrective_action="test")

    docker.service_state = fail_state  # type: ignore[method-assign]
    ready = cast(Any, capture_module)._wait_readiness(
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

    timed_out = cast(Any, capture_module)._wait_readiness(
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

    workload, capture = cast(Any, capture_module)._observe_workload(
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
    experiment_path, _prepared_run = _prepared(valid_config_data, tmp_path, monkeypatch)
    scenario = "readiness_timeout" if interruption_point == "readiness" else "partial_start_interrupt"
    docker = _Docker(scenario)
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
            clock=_Clock(docker),
            interruption=(lambda: True) if interruption_point == "readiness" else (lambda: False),
        )

    assert caught.value.exit_code == 130
    assert "injected diagnostic" in str(caught.value)


def test_keyboard_interrupt_before_capture_state_is_known_skips_diagnostic_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a known cleanly closed capture, interruption diagnostics must not be invented."""
    experiment_path, _prepared_run = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker("normal")

    def interrupt_state(*args: object, **kwargs: object) -> ServiceState:
        del args, kwargs
        raise KeyboardInterrupt

    def unexpected_publish(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("unexpected diagnostic publication")

    docker.service_state = interrupt_state  # type: ignore[method-assign]
    monkeypatch.setattr(capture_module, "publish_capture_pair", unexpected_publish)

    with pytest.raises(TrafficlabError) as caught:
        capture_experiment(experiment_path, docker=docker, clock=_Clock(docker), interruption=lambda: False)

    assert caught.value.exit_code == 130


@pytest.mark.parametrize("clock", [lambda: float("nan"), lambda: (_ for _ in ()).throw(OverflowError())])
def test_deadline_rejects_nonfinite_and_arithmetic_failure(clock: object) -> None:
    """An invalid clock must fail directly instead of allowing an unbounded Docker operation."""
    with pytest.raises(TrafficlabError, match="deadline"):
        cast(Any, capture_module)._future_deadline(cast(Any, clock), 1.0, stage="test")


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
    experiment_path, _prepared_run = _prepared(valid_config_data, tmp_path, monkeypatch)
    docker = _Docker(scenario)

    with pytest.raises(TrafficlabError, match=expected):
        capture_experiment(
            experiment_path,
            docker=docker,
            clock=_Clock(docker),
            interruption=cast(Any, interruption),
        )

    assert "start_target" not in docker.calls


def test_total_timeout_and_nonzero_flush_exit_use_their_direct_policy_transitions(tmp_path: Path) -> None:
    """Folding these into stage timeout or success would record the wrong primary failure."""
    docker = _Docker("workload_timeout")
    docker.target_started = True
    outcome, _capture = cast(Any, capture_module)._observe_workload(
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

    docker = _Docker("flush_nonzero")
    docker.capture_signalled = True
    original_state = docker.service_state

    def nonzero_capture(compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        if service == "capture":
            return ServiceState("capture", "capture", "capture", "exited", 9)
        return original_state(compose_path, project_name, service, deadline=deadline)

    docker.service_state = nonzero_capture  # type: ignore[method-assign]
    flushed = cast(Any, capture_module)._flush_capture(
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


def test_readiness_and_workload_poll_again_when_no_event_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Healthy intermediate observations must loop instead of inventing a lifecycle event."""
    docker = _Docker("normal")
    readiness_checks = iter((False, True))

    def capture_ready(*args: object) -> bool:
        return next(readiness_checks)

    monkeypatch.setattr(capture_module, "_capture_ready", capture_ready)

    ready = cast(Any, capture_module)._wait_readiness(
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
    observed, _capture = cast(Any, capture_module)._observe_workload(
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


@pytest.mark.parametrize("operation", ["signal", "state", "kill"])
@pytest.mark.parametrize("target_status", [0, 23], ids=["target-zero", "target-nonzero"])
def test_flush_docker_errors_are_contextual_and_preserve_natural_target_precedence(
    tmp_path: Path, operation: str, target_status: int
) -> None:
    """An escaped boundary error would be mislabeled or dropped instead of becoming ordered flush evidence."""
    suffix = "_nonzero" if target_status else ""
    docker = _Docker(f"flush_{operation}_error{suffix}")
    outcome = record_natural_target_status(CaptureOutcome(), target_status)

    result = cast(Any, capture_module)._flush_capture(
        docker,
        tmp_path / "compose.json",
        "trafficlab-capture-test",
        {},
        outcome,
        stage_deadline=105.0,
        total_deadline=160.0,
        clock=_Clock(docker),
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
    docker = _Docker(scenario)
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 100.0

    result = cast(Any, capture_module)._flush_capture(
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
    docker = _Docker("flush_timeout")
    readings = iter((100.0, 160.0))

    result = cast(Any, capture_module)._flush_capture(
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
    docker = _Docker("normal")

    result = cast(Any, capture_module)._flush_capture(
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
    docker = _Docker("normal")

    def expired(*args: object, deadline: float, **kwargs: object) -> CommandResult | ServiceState:
        del args, kwargs
        assert deadline == min(stage_deadline, total_deadline)
        raise TrafficlabError("Docker command deadline expired before launch", corrective_action="test")

    if operation == "signal":
        docker.signal_capture = expired  # type: ignore[method-assign]
    else:
        docker.service_state = expired  # type: ignore[method-assign]
    readings = iter((100.0, min(stage_deadline, total_deadline)))

    result = cast(Any, capture_module)._flush_capture(
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
    before_signal = _Docker("normal")

    expired = cast(Any, capture_module)._flush_capture(
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

    while_waiting = _Docker("flush_timeout")
    readings = iter((100.0, 160.0))
    expired = cast(Any, capture_module)._flush_capture(
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
    docker = _Docker("normal")
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
    outcome = cast(Any, capture_module)._flush_capture(
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
