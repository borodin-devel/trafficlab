import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
import tomli_w

import trafficlab.capture.stage as capture_module
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.capture.compose import ComposePaths, write_production_compose
from trafficlab.capture.docker_cli import CommandResult, ServiceState
from trafficlab.capture.stage import CaptureResult, capture_experiment
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata
from trafficlab.preflight.stage import run_preflight

pytestmark = pytest.mark.integration


@dataclass
class _CleanupHandle:
    def wait(self, *, timeout: float) -> CommandResult:
        assert timeout > 0.0
        return CommandResult(0, "", "")

    def terminate(self) -> None:
        raise AssertionError("successful cleanup was terminated")

    def kill(self) -> None:
        raise AssertionError("successful cleanup was killed")

    def reap(self) -> bool:
        return True


class _HappyDocker:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.production_created = False
        self.capture_signalled = False
        self.production_images: tuple[str, str] | None = None

    def _production(self, project_name: str) -> bool:
        return project_name.startswith("trafficlab-capture-")

    def _output(self, compose_path: Path) -> Path:
        document = cast(dict[str, object], json.loads(compose_path.read_bytes()))
        services = cast(dict[str, object], document["services"])
        capture = cast(dict[str, object], services["capture"])
        volume = cast(dict[str, object], cast(list[object], capture["volumes"])[0])
        return Path(cast(str, volume["source"]))

    def info(self, *, deadline: float) -> CommandResult:
        return CommandResult(0, json.dumps({"Architecture": "x86_64", "OSType": "linux"}), "")

    def compose_version(self, *, deadline: float) -> CommandResult:
        return CommandResult(0, json.dumps({"version": "v5.4.0"}), "")

    def image_inspect(self, image: str, *, deadline: float) -> CommandResult:
        content_id = (
            "sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c"
            if image.startswith("trafficlab-capture:")
            else "sha256:" + ("c" * 64)
        )
        return CommandResult(
            0,
            json.dumps(
                [
                    {
                        "Id": content_id,
                        "Architecture": "amd64",
                        "Os": "linux",
                        "RepoDigests": [],
                        "RepoTags": [image],
                    }
                ]
            ),
            "",
        )

    def image_pull(self, image: str, *, deadline: float) -> CommandResult:
        raise AssertionError(f"available image was pulled: {image}")

    def config(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        return CommandResult(0, "{}", "")

    def create_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        output = self._output(compose_path)
        metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
        (output / "capture.json").write_bytes(render_capture_metadata(metadata))
        (output / "reference.pcapng.tmp").write_bytes(
            encode_pcapng(
                (
                    TraceEvent(0.0, Direction.OUTBOUND, 64),
                    TraceEvent(0.1, Direction.INBOUND, 96),
                ),
                metadata,
            )
        )
        if self._production(project_name):
            self.events.append("create_capture")
            self.production_created = True
        return CommandResult(0, "", "")

    def start_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        if self._production(project_name):
            self.capture_signalled = False
            self.events.append("start_capture")
        return CommandResult(0, "", "")

    def start_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        if self._production(project_name):
            self.events.append("start_target")
        return CommandResult(0, "", "")

    def service_state(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        if self._production(project_name):
            self.events.append(f"observe_{service}")
        if service == "target":
            return ServiceState("target-id", f"{project_name}-target-1", "target", "exited", 0)
        state = "exited" if self.capture_signalled else "running"
        return ServiceState("capture-id", f"{project_name}-capture-1", "capture", state, 0)

    def service_logs(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> str:
        return "capture log"

    def kill_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        raise AssertionError("successful target was killed")

    def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        if self._production(project_name):
            self.events.append("signal_capture")
        self.capture_signalled = True
        return CommandResult(0, "", "")

    def kill_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        raise AssertionError("successful capture was killed")

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> _CleanupHandle:
        if self._production(project_name):
            self.events.append("cleanup")
        return _CleanupHandle()


class _Clock:
    def __init__(self, docker: _HappyDocker, events: list[str]) -> None:
        self.docker = docker
        self.events = events
        self.deadline_started = False

    def __call__(self) -> float:
        if self.docker.production_created and not self.deadline_started:
            self.events.append("start_total_deadline")
            self.deadline_started = True
        return 100.0


def test_capture_happy_path_orders_full_lifecycle_and_publishes_reference(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reordering lifecycle stages could start workload early, lose packets, or publish before teardown."""
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    events: list[str] = []
    docker = _HappyDocker(events)
    clock = _Clock(docker, events)

    def traced_preflight(*args: object, **kwargs: object) -> object:
        prepared = run_preflight(*args, **kwargs)  # type: ignore[arg-type]
        (prepared.run_directory / "diagnostic-capture.json").write_bytes(b"stale metadata")
        (prepared.run_directory / "diagnostic-reference.pcapng").write_bytes(b"stale pcapng")
        events.append("run_preflight")
        return prepared

    def traced_write(
        path: Path,
        config: ExperimentConfig,
        paths: ComposePaths,
        *,
        target_image: str,
        capture_image: str,
    ) -> None:
        docker.production_images = (target_image, capture_image)
        write_production_compose(
            path,
            config,
            paths,
            target_image=target_image,
            capture_image=capture_image,
        )
        events.append("render_write_compose")

    original_publish = capture_module.publish_capture_pair

    def traced_publish(*args: object, **kwargs: object) -> object:
        events.append("validate_publish")
        return original_publish(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(capture_module, "run_preflight", traced_preflight)
    monkeypatch.setattr(capture_module, "write_production_compose", traced_write)
    monkeypatch.setattr(capture_module, "publish_capture_pair", traced_publish)

    result = capture_experiment(
        experiment_path,
        docker=docker,
        clock=clock,
        interruption=lambda: False,
    )

    assert type(result) is CaptureResult
    assert result.reference_path == Path(cast(str, cast(dict[str, object], valid_config_data["run"])["directory"])) / (
        "reference.pcapng"
    )
    assert result.packet_count == 2
    assert result.target_status == 0
    assert docker.production_images == (
        "sha256:" + ("c" * 64),
        "sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c",
    )
    assert events == [
        "run_preflight",
        "run_preflight",
        "render_write_compose",
        "create_capture",
        "start_total_deadline",
        "start_capture",
        "observe_capture",
        "start_target",
        "observe_target",
        "observe_capture",
        "signal_capture",
        "observe_capture",
        "validate_publish",
        "cleanup",
    ]
    assert (result.run_directory / "capture.json").exists()
    assert result.reference_path.exists()
    assert {path.name for path in result.run_directory.iterdir()} == {
        "capture.json",
        "experiment.toml",
        "reference.pcapng",
        "run.log",
    }
    assert all(
        ".tmp" not in path.name and "quarantine" not in path.name and ".trafficlab-capture-" not in path.name
        for path in result.run_directory.iterdir()
    )
