import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

import trafficlab.preflight as preflight_module
from trafficlab import USER_AGENT
from trafficlab.config import ExperimentConfig
from trafficlab.docker_cli import CommandResult, ServiceState
from trafficlab.errors import TrafficlabError
from trafficlab.pcapng import encode_pcapng
from trafficlab.preflight import check_docker
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata

_CAPTURE_IMAGE_ID = "sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c"
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

    def reap(self) -> bool:
        return True


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
        daemon_os: str = "linux",
        daemon_architecture: str = "x86_64",
        image_os: str = "linux",
        image_architecture: str = "amd64",
        image_platforms: dict[str, tuple[str, str]] | None = None,
        compose_version_stdout: str = '{"version":"v5.4.0"}',
        compose_version_returncode: int = 0,
        info_returncode: int = 0,
        config_returncode: int = 0,
    ) -> None:
        self.missing_images = set(missing_images or ())
        self.failure = failure
        self.target_exit = target_exit
        self.cleanup = cleanup or _CleanupHandle()
        self.image_ids = dict(image_ids or {})
        self.image_references = dict(image_references or {})
        self.daemon_os = daemon_os
        self.daemon_architecture = daemon_architecture
        self.image_os = image_os
        self.image_architecture = image_architecture
        self.image_platforms = dict(image_platforms or {})
        self.compose_version_stdout = compose_version_stdout
        self.compose_version_returncode = compose_version_returncode
        self.info_returncode = info_returncode
        self.config_returncode = config_returncode
        self.calls: list[tuple[str, tuple[object, ...], float]] = []
        self.documents: list[dict[str, object]] = []
        self.capture_signalled = False

    def _record(self, name: str, *args: object, deadline: float) -> None:
        self.calls.append((name, args, deadline))
        if self.failure is not None and self.failure[0] == name:
            raise self.failure[1]

    def info(self, *, deadline: float) -> CommandResult:
        self._record("info", deadline=deadline)
        return CommandResult(
            self.info_returncode,
            json.dumps({"Architecture": self.daemon_architecture, "OSType": self.daemon_os}),
            "",
        )

    def compose_version(self, *, deadline: float) -> CommandResult:
        self._record("compose_version", deadline=deadline)
        return CommandResult(self.compose_version_returncode, self.compose_version_stdout, "")

    def image_inspect(self, image: str, *, deadline: float) -> CommandResult:
        self._record("image_inspect", image, deadline=deadline)
        if image in self.missing_images:
            raise TrafficlabError("image is absent", corrective_action="pull it")
        default_id = _CAPTURE_IMAGE_ID if image.startswith("trafficlab-capture:") else _TARGET_IMAGE_ID
        image_os, image_architecture = self.image_platforms.get(
            image,
            (self.image_os, self.image_architecture),
        )
        return CommandResult(
            0,
            json.dumps(
                [
                    {
                        "Id": self.image_ids.get(image, default_id),
                        "Architecture": image_architecture,
                        "Os": image_os,
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
        return CommandResult(self.config_returncode, "{}", "")

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


def _config(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    *,
    mounts: list[dict[str, object]] | None = None,
) -> ExperimentConfig:
    data = dict(valid_config_data)
    run = dict(cast(dict[str, object], data["run"]))
    run["directory"] = str(tmp_path / "run")
    data["run"] = run
    if mounts is not None:
        target = dict(cast(dict[str, object], data["target"]))
        target["mounts"] = mounts
        data["target"] = target
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
        "docker_daemon",
        "capture_platform",
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
    ]
    assert {deadline for _name, _args, deadline in docker.calls} == {160.0}
    production, probe = docker.documents
    assert set(cast(dict[str, object], production["services"])) == {"capture", "target"}
    production_target = cast(dict[str, object], cast(dict[str, object], production["services"])["target"])
    assert production_target["image"] == _TARGET_IMAGE_ID
    assert production_target["command"] == list(config.target.argv)
    production_capture = cast(dict[str, object], cast(dict[str, object], production["services"])["capture"])
    assert production_capture["image"] == _CAPTURE_IMAGE_ID
    capture_mount = cast(dict[str, object], cast(list[object], production_capture["volumes"])[0])
    assert Path(cast(str, capture_mount["source"])).is_absolute()
    probe_services = cast(dict[str, object], probe["services"])
    probe_target = cast(dict[str, object], probe_services["target"])
    assert set(probe_services) == {"capture", "target"}
    assert probe_target["image"] == _CAPTURE_IMAGE_ID
    probe_capture = cast(dict[str, object], probe_services["capture"])
    assert probe_capture["image"] == _CAPTURE_IMAGE_ID
    entrypoint = cast(list[object], probe_target["entrypoint"])
    assert entrypoint == [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--user-agent",
        USER_AGENT,
        "--connect-timeout",
        "60",
        "--max-time",
        "60",
        "--range",
        "0-0",
        "--output",
        "/dev/null",
        config.capture.network_probe_url,
    ]
    assert probe_target["command"] == []
    assert probe_target["network_mode"] == "service:capture"
    assert probe_target["init"] is True
    assert docker.calls[-1][1][1] == docker.calls[-2][1][1]
    project_names = [cast(str, args[1]) for name, args, _deadline in docker.calls if name == "config"]
    assert project_names[0] != project_names[1]
    assert project_names[1].startswith("trafficlab-preflight-")
    assert docker.cleanup.waits == [59.0]


@pytest.mark.parametrize("version", ["v2.29.0", "v5.4.0"])
def test_full_docker_preflight_accepts_supported_compose_version_json_and_reaches_live_feature_probe(
    valid_config_data: dict[str, object], tmp_path: Path, version: str
) -> None:
    """Supported Compose v2/v5 plugins must reach the required live feature probes."""
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(compose_version_stdout=f'{{"version":"{version}"}}')

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert all(finding.ok for finding in report.findings)
    assert docker.calls[1] == ("compose_version", (), 160.0)
    assert _names(docker) == [
        "info",
        "compose_version",
        "image_inspect",
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
    ]


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [
        ("not JSON", 0),
        ("[]", 0),
        ('{"version":""}', 0),
        ('{"version":false}', 0),
        ('{"version":"v5.4.0"}', 1),
        ('{"version":"v1.29.2"}', 0),
        ('{"version":"v3.0.0"}', 0),
        ('{"version":"v4.0.0"}', 0),
        ('{"version":"v6.0.0"}', 0),
        ('{"version":"not-a-version"}', 0),
    ],
)
def test_full_docker_preflight_rejects_malformed_or_unsupported_compose_version_before_feature_probe(
    valid_config_data: dict[str, object], tmp_path: Path, stdout: str, returncode: int
) -> None:
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(compose_version_stdout=stdout, compose_version_returncode=returncode)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.findings[-1].name == "docker_compose"
    assert report.findings[-1].detail == "Docker Compose version is incompatible"
    assert _names(docker) == ["info", "compose_version"]


def test_full_docker_preflight_rejects_a_nonzero_docker_info_result(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(info_returncode=1)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.findings[-1].name == "docker_daemon"
    assert report.findings[-1].detail == "Docker Engine is unavailable"
    assert _names(docker) == ["info"]


def test_full_docker_preflight_stops_before_compose_for_an_unavailable_mount(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    missing_source = tmp_path / "missing-input"
    config = _config(
        valid_config_data,
        tmp_path,
        mounts=[{"source": str(missing_source), "target": "/work/input", "read_only": True}],
    )
    docker = _Docker()

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.findings[-1].name == "mounts"
    assert not report.findings[-1].ok
    assert _names(docker) == ["info", "compose_version", "image_inspect", "image_inspect"]


def test_full_docker_preflight_attributes_a_nonzero_compose_config_to_the_mount(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    mount_source = tmp_path / "input"
    mount_source.write_bytes(b"input")
    config = _config(
        valid_config_data,
        tmp_path,
        mounts=[{"source": str(mount_source), "target": "/work/input", "read_only": True}],
    )
    docker = _Docker(config_returncode=1)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.findings[-1].name == "compose_config"
    assert report.findings[-1].detail == "mount target /work/input is incompatible"
    assert _names(docker) == ["info", "compose_version", "image_inspect", "image_inspect", "config"]


def test_capture_image_identity_mismatch_stops_before_compose_probe(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(image_ids={config.capture.image: "sha256:" + ("d" * 64)})

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.environment_identity is None
    assert report.findings[-1].name == "capture_image"
    assert report.findings[-1].ok is False
    assert report.findings[-1].detail == "capture image identity is incompatible"
    assert report.findings[-1].corrective_action == "restore the declared image content identity and architecture"
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


@pytest.mark.parametrize(
    ("image_kind", "operating_system", "architecture", "expected_finding"),
    [
        ("target", "linux", "arm64", "target_image"),
        ("capture", "windows", "amd64", "capture_image"),
    ],
)
def test_unsupported_image_platform_stops_at_its_owning_preflight_stage(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    image_kind: str,
    operating_system: str,
    architecture: str,
    expected_finding: str,
) -> None:
    config = _config(valid_config_data, tmp_path)
    image = config.target.image if image_kind == "target" else config.capture.image
    docker = _Docker(image_platforms={image: (operating_system, architecture)})

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.environment_identity is None
    assert report.findings[-1].name == expected_finding
    assert report.findings[-1].ok is False
    assert "linux/amd64" in report.findings[-1].detail
    assert "config" not in _names(docker)


@pytest.mark.parametrize(
    ("daemon_os", "daemon_architecture"),
    [("linux", "arm64"), ("windows", "amd64"), ("", "amd64"), ("linux", "")],
)
def test_unsupported_remote_capture_platform_stops_before_compose_or_images(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    daemon_os: str,
    daemon_architecture: str,
) -> None:
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(daemon_os=daemon_os, daemon_architecture=daemon_architecture)

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert [finding.name for finding in report.findings] == [
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
    ]
    assert report.findings[0].ok is True
    assert report.findings[1].ok is True
    assert report.findings[2].ok is False
    assert "linux/amd64" in report.findings[2].detail
    assert report.findings[2].corrective_action is not None
    assert "linux/amd64" in report.findings[2].corrective_action
    assert _names(docker) == ["info"]


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
    expected_detail = {
        "info": "Docker Engine is unavailable",
        "compose_version": "Docker Compose version is incompatible",
    }.get(operation, str(failure))
    expected_action = {
        "info": "restore Docker Engine and Compose availability",
        "compose_version": "provide the named required Docker and Compose features",
    }.get(operation, f"repair {operation}")
    assert failed[0].detail == expected_detail
    with pytest.raises(TrafficlabError, match=expected_detail) as caught:
        report.require_success()
    assert caught.value.corrective_action == expected_action
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
    assert failures[0].detail == "capture prerequisite is unavailable"
    assert "status 19" in failures[1].detail
    with pytest.raises(TrafficlabError, match="capture prerequisite is unavailable.*status 19") as caught:
        report.require_success()
    assert caught.value.corrective_action == "make the named prerequisite available"
    assert _names(docker)[-1:] == ["start_down"]


def test_probe_failure_remains_primary_when_cleanup_clock_fails_after_launch(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A malformed post-launch cleanup clock must become a secondary finding, not escape preflight finally."""
    config = _config(valid_config_data, tmp_path)
    docker = _Docker(target_exit=7)

    def cleanup_clock() -> float:
        return float("nan") if _names(docker)[-1:] == ["start_down"] else 100.0

    report = check_docker(config, docker, deadline=160.0, clock=cleanup_clock)

    failures = [finding for finding in report.findings if not finding.ok]
    assert [finding.name for finding in failures] == ["network_probe", "probe_cleanup"]
    assert failures[0].detail == "capture prerequisite is unavailable"
    assert failures[1].detail.startswith("cleanup clock failed after launch:")
    with pytest.raises(TrafficlabError, match="capture prerequisite is unavailable.*cleanup clock failed"):
        report.require_success()


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


def test_image_still_missing_after_pull_is_owned_by_the_image_stage(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)

    class MissingAfterPull(_Docker):
        def image_inspect(self, image: str, *, deadline: float) -> CommandResult:
            self._record("image_inspect", image, deadline=deadline)
            if image == config.target.image:
                return CommandResult(1, "", "still missing")
            return super().image_inspect(image, deadline=deadline)

    report = check_docker(config, MissingAfterPull(), deadline=160.0, clock=lambda: 100.0)

    assert report.findings[-1].name == "target_image"
    assert report.findings[-1].detail == f"target image {config.target.image} is unavailable"


def test_capture_readiness_distinguishes_missing_and_unreadable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "probe"
    output.mkdir()
    assert preflight_module._capture_ready(output) is False  # pyright: ignore[reportPrivateUsage]

    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (output / "capture.json").write_bytes(render_capture_metadata(metadata))
    capture_path = output / "reference.pcapng.tmp"
    capture_path.write_bytes(b"x" * 28)
    real_read_bytes = Path.read_bytes

    def fail_capture_read(path: Path) -> bytes:
        if path == capture_path:
            raise OSError("injected header read failure")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_capture_read)
    with pytest.raises(TrafficlabError, match="could not inspect preflight capture header"):
        preflight_module._capture_ready(output)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("states", [(None,), ("running", "exited")], ids=["missing", "not-ready-then-exited"])
def test_capture_readiness_rejects_a_missing_or_stopped_service(tmp_path: Path, states: tuple[str | None, ...]) -> None:
    output = tmp_path / "probe"
    output.mkdir()

    class States:
        def __init__(self) -> None:
            self.values = iter(states)

        def service_state(
            self, compose_path: Path, project_name: str, service: str, *, deadline: float
        ) -> ServiceState | None:
            del compose_path, project_name, service, deadline
            state = next(self.values)
            if state is None:
                return None
            return ServiceState("capture", "capture", "capture", state, 127 if state == "exited" else 0)

    with pytest.raises(TrafficlabError, match="dumpcap is unavailable"):
        preflight_module._wait_capture_ready(  # pyright: ignore[reportPrivateUsage]
            cast(preflight_module.DockerPreflight, States()),
            tmp_path / "compose.json",
            "probe",
            output,
            deadline=2.0,
            clock=lambda: 1.0,
            observed={},
        )


def test_target_probe_waits_through_absent_and_running_states(tmp_path: Path) -> None:
    states = iter(
        (
            None,
            ServiceState("target", "target", "target", "running", 0),
            ServiceState("target", "target", "target", "exited", 0),
        )
    )

    class States:
        def service_state(
            self, compose_path: Path, project_name: str, service: str, *, deadline: float
        ) -> ServiceState | None:
            del compose_path, project_name, service, deadline
            return next(states)

    observed: dict[str, ServiceState] = {}
    preflight_module._wait_target(  # pyright: ignore[reportPrivateUsage]
        cast(preflight_module.DockerPreflight, States()),
        tmp_path / "compose.json",
        "probe",
        deadline=2.0,
        clock=lambda: 1.0,
        observed=observed,
    )

    assert observed["target"].state == "exited"


def test_nonmount_compose_configuration_failure_keeps_its_owner(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, tmp_path)

    class InvalidCompose(_Docker):
        def config(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
            self._record("config", compose_path, project_name, deadline=deadline)
            return CommandResult(1, "", "invalid compose")

    report = check_docker(config, InvalidCompose(), deadline=160.0, clock=lambda: 100.0)

    assert report.findings[-1].name == "compose_config"
    assert report.findings[-1].detail == "production Compose configuration is incompatible"


def test_mount_compose_exception_is_canonicalized_at_the_real_owner(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    source = tmp_path / "fixture-data"
    source.write_bytes(b"fixture")
    config = _config(
        valid_config_data,
        tmp_path,
        mounts=[{"source": str(source), "target": "/work/data", "read_only": True}],
    )
    failure = TrafficlabError("compose rejected mount", corrective_action="raw compose repair")
    docker = _Docker(failure=("config", failure))

    report = check_docker(config, docker, deadline=160.0, clock=lambda: 100.0)

    assert report.findings[-1].name == "compose_config"
    assert report.findings[-1].detail == "mount target /work/data is incompatible"
    assert report.findings[-1].corrective_action == "correct the declared container target and mode"
