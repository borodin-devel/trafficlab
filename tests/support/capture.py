import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest
import tomli_w

import trafficlab.artifacts.io as artifact_io
import trafficlab.capture.lineage as lineage_module
import trafficlab.capture.stage as capture_module
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.capture.docker.types import CommandResult, ServiceState
from trafficlab.common.compatibility import identify_file
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata
from trafficlab.preflight.stage import (
    CaptureEnvironmentIdentity,
    PreparedExperiment,
    open_or_prepare_experiment,
)


@dataclass
class CleanupHandle:
    returncode: int = 0

    def wait(self, *, timeout: float) -> CommandResult:
        return CommandResult(self.returncode, "", "cleanup failed" if self.returncode else "")

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def reap(self) -> bool:
        return True


class DockerDouble:
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

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> CleanupHandle:
        self._record("start_down")
        return CleanupHandle(returncode=5 if "cleanup_failure" in self.scenario else 0)


class Clock:
    def __init__(self, docker: DockerDouble) -> None:
        self.docker = docker

    def __call__(self) -> float:
        if self.docker.scenario.endswith("cleanup_clock_error") and self.docker.calls[-1:] == ["start_down"]:
            return float("nan")
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


def prepared_capture(
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
                mounted_inputs=lineage_module.identify_mounted_inputs(prepared.config),
            ),
        ),
    )

    def already_prepared(*args: object, **kwargs: object) -> PreparedExperiment:
        return prepared

    monkeypatch.setattr(capture_module, "run_preflight", already_prepared)
    return experiment_path, prepared


def seed_capture_lineage(prepared: PreparedExperiment) -> None:
    environment = prepared.report.environment_identity
    assert environment is not None
    artifact_io.append_run_log(
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
