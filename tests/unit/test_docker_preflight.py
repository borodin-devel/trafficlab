import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

import trafficlab.cleanup as cleanup_module
import trafficlab.preflight as preflight_module
from trafficlab.cleanup import CleanupResult
from trafficlab.config import ExperimentConfig
from trafficlab.docker_cli import CommandResult, ProjectInventory, ServiceState
from trafficlab.errors import TrafficlabError
from trafficlab.pcapng import encode_pcapng
from trafficlab.preflight import check_docker
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata

_CAPTURE_IMAGE_ID = "sha256:854b21990ba8c1a566c0b5f5abaef8d72840cbf4a0ebb22230da7127462ed602"
_TARGET_IMAGE_ID = "sha256:" + ("c" * 64)


@dataclass
class _CleanupHandle:
    result: CommandResult | None = CommandResult(0, "", "")
    waits: list[float] | None = None
    terminated: bool = False
    killed: bool = False

    def wait(self, *, timeout: float) -> CommandResult | None:
        if self.waits is None:
            self.waits = []
        self.waits.append(timeout)
        return self.result

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class _Docker:
    def __init__(
        self,
        *,
        missing_images: set[str] | None = None,
        failure: tuple[str, TrafficlabError] | None = None,
        target_exit: int = 0,
        cleanup: _CleanupHandle | None = None,
        image_ids: dict[str, str] | None = None,
        image_references: dict[str, str] | None = None,
    ) -> None:
        self.missing_images = set(missing_images or ())
        self.failure = failure
        self.target_exit = target_exit
        self.cleanup = cleanup or _CleanupHandle()
        self.image_ids = dict(image_ids or {})
        self.image_references = dict(image_references or {})
        self.calls: list[tuple[str, tuple[object, ...], float]] = []
        self.documents: list[dict[str, object]] = []
        self.capture_signalled = False

    def _record(self, name: str, *args: object, deadline: float) -> None:
        self.calls.append((name, args, deadline))
        if self.failure is not None and self.failure[0] == name:
            raise self.failure[1]

    def info(self, *, deadline: float) -> CommandResult:
        self._record("info", deadline=deadline)
        return CommandResult(0, "daemon ready", "")

    def compose_version(self, *, deadline: float) -> CommandResult:
        self._record("compose_version", deadline=deadline)
        return CommandResult(0, "Docker Compose version v2", "")

    def image_inspect(self, image: str, *, deadline: float) -> CommandResult:
        self._record("image_inspect", image, deadline=deadline)
        if image in self.missing_images:
            raise TrafficlabError("image is absent", corrective_action="pull it")
        default_id = _CAPTURE_IMAGE_ID if image.startswith("trafficlab-capture:") else _TARGET_IMAGE_ID
        return CommandResult(
            0,
            json.dumps(
                [
                    {
                        "Id": self.image_ids.get(image, default_id),
                        "RepoDigests": [],
                        "RepoTags": [self.image_references.get(image, image)],
                    }
                ]
            ),
            "",
        )

    def image_pull(self, image: str, *, deadline: float) -> CommandResult:
        self._record("image_pull", image, deadline=deadline)
        self.missing_images.discard(image)
        return CommandResult(0, "pulled", "")

    def config(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("config", compose_path, project_name, deadline=deadline)
        self.documents.append(cast(dict[str, object], json.loads(compose_path.read_bytes())))
        return CommandResult(0, "{}", "")

    def create_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("create_capture", compose_path, project_name, deadline=deadline)
        document = cast(dict[str, object], json.loads(compose_path.read_bytes()))
        services = cast(dict[str, object], document["services"])
        capture = cast(dict[str, object], services["capture"])
        volume = cast(dict[str, object], cast(list[object], capture["volumes"])[0])
        output = Path(cast(str, volume["source"]))
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
        return CommandResult(0, "", "")

    def start_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("start_capture", compose_path, project_name, deadline=deadline)
        return CommandResult(0, "", "")

    def start_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("start_target", compose_path, project_name, deadline=deadline)
        return CommandResult(0, "", "")

    def service_state(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> ServiceState:
        self._record("service_state", compose_path, project_name, service, deadline=deadline)
        if service == "target":
            return ServiceState("target-id", f"{project_name}-target-1", "target", "exited", self.target_exit)
        state = "exited" if self.capture_signalled else "running"
        return ServiceState("capture-id", f"{project_name}-capture-1", "capture", state, 0)

    def service_logs(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> str:
        self._record("service_logs", compose_path, project_name, service, deadline=deadline)
        return "probe detail"

    def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        self._record("signal_capture", compose_path, project_name, deadline=deadline)
        self.capture_signalled = True
        return CommandResult(0, "", "")

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> _CleanupHandle:
        self._record("start_down", compose_path, project_name, deadline=deadline)
        return self.cleanup

    def project_inventory(self, compose_path: Path, project_name: str, *, deadline: float) -> ProjectInventory:
        self._record("project_inventory", compose_path, project_name, deadline=deadline)
        return ProjectInventory(containers=())


def _config(valid_config_data: dict[str, object], tmp_path: Path) -> ExperimentConfig:
    data = dict(valid_config_data)
    run = dict(cast(dict[str, object], data["run"]))
    run["directory"] = str(tmp_path / "run")
    data["run"] = run
    config = ExperimentConfig.model_validate(data)
    config.run.directory.mkdir()
    return config


def _names(docker: _Docker) -> list[str]:
    return [name for name, _args, _deadline in docker.calls]


def test_full_docker_preflight_checks_images_topology_capture_and_network(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Skipping any stage could report readiness without the actual images, topology, capture tool, or network."""
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(missing_images={config.target.image, config.capture.image})

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert all(finding.ok for finding in report.findings)
    assert [finding.name for finding in report.findings] == [
        "capture_image_lock",
        "capture_platform",
        "docker_daemon",
        "docker_compose",
        "target_image",
        "capture_image",
        "compose_config",
        "network_probe",
        "probe_cleanup",
    ]
    assert report.environment_identity is not None
    assert report.environment_identity.target_reference == config.target.image
    assert report.environment_identity.target_content_id == _TARGET_IMAGE_ID
    assert report.environment_identity.capture_reference == config.capture.image
    assert report.environment_identity.capture_content_id == _CAPTURE_IMAGE_ID
    assert report.environment_identity.capture_tool_version == "4.0.17"
    assert report.environment_identity.host_architecture == "linux/amd64"
    assert _names(docker) == [
        "info",
        "compose_version",
        "image_inspect",
        "image_pull",
        "image_inspect",
        "image_inspect",
        "image_pull",
        "image_inspect",
        "config",
        "config",
        "create_capture",
        "start_capture",
        "service_state",
        "start_target",
        "service_state",
        "service_state",
        "signal_capture",
        "service_state",
        "start_down",
        "project_inventory",
    ]
    assert {deadline for _name, _args, deadline in docker.calls} == {160.0}
    production, probe = docker.documents
    assert set(cast(dict[str, object], production["services"])) == {"capture", "target"}
    production_target = cast(dict[str, object], cast(dict[str, object], production["services"])["target"])
    assert production_target["image"] == config.target.image
    assert production_target["command"] == list(config.target.argv)
    production_capture = cast(dict[str, object], cast(dict[str, object], production["services"])["capture"])
    capture_mount = cast(dict[str, object], cast(list[object], production_capture["volumes"])[0])
    assert Path(cast(str, capture_mount["source"])).is_absolute()
    probe_services = cast(dict[str, object], probe["services"])
    probe_target = cast(dict[str, object], probe_services["target"])
    assert set(probe_services) == {"capture", "target"}
    assert probe_target["image"] == config.capture.image
    entrypoint = cast(list[object], probe_target["entrypoint"])
    assert entrypoint[-1] == config.capture.network_probe_url
    assert entrypoint[0] == "curl"
    assert probe_target["command"] == []
    assert probe_target["network_mode"] == "service:capture"
    assert probe_target["init"] is True
    assert docker.calls[-1][1][1] == docker.calls[-2][1][1]
    project_names = [cast(str, args[1]) for name, args, _deadline in docker.calls if name == "config"]
    assert project_names[0] != project_names[1]
    assert project_names[1].startswith("trafficlab-preflight-")
    assert docker.cleanup.waits == [59.0]


def test_capture_image_identity_mismatch_stops_before_compose_probe(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(image_ids={config.capture.image: "sha256:" + ("d" * 64)})

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.environment_identity is None
    assert report.findings[-1].name == "capture_image"
    assert report.findings[-1].ok is False
    assert "expected capture image" in report.findings[-1].detail
    assert "config" not in _names(docker)


def test_target_reference_mismatch_stops_before_capture_probe(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(image_references={config.target.image: "unexpected-target:test"})

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.environment_identity is None
    assert report.findings[-1].name == "target_image"
    assert report.findings[-1].ok is False
    assert "does not match requested reference" in report.findings[-1].detail
    assert _names(docker).count("image_inspect") == 1


@pytest.mark.parametrize("host_architecture", ["aarch64", "arm64", "", "unknown"])
def test_unsupported_capture_platform_stops_before_docker(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_architecture: str,
) -> None:
    config = _config(valid_config_data, tmp_path)
    docker = _Docker()
    monkeypatch.setattr(preflight_module.platform, "machine", lambda: host_architecture)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert [finding.name for finding in report.findings] == [
        "capture_image_lock",
        "capture_platform",
    ]
    assert report.findings[0].ok is True
    assert report.findings[1].ok is False
    assert "linux/amd64" in report.findings[1].detail
    assert report.findings[1].corrective_action is not None
    assert "linux/amd64" in report.findings[1].corrective_action
    assert docker.calls == []


def test_invalid_checked_capture_inputs_stop_before_docker(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(valid_config_data, tmp_path)
    docker = _Docker()
    monkeypatch.setattr(
        preflight_module,
        "_CAPTURE_IMAGE_LOCK_PATH",
        tmp_path / "missing-image-lock.json",
    )

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert len(report.findings) == 1
    assert report.findings[0].name == "capture_image_lock"
    assert report.findings[0].ok is False
    assert "cannot read" in report.findings[0].detail
    assert docker.calls == []


def test_probe_project_name_is_unique_across_full_preflight_runs(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Reusing a fixed probe name could tear down another concurrent preflight project."""
    config = _config(valid_config_data, tmp_path)
    first = _Docker()
    second = _Docker()

    assert all(finding.ok for finding in check_docker(config, first, deadline=160.0, clock=lambda: 100.0).findings)
    assert all(finding.ok for finding in check_docker(config, second, deadline=160.0, clock=lambda: 100.0).findings)

    first_probe = next(cast(str, args[1]) for name, args, _deadline in first.calls if name == "start_down")
    second_probe = next(cast(str, args[1]) for name, args, _deadline in second.calls if name == "start_down")
    assert first_probe != second_probe


@pytest.mark.parametrize("operation", ["info", "compose_version", "config"])
def test_full_preflight_returns_the_direct_boundary_failure_without_later_stages(
    valid_config_data: dict[str, object], tmp_path: Path, operation: str
) -> None:
    """Hiding a precise Docker error behind later checks would make the first failure harder to diagnose."""
    config = _config(valid_config_data, tmp_path)
    failure = TrafficlabError(f"{operation} unavailable", corrective_action=f"repair {operation}")
    docker = _Docker(failure=(operation, failure))

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    failed = [finding for finding in report.findings if not finding.ok]
    assert len(failed) == 1
    assert failed[0].detail == str(failure)
    with pytest.raises(TrafficlabError, match=f"{operation} unavailable") as caught:
        report.require_success()
    assert caught.value.corrective_action == f"repair {operation}"
    if operation != "config":
        assert "start_down" not in _names(docker)


def test_probe_failure_remains_primary_when_cleanup_also_fails(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A secondary cleanup failure must not replace the target probe status that caused preflight to fail."""
    config = _config(valid_config_data, tmp_path)
    cleanup = _CleanupHandle(result=CommandResult(19, "", "cleanup failed"))
    docker = _Docker(target_exit=7, cleanup=cleanup)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    failures = [finding for finding in report.findings if not finding.ok]
    assert [finding.name for finding in failures] == ["network_probe", "probe_cleanup"]
    assert "status 7" in failures[0].detail
    assert "status 19" in failures[1].detail
    with pytest.raises(TrafficlabError, match="status 7.*status 19") as caught:
        report.require_success()
    assert "probe endpoint" in caught.value.corrective_action
    assert _names(docker)[-2:] == ["start_down", "project_inventory"]


@pytest.mark.parametrize(
    ("failure_operation", "target_exit", "expected_states"),
    [
        (None, 0, (("capture", "exited", 0), ("target", "exited", 0))),
        ("create_capture", 0, ()),
        ("start_capture", 0, ()),
        ("start_target", 0, (("capture", "running", 0),)),
        (None, 7, (("capture", "running", 0), ("target", "exited", 7))),
        ("signal_capture", 0, (("capture", "running", 0), ("target", "exited", 0))),
    ],
    ids=[
        "success",
        "create-may-have-partially-succeeded",
        "after-create",
        "capture-observed",
        "target-observed-nonzero",
        "both-observed",
    ],
)
def test_probe_cleanup_receives_the_freshest_observed_service_inventory(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_operation: str | None,
    target_exit: int,
    expected_states: tuple[tuple[str, str, int], ...],
) -> None:
    """Discarding observed service identities would make zero-budget or hanging cleanup report empty evidence."""
    config = _config(valid_config_data, tmp_path)
    failure = None
    if failure_operation is not None:
        failure = (
            failure_operation,
            TrafficlabError(f"{failure_operation} failed", corrective_action="repair probe"),
        )
    docker = _Docker(failure=failure, target_exit=target_exit)
    captured: list[ProjectInventory] = []

    def controlled_cleanup(
        compose: object,
        compose_path: Path,
        project_name: str,
        last_known_inventory: ProjectInventory,
        *,
        deadline: float,
        clock: object,
    ) -> CleanupResult:
        captured.append(last_known_inventory)
        return CleanupResult(
            success=False,
            timed_out=True,
            detail="controlled cleanup timeout",
            possibly_remaining=last_known_inventory,
        )

    monkeypatch.setattr(cleanup_module, "cleanup_project", controlled_cleanup)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    probe_name = [cast(str, args[1]) for name, args, _deadline in docker.calls if name == "config"][1]
    expected = ProjectInventory(
        containers=tuple(
            ServiceState(
                f"{service}-id",
                f"{probe_name}-{service}-1",
                service,
                state,
                status,
            )
            for service, state, status in expected_states
        ),
        networks=(f"{probe_name}_default",),
    )
    assert captured == [expected]
    failures = [finding.name for finding in report.findings if not finding.ok]
    assert failures[-1] == "probe_cleanup"


def test_probe_cleanup_finding_keeps_primary_before_secondary_cleanup_detail(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping cleanup secondary detail would hide why its known command failure could not be verified."""
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(target_exit=7)

    def controlled_cleanup(
        compose: object,
        compose_path: Path,
        project_name: str,
        last_known_inventory: ProjectInventory,
        *,
        deadline: float,
        clock: object,
    ) -> CleanupResult:
        return CleanupResult(
            success=False,
            timed_out=False,
            detail="cleanup command failed with status 23",
            possibly_remaining=last_known_inventory,
            secondary_details=("inventory query failed",),
        )

    monkeypatch.setattr(cleanup_module, "cleanup_project", controlled_cleanup)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    failures = [finding for finding in report.findings if not finding.ok]
    assert [finding.name for finding in failures] == ["network_probe", "probe_cleanup"]
    assert failures[1].detail == "cleanup command failed with status 23; secondary: inventory query failed"


def test_total_deadline_exhaustion_stops_before_the_next_docker_action(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Launching another Docker command after the total deadline would make preflight unbounded."""
    config = _config(valid_config_data, tmp_path)
    times = iter((100.0, 161.0))
    docker = _Docker()

    report = check_docker(config, docker, deadline=160.0, clock=times.__next__)

    assert not report.findings[-1].ok
    assert "deadline" in report.findings[-1].detail
    assert _names(docker) == ["info"]


def test_disposable_workspace_failure_is_returned_as_an_actionable_finding(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaking a raw filesystem error would turn an expected preflight failure into a traceback."""
    config = _config(valid_config_data, tmp_path)
    docker = _Docker()

    def fail_workspace(*args: object, **kwargs: object) -> object:
        raise PermissionError("workspace denied")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", fail_workspace)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.findings[-1].name == "compose_config"
    assert not report.findings[-1].ok
    assert "workspace denied" in report.findings[-1].detail
    with pytest.raises(TrafficlabError, match="workspace denied"):
        report.require_success()
